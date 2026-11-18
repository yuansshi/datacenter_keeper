"""Background polling: one loop per enabled provider, results written to the devices table."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import run_in_session
from ..events import record
from ..models import Device, Network, Testbed
from ..timeutil import utcnow
from .base import DeviceRef, StatusProvider, StatusUpdate

log = logging.getLogger(__name__)


@dataclass
class ProviderHealth:
    name: str
    label: str
    enabled: bool
    disabled_reason: str | None = None
    last_run: datetime | None = None
    last_ok: datetime | None = None
    last_error: str | None = None
    last_duration_ms: int | None = None
    device_count: int = 0
    details: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "enabled": self.enabled,
            "disabled_reason": self.disabled_reason,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "last_ok": self.last_ok.isoformat() if self.last_ok else None,
            "last_error": self.last_error,
            "last_duration_ms": self.last_duration_ms,
            "device_count": self.device_count,
            **(self.details or {}),
        }


def _refs(session: Session, provider: str, device_ids: list[int] | None = None) -> list[DeviceRef]:
    q = select(Device).where(Device.provider == provider, Device.removed_at.is_(None), Device.is_virtual.is_(False))
    if device_ids:
        q = q.where(Device.id.in_(device_ids))
    return [
        DeviceRef(
            id=d.id, key=d.key, name=d.name, serial=d.serial, mgmt_ip=d.mgmt_ip, org_id=d.org_id,
            model=d.model, role=d.role, type=d.type,
        )
        for d in session.scalars(q)
    ]


def _testbed_customs(session: Session) -> list[dict[str, Any]]:
    return [t.custom or {} for t in session.scalars(select(Testbed).where(Testbed.removed_at.is_(None)))]


def device_ref(d: Device) -> DeviceRef:
    return DeviceRef(
        id=d.id, key=d.key, name=d.name, serial=d.serial, mgmt_ip=d.mgmt_ip, org_id=d.org_id,
        model=d.model, role=d.role, type=d.type,
    )


def apply_updates(session: Session, updates: dict[str, StatusUpdate], provider: str) -> int:
    if not updates:
        return 0
    now = utcnow()
    devices = {d.key: d for d in session.scalars(select(Device).where(Device.key.in_(list(updates))))}
    networks = {n.key: n for n in session.scalars(select(Network))}
    changed = 0
    for key, up in updates.items():
        d = devices.get(key)
        if d is None:
            continue
        if up.network:
            net = networks.get(up.network.key)
            if net is None:
                net = Network(key=up.network.key, name=up.network.name, provider=up.network.provider)
                session.add(net)
                networks[up.network.key] = net
            net.name = up.network.name
            net.org_id = up.network.org_id
            net.url = up.network.url
            net.product_types = up.network.product_types
            net.tags = up.network.tags
            if up.network.region:
                net.region = up.network.region
                net.api_host = up.network.api_host
            if d.network is None or not (d.network.key or "").startswith("yaml:"):
                d.network = net
        if d.status != up.status:
            old = d.status
            d.status = up.status
            d.status_changed_at = now
            changed += 1
            if old != "unknown" or up.status in {"offline", "alerting"}:
                record(
                    session,
                    actor=provider,
                    action="status",
                    object_type="device",
                    object_id=d.id,
                    object_name=d.name,
                    message=f"{old} → {up.status}" + (f": {up.detail}" if up.detail else ""),
                )
        d.status_detail = up.detail
        d.status_checked_at = now
        if up.last_seen:
            d.last_seen_at = up.last_seen
        if up.live:
            d.live = {**(d.live or {}), **up.live}
    _apply_stacks(session, devices, updates, provider)
    return changed


STACK_PREFIX = "stack:"  # key of a stack device created from provider data ("stack:meraki:<id>")


def _apply_stacks(session: Session, devices: dict[str, Device], updates: dict[str, StatusUpdate], provider: str) -> None:
    """Group stack members under one logical stack device, as the provider reports them.

    The stack device is ``is_virtual`` like a ``chassis_type: stack`` device from a testbed file, so it
    gets its status from its worst member and counts as in use when a member is booked. A member
    that the testbed file already puts under a logical stack keeps that parent; the provider's view
    (roles, switch numbers) is added to it instead of creating a second stack.
    """
    checked = {k: up for k, up in updates.items() if up.stack_checked and k in devices}
    if not checked:
        return
    now = utcnow()
    stacks = {s.key: s for s in session.scalars(select(Device).where(Device.key.like(STACK_PREFIX + "%")))}
    for key, up in checked.items():
        d = devices[key]
        parent = d.parent
        info = up.stack
        if info is None:
            d.live = {k: v for k, v in (d.live or {}).items() if k != "stack"}
            if parent is not None and parent.key.startswith(STACK_PREFIX):
                record(session, actor=provider, action="unstacked", object_type="device", object_id=d.id,
                       object_name=d.name, message=f"no longer in stack {parent.name}")
                d.parent = None
            continue
        roster = {
            "key": info.key, "name": info.name, "provider": provider,
            "members": info.members, **{k: v for k, v in info.extra.items() if v is not None},
        }
        d.live = {**(d.live or {}), "stack": {"key": info.key, "name": info.name, "role": info.role, "number": info.number}}
        if parent is not None and parent.is_virtual and not parent.key.startswith(STACK_PREFIX):
            parent.live = {**(parent.live or {}), "stack": roster}
            continue
        skey = STACK_PREFIX + info.key
        stack = stacks.get(skey)
        if stack is None:
            stack = Device(
                key=skey, name=info.name, is_virtual=True, role="switch", type="switch-stack", u_height=0,
                connections={}, custom={}, tags=[], live={}, status="unknown",
            )
            session.add(stack)
            stacks[skey] = stack
        stack.name = info.name
        stack.vendor = d.vendor
        stack.provider = provider
        stack.org_id = d.org_id
        stack.network = d.network
        stack.removed_at = None
        stack.live = {**(stack.live or {}), "stack": roster}
        if parent is not stack:
            d.parent = stack
            record(session, actor=provider, action="stacked", object_type="device", object_id=d.id,
                   object_name=d.name, message=f"member of stack {info.name}")
    session.flush()
    for stack in stacks.values():
        if stack.removed_at is None and not any(m.removed_at is None for m in stack.members):
            stack.removed_at = now


class Poller:
    def __init__(self, providers: dict[str, StatusProvider]):
        self.providers = providers
        self.health = {
            name: ProviderHealth(name=name, label=p.label or name, enabled=p.enabled, disabled_reason=p.disabled_reason)
            for name, p in providers.items()
        }
        self._tasks: list[asyncio.Task] = []
        self._wake: dict[str, asyncio.Event] = {name: asyncio.Event() for name in providers}

    async def poll_once(self, name: str, device_ids: list[int] | None = None) -> int:
        provider = self.providers[name]
        health = self.health[name]
        if type(provider).observe_testbeds is not StatusProvider.observe_testbeds:
            provider.observe_testbeds(await run_in_session(_testbed_customs))
        health.enabled = provider.enabled
        health.disabled_reason = provider.disabled_reason
        if not provider.enabled:
            return 0
        refs = await run_in_session(lambda s: _refs(s, name, device_ids))
        if not device_ids:
            health.device_count = len(refs)
        if not refs:
            return 0
        started = utcnow()
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        health.last_run = started
        try:
            updates = await provider.poll(refs)
        except Exception as exc:
            health.last_error = str(exc)[:500]
            log.warning("provider %s poll failed: %s", name, exc)
            return 0
        health.last_error = None
        health.last_ok = utcnow()
        health.last_duration_ms = int((loop.time() - t0) * 1000)
        health.details = provider.details() or None
        return await run_in_session(lambda s: apply_updates(s, updates, name))

    async def _loop(self, name: str) -> None:
        provider = self.providers[name]
        await asyncio.sleep(1.0)
        while True:
            try:
                await self.poll_once(name)
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - defensive
                log.exception("poll loop for %s crashed; continuing", name)
            wake = self._wake[name]
            try:
                await asyncio.wait_for(wake.wait(), timeout=max(provider.poll_interval, 5.0))
            except TimeoutError:
                pass
            wake.clear()

    def trigger(self, name: str | None = None) -> None:
        for n, ev in self._wake.items():
            if name is None or n == name:
                ev.set()

    def start(self) -> None:
        for name, provider in self.providers.items():
            if provider.enabled:
                self._tasks.append(asyncio.create_task(self._loop(name), name=f"poll-{name}"))

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()
        for p in self.providers.values():
            try:
                await p.aclose()
            except Exception:  # pragma: no cover
                pass

    def status(self) -> list[dict[str, Any]]:
        for name, p in self.providers.items():
            self.health[name].enabled = p.enabled
            self.health[name].disabled_reason = p.disabled_reason
            self.health[name].details = p.details() or None
        return [h.as_dict() for h in self.health.values()]
