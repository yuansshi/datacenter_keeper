from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from labkeeper.importer import apply_scan, masked_source, run_scan
from labkeeper.importer.pyats import Normalizer, TestbedFileError, TestbedLoader, parse_location
from labkeeper.models import Device, ImportSource, Rack, Testbed
from labkeeper.secrets_store import redact, secret_store

from .conftest import write


def test_leaf_files_become_testbeds_and_extended_files_are_bases(imported, session):
    assert imported.testbeds == 3
    assert imported.bases == 3
    names = sorted(t.name for t in session.scalars(select(Testbed)))
    assert names == ["blue_smoke", "red_blue", "red_smoke"]
    kinds = {s.path.split("testbeds/")[-1]: s.kind for s in session.scalars(select(ImportSource))}
    assert kinds["stacks/red.yaml"] == "base"
    assert kinds["smoke/red.yaml"] == "testbed"


def test_devices_dedupe_by_serial_across_testbeds(imported, session):
    reds = list(session.scalars(select(Device).where(Device.name == "RED_01")))
    assert len(reds) == 1
    assert reds[0].key == "serial:Q2RR-0000-0001"
    assert reds[0].org_id == "111"
    assert len(reds[0].memberships) == 2  # red_smoke and red_blue


def test_rack_position_and_stack_parent(imported, session):
    red1 = session.scalar(select(Device).where(Device.name == "RED_01"))
    assert red1.rack.name == "LAB_A01" and red1.rack_position == 10
    stack = session.scalar(select(Device).where(Device.name == "RED_C"))
    assert stack.is_virtual and stack.rack is None
    assert red1.parent_id == stack.id
    assert {r.name for r in session.scalars(select(Rack))} == {"LAB_A01", "LAB_A02"}


def test_shared_support_devices_are_not_exclusive(imported, session):
    tb = session.scalar(select(Testbed).where(Testbed.name == "red_smoke"))
    by_name = {m.device.name: m.exclusive for m in tb.members}
    assert by_name["jump1"] is False
    assert by_name["RED_01"] is True


def test_secrets_never_reach_the_database_but_proxy_plan_has_them(imported, session):
    red1 = session.scalar(select(Device).where(Device.name == "RED_01"))
    blob = json.dumps({"c": red1.connections, "x": red1.custom})
    assert "console-pass" not in blob and "s3cret-jump" not in blob
    plan = secret_store.plans_for(red1.key)["console"]
    assert plan.is_console and plan.port == 9001
    assert plan.proxy is not None and plan.proxy.username == "lab" and plan.proxy.password == "s3cret-jump"


def test_masked_source_hides_passwords():
    text = "credentials:\n  default:\n    username: lab\n    password: hunter2\n  enable: topsecret\nsession_enabled: true\n"
    out = masked_source(text)
    assert "hunter2" not in out and "topsecret" not in out
    assert "session_enabled: true" in out and "username: lab" in out


def test_redact_keeps_structure():
    data = {"a": {"password": "x", "username": "u"}, "list": [{"api_key": "k"}], "enable": "e"}
    assert redact(data) == {"a": {"password": "••••••", "username": "u"}, "list": [{"api_key": "••••••"}], "enable": "••••••"}


def test_env_markup_resolves(tmp_path, monkeypatch):
    monkeypatch.setenv("JUMP_PW", "from-env")
    root = tmp_path / "tb"
    write(root, "t.yaml", """
        testbed:
          servers:
            j: {address: 10.1.1.1, credentials: {default: {username: u, password: "%ENV{JUMP_PW}"}}}
        devices:
          D1:
            type: switch
            connections:
              console: {protocol: telnet, ip: 127.0.0.1, port: 2001, proxy: j}
    """)
    from labkeeper.importer.pyats import scan

    res = scan([root], Normalizer([], r"^(?P<rack>.+?)_U(?P<position>\d+)$"))
    dev = next(iter(res.devices.values()))
    assert dev.plans["console"].proxy.password == "from-env"


def test_extends_outside_roots_is_rejected(tmp_path):
    root = tmp_path / "inside"
    write(tmp_path, "outside.yaml", "devices: {X: {type: linux}}\n")
    leaf = write(root, "leaf.yaml", "extends: [../outside.yaml]\ndevices: {Y: {type: linux}}\n")
    with pytest.raises(TestbedFileError, match="outside"):
        TestbedLoader([root]).load(leaf)


def test_extends_cycle_is_reported(tmp_path):
    root = tmp_path / "tb"
    write(root, "a.yaml", "extends: [b.yaml]\ndevices: {A: {type: linux}}\n")
    write(root, "b.yaml", "extends: [a.yaml]\ndevices: {B: {type: linux}}\n")
    with pytest.raises(TestbedFileError, match="cycle"):
        TestbedLoader([root]).load(root / "a.yaml")


def test_missing_base_marks_file_invalid(lab, settings, session):
    write(lab, "broken/x.yaml", "extends: [../nope.yaml]\ntestbed: {name: x}\n")
    summary = apply_scan(session, run_scan(settings), settings)
    assert summary.invalid == 1
    assert "file not found" in summary.errors[0]["error"]


def test_reimport_is_idempotent_and_removals_are_soft(lab, settings, session, imported):
    again = apply_scan(session, run_scan(settings), settings)
    assert again.devices_added == 0 and again.devices_updated == 0
    (lab / "smoke/blue.yaml").unlink()
    (lab / "regression/red_blue.yaml").unlink()
    (lab / "stacks/blue.yaml").unlink()
    after = apply_scan(session, run_scan(settings), settings)
    assert after.testbeds_removed == 2
    blue = session.scalar(select(Device).where(Device.name == "BLUE_01"))
    assert blue.removed_at is not None


def test_manual_placement_survives_reimport(settings, session, imported):
    from labkeeper import services as sv

    red1 = session.scalar(select(Device).where(Device.name == "RED_01"))
    rack = session.scalar(select(Rack).where(Rack.name == "LAB_A02"))
    sv.place_device(session, red1, rack_id=rack.id, position=1, face="front", actor="t")
    session.commit()
    apply_scan(session, run_scan(settings), settings)
    session.refresh(red1)
    assert red1.rack.name == "LAB_A02" and red1.rack_position == 1


@pytest.mark.parametrize(
    ("loc", "expected"),
    [("B1_C03_U20", ("B1_C03", 20)), ("B1_C03", ("B1_C03", None)), ("B1-C03-U42", ("B1-C03", 42)), (None, (None, None))],
)
def test_parse_location(loc, expected):
    import re

    assert parse_location(loc, re.compile(r"^(?P<rack>.+?)[_\-\s]U(?P<position>\d{1,3})$")) == expected


def _benchmark_lab_style(root):
    write(root, "benchmark/common/base.yaml", """
        testbed:
          name: BENCHMARK_COMMON
          custom: {organization_id: "example-org"}
    """)
    write(root, "benchmark/devices/switches.yaml", """
        devices:
          ACCESS_01:
            os: linux
            type: switch
            model: ExampleSwitch
            connections:
              ssh_1: {protocol: ssh, ip: 198.51.100.10, port: 22}
              console: {protocol: ssh, ip: 192.0.2.10, port: 3021}
            custom:
              sku: ExampleSwitch-24P
              members:
                - {serial: LAB-DEMO-ACCESS-001, mac: "02:00:00:00:00:01"}
          STACK_01:
            type: switch
            chassis_type: stack
            model: ExampleSwitch
            custom:
              sku: ExampleSwitch-48P
              stack_name: EXAMPLE_STACK
              members:
                - {serial: LAB-DEMO-STACK-001}
    """)
    write(root, "benchmark/BENCHMARK_LAB.yaml", """
        extends: [common/base.yaml, devices/switches.yaml]
        testbed: {name: BENCHMARK_LAB}
    """)
    write(root, "benchmark/firmware/model_platforms.yaml", "ExampleSwitch: generic\n")


def test_generic_layout_members_sku_and_org(tmp_path):
    from labkeeper.importer.pyats import scan

    root = tmp_path / "example"
    _benchmark_lab_style(root)
    res = scan([root], Normalizer(["rpi"], r"^(?P<rack>.+?)_U(?P<position>\d+)$"), include=["BENCHMARK_LAB"])
    devs = {d.name: d for d in res.devices.values()}
    assert sorted(devs) == ["ACCESS_01", "STACK_01"]
    member = devs["STACK_01"]
    assert not member.is_virtual and member.provider is None and member.stack_name == "EXAMPLE_STACK"
    assert member.key == "serial:LAB-DEMO-STACK-001" and member.u_height == 1
    dev = devs["ACCESS_01"]
    assert dev.serial == "LAB-DEMO-ACCESS-001" and dev.key == "serial:LAB-DEMO-ACCESS-001"
    assert dev.model == "ExampleSwitch-24P" and dev.provider == "reachability" and dev.vendor == "Linux"
    assert dev.org_id == "example-org" and dev.mgmt_ip == "198.51.100.10"
    assert dev.plans["console"].is_console and dev.plans["console"].protocol == "ssh"
    kinds = {r.path.name: r.kind for r in res.files}
    assert kinds["BENCHMARK_LAB.yaml"] == "testbed" and kinds["model_platforms.yaml"] == "excluded"
    assert not [r for r in res.files if r.kind == "invalid"]


def test_include_allowlist_tracks_only_listed_testbeds(lab, settings, session, imported):
    assert imported.testbeds == 3
    settings.testbeds.include = ["red_smoke", "regression/*"]
    summary = apply_scan(session, run_scan(settings), settings)
    active = sorted(t.name for t in session.scalars(select(Testbed).where(Testbed.removed_at.is_(None))))
    assert active == ["red_blue", "red_smoke"] and summary.testbeds_removed == 1
    settings.testbeds.include = ["red_smoke"]
    apply_scan(session, run_scan(settings), settings)
    blue = session.scalar(select(Device).where(Device.name == "BLUE_01"))
    assert blue.removed_at is not None  # only devices of tracked testbeds stay
    red = session.scalar(select(Device).where(Device.name == "RED_01"))
    assert red.removed_at is None
