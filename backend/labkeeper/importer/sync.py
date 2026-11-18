"""Apply a scan of testbed files to the database (idempotent upsert)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..events import record
from ..models import Device, ImportSource, Network, Rack, Testbed, TestbedDevice
from ..secrets_store import secret_store
from ..timeutil import utcnow
from .pyats import Normalizer, ScanResult, scan

log = logging.getLogger(__name__)


@dataclass
class ImportSummary:
    files: int = 0
    testbeds: int = 0
    bases: int = 0
    invalid: int = 0
    devices: int = 0
    devices_added: int = 0
    devices_updated: int = 0
    devices_removed: int = 0
    testbeds_added: int = 0
    testbeds_removed: int = 0
    racks_added: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)
    finished_at: str | None = None

    def as_dict(self) -> dict:
        return dict(self.__dict__)


DEVICE_FIELDS = (
    "name",
    "alias",
    "vendor",
    "type",
    "os",
    "platform",
    "model",
    "serial",
    "role",
    "mgmt_ip",
    "is_shared",
    "is_virtual",
    "stack_name",
    "org_id",
    "provider",
    "location",
    "connections",
    "custom",
    "tags",
)


def run_scan(settings: Settings) -> ScanResult:
    normalizer = Normalizer(settings.testbeds.shared_device_types, settings.testbeds.location_pattern)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    return scan(settings.testbed_roots(), normalizer, settings.testbeds.exclude, settings.testbeds.include)


def apply_scan(session: Session, result: ScanResult, settings: Settings, actor: str = "importer") -> ImportSummary:
    now = utcnow()
    summary = ImportSummary(files=len(result.files))
    demo_mode = settings.demo

    # --- racks -------------------------------------------------------------------------------------
    racks = {r.name: r for r in session.scalars(select(Rack))}
    for spec in result.devices.values():
        if spec.rack and spec.rack not in racks:
            rack = Rack(name=spec.rack, height=settings.testbeds.default_rack_height, source="yaml")
            session.add(rack)
            racks[spec.rack] = rack
            summary.racks_added += 1
    session.flush()

    # --- yaml-declared networks ------------------------------------------------------------------
    networks = {n.key: n for n in session.scalars(select(Network))}
    for spec in result.devices.values():
        if spec.network:
            nkey = f"yaml:{spec.network}"
            if nkey not in networks:
                net = Network(key=nkey, name=str(spec.network), provider="yaml")
                session.add(net)
                networks[nkey] = net
    session.flush()

    # --- devices -----------------------------------------------------------------------------------
    existing = {d.key: d for d in session.scalars(select(Device))}
    seen: set[str] = set()
    plans = {}
    for key, spec in result.devices.items():
        seen.add(key)
        plans[key] = spec.plans
        dev = existing.get(key)
        values = {f: getattr(spec, f) for f in DEVICE_FIELDS}
        if demo_mode and not values["is_virtual"]:
            values["provider"] = "demo"
        values["source_file"] = str(spec.origin)
        if dev is None:
            dev = Device(key=key, **values)
            dev.u_height = spec.u_height
            session.add(dev)
            existing[key] = dev
            summary.devices_added += 1
        else:
            changed = False
            for f, v in values.items():
                if getattr(dev, f) != v:
                    setattr(dev, f, v)
                    changed = True
            if dev.removed_at is not None:
                dev.removed_at = None
                changed = True
            if changed:
                summary.devices_updated += 1
        # Placement from YAML unless someone placed the device by hand in the rack editor.
        if dev.placement_source != "manual":
            rack = racks.get(spec.rack) if spec.rack else None
            dev.rack = rack
            dev.rack_position = spec.rack_position if rack else None
            dev.placement_source = "yaml" if rack else None
            if spec.u_height and dev.u_height != spec.u_height and dev.placement_source != "manual":
                dev.u_height = spec.u_height
        if spec.network:
            dev.network = networks.get(f"yaml:{spec.network}")
    session.flush()

    for key, dev in existing.items():
        if key not in seen and dev.removed_at is None and dev.source_file and _under_roots(dev.source_file, result.roots):
            dev.removed_at = now
            summary.devices_removed += 1

    # stack members -> logical stack device (same defining file, stack name prefix match)
    by_origin: dict[str, list[Device]] = {}
    for key in seen:
        d = existing[key]
        by_origin.setdefault(d.source_file or "", []).append(d)
    for devs in by_origin.values():
        stacks = [d for d in devs if d.is_virtual]
        if not stacks:
            continue
        for d in devs:
            if d.is_virtual or not d.stack_name:
                continue
            match = next((s for s in stacks if s.name.upper().startswith(d.stack_name.upper())), None)
            if match is None and len(stacks) == 1:
                match = stacks[0]
            if match is not None:  # leave stacks found by a status provider alone
                d.parent = match
    session.flush()

    # --- testbeds ----------------------------------------------------------------------------------
    tb_existing = {t.key: t for t in session.scalars(select(Testbed))}
    tb_seen: set[str] = set()
    for spec in result.testbeds:
        tb_seen.add(spec.key)
        tb = tb_existing.get(spec.key)
        if tb is None:
            tb = Testbed(key=spec.key)
            session.add(tb)
            tb_existing[spec.key] = tb
            summary.testbeds_added += 1
        tb.name = spec.name
        tb.source_file = str(spec.path)
        tb.group = spec.group
        tb.org_id = spec.org_id
        tb.custom = spec.custom
        tb.topology = spec.topology
        tb.tags = spec.tags
        if spec.description:
            tb.description = spec.description
        tb.removed_at = None
        session.flush()
        wanted = {}
        for dkey in spec.device_keys:
            dev = existing[dkey]
            wanted[dev.id] = not dev.is_shared
        current = {m.device_id: m for m in tb.members}
        for dev_id, exclusive in wanted.items():
            m = current.get(dev_id)
            if m is None:
                tb.members.append(TestbedDevice(device_id=dev_id, exclusive=exclusive))
            elif m.exclusive != exclusive:
                m.exclusive = exclusive
        for dev_id, m in current.items():
            if dev_id not in wanted:
                tb.members.remove(m)
    for key, tb in tb_existing.items():
        if key not in tb_seen and tb.removed_at is None and tb.source_file and _under_roots(tb.source_file, result.roots):
            tb.removed_at = now
            summary.testbeds_removed += 1
    session.flush()

    # Adapter declarations are optional; the importer does not interpret vendor-specific fields.
    from ..providers.base import declared_networks

    for spec in result.testbeds:
        for declaration in declared_networks(spec.custom or {}):
            decl = declaration.network
            nkey = decl.key
            net = networks.get(nkey)
            if net is None:
                net = Network(key=nkey, name=decl.name, provider=decl.provider, org_id=decl.org_id,
                              product_types=[], tags=[])
                session.add(net)
                networks[nkey] = net
            net.region = net.region or decl.region
            net.api_host = net.api_host or decl.api_host
            net.org_id = net.org_id or decl.org_id
    session.flush()

    # --- import sources ------------------------------------------------------------------------------
    sources = {s.path: s for s in session.scalars(select(ImportSource))}
    seen_paths = set()
    for rep in result.files:
        p = str(rep.path)
        seen_paths.add(p)
        src = sources.get(p) or ImportSource(path=p, root=str(rep.root))
        if src.id is None:
            session.add(src)
        src.root = str(rep.root)
        src.kind = rep.kind
        src.error = rep.error
        src.mtime = rep.mtime
        src.device_count = rep.device_count
        src.last_imported_at = now
        src.testbed_id = tb_existing[rep.testbed_key].id if rep.testbed_key in tb_existing else None
        if rep.kind == "testbed":
            summary.testbeds += 1
        elif rep.kind in {"base", "excluded"}:
            summary.bases += rep.kind == "base"
        else:
            summary.invalid += 1
            summary.errors.append({"file": p, "error": rep.error or "invalid"})
    for p, src in sources.items():
        if p not in seen_paths:
            session.delete(src)

    summary.devices = len(seen)
    summary.finished_at = now.isoformat()
    secret_store.replace_all(plans)

    if summary.devices_added or summary.devices_removed or summary.testbeds_added or summary.testbeds_removed:
        record(
            session,
            actor=actor,
            action="imported",
            object_type="import",
            object_name=f"{summary.testbeds} testbeds",
            message=(
                f"{summary.testbeds} testbeds, {summary.devices} devices "
                f"(+{summary.devices_added} / -{summary.devices_removed} devices, "
                f"+{summary.testbeds_added} / -{summary.testbeds_removed} testbeds)"
            ),
            data={k: v for k, v in summary.as_dict().items() if k != "errors"},
        )
    return summary


def _under_roots(path: str, roots: list[Path]) -> bool:
    p = Path(path)
    return any(p == r or p.is_relative_to(r) for r in roots)
