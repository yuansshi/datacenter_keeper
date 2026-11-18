"""Switch stacks reported by a status provider: grouping, combined detail, adoption of file-declared stacks."""

from __future__ import annotations

import httpx
from sqlalchemy import select

from labkeeper import services as sv
from labkeeper.config import Settings
from labkeeper.models import Device
from labkeeper.providers.base import DeviceRef, StackInfo, StatusUpdate
from labkeeper.providers.meraki import MerakiProvider
from labkeeper.providers.poller import apply_updates


def _ref(serial: str, key: str) -> DeviceRef:
    return DeviceRef(id=1, key=key, name=key, serial=serial, mgmt_ip=None, org_id="111", model="MS150", role="switch", type="meraki-ms")


def _transport(calls: list[str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(path)
        if path.endswith("/api/v1/organizations"):
            return httpx.Response(200, json=[{"id": "111", "name": "Lab"}])
        if path.endswith("/devices/availabilities"):
            return httpx.Response(200, json=[
                {"serial": s, "status": "online", "network": {"id": "N_1"}} for s in ("Q4AA-0001", "Q4AA-0002", "Q4AA-0003")
            ])
        if path.endswith("/organizations/111/devices"):
            return httpx.Response(200, json=[
                {"serial": s, "model": "MS150-48MP-4X", "networkId": "N_1", "productType": "switch"}
                for s in ("Q4AA-0001", "Q4AA-0002", "Q4AA-0003")
            ])
        if path.endswith("/organizations/111/networks"):
            return httpx.Response(200, json=[{"id": "N_1", "name": "poe", "productTypes": ["switch"]}])
        if path.endswith("/networks/N_1/switch/stacks"):
            return httpx.Response(200, json=[{
                "id": "S1", "name": "2M Stack", "serials": ["Q4AA-0001", "Q4AA-0002"], "virtualMac": "00:18:0a:00:00:01",
                "members": [
                    {"serial": "Q4AA-0002", "name": "sw-2", "model": "MS150-48MP-4X", "role": "member", "switchNumber": 2},
                    {"serial": "Q4AA-0001", "name": "sw-1", "model": "MS150-48MP-4X", "role": "active", "switchNumber": 1},
                ],
            }])
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def test_meraki_reports_switch_stacks_per_network():
    calls: list[str] = []
    p = MerakiProvider(Settings(meraki={"enabled": True, "api_key": "k", "base_url": "https://api.meraki.test/api/v1"}), transport=_transport(calls))
    refs = [_ref("Q4AA-0001", "a"), _ref("Q4AA-0002", "b"), _ref("Q4AA-0003", "c")]
    updates = await p.poll(refs)
    await p.poll(refs)  # stacks are cached at the inventory pace
    await p.aclose()
    a, b, c = updates["a"], updates["b"], updates["c"]
    assert a.stack_checked and a.stack.key == "meraki:S1" and a.stack.name == "2M Stack"
    assert (a.stack.role, a.stack.number, b.stack.role, b.stack.number) == ("active", 1, "member", 2)
    assert [m["serial"] for m in a.stack.members] == ["Q4AA-0002", "Q4AA-0001"]
    assert c.stack_checked and c.stack is None  # looked, not stacked
    assert sum(path.endswith("/switch/stacks") for path in calls) == 1


def _stack(name="Blue Stack", role="active", number=1) -> StackInfo:
    return StackInfo(key="meraki:S9", name=name, role=role, number=number,
                     members=[{"serial": "Q2BB-0000-0003", "name": "BLUE_01", "role": role, "number": number},
                              {"serial": "Q2XX-9999-0000", "name": "not tracked", "role": "member", "number": 2}])


def test_provider_stack_groups_members_and_combines_detail(session, imported):
    blue = session.scalar(select(Device).where(Device.name == "BLUE_01"))
    apply_updates(session, {blue.key: StatusUpdate(status="alerting", stack=_stack(), stack_checked=True)}, "meraki")
    session.flush()
    stack = session.scalar(select(Device).where(Device.key == "stack:meraki:S9"))
    assert stack is not None and stack.is_virtual and blue.parent is stack and stack.name == "Blue Stack"
    assert stack.live["stack"]["members"][1]["name"] == "not tracked"

    listed = {d.name: d for d in sv.list_devices(session)}
    row = listed["Blue Stack"]
    assert row.member_count == 1 and row.status == "alerting" and row.testbed_count == 2
    assert listed["BLUE_01"].parent_id == stack.id and listed["BLUE_01"].stack_role == "active"
    # a filtered list still brings the stack of every member it shows
    by_testbed = {d.name for d in sv.list_devices(session, testbed_id=blue.memberships[0].testbed_id)}
    assert {"BLUE_01", "Blue Stack"} <= by_testbed

    detail = sv.device_detail(session, stack)
    assert [m.name for m in detail.members] == ["BLUE_01"]
    assert sorted(t.name for t in detail.testbeds) == ["blue_smoke", "red_blue"]
    assert any(e.action == "stacked" for e in detail.events)  # members' history shows on the stack

    # the provider looked again and the switch is no longer stacked: parent cleared, empty stack removed
    apply_updates(session, {blue.key: StatusUpdate(status="online", stack_checked=True)}, "meraki")
    session.flush()
    assert blue.parent is None and "stack" not in blue.live and stack.removed_at is not None


def test_file_declared_stack_is_kept_and_annotated(session, imported):
    red = session.scalar(select(Device).where(Device.name == "RED_01"))
    yaml_stack = red.parent
    assert yaml_stack is not None and yaml_stack.key.startswith("virtual:")
    apply_updates(session, {red.key: StatusUpdate(status="online", stack=_stack("RED"), stack_checked=True)}, "meraki")
    session.flush()
    assert red.parent is yaml_stack and yaml_stack.live["stack"]["name"] == "RED"
    assert session.scalar(select(Device).where(Device.key == "stack:meraki:S9")) is None
