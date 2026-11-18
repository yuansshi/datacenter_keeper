"""Vendor-neutral reachability: a device is online if any management port accepts TCP."""

from __future__ import annotations

import asyncio

from ..timeutil import utcnow
from .base import DeviceRef, StatusProvider, StatusUpdate


async def tcp_open(host: str, port: int, timeout: float) -> bool:
    try:
        _reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except (OSError, TimeoutError):
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return True


class ReachabilityProvider(StatusProvider):
    name = "reachability"
    label = "TCP reachability"

    @property
    def enabled(self) -> bool:
        return self.settings.reachability.enabled

    @property
    def disabled_reason(self) -> str | None:
        return None if self.enabled else "disabled in config"

    @property
    def poll_interval(self) -> float:
        return float(self.settings.reachability.poll_interval)

    async def poll(self, devices: list[DeviceRef]) -> dict[str, StatusUpdate]:
        cfg = self.settings.reachability
        sem = asyncio.Semaphore(cfg.concurrency)
        updates: dict[str, StatusUpdate] = {}

        async def check(ref: DeviceRef) -> None:
            if not ref.mgmt_ip:
                return
            async with sem:
                results = await asyncio.gather(*(tcp_open(ref.mgmt_ip, p, cfg.timeout) for p in cfg.ports))
            open_ports = [p for p, ok in zip(cfg.ports, results, strict=True) if ok]
            live = {
                "reachability": {"open_ports": open_ports, "checked_at": utcnow().isoformat()},
                "inventory": {"source": self.name, "model": ref.model, "lan_ip": ref.mgmt_ip,
                              "checked_at": utcnow().isoformat()},
            }
            if open_ports:
                updates[ref.key] = StatusUpdate(
                    status="online",
                    detail=f"TCP {', '.join(map(str, open_ports))} open on {ref.mgmt_ip}",
                    live=live,
                    last_seen=utcnow(),
                )
            else:
                updates[ref.key] = StatusUpdate(
                    status="offline",
                    detail=f"no answer on TCP {', '.join(map(str, cfg.ports))} at {ref.mgmt_ip}",
                    live=live,
                )

        await asyncio.gather(*(check(r) for r in devices))
        return updates
