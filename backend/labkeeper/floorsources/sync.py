"""Create/update floor plans from source plugins, by hand or on a schedule."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import services as sv
from ..db import run_in_session
from ..events import record
from ..floorplan import merge_cells, normalise, suggest_rack_prefix
from ..models import FloorPlan, Rack
from ..timeutil import utcnow
from .base import FloorPlanSource, FloorSourceError, SourcePlan

log = logging.getLogger(__name__)


def _cell_key(c: dict[str, Any]) -> tuple:
    return (c.get("row"), c.get("col"), c.get("type"), c.get("span_rows"), c.get("span_cols"), c.get("label"), c.get("metric"))


def upsert_source_plan(
    session: Session, source: FloorPlanSource, sp: SourcePlan, *, actor: str
) -> tuple[FloorPlan, bool, int]:
    """Insert or update the floor plan for ``<source>:<site>``. Returns (plan, created, cells changed)."""
    ref = f"{source.name}:{sp.site_id}"
    data = merge_cells(normalise(sp.plan))
    plan = session.scalar(select(FloorPlan).where(FloorPlan.source_ref == ref))
    adopted = False
    if plan is None:
        # A hand-made plan with the same name (e.g. drawn before the source was connected) becomes
        # the synced plan instead of getting a duplicate next to it.
        plan = session.scalar(select(FloorPlan).where(FloorPlan.name == data.name, FloorPlan.source_ref.is_(None)))
        if plan is not None:
            plan.source_ref = ref
            plan.source = source.name
            adopted = True
    created = plan is None
    if plan is None:
        name = data.name
        n = 2
        while session.scalar(select(FloorPlan).where(FloorPlan.name == name)):
            name = f"{data.name} ({n})"
            n += 1
        prefix = sp.rack_prefix
        if prefix is None:
            prefix = suggest_rack_prefix(data.rows, data.columns, list(session.scalars(select(Rack.name)))) or ""
        plan = FloorPlan(
            name=name, site=data.site, rack_prefix=prefix, metric_label=data.metric_label,
            source=source.name, source_ref=ref, notes=data.notes,
        )
        session.add(plan)
        before: set[tuple] = set()
    else:
        before = {_cell_key(c) for c in plan.cells or []}
        if adopted and not plan.rack_prefix:
            plan.rack_prefix = sp.rack_prefix or suggest_rack_prefix(
                data.rows, data.columns, list(session.scalars(select(Rack.name)))
            ) or ""
        if sp.rack_prefix is not None and not plan.rack_prefix:
            plan.rack_prefix = sp.rack_prefix
        plan.metric_label = plan.metric_label or data.metric_label
    new_cells = [c.model_dump() for c in data.cells]
    after = {_cell_key(c) for c in new_cells}
    changes = len(before ^ after)
    plan.rows = list(data.rows)
    plan.columns = list(data.columns)
    plan.cells = new_cells
    plan.synced_at = utcnow()
    plan.sync_error = None
    plan.source_extra = dict(sp.extra or {})
    session.flush()
    valid = {(str(c["row"]), str(c["col"])) for c in new_cells if c.get("type") in {"rack", "empty_rack"}}
    for r in list(plan.racks):
        if (r.floor_row, r.floor_col) not in valid:
            r.floor_plan = None
            r.floor_row = r.floor_col = None
    sv.link_floor_racks(session, plan)
    if created or changes:
        record(
            session,
            actor=actor,
            action="created" if created else "synced",
            object_type="floor_plan",
            object_id=plan.id,
            object_name=plan.name,
            message=(f"imported from {source.label or source.name} ({len(new_cells)} cells)" if created
                     else f"{changes} cell change{'s' if changes != 1 else ''} from {source.label or source.name}"),
        )
    return plan, created, changes


class FloorSync:
    """Background re-sync of every floor plan whose source supports live access."""

    def __init__(self, sources: dict[str, FloorPlanSource]):
        self.sources = sources
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self.last_run: dict[str, str] = {}

    def source_for(self, ref: str | None) -> tuple[FloorPlanSource | None, str | None]:
        if not ref or ":" not in ref:
            return None, None
        name, site = ref.split(":", 1)
        return self.sources.get(name), site

    async def sync_plan(self, plan_id: int, actor: str = "floor sync") -> dict[str, Any]:
        ref = await run_in_session(lambda s: (s.get(FloorPlan, plan_id) or FloorPlan()).source_ref)
        source, site = self.source_for(ref)
        if source is None or site is None:
            raise FloorSourceError("this floor plan is not linked to a source plugin")
        if not source.can_sync:
            raise FloorSourceError(source.sync_disabled_reason or f"{source.label} cannot sync from the server")
        try:
            sp = await source.fetch(site)
        except FloorSourceError as exc:
            message = str(exc)  # `exc` is cleared when the except block ends
            await run_in_session(lambda s: _set_error(s, plan_id, message))
            raise

        def apply(s: Session) -> dict[str, Any]:
            plan, _created, changes = upsert_source_plan(s, source, sp, actor=actor)
            return {"id": plan.id, "name": plan.name, "changes": changes, "synced_at": plan.synced_at}

        return await run_in_session(apply)

    async def import_site(self, source_name: str, site_id: str, actor: str) -> dict[str, Any]:
        source = self.sources.get(source_name)
        if source is None:
            raise FloorSourceError(f"no floor-plan source named {source_name}")
        if not source.can_sync:
            raise FloorSourceError(source.sync_disabled_reason or f"{source.label} cannot fetch from the server")
        sp = await source.fetch(site_id)

        def apply(s: Session) -> dict[str, Any]:
            plan, created, changes = upsert_source_plan(s, source, sp, actor=actor)
            return {"id": plan.id, "name": plan.name, "created": created, "changes": changes}

        return await run_in_session(apply)

    async def run_once(self) -> None:
        plans = await run_in_session(
            lambda s: [(p.id, p.source_ref, p.synced_at) for p in s.scalars(select(FloorPlan).where(FloorPlan.source_ref.is_not(None)))]
        )
        now = utcnow()
        for pid, ref, synced_at in plans:
            source, _site = self.source_for(ref)
            if source is None or not source.can_sync:
                continue
            if synced_at and (now - synced_at).total_seconds() < source.sync_interval * 0.9:
                continue
            try:
                await self.sync_plan(pid)
            except FloorSourceError as exc:
                log.warning("floor sync for %s failed: %s", ref, exc)
            except Exception:  # pragma: no cover - defensive
                log.exception("floor sync for %s crashed", ref)
        self.last_run["at"] = utcnow().isoformat()

    async def _loop(self) -> None:
        await asyncio.sleep(5)
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover
                log.exception("floor sync loop error")
            interval = min((s.sync_interval for s in self.sources.values() if s.can_sync), default=900.0)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=max(60.0, interval / 3))
            except TimeoutError:
                pass
            self._wake.clear()

    def start(self) -> None:
        if self._task is None and any(s.can_sync for s in self.sources.values()):
            self._task = asyncio.create_task(self._loop(), name="floor-sync")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        for s in self.sources.values():
            try:
                await s.aclose()
            except Exception:  # pragma: no cover
                pass


def _set_error(s: Session, plan_id: int, message: str) -> None:
    plan = s.get(FloorPlan, plan_id)
    if plan is not None:
        plan.sync_error = message[:500]
