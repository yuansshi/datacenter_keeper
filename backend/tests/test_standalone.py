"""The core workflow works with local hardware simulators and no cloud accounts."""

from __future__ import annotations

import asyncio
import sys

import httpx

from labkeeper.app import create_app
from labkeeper.config import Settings, configure
from labkeeper.importer import run_scan
from tests.conftest import write


def test_optional_services_are_off_by_default():
    settings = Settings()
    assert not settings.meraki.enabled
    assert not settings.agent.enabled
    assert not settings.agent.model
    assert settings.reachability.enabled


def test_vendor_serial_does_not_require_a_vendor_account(tmp_path):
    root = tmp_path / "testbeds"
    write(root, "example.yaml", """
        testbed: {name: example}
        devices:
          SWITCH_01:
            type: switch
            serial: Q2AA-0000-0001
            mgmt_ip: 192.0.2.10
          ADAPTER_01:
            type: switch
            serial: LAB-DEMO-ADAPTER-001
            custom: {labkeeper: {provider: meraki}}
    """)
    settings = Settings(data_dir=tmp_path / "data", testbeds={"paths": [str(root)]})
    devices = {device.name: device for device in run_scan(settings).devices.values()}
    assert devices["SWITCH_01"].provider == "reachability"
    assert devices["ADAPTER_01"].provider == "meraki"


async def test_health_console_and_shared_booking_without_accounts(tmp_path, monkeypatch):
    for name in ("MERAKI_DASHBOARD_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setitem(sys.modules, "anthropic", None)

    async def console(reader, writer):
        try:
            if await reader.read(64):
                writer.write(b"\r\nEXAMPLE_SWITCH#\r\n")
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(console, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    root = tmp_path / "testbeds"
    write(root, "common.yaml", f"""
        devices:
          EXAMPLE_SWITCH:
            type: switch
            model: ExampleSwitch-24P
            serial: LAB-DEMO-SWITCH-001
            connections:
              console: {{protocol: telnet, ip: 127.0.0.1, port: {port}}}
            custom:
              labkeeper:
                mgmt_ip: 127.0.0.1
                network: Example lab
    """)
    for name in ("example_a", "example_b"):
        write(root, name + ".yaml", f"extends: [common.yaml]\ntestbed: {{name: {name}}}\n")
    settings = configure(Settings(
        data_dir=tmp_path / "data",
        testbeds={"paths": [str(root)], "watch": False},
        reachability={"ports": [port], "timeout": 1},
    ))
    app = create_app(settings)
    try:
        async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://example.test") as client:
            devices = (await client.get("/api/v1/devices")).json()
            assert len(devices) == 1 and devices[0]["provider"] == "reachability"
            device_id = devices[0]["id"]
            refreshed = await client.post(f"/api/v1/devices/{device_id}/refresh")
            assert refreshed.status_code == 200 and refreshed.json()["status"] == "online"
            detail = (await client.get(f"/api/v1/devices/{device_id}")).json()
            assert detail["live"]["inventory"]["source"] == "reachability"
            networks = (await client.get("/api/v1/networks")).json()
            assert networks[0]["name"] == "Example lab"
            assert {item["name"] for item in networks[0]["testbeds"]} == {"example_a", "example_b"}

            testbeds = {item["name"]: item["id"] for item in (await client.get("/api/v1/testbeds")).json()}
            first = await client.post(f"/api/v1/testbeds/{testbeds['example_a']}/bookings", json={"duration_minutes": 30}, headers={"X-Labkeeper-User": "alice"})
            assert first.status_code == 201
            conflict = await client.post(f"/api/v1/testbeds/{testbeds['example_b']}/bookings", json={"duration_minutes": 30}, headers={"X-Labkeeper-User": "bob"})
            assert conflict.status_code == 409
            check = await client.post(f"/api/v1/devices/{device_id}/console-test", json={})
            assert check.status_code == 200 and check.json()["results"][0]["state"] == "ready"
            release = await client.post(f"/api/v1/bookings/{first.json()['id']}/release", json={}, headers={"X-Labkeeper-User": "alice"})
            assert release.status_code == 200
            second = await client.post(f"/api/v1/testbeds/{testbeds['example_b']}/bookings", json={"duration_minutes": 30}, headers={"X-Labkeeper-User": "bob"})
            assert second.status_code == 201
            events = (await client.get("/api/v1/events")).json()
            assert {"booked", "released", "console_test"} <= {event["action"] for event in events}
    finally:
        server.close()
        await server.wait_closed()


def test_shipped_demo_is_vendor_neutral(tmp_path):
    settings = Settings(demo=True, data_dir=tmp_path / "data", testbeds={"watch": False})
    scan = run_scan(settings)
    assert scan.testbeds and scan.devices
    assert not [item for item in scan.files if item.kind == "invalid"]
    assert not any("meraki_device" in device.custom for device in scan.devices.values())
    assert all(not device.serial or device.serial.startswith("LAB-DEMO-") for device in scan.devices.values())
