from __future__ import annotations

import httpx

from labkeeper.config import Settings
from labkeeper.providers.base import DeviceRef
from labkeeper.providers.meraki import MerakiProvider


def ref(serial: str, key: str, role: str = "switch", org: str = "111") -> DeviceRef:
    return DeviceRef(id=1, key=key, name=key, serial=serial, mgmt_ip=None, org_id=org, model="C9300", role=role, type="meraki-ms")


def make_transport(calls: list[str]):
    state = {"throttled": False}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}?{request.url.query.decode()}")
        assert request.headers["X-Cisco-Meraki-API-Key"] == "test-key"
        assert "Authorization" not in request.headers
        path = request.url.path
        if path.endswith("/api/v1/organizations"):
            return httpx.Response(200, json=[{"id": "111", "name": "Lab", "api": {"enabled": True}}])
        if path.endswith("/organizations/111/devices/availabilities"):
            if not state["throttled"]:
                state["throttled"] = True
                return httpx.Response(429, headers={"Retry-After": "0"})
            if "startingAfter" not in str(request.url):
                return httpx.Response(
                    200,
                    json=[{"serial": "Q2AA-0001", "status": "online", "network": {"id": "N_1"}, "name": "sw1"}],
                    headers={"Link": '<https://api.meraki.test/api/v1/organizations/111/devices/availabilities?startingAfter=x>; rel="next"'},
                )
            return httpx.Response(200, json=[{"serial": "Q2AA-0002", "status": "alerting", "network": {"id": "N_1"}}])
        if path.endswith("/organizations/111/devices"):
            return httpx.Response(200, json=[
                {"serial": "Q2AA-0001", "model": "C9300-24UX", "firmware": "MS 17.2", "lanIp": "10.0.0.5", "url": "https://dash/sw1", "networkId": "N_1"},
                {"serial": "Q2AA-0002", "model": "C9300-24UX", "firmware": "MS 17.2", "networkId": "N_1"},
            ])
        if path.endswith("/organizations/111/networks"):
            return httpx.Response(200, json=[{"id": "N_1", "name": "Smoke lab", "productTypes": ["switch"], "url": "https://dash/net"}])
        if path.endswith("/switch/ports/statuses"):
            return httpx.Response(200, json=[
                {"portId": "2", "enabled": True, "status": "Disconnected"},
                {"portId": "1", "enabled": True, "status": "Connected", "speed": "1 Gbps", "lldp": {"systemName": "core"}, "poe": {"isAllocated": True}},
            ])
        if path.endswith("/switch/ports"):
            return httpx.Response(200, json=[{"portId": "1", "name": "uplink", "vlan": 10, "type": "trunk"}])
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def provider(calls):
    s = Settings(meraki={"enabled": True, "api_key": "test-key", "base_url": "https://api.meraki.test/api/v1"})
    return MerakiProvider(s, transport=make_transport(calls))


async def test_poll_maps_status_inventory_and_network_with_retry_and_paging():
    calls: list[str] = []
    p = provider(calls)
    updates = await p.poll([ref("Q2AA-0001", "a"), ref("Q2AA-0002", "b"), ref("Q2ZZ-9999", "c"), ref("Q2YY-0000", "d", org="999")])
    await p.aclose()
    assert updates["a"].status == "online"
    assert updates["a"].live["meraki"]["firmware"] == "MS 17.2"
    assert updates["a"].live["meraki"]["dashboard_url"] == "https://dash/sw1"
    assert updates["a"].network.name == "Smoke lab" and updates["a"].network.key == "meraki:N_1"
    assert updates["b"].status == "alerting"
    assert updates["c"].status == "unknown" and "not found" in updates["c"].detail
    assert updates["d"].status == "unknown" and "no access to org 999" in updates["d"].detail
    assert sum("availabilities" in c for c in calls) == 3  # 429 retry + two pages


async def test_device_live_merges_port_status_and_config_sorted():
    calls: list[str] = []
    p = provider(calls)
    live = await p.device_live(ref("Q2AA-0001", "a"))
    await p.aclose()
    assert [port["id"] for port in live["ports"]] == ["1", "2"]
    first = live["ports"][0]
    assert first["name"] == "uplink" and first["vlan"] == 10 and first["neighbor"] == "core" and first["poe"] is True


def test_disabled_without_key(monkeypatch):
    monkeypatch.delenv("MERAKI_DASHBOARD_API_KEY", raising=False)
    p = MerakiProvider(Settings(meraki={"enabled": True, "api_key": None}))
    assert not p.enabled and "API_KEY" in p.disabled_reason
