"""The reserve / release API used by scripts and CI jobs."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from labkeeper.app import create_app

ALICE = {"X-Labkeeper-User": "alice"}
BOB = {"X-Labkeeper-User": "bob"}


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


def test_reserve_by_name_conflict_then_courtesy_release(client):
    r = client.post("/api/v1/reserve", json={"testbed": "red_smoke", "duration_minutes": 60, "purpose": "ci"}, headers=ALICE)
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["owner"] == "alice" and b["testbed_name"] == "red_smoke" and b["created_via"] == "api"
    assert r.headers["Location"] == f"/api/v1/bookings/{b['id']}"

    r = client.post("/api/v1/reserve", json={"testbed": "red_blue"}, headers=BOB)  # shares RED with red_smoke
    assert r.status_code == 409
    (c,) = r.json()["detail"]["conflicts"]
    assert c["booking_id"] == b["id"] and c["owner"] == "alice" and c["testbed"] == "red_smoke"

    listed = client.get("/api/v1/reservations", params={"testbed": "red_smoke"}).json()
    assert [x["id"] for x in listed] == [b["id"]]

    r = client.post("/api/v1/release", json={"booking_id": b["id"]}, headers=BOB)
    assert r.status_code == 200 and r.json()["released"][0]["state"] == "released"
    assert client.get("/api/v1/reservations", params={"testbed": "red_smoke"}).json() == []
    assert client.post("/api/v1/release", json={"booking_id": b["id"]}).status_code == 400  # already released


def test_reserve_devices_release_by_testbed_and_extend(client):
    r = client.post("/api/v1/testbeds/red_blue/reserve", json={"devices": ["BLUE_01"], "owner": "ci-bot"})
    assert r.status_code == 201, r.text
    held = r.json()
    assert held["owner"] == "ci-bot" and held["devices_label"] == "BLUE_01"
    # the rest of red_blue stays bookable by someone else
    r = client.post("/api/v1/reserve", json={"testbed": "red_blue", "devices": ["RED_C"]}, headers=ALICE)
    assert r.status_code == 201, r.text

    r = client.post("/api/v1/extend", json={"testbed": "red_blue", "minutes": 30, "by": "ci-bot"})
    assert r.status_code == 200 and r.json()["id"] == held["id"]

    # release only ci-bot's booking of the testbed; alice keeps hers
    r = client.post("/api/v1/testbeds/red_blue/release", json={"owner": "ci-bot"}, headers=BOB)
    assert [x["id"] for x in r.json()["released"]] == [held["id"]]
    # then everything still held on red_blue
    r = client.post("/api/v1/testbeds/red_blue/release", headers=BOB)
    assert [x["owner"] for x in r.json()["released"]] == ["alice"]
    r = client.post("/api/v1/testbeds/red_blue/release", headers=BOB)
    assert r.status_code == 404 and "no active booking" in r.json()["detail"]


def test_bad_requests(client):
    assert client.post("/api/v1/reserve", json={"testbed": "nope"}).status_code == 404
    assert client.post("/api/v1/reserve", json={"testbed": "red_smoke", "devices": ["jump1"]}).status_code == 400
    assert client.post("/api/v1/reserve", json={"testbed": "red_smoke", "owner": "bad\nname"}).status_code == 422
    assert client.post("/api/v1/release", json={}).status_code == 422
    assert client.get("/api/v1/testbeds/red_smoke").json()["name"] == "red_smoke"  # names work on detail too


def test_owner_policy_still_available(settings):
    settings.booking.release_policy = "owner"
    settings.booking.allow_force_release = False
    with TestClient(create_app(settings)) as client:
        b = client.post("/api/v1/reserve", json={"testbed": "red_smoke"}, headers=ALICE).json()
        r = client.post(f"/api/v1/bookings/{b['id']}/release", json={}, headers=BOB)
        assert r.status_code == 403
