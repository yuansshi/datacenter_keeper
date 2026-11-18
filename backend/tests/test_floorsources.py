from __future__ import annotations


import json
import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

from labkeeper import db
from labkeeper.floorsources import BrowserExport, FloorPlanSource, FloorSourceError, SourcePlan, SourceSite, upsert_source_plan
from labkeeper.models import Base, Event
from labkeeper.schemas import FloorCell, FloorPlanIn


def grid(metric: float | None = 1.0) -> FloorPlanIn:
    cells = [FloorCell(row="A", col="01", type="rack", metric=metric), FloorCell(row="A", col="02", type="rack")]
    cells += [FloorCell(row="B", col=c, type="unassigned") for c in ("01", "02")]  # merged into one block
    return FloorPlanIn(name="Room 9", rows=["A", "B"], columns=["01", "02"], cells=cells, metric_label="kW")


class FakeSource(FloorPlanSource):
    name = "fake"
    label = "Fake DCIM"

    def __init__(self, settings, live: bool = True):
        super().__init__(settings)
        self.live = live
        self.metric = 1.0
        self.fail = False

    @property
    def can_sync(self) -> bool:
        return self.live

    @property
    def sync_disabled_reason(self):
        return None if self.live else "no token"

    async def list_sites(self):
        return [SourceSite(id="9", name="Room 9", group="Bldg")]

    async def fetch(self, site_id):
        if self.fail:
            raise FloorSourceError("token expired")
        return SourcePlan(site_id=site_id, plan=grid(self.metric))

    def browser_export(self):
        return BrowserExport(title="Export", instructions="click", bookmarklet="javascript:void(0)", snippet="void 0",
                             origins=["https://dcim.example"])

    def matches_export(self, data):
        return isinstance(data, dict) and data.get("format") == "fake/1"

    def parse_export(self, data):
        return [SourcePlan(site_id=str(data["site"]), plan=grid(data.get("kw")))]


def test_upsert_creates_guesses_prefix_merges_and_keeps_user_edits(session, settings, imported):
    src = FakeSource(settings)
    plan, created, _ = upsert_source_plan(session, src, SourcePlan(site_id="9", plan=grid()), actor="t")
    session.commit()
    assert created and plan.source_ref == "fake:9" and plan.rack_prefix == "LAB_"  # from racks LAB_A01/LAB_A02
    assert {r.name for r in plan.racks} == {"LAB_A01", "LAB_A02"}
    blocks = [c for c in plan.cells if c["type"] == "unassigned"]
    assert len(blocks) == 1 and blocks[0]["span_cols"] == 2
    plan.name = "Renamed by user"
    session.commit()
    plan2, created2, changes = upsert_source_plan(session, src, SourcePlan(site_id="9", plan=grid(3.0)), actor="t")
    session.commit()
    assert not created2 and plan2.id == plan.id and changes == 2  # one cell changed (old + new key)
    assert plan2.name == "Renamed by user" and plan2.rack_prefix == "LAB_"
    assert session.scalar(select(Event).where(Event.action == "synced")) is not None


@pytest.fixture
def client(settings, monkeypatch):
    holder: dict[str, FakeSource] = {}

    def fake_loader(s):
        holder["src"] = FakeSource(s)
        return {"fake": holder["src"]}

    monkeypatch.setattr("labkeeper.app.load_floor_sources", fake_loader)
    from labkeeper.app import create_app

    with TestClient(create_app(settings)) as c:
        c.source = holder["src"]  # type: ignore[attr-defined]
        yield c


H = {"X-Labkeeper-User": "alice"}


def test_export_drop_upserts_without_draft(client):
    body = json.dumps({"format": "fake/1", "site": 9, "kw": 2.0}).encode()
    r = client.post("/api/v1/floorplans/extract", files={"file": ("export.json", body, "application/json")}, headers=H)
    assert r.status_code == 200 and r.json()["method"] == "source"
    (p,) = r.json()["plans"]
    assert p["created"] is True
    again = client.post("/api/v1/floorplans/extract", files={"file": ("export.json", body, "application/json")}, headers=H).json()
    assert again["plans"][0]["id"] == p["id"] and again["plans"][0]["created"] is False
    plan = client.get(f"/api/v1/floorplans/{p['id']}").json()
    assert plan["source_ref"] == "fake:9" and plan["synced_at"]


def test_sources_listing_import_sync_and_detach(client):
    (desc,) = client.get("/api/v1/floorsources").json()
    assert desc["name"] == "fake" and desc["can_sync"] and desc["browser_export"]["bookmarklet"].startswith("javascript:")
    assert client.get("/api/v1/floorsources/fake/sites").json()[0]["id"] == "9"
    imported = client.post("/api/v1/floorsources/fake/import", json={"site_id": "9"}, headers=H).json()
    pid = imported["id"]
    client.source.metric = 5.0  # type: ignore[attr-defined]
    synced = client.post(f"/api/v1/floorplans/{pid}/sync", headers=H).json()
    assert synced["changes"] == 2
    client.source.fail = True  # type: ignore[attr-defined]
    r = client.post(f"/api/v1/floorplans/{pid}/sync", headers=H)
    assert r.status_code == 409 and "expired" in r.json()["detail"]
    assert client.get(f"/api/v1/floorplans/{pid}").json()["sync_error"] == "token expired"
    detached = client.post(f"/api/v1/floorplans/{pid}/detach", headers=H).json()
    assert detached["source_ref"] is None
    assert client.post(f"/api/v1/floorplans/{pid}/sync", headers=H).status_code == 409


def test_ensure_columns_upgrades_old_database(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE floor_plans (id INTEGER PRIMARY KEY, name VARCHAR(200), rows JSON, columns JSON, cells JSON)")
    con.commit()
    con.close()
    eng = create_engine(f"sqlite:///{path}")
    added = db.ensure_columns(eng, Base.metadata)
    assert "floor_plans.source_ref" in added and "floor_plans.synced_at" in added
    assert db.ensure_columns(eng, Base.metadata) == []  # idempotent


def test_rack_name_prefix_guess():
    from labkeeper.floorplan import suggest_rack_prefix

    assert suggest_rack_prefix(["V", "W"], ["14", "15"], ["SJ_V15", "SJ_W14", "OTHER_X1", "SJ_Q9"]) == "SJ_"
    assert suggest_rack_prefix(["A"], ["01"], ["nomatch"]) is None





def test_export_cors_only_for_declared_origin_and_extract_endpoint(client):
    pre = {"Origin": "https://dcim.example", "Access-Control-Request-Method": "POST", "Access-Control-Request-Private-Network": "true"}
    r = client.options("/api/v1/floorplans/extract", headers=pre)
    assert r.status_code == 204
    assert r.headers["access-control-allow-origin"] == "https://dcim.example"
    assert r.headers["access-control-allow-private-network"] == "true"
    assert "X-Labkeeper-User" in r.headers["access-control-allow-headers"]
    other = client.options("/api/v1/floorplans/extract", headers=pre | {"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in other.headers
    elsewhere = client.get("/api/v1/devices", headers={"Origin": "https://dcim.example"})
    assert "access-control-allow-origin" not in elsewhere.headers
    body = json.dumps({"format": "fake/1", "site": 9}).encode()
    posted = client.post("/api/v1/floorplans/extract", files={"file": ("x.json", body, "application/json")},
                         headers={"Origin": "https://dcim.example", **H})
    assert posted.status_code == 200 and posted.headers["access-control-allow-origin"] == "https://dcim.example"


def test_first_import_adopts_hand_made_plan_with_same_name(session, settings, imported):
    from labkeeper.models import FloorPlan

    manual = FloorPlan(name="Room 9", rows=["A"], columns=["01"], cells=[], rack_prefix="LAB_")
    session.add(manual)
    session.commit()
    plan, created, _ = upsert_source_plan(session, FakeSource(settings), SourcePlan(site_id="9", plan=grid()), actor="t")
    session.commit()
    assert not created and plan.id == manual.id and plan.source_ref == "fake:9" and plan.rows == ["A", "B"]


def test_meraki_key_from_alternate_env(monkeypatch):
    from labkeeper.config import Settings

    monkeypatch.delenv("MERAKI_DASHBOARD_API_KEY", raising=False)
    monkeypatch.setenv("OTHER_TOOL_MERAKI_KEY", "k-123")
    assert Settings(meraki={"api_key_env": "OTHER_TOOL_MERAKI_KEY"}).meraki_api_key == "k-123"
