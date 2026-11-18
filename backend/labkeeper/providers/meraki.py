"""Cisco Meraki Dashboard API provider.

Uses (all read-only GETs):
  GET /organizations                                  - discover orgs when none are configured
  GET /organizations/{org}/devices/availabilities     - online / alerting / offline / dormant
  GET /organizations/{org}/devices                    - model, firmware, LAN IP, dashboard URL, network
  GET /organizations/{org}/networks                   - network names (for "organize by network")
  GET /networks/{network}/switch/stacks               - switch stacks and their members (with inventory)
  GET /devices/{serial}/switch/ports/statuses         - per-port live state (device page, on demand)
  GET /devices/{serial}/switch/ports                  - per-port config (name, VLAN, type)

``getOrganizationDevicesStatuses`` is deprecated upstream, so availabilities is used for state.
Several Meraki clouds can be polled at once: global (api.meraki.com) plus regional ones such as
Canada (api.meraki.ca), China (api.meraki.cn), India (api.meraki.in) and the US government cloud
(api.gov-meraki.com), each with its own key, from ``meraki.regions`` or declared in testbed files.

Requests honour ``Retry-After`` on 429 (Meraki allows ~10 req/s per org) and follow ``Link`` pagination.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

from ..timeutil import utcnow
from .base import DeviceRef, NetworkDeclaration, NetworkInfo, StackInfo, StatusProvider, StatusUpdate

log = logging.getLogger(__name__)

USER_AGENT = "Labkeeper/0.1 Labkeeper"  # Meraki asks integrations to identify as "<app> <vendor>"


class MerakiError(Exception):
    pass


class MerakiClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.meraki.com/api/v1",
        timeout: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
        max_retries: int = 5,
    ):
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        host = (urlparse(self.base_url).hostname or "").lower()
        # Some clouds (the US government one) answer with a 308 to a shard host. httpx drops the key on a
        # cross-host redirect, so redirects are followed here, and only within the cloud's own domain.
        self._domain = ".".join(host.split(".")[-2:])
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
            headers={
                "X-Cisco-Meraki-API-Key": api_key,
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        self._sem = asyncio.Semaphore(8)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, url: str, params: dict[str, Any] | list[tuple[str, Any]] | None = None) -> httpx.Response:
        attempt = 0
        hops = 0
        while True:
            async with self._sem:
                resp = await self._client.get(url, params=params)
            if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("Location"):
                target = resp.url.join(resp.headers["Location"])
                target_host = (target.host or "").lower()
                if hops >= 5 or not (target_host == self._domain or target_host.endswith("." + self._domain)):
                    raise MerakiError(f"GET {url}: refusing redirect to {target_host or 'unknown host'}")
                url, params, hops = str(target), None, hops + 1
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                attempt += 1
                if attempt > self.max_retries:
                    raise MerakiError(f"GET {url}: HTTP {resp.status_code} after {self.max_retries} retries")
                try:
                    delay = float(resp.headers.get("Retry-After", "1"))
                except ValueError:
                    delay = 1.0
                await asyncio.sleep(min(max(delay, 0.2), 30.0) * (1 if resp.status_code == 429 else attempt))
                continue
            if resp.status_code in (401, 403):
                raise MerakiError(f"GET {url}: HTTP {resp.status_code} (check the API key and its org access)")
            if resp.status_code == 404:
                raise MerakiError(f"GET {url}: not found")
            if resp.status_code >= 400:
                raise MerakiError(f"GET {url}: HTTP {resp.status_code}")
            return resp

    async def get_all(self, path: str, params: dict[str, Any] | None = None, max_pages: int = 50) -> list[Any]:
        items: list[Any] = []
        url: str | None = path
        query: dict[str, Any] | None = {"perPage": 1000, **(params or {})}
        for _ in range(max_pages):
            assert url is not None
            resp = await self._get(url, params=query)
            data = resp.json()
            if isinstance(data, list):
                items.extend(data)
            elif isinstance(data, dict) and isinstance(data.get("items"), list):
                items.extend(data["items"])
            else:
                return data if isinstance(data, list) else [data]
            nxt = resp.links.get("next", {}).get("url")
            if not nxt:
                break
            url, query = nxt, None  # the next link already carries the query
        else:
            raise MerakiError(f"GET {path}: pagination limit reached before the complete response")
        return items

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        resp = await self._get(path, params=params)
        return resp.json()

    async def organizations(self) -> list[dict[str, Any]]:
        return await self.get_all("/organizations")

    async def availabilities(self, org_id: str) -> list[dict[str, Any]]:
        return await self.get_all(f"/organizations/{org_id}/devices/availabilities")

    async def devices(self, org_id: str) -> list[dict[str, Any]]:
        return await self.get_all(f"/organizations/{org_id}/devices")

    async def networks(self, org_id: str) -> list[dict[str, Any]]:
        return await self.get_all(f"/organizations/{org_id}/networks")

    async def switch_stacks(self, network_id: str) -> list[dict[str, Any]]:
        return await self.get(f"/networks/{network_id}/switch/stacks")

    async def switch_port_statuses(self, serial: str) -> list[dict[str, Any]]:
        return await self.get(f"/devices/{serial}/switch/ports/statuses")

    async def switch_ports(self, serial: str) -> list[dict[str, Any]]:
        return await self.get(f"/devices/{serial}/switch/ports")


def _port_sort_key(port_id: str) -> tuple:
    parts = []
    for chunk in str(port_id).replace("/", "_").split("_"):
        parts.append((0, int(chunk), "") if chunk.isdigit() else (1, 0, chunk))
    return tuple(parts)


GLOBAL_HOST = "api.meraki.com"
STATUS_RANK = {"online": 0, "alerting": 0, "offline": 1, "dormant": 2}  # where a moved device really checks in
AVAILABILITY_STATUSES = ("online", "alerting", "offline", "dormant", "unknown")


def _availability_record(value: Any) -> tuple[str, str]:
    if not isinstance(value, dict):
        raise MerakiError("Meraki availability response contains an invalid record")
    serial, status = value.get("serial"), value.get("status")
    if not isinstance(serial, str) or not serial.strip() or not isinstance(status, str) or not status.strip():
        raise MerakiError("Meraki availability response contains an invalid record")
    normalized_status = status.strip().lower()
    return serial.strip().upper(), normalized_status if normalized_status in AVAILABILITY_STATUSES else "unknown"


def _api_host(base_url: str) -> str:
    """API host of a base URL. Shard hosts (n123.meraki.com) are the global cloud: Meraki retired them."""
    host = (urlparse(base_url if "://" in base_url else f"https://{base_url}").hostname or "").lower()
    return GLOBAL_HOST if host.endswith(".meraki.com") else host


@dataclass
class Region:
    """One Meraki cloud: global, or a regional one (Canada, China, India, US government)."""

    host: str
    name: str
    base_url: str
    api_key: str | None = None
    api_key_env: str | None = None
    org_ids: set[str] = field(default_factory=set)  # named by config or testbed files
    explicit_orgs: bool = False  # config lists the orgs: poll only those
    source: str = "config"


def regions_from_testbed(custom: dict[str, Any]) -> list[Region]:
    """Regions a testbed file declares (system_test style):
    custom.meraki_api_base_url + api_key_env + organization_id (+ geocloud_name), and custom.geoclouds."""
    out: list[Region] = []

    def add(name: Any, base: Any, env: Any, org: Any) -> None:
        if not base:
            return
        host = _api_host(str(base))
        out.append(Region(
            host=host, name=str(name or host), base_url=f"https://{host}/api/v1",
            api_key_env=str(env) if env else None, org_ids={str(org)} if org else set(), source="testbed",
        ))

    if not isinstance(custom, dict):
        return out
    add(custom.get("geocloud_name"), custom.get("meraki_api_base_url"), custom.get("api_key_env"), custom.get("organization_id"))
    clouds = custom.get("geoclouds")
    for name, gc in (clouds.items() if isinstance(clouds, dict) else []):
        if isinstance(gc, dict):
            add(name, gc.get("base_url"), gc.get("api_key_env"), gc.get("organization_id"))
    return out


def dashboard_host(api_host: str) -> str:
    """Dashboard UI host of a cloud: api.meraki.ca -> dashboard.meraki.ca."""
    return "dashboard." + api_host[4:] if api_host.startswith("api.") else api_host


def cloud_label(name: str) -> str:
    """How a declared cloud network is named until the API reports its real name."""
    if re.fullmatch(r"federal|fedramp|us government", name.strip(), re.I):
        return "FedRAMP (US government cloud)"
    if re.fullmatch(r"china|canada|india", name.strip(), re.I):
        return f"GeoCloud {name.strip().title()}"
    return name.strip()


def networks_from_testbed(custom: dict[str, Any]) -> list[dict[str, Any]]:
    """Meraki networks a testbed file declares (system_test style), with their cloud:
    custom.network_id + organization_id (+ geocloud_name, meraki_api_base_url) is the testbed's own
    network; custom.geoclouds and custom.client_* name related networks. ``own`` marks the first."""
    out: list[dict[str, Any]] = []
    if not isinstance(custom, dict):
        return out

    def add(name: Any, network: Any, org: Any, base: Any, own: bool) -> None:
        if not network:
            return
        host = _api_host(str(base)) if base else GLOBAL_HOST
        cloud = "Global" if host == GLOBAL_HOST else (str(name).strip() if name else host)
        label = str(name).strip() if name else cloud
        out.append({"network_id": str(network), "org_id": str(org) if org else None, "cloud": cloud,
                    "label": label, "api_host": host, "own": own})

    add(custom.get("geocloud_name"), custom.get("network_id"), custom.get("organization_id"),
        custom.get("meraki_api_base_url"), True)
    add(None, custom.get("client_network_id"), custom.get("client_organization_id"),
        custom.get("client_meraki_api_base_url"), False)
    clouds = custom.get("geoclouds")
    for name, gc in (clouds.items() if isinstance(clouds, dict) else []):
        if isinstance(gc, dict):
            add(name, gc.get("network_id"), gc.get("organization_id"), gc.get("base_url"), False)
    contexts = custom.get("geocloud_membership_contexts")
    for name, gc in (contexts.items() if isinstance(contexts, dict) else []):
        if isinstance(gc, dict):
            add(name, gc.get("network_id"), gc.get("organization_id"), gc.get("base_url"), False)
    return out


class MerakiProvider(StatusProvider):
    name = "meraki"
    label = "Cisco Meraki Dashboard"

    @classmethod
    def declared_networks(cls, custom: dict[str, Any]) -> list[NetworkDeclaration]:
        return [
            NetworkDeclaration(
                network=NetworkInfo(
                    key=f"meraki:{item['network_id']}", name=cloud_label(item["label"]),
                    provider=cls.name, org_id=item["org_id"], region=item["cloud"], api_host=item["api_host"],
                ),
                own=item["own"],
            )
            for item in networks_from_testbed(custom)
        ]

    @classmethod
    def external_host(cls, api_host: str) -> str | None:
        return dashboard_host(api_host)

    def __init__(self, settings, transport: httpx.AsyncBaseTransport | None = None):
        super().__init__(settings)
        self._transport = transport
        self._clients: dict[str, MerakiClient] = {}  # region host -> client
        self._inventory: dict[str, dict[str, dict[str, Any]]] = {}  # serial -> region host -> device record
        self._networks: dict[str, dict[str, Any]] = {}  # network id -> record (+ _region)
        self._inventory_at: dict[tuple[str, str], float] = {}  # (region, org) -> monotonic time of last refresh
        self._visible: dict[str, tuple[float, list[str] | None]] = {}  # region -> (when, orgs the key can list)
        self._where: dict[str, str] = {}  # serial -> region it was last seen in
        self._region_state: dict[str, dict[str, Any]] = {}  # region -> last poll result for the settings page
        self._discovered: list[Region] = []
        self._port_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._stacks: dict[str, StackInfo] = {}  # serial -> the stack it is in
        self._stacks_at: dict[str, float] = {}  # network id -> monotonic time of last successful read

    @property
    def cfg(self):
        return self.settings.meraki

    # -- regions -----------------------------------------------------------------------------------------
    def regions(self) -> list[Region]:
        """Global (the configured key) + regions from config + regions declared in testbed files."""
        merged: dict[str, Region] = {}

        def put(r: Region) -> None:
            cur = merged.get(r.host)
            if cur is None:
                merged[r.host] = r
                return
            cur.org_ids |= r.org_ids
            cur.api_key = cur.api_key or r.api_key
            cur.api_key_env = cur.api_key_env or r.api_key_env
            if cur.name == cur.host:
                cur.name = r.name

        put(Region(
            host=_api_host(self.cfg.base_url), name="Global", base_url=self.cfg.base_url.rstrip("/"),
            api_key=self.settings.meraki_api_key, api_key_env=self.cfg.api_key_env,
            org_ids={str(o) for o in self.cfg.org_ids}, explicit_orgs=bool(self.cfg.org_ids),
        ))
        for rc in self.cfg.regions:
            put(Region(
                host=_api_host(rc.base_url), name=rc.name, base_url=rc.base_url.rstrip("/"),
                api_key=rc.api_key or (os.environ.get(rc.api_key_env) if rc.api_key_env else None) or None,
                api_key_env=rc.api_key_env, org_ids={str(o) for o in rc.org_ids}, explicit_orgs=bool(rc.org_ids),
            ))
        if self.cfg.regions_from_testbeds:
            for r in self._discovered:
                put(Region(host=r.host, name=r.name, base_url=r.base_url, api_key_env=r.api_key_env,
                           org_ids=set(r.org_ids), source="testbed"))
        for r in merged.values():
            if not r.api_key and r.api_key_env:
                r.api_key = os.environ.get(r.api_key_env) or None
        return list(merged.values())

    def observe_testbeds(self, customs: list[dict[str, Any]]) -> None:
        found: list[Region] = []
        for c in customs:
            found.extend(regions_from_testbed(c))
        self._discovered = found

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.enabled and any(r.api_key for r in self.regions()))

    @property
    def disabled_reason(self) -> str | None:
        if not self.cfg.enabled:
            return "disabled in config"
        if not self.enabled:
            return "set LABKEEPER_MERAKI__API_KEY or MERAKI_DASHBOARD_API_KEY"
        return None

    @property
    def poll_interval(self) -> float:
        return float(self.cfg.poll_interval)

    def client(self, region: Region | str | None = None) -> MerakiClient:
        regions = {r.host: r for r in self.regions()}
        host = region.host if isinstance(region, Region) else (region or _api_host(self.cfg.base_url))
        r = regions.get(host)
        if r is None or not r.api_key:
            raise MerakiError(f"no Meraki API key for {r.name if r else host}")
        if host not in self._clients:
            self._clients[host] = MerakiClient(r.api_key, r.base_url, self.cfg.timeout, transport=self._transport)
        return self._clients[host]

    async def aclose(self) -> None:
        for c in self._clients.values():
            await c.aclose()
        self._clients.clear()

    def _empty_availability(self, region: Region) -> dict[str, Any]:
        return {
            "state": "pending" if region.api_key else "not-configured",
            "counts": None,
            "checked_at": None,
            "stale_after_seconds": max(180, 3 * self.cfg.poll_interval),
        }

    def details(self) -> dict[str, Any]:
        out = []
        for r in sorted(self.regions(), key=lambda r: (r.name != "Global", r.name)):
            st = self._region_state.get(r.host, {})
            availability = dict(st.get("availability") or self._empty_availability(r))
            if availability["counts"] is not None:
                availability["counts"] = dict(availability["counts"])
            out.append({
                "name": r.name, "host": r.host, "has_key": bool(r.api_key), "key_env": r.api_key_env,
                "source": r.source, "orgs": st.get("orgs"), "devices": st.get("devices", 0), "error": st.get("error"),
                "availability": availability,
            })
        return {"regions": out}

    async def _org_ids(self, region: Region, refs: list[DeviceRef]) -> list[str]:
        """Orgs to poll in a region: the configured list, else every org its key can see (devices move
        between orgs, so the org named in a testbed file is only a hint). Falls back to the orgs named
        for the region, or for the global cloud to the testbed-file orgs, if the key may not list orgs."""
        if region.explicit_orgs:
            return sorted(region.org_ids)
        visible = await self._visible_orgs(region)
        if visible is not None:
            return sorted(visible)
        hinted = set(region.org_ids)
        if region.host == _api_host(self.cfg.base_url):
            hinted |= {r.org_id for r in refs if r.org_id}
        return sorted(hinted)

    async def _visible_orgs(self, region: Region) -> set[str] | None:
        at, orgs = self._visible.get(region.host, (0.0, None))
        if not at or time.monotonic() - at > 3600:
            try:
                listed = await self.client(region).organizations()
                orgs = [str(o["id"]) for o in listed if (o.get("api") or {}).get("enabled", True)]
            except MerakiError:
                orgs = None
            self._visible[region.host] = (time.monotonic(), orgs)
        return set(orgs) if orgs is not None else None

    async def _refresh_inventory(self, region: Region, org_id: str, force: bool = False) -> None:
        last = self._inventory_at.get((region.host, org_id), 0.0)
        if not force and last and time.monotonic() - last < self.cfg.inventory_interval:
            return
        client = self.client(region)
        devices, networks = await asyncio.gather(client.devices(org_id), client.networks(org_id))
        for d in devices:
            if d.get("serial"):
                self._inventory.setdefault(str(d["serial"]).upper(), {})[region.host] = d | {"organizationId": org_id}
        for n in networks:
            if n.get("id"):
                self._networks[str(n["id"])] = n | {"organizationId": org_id, "_region": region.host}
        self._inventory_at[(region.host, org_id)] = time.monotonic()

    def _inv(self, serial: str) -> dict[str, Any]:
        by_region = self._inventory.get(serial, {})
        where = self._where.get(serial)
        return by_region.get(where) if where in by_region else next(iter(by_region.values()), {})

    def _is_switch(self, serial: str) -> bool:
        inv = self._inv(serial)
        if inv.get("productType"):
            return inv["productType"] == "switch"
        net = self._networks.get(str(inv.get("networkId")), {})
        return "switch" in (net.get("productTypes") or [])

    async def _refresh_stacks(self, network_ids: set[str]) -> None:
        """Read switch stacks for the networks our switches are in, at the inventory pace.
        A network whose read fails keeps what was read last time (and counts as unchecked if never read)."""
        due = [
            n for n in sorted(network_ids)
            if time.monotonic() - self._stacks_at.get(n, -1e9) >= self.cfg.inventory_interval
        ]

        async def one(net: str) -> None:
            try:
                stacks = await self.client(self._networks.get(net, {}).get("_region")).switch_stacks(net)
            except MerakiError as exc:
                log.debug("switch stacks for %s: %s", net, exc)
                return
            for serial in [s for s, info in self._stacks.items() if info.extra.get("network_id") == net]:
                del self._stacks[serial]
            for st in stacks if isinstance(stacks, list) else []:
                members = st.get("members") or [{"serial": s} for s in st.get("serials") or []]
                roster = [
                    {
                        "serial": str(m.get("serial") or "").upper(),
                        "name": m.get("name"),
                        "model": m.get("model"),
                        "role": m.get("role"),
                        "number": m.get("switchNumber"),
                    }
                    for m in members
                    if m.get("serial")
                ]
                extra = {"network_id": net, "stack_id": str(st.get("id")), "virtual_mac": st.get("virtualMac"),
                         "monitor_only": st.get("isMonitorOnly")}
                for m in roster:
                    self._stacks[m["serial"]] = StackInfo(
                        key=f"meraki:{st.get('id')}",
                        name=str(st.get("name") or f"Stack {st.get('id')}"),
                        role=m["role"],
                        number=m["number"],
                        members=roster,
                        extra=extra,
                    )
            self._stacks_at[net] = time.monotonic()

        await asyncio.gather(*(one(n) for n in due))

    async def poll(self, devices: list[DeviceRef]) -> dict[str, StatusUpdate]:
        refs = [d for d in devices if d.serial]
        if not refs:
            return {}
        wanted = {r.serial.upper(): r for r in refs if r.serial}
        regions = self.regions()
        names = {r.host: r.name for r in regions}
        found: dict[str, list[dict[str, Any]]] = defaultdict(list)  # serial -> availability per region
        errors: list[str] = []

        async def one_region(region: Region) -> None:
            summary = self._empty_availability(region)
            state: dict[str, Any] = {"orgs": 0, "devices": 0, "error": None, "availability": summary}
            self._region_state[region.host] = state
            if not region.api_key:
                state["error"] = f"no key: set {region.api_key_env}" if region.api_key_env else "no API key"
                return
            orgs = await self._org_ids(region, refs)
            state["orgs"] = len(orgs)
            if not orgs:
                state["error"] = "no Meraki organizations are available for this region"
                summary["state"] = "unavailable"
                return
            bad: list[str] = []
            region_statuses: dict[str, str] = {}
            matched_serials: set[str] = set()

            async def one(org: str) -> None:
                try:
                    await self._refresh_inventory(region, org)
                    records = await self.client(region).availabilities(org)
                    validated = [(a, *_availability_record(a)) for a in records]
                    for a, serial, status in validated:
                        previous = region_statuses.get(serial)
                        if previous is None or STATUS_RANK.get(status, 3) < STATUS_RANK.get(previous, 3):
                            region_statuses[serial] = status
                        if serial in wanted:
                            found[serial].append(a | {"organizationId": org, "_region": region.host})
                            matched_serials.add(serial)
                            state["devices"] = len(matched_serials)
                except (MerakiError, httpx.HTTPError, ValueError) as exc:
                    detail = str(exc) if isinstance(exc, MerakiError) else "Meraki availability read failed"
                    bad.append(f"{region.name} org {org}: {detail}")

            await asyncio.gather(*(one(o) for o in orgs))
            errors.extend(bad)
            if bad and not state["devices"]:
                state["error"] = bad[0]
            if bad:
                summary["state"] = "unavailable"
            else:
                summary.update(
                    state="available",
                    counts={status: sum(value == status for value in region_statuses.values()) for status in AVAILABILITY_STATUSES},
                    checked_at=utcnow().isoformat(),
                )

        await asyncio.gather(*(one_region(r) for r in regions))
        if errors and not found:
            raise MerakiError("; ".join(errors))

        availability: dict[str, dict[str, Any]] = {}
        for serial, seen in found.items():
            # a device moved between clouds can be listed in both: trust the one it checks in to
            best = min(seen, key=lambda a: STATUS_RANK.get(str(a.get("status") or "").lower(), 3))
            availability[serial] = best
            self._where[serial] = best["_region"]
        for serial in wanted:
            if serial not in availability and serial not in self._where and self._inventory.get(serial):
                self._where[serial] = next(iter(self._inventory[serial]))

        def network_of(serial: str) -> str | None:
            nid = ((availability.get(serial) or {}).get("network") or {}).get("id") or self._inv(serial).get("networkId")
            return str(nid) if nid else None

        switch_nets = {n for s in wanted if self._is_switch(s) and (n := network_of(s))}
        if switch_nets:
            await self._refresh_stacks(switch_nets)

        keyless = [r for r in regions if not r.api_key]
        global_orgs = self._visible.get(_api_host(self.cfg.base_url), (0.0, None))[1]
        hinted_region = {o: r for r in regions for o in r.org_ids}
        now = utcnow()
        updates: dict[str, StatusUpdate] = {}
        for serial, ref in wanted.items():
            a = availability.get(serial)
            inv = self._inv(serial)
            if a is None and not inv:
                home = hinted_region.get(ref.org_id or "")
                if keyless:
                    # devices move between clouds, so the testbed file's org is only a hint: name every cloud
                    # we cannot read yet, the hinted one first
                    order = sorted(keyless, key=lambda r: (r is not home, r.name))
                    detail = "not found in the Meraki clouds Labkeeper can read; no key yet for " + ", ".join(
                        f"{r.name} ({r.api_key_env})" if r.api_key_env else r.name for r in order
                    )
                elif ref.org_id and global_orgs is not None and ref.org_id not in global_orgs and not self.cfg.org_ids \
                        and ref.org_id not in hinted_region:
                    detail = f"Meraki API key has no access to org {ref.org_id} (from the testbed file)"
                else:
                    detail = "not found in the Meraki organizations this API key can see"
                updates[ref.key] = StatusUpdate(status="unknown", detail=detail)
                continue
            region_host = (a or {}).get("_region") or self._where.get(serial)
            status = str((a or {}).get("status") or "unknown").lower()
            net_id = network_of(serial)
            net = self._networks.get(str(net_id)) if net_id else None
            org = (a or inv).get("organizationId")
            live = {
                "meraki": {
                    "status": status,
                    "name": (a or {}).get("name") or inv.get("name"),
                    "model": inv.get("model"),
                    "firmware": inv.get("firmware"),
                    "lan_ip": inv.get("lanIp"),
                    "mac": inv.get("mac") or (a or {}).get("mac"),
                    "product_type": inv.get("productType") or (a or {}).get("productType"),
                    "dashboard_url": inv.get("url"),
                    "address": inv.get("address"),
                    "tags": inv.get("tags") or (a or {}).get("tags") or [],
                    "network_id": net_id,
                    "network_name": (net or {}).get("name"),
                    "organization_id": org,
                    "region": names.get(region_host or "", region_host),
                    "api_host": region_host,
                    "checked_at": now.isoformat(),
                }
            }
            live["inventory"] = {
                "source": self.name,
                **{key: live["meraki"].get(key) for key in (
                    "status", "model", "firmware", "lan_ip", "mac", "network_name", "region", "product_type", "checked_at",
                )},
                "external_url": live["meraki"].get("dashboard_url"),
            }
            network = None
            if net:
                network = NetworkInfo(
                    key=f"meraki:{net_id}",
                    name=str(net.get("name") or net_id),
                    provider="meraki",
                    org_id=str(org) if org else None,
                    url=net.get("url"),
                    product_types=list(net.get("productTypes") or []),
                    tags=list(net.get("tags") or []),
                    region=names.get(region_host or "", region_host),
                    api_host=region_host,
                )
            elif net_id:
                network = NetworkInfo(key=f"meraki:{net_id}", name=str(net_id), provider="meraki", org_id=str(org))
            detail = {
                "online": None,
                "alerting": "device is reporting an alert in Dashboard",
                "offline": "device is not checking in to Dashboard",
                "dormant": "device has been offline for a long time",
            }.get(status)
            if detail and region_host and region_host != _api_host(self.cfg.base_url):
                detail += f" ({names.get(region_host, region_host)} cloud)"
            stack_checked = self._is_switch(serial) and (net_id or "") in self._stacks_at
            updates[ref.key] = StatusUpdate(
                status=status if status in {"online", "alerting", "offline", "dormant"} else "unknown",
                detail=detail,
                live=live,
                network=network,
                last_seen=now if status in {"online", "alerting"} else None,
                stack=self._stacks.get(serial) if stack_checked else None,
                stack_checked=stack_checked,
            )
        return updates

    async def device_live(self, device: DeviceRef) -> dict[str, Any] | None:
        if not device.serial or not self.enabled:
            return None
        cached = self._port_cache.get(device.serial)
        if cached and time.monotonic() - cached[0] < self.cfg.port_cache_seconds:
            return cached[1]
        result: dict[str, Any] = {"source": "meraki", "ports": [], "error": None}
        if device.role == "switch":
            try:
                client = self.client(self._where.get(device.serial.upper()))
                statuses, configs = await asyncio.gather(
                    client.switch_port_statuses(device.serial),
                    client.switch_ports(device.serial),
                    return_exceptions=True,
                )
                if isinstance(statuses, Exception):
                    raise statuses
                cfg_by_id = {} if isinstance(configs, Exception) else {str(c.get("portId")): c for c in configs}
                ports = []
                for s in statuses:
                    pid = str(s.get("portId"))
                    c = cfg_by_id.get(pid, {})
                    lldp = s.get("lldp") or {}
                    cdp = s.get("cdp") or {}
                    ports.append(
                        {
                            "id": pid,
                            "name": c.get("name"),
                            "enabled": s.get("enabled", c.get("enabled", True)),
                            "status": s.get("status"),
                            "is_uplink": bool(s.get("isUplink")),
                            "speed": s.get("speed"),
                            "duplex": s.get("duplex"),
                            "errors": s.get("errors") or [],
                            "warnings": s.get("warnings") or [],
                            "clients": s.get("clientCount"),
                            "poe": bool((s.get("poe") or {}).get("isAllocated")),
                            "power_wh": s.get("powerUsageInWh"),
                            "type": c.get("type"),
                            "vlan": c.get("vlan"),
                            "allowed_vlans": c.get("allowedVlans"),
                            "neighbor": lldp.get("systemName") or cdp.get("deviceId"),
                            "neighbor_port": lldp.get("portId") or cdp.get("portId"),
                            "traffic_kbps": (s.get("trafficInKbps") or {}).get("total"),
                        }
                    )
                result["ports"] = sorted(ports, key=lambda p: _port_sort_key(p["id"]))
            except Exception as exc:
                result["error"] = str(exc)
        self._port_cache[device.serial] = (time.monotonic(), result)
        return result
