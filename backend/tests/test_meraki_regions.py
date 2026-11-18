"""Several Meraki clouds (global + regional) polled at once, regions declared in testbed files."""

from __future__ import annotations

import httpx

from labkeeper.config import Settings
from labkeeper.providers.base import DeviceRef
from labkeeper.providers.meraki import MerakiProvider, regions_from_testbed
from labkeeper.secrets_store import redact

GEO = {
    "geocloud_name": "Canada",
    "api_key_env": "CANADA_KEY_T",
    "meraki_api_base_url": "https://api.meraki.ca/api/v1",
    "organization_id": 906,
    "geoclouds": {
        "India": {"api_key_env": "INDIA_KEY_T", "organization_id": 963, "base_url": "https://api.meraki.in/api/v1"},
        "Infra": {"api_key_env": "MERAKI_API_KEY", "organization_id": 111, "base_url": "https://n1003.meraki.com/api/v1"},
    },
}


def ref(serial: str, key: str, org: str | None = None) -> DeviceRef:
    return DeviceRef(id=1, key=key, name=key, serial=serial, mgmt_ip=None, org_id=org, model="C9300", role="switch", type="switch")


def transport(calls: list[str]) -> httpx.MockTransport:
    data = {
        # host -> org -> [(serial, status)]
        "api.meraki.test": {"111": [("Q2AA-0001", "online"), ("Q2AA-0002", "offline")]},
        "api.meraki.ca": {"906": [("Q2AA-0002", "online")]},  # moved to Canada; global still lists it, offline
    }

    def handler(request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        calls.append(f"{host}{path}")
        orgs = data.get(host)
        if orgs is None:
            return httpx.Response(404)
        expected = {"api.meraki.test": "global-key", "api.meraki.ca": "canada-key"}[host]
        assert request.headers["X-Cisco-Meraki-API-Key"] == expected
        assert "Authorization" not in request.headers
        if path.endswith("/api/v1/organizations"):
            return httpx.Response(200, json=[{"id": o, "name": o} for o in orgs])
        for org, devs in orgs.items():
            if path.endswith(f"/organizations/{org}/devices/availabilities"):
                return httpx.Response(200, json=[{"serial": s, "status": st, "network": {"id": f"N_{org}"}} for s, st in devs])
            if path.endswith(f"/organizations/{org}/devices"):
                return httpx.Response(200, json=[{"serial": s, "model": "C9300", "networkId": f"N_{org}", "url": f"https://dash.{host}/{s}"} for s, _ in devs])
            if path.endswith(f"/organizations/{org}/networks"):
                return httpx.Response(200, json=[{"id": f"N_{org}", "name": f"net {org}", "productTypes": ["switch"]}])
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_regions_from_testbed_files():
    regions = {r.name: r for r in regions_from_testbed(GEO)}
    assert regions["Canada"].host == "api.meraki.ca" and regions["Canada"].api_key_env == "CANADA_KEY_T"
    assert regions["Canada"].org_ids == {"906"} and regions["India"].base_url == "https://api.meraki.in/api/v1"
    assert regions["Infra"].host == "api.meraki.com"  # retired shard host = global cloud


def test_key_variable_names_are_not_masked_but_keys_are():
    out = redact({"api_key_env": "CANADA_MERAKI_API_KEY", "api_key": "abc123", "token_env": "lower case is a value"})
    assert out["api_key_env"] == "CANADA_MERAKI_API_KEY" and out["api_key"] != "abc123" and out["token_env"] != "lower case is a value"


async def test_polls_every_region_with_a_key_and_names_missing_keys(monkeypatch):
    monkeypatch.setenv("CANADA_KEY_T", "canada-key")
    monkeypatch.delenv("INDIA_KEY_T", raising=False)
    calls: list[str] = []
    p = MerakiProvider(Settings(meraki={"enabled": True, "api_key": "global-key", "base_url": "https://api.meraki.test/api/v1"}), transport=transport(calls))
    p.observe_testbeds([GEO])
    updates = await p.poll([ref("Q2AA-0001", "a"), ref("Q2AA-0002", "b"), ref("Q2AA-0009", "c", org="963")])
    details = {r["name"]: r for r in p.details()["regions"]}
    await p.aclose()
    assert updates["a"].status == "online" and updates["a"].live["meraki"]["region"] == "Global"
    # listed in both clouds: the one it checks in to wins
    b = updates["b"]
    assert b.status == "online" and b.live["meraki"]["region"] == "Canada" and b.live["meraki"]["dashboard_url"].startswith("https://dash.api.meraki.ca")
    assert updates["c"].status == "unknown" and "no key yet for India (INDIA_KEY_T)" in updates["c"].detail
    assert details["Canada"]["has_key"] and details["Canada"]["devices"] == 1
    assert not details["India"]["has_key"] and details["India"]["error"] == "no key: set INDIA_KEY_T"
    assert not any("api.meraki.in" in c for c in calls)  # never called without a key


async def test_redirects_keep_the_key_within_the_cloud_and_refuse_other_hosts():
    from labkeeper.providers.meraki import MerakiClient, MerakiError

    seen: list[tuple[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, request.headers.get("X-Cisco-Meraki-API-Key")))
        assert "Authorization" not in request.headers
        if request.url.host == "api.gov-meraki.test":
            return httpx.Response(308, headers={"Location": "https://n2.dashboard.gov-meraki.test/api/v1/organizations"})
        if request.url.host == "n2.dashboard.gov-meraki.test":
            return httpx.Response(200, json=[{"id": "1"}])
        return httpx.Response(308, headers={"Location": "https://evil.example/api/v1/organizations"})

    gov = MerakiClient("k", "https://api.gov-meraki.test/api/v1", transport=httpx.MockTransport(handler))
    assert await gov.organizations() == [{"id": "1"}]
    assert seen[-1] == ("n2.dashboard.gov-meraki.test", "k")
    other = MerakiClient("k", "https://api.meraki.zz/api/v1", transport=httpx.MockTransport(handler))
    try:
        await other.organizations()
        raise AssertionError("followed a redirect off the Meraki domain")
    except MerakiError as exc:
        assert "refusing redirect to evil.example" in str(exc)
    assert not any(h == "evil.example" for h, _ in seen)
    await gov.aclose()
    await other.aclose()


async def test_federal_api_accepts_key_header_instead_of_bearer_after_redirect():
    from labkeeper.providers.meraki import MerakiClient

    seen: list[tuple[str, str, str | None, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, request.url.path,
                     request.headers.get("X-Cisco-Meraki-API-Key"), request.headers.get("Authorization")))
        if request.headers.get("X-Cisco-Meraki-API-Key") != "federal-test-key" or request.headers.get("Authorization"):
            return httpx.Response(401, json={"errors": ["API-key authentication required"]})
        if request.url.host == "api.gov-meraki.test":
            return httpx.Response(308, headers={
                "Location": f"https://n2.dashboard.gov-meraki.test{request.url.path}",
            })
        if request.url.path.endswith("/organizations"):
            return httpx.Response(200, json=[{"id": "1", "name": "Federal lab"}])
        return httpx.Response(200, json=[{"serial": "Q2AA-FEDERAL-0001", "status": "online"}])

    client = MerakiClient("federal-test-key", "https://api.gov-meraki.test/api/v1",
                          transport=httpx.MockTransport(handler))
    try:
        assert await client.organizations() == [{"id": "1", "name": "Federal lab"}]
        assert await client.availabilities("1") == [{"serial": "Q2AA-FEDERAL-0001", "status": "online"}]
        assert [host for host, _, _, _ in seen] == [
            "api.gov-meraki.test", "n2.dashboard.gov-meraki.test",
            "api.gov-meraki.test", "n2.dashboard.gov-meraki.test",
        ]
        assert all(key == "federal-test-key" and authorization is None for _, _, key, authorization in seen)
    finally:
        await client.aclose()


def test_testbed_files_declare_one_network_per_cloud(tmp_path):
    from sqlalchemy import select

    from labkeeper import db
    from labkeeper import services as sv
    from labkeeper.importer import apply_scan, run_scan
    from labkeeper.models import Base, Network
    from tests.conftest import write

    root = tmp_path / "tb"
    write(root, "common.yaml", """
        testbed:
          custom:
            client_organization_id: 111
            client_network_id: L_INFRA
            client_meraki_api_base_url: https://n1003.meraki.com/api/v1
            geoclouds:
              India: {api_key_env: INDIA_KEY_T, organization_id: 963, network_id: L_IN, base_url: https://api.meraki.in/api/v1}
        devices:
          GEO_1:
            type: switch
            custom: {members: [{serial: Q5AA-0000-0001}]}
    """)
    write(root, "canada.yaml", """
        extends: [common.yaml]
        testbed:
          name: GEO_CANADA
          custom: {geocloud_name: Canada, api_key_env: CANADA_KEY_T, meraki_api_base_url: https://api.meraki.ca/api/v1,
                   network_id: L_CA, organization_id: 906}
    """)
    s = Settings(data_dir=tmp_path / "data", testbeds={"paths": [str(root)], "watch": False})
    s.data_dir.mkdir(parents=True, exist_ok=True)
    db.init_engine(s.resolved_database_url)
    Base.metadata.create_all(db.engine())
    with db.new_session() as ses:
        apply_scan(ses, run_scan(s), s)
        nets = {n.key: n for n in ses.scalars(select(Network))}
        assert nets["meraki:L_CA"].name == "GeoCloud Canada" and nets["meraki:L_CA"].region == "Canada"
        assert nets["meraki:L_CA"].api_host == "api.meraki.ca" and nets["meraki:L_CA"].org_id == "906"
        assert nets["meraki:L_IN"].region == "India" and nets["meraki:L_INFRA"].region == "Global"
        listed = {n.key: n for n in sv.list_networks(ses, {"api.meraki.ca": {"has_key": False, "key_env": "CANADA_KEY_T"}})}
        ca = listed["meraki:L_CA"]
        assert ca.device_count == 1 and ca.reported_count == 0 and [t.name for t in ca.testbeds] == ["GEO_CANADA"]
        assert ca.dashboard_host == "dashboard.meraki.ca" and ca.has_key is False and ca.key_env == "CANADA_KEY_T"
        assert listed["meraki:L_IN"].device_count == 0  # named, but no testbed's own network
        assert [d.name for d in sv.list_devices(ses, network_id=nets["meraki:L_CA"].id)] == ["GEO_1"]
