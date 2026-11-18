from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from labkeeper.app import create_app

H = {"X-Labkeeper-User": "alice"}


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


def ids(client, path):
    return {x["name"]: x["id"] for x in client.get(path).json()}


def test_system_and_import(client):
    sysinfo = client.get("/api/v1/system", headers=H).json()
    assert sysinfo["user"] == "alice"
    assert sysinfo["last_import"]["testbeds"] == 3
    meraki = next(p for p in sysinfo["providers"] if p["name"] == "meraki")
    assert meraki["enabled"] is False


def test_booking_flow_and_conflict(client):
    tbs = ids(client, "/api/v1/testbeds")
    r = client.post(f"/api/v1/testbeds/{tbs['red_smoke']}/bookings", json={"duration_minutes": 60}, headers=H)
    assert r.status_code == 201
    booking = r.json()
    r = client.post(f"/api/v1/testbeds/{tbs['red_blue']}/bookings", json={"duration_minutes": 60}, headers={"X-Labkeeper-User": "bob"})
    assert r.status_code == 409 and "alice" in r.json()["detail"]["message"]
    states = {t["name"]: t["status"] for t in client.get("/api/v1/testbeds").json()}
    assert states == {"red_smoke": "in_use", "red_blue": "blocked", "blue_smoke": "free"}
    # courtesy: anyone may release anyone's booking, logged under their own name
    r = client.post(f"/api/v1/bookings/{booking['id']}/release", json={}, headers={"X-Labkeeper-User": "bob"})
    assert r.status_code == 200 and r.json()["state"] == "released"
    events = client.get("/api/v1/events").json()
    assert [e["action"] for e in events[:2]] == ["released", "booked"]
    assert events[0]["actor"] == "bob" and "alice's booking, courtesy release" in events[0]["message"]


def test_rack_placement_rules(client):
    devices = ids(client, "/api/v1/devices")
    racks = ids(client, "/api/v1/racks")
    # RED_02 sits at LAB_A01 U9; moving RED_01 (U10) onto U9 must fail
    r = client.put(f"/api/v1/devices/{devices['RED_01']}/placement", json={"rack_id": racks["LAB_A01"], "position": 9}, headers=H)
    assert r.status_code == 422 and "overlaps RED_02" in r.json()["detail"]
    r = client.put(f"/api/v1/devices/{devices['RED_01']}/placement", json={"rack_id": racks["LAB_A01"], "position": 42}, headers=H)
    assert r.status_code == 200 and r.json()["rack_position"] == 42
    r = client.put(f"/api/v1/devices/{devices['RED_C']}/placement", json={"rack_id": racks["LAB_A01"], "position": 1}, headers=H)
    assert r.status_code == 422  # logical stack


def test_device_detail_hides_secrets(client):
    devices = ids(client, "/api/v1/devices")
    body = client.get(f"/api/v1/devices/{devices['RED_01']}").text
    assert "console-pass" not in body and "s3cret-jump" not in body
    assert '"UPLINK_01"' in body  # topology tag


def test_floor_plan_grid_upload_and_cell_rack(client):
    grid = b",01,02\nA,R,R\n"
    r = client.post("/api/v1/floorplans/extract", files={"file": ("lab.csv", grid, "text/csv")}, data={"name": "Room 1"}, headers=H)
    assert r.status_code == 200 and r.json()["method"] == "grid"
    draft = r.json()["draft"] | {"rack_prefix": "LAB_"}
    r = client.post("/api/v1/floorplans", json=draft, headers=H)
    assert r.status_code == 201
    plan = r.json()
    linked = {x["rack_name"]: bool(x["rack"]) for x in plan["racks"]}
    assert linked == {"LAB_A01": True, "LAB_A02": True}
    a01 = next(x for x in plan["racks"] if x["rack_name"] == "LAB_A01")
    assert {d["name"] for d in a01["devices"]} == {"RED_01", "RED_02"}


def test_testbed_source_is_masked(client):
    tbs = ids(client, "/api/v1/testbeds")
    files = client.get(f"/api/v1/testbeds/{tbs['red_smoke']}/source").json()["files"]
    text = "\n".join(f["text"] for f in files)
    assert "red.yaml" in " ".join(f["name"] for f in files)
    assert "console-pass" not in text and "s3cret-jump" not in text


def test_testbed_detail_lists_consoles_links_and_traffic_generators(tmp_path):
    from sqlalchemy import select

    from labkeeper import db
    from labkeeper import services as sv
    from labkeeper.config import Settings
    from labkeeper.importer import apply_scan, run_scan
    from labkeeper.models import Base, Testbed
    from tests.conftest import write

    root = tmp_path / "tb"
    write(root, "poe.yaml", """
        testbed: {name: poe}
        devices:
          DUT:
            type: meraki-ms
            connections:
              console: {protocol: telnet, ip: 10.9.9.9, port: 2003}
            custom: {meraki_device: {serial: Q4AA-0000-0001, model: MS150-24P-4G}}
          IXIA:
            type: ixia-rest
            os: windows
            connections:
              rest: {protocol: http, ip: 10.9.9.10, port: 11009}
            custom:
              traffic_generator: {chassis: 10.9.9.11, session: 1, product: IxNetwork}
        topology:
          DUT:
            interfaces:
              "10": {type: ethernet, tag: IXIA_01, link: l1}
          IXIA:
            interfaces:
              1/9/1: {type: ethernet, tag: DUT_01, media: copper, ipv4: 198.18.0.1/24, link: l1}
    """)
    s = Settings(data_dir=tmp_path / "data", testbeds={"paths": [str(root)], "watch": False})
    s.data_dir.mkdir(parents=True, exist_ok=True)
    db.init_engine(s.resolved_database_url)
    Base.metadata.create_all(db.engine())
    with db.new_session() as ses:
        apply_scan(ses, run_scan(s), s)
        t = sv.testbed_detail(ses, ses.scalar(select(Testbed).where(Testbed.name == "poe")))
    assert [(c.device, c.protocol, c.host, c.port) for c in t.consoles] == [("DUT", "telnet", "10.9.9.9", 2003)]
    assert [(e.device, e.interface) for e in t.links[0].ends] == [("DUT", "10"), ("IXIA", "1/9/1")]
    (gen,) = t.traffic
    assert gen.api == "http://10.9.9.10:11009" and gen.chassis == "10.9.9.11" and gen.session == "1"
    (port,) = gen.ports
    assert port.media == "copper" and port.ipv4 == "198.18.0.1/24" and port.peer.device == "DUT" and port.peer.interface == "10"
