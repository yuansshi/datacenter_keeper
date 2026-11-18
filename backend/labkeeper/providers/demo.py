"""Simulated provider so the UI is alive without hardware or API keys (``labkeeper serve --demo``)."""

from __future__ import annotations

import hashlib
import random
import time
from typing import Any

from ..timeutil import utcnow
from .base import DeviceRef, NetworkInfo, StatusProvider, StatusUpdate

DEMO_NETWORKS = [
    ("L_DEMO_SMOKE", "Example smoke lab"),
    ("L_DEMO_REGRESSION", "Example regression lab"),
    ("L_DEMO_PERF", "Example performance lab"),
]


def _seed(key: str) -> int:
    return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)


def _port_count(model: str | None) -> int:
    m = (model or "").upper()
    for n in (48, 24, 16, 12, 8):
        if f"-{n}" in m or m.endswith(str(n)) or f"{n}P" in m or f"{n}U" in m or f"{n}L" in m or f"{n}Y" in m:
            return n
    return 24


class DemoProvider(StatusProvider):
    name = "demo"
    label = "Demo simulator"

    @property
    def enabled(self) -> bool:
        return bool(self.settings.demo)

    @property
    def disabled_reason(self) -> str | None:
        return None if self.enabled else "only active with --demo"

    @property
    def poll_interval(self) -> float:
        return 20.0

    def _status(self, ref: DeviceRef) -> str:
        rnd = random.Random(_seed(ref.key) + int(time.time() // 300))  # flips every ~5 minutes
        base = random.Random(_seed(ref.key)).random()
        if base < 0.06:
            return "offline"
        if base < 0.11:
            return "alerting" if rnd.random() < 0.7 else "online"
        return "online" if rnd.random() > 0.03 else "alerting"

    async def poll(self, devices: list[DeviceRef]) -> dict[str, StatusUpdate]:
        now = utcnow()
        out: dict[str, StatusUpdate] = {}
        for ref in devices:
            status = self._status(ref)
            net_id, net_name = DEMO_NETWORKS[_seed(ref.name.split("_")[0]) % len(DEMO_NETWORKS)]
            live: dict[str, Any] = {
                "inventory": {
                    "source": self.name,
                    "status": status,
                    "model": ref.model,
                    "firmware": "Demo firmware 1.0",
                    "lan_ip": f"192.0.2.{_seed(ref.name) % 250 + 2}",
                    "external_url": None,
                    "network_name": net_name,
                    "checked_at": now.isoformat(),
                }
            }
            out[ref.key] = StatusUpdate(
                status=status,
                detail={
                    "offline": "simulated device is not responding",
                    "alerting": "uplink flapping on port 24 (simulated)",
                }.get(status),
                live=live,
                network=NetworkInfo(key=f"demo:{net_id}", name=net_name, provider="demo", org_id="000000"),
                last_seen=now if status != "offline" else None,
            )
        return out

    async def device_live(self, device: DeviceRef) -> dict[str, Any] | None:
        if device.role != "switch":
            return {"source": "demo", "ports": [], "error": None}
        rnd = random.Random(_seed(device.key) + int(time.time() // 60))
        n = _port_count(device.model)
        ports = []
        for i in range(1, n + 1):
            r = rnd.random()
            status = "Connected" if r < 0.55 else ("Disconnected" if r < 0.93 else "Disabled")
            uplink = i == n
            ports.append(
                {
                    "id": str(i),
                    "name": "Uplink" if uplink else None,
                    "enabled": status != "Disabled",
                    "status": "Connected" if uplink else status,
                    "is_uplink": uplink,
                    "speed": ("10 Gbps" if uplink else rnd.choice(["1 Gbps", "1 Gbps", "100 Mbps", "2.5 Gbps"]))
                    if status == "Connected" or uplink
                    else None,
                    "duplex": "full" if status == "Connected" else None,
                    "errors": ["CRC errors"] if r > 0.985 else [],
                    "warnings": [],
                    "clients": rnd.randint(1, 6) if status == "Connected" else 0,
                    "poe": status == "Connected" and rnd.random() < 0.4,
                    "power_wh": round(rnd.uniform(1, 30), 1) if status == "Connected" else 0,
                    "type": "trunk" if uplink else "access",
                    "vlan": 1 if uplink else rnd.choice([10, 20, 30, 100]),
                    "allowed_vlans": "all" if uplink else None,
                    "neighbor": "core-sw-01" if uplink else None,
                    "neighbor_port": "Ethernet1/12" if uplink else None,
                    "traffic_kbps": round(rnd.uniform(0, 900), 1) if status == "Connected" else 0,
                }
            )
        return {"source": "demo", "ports": ports, "error": None}
