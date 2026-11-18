from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from labkeeper import db
from labkeeper.config import Settings, configure
from labkeeper.models import Base


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip())
    return path


@pytest.fixture
def lab(tmp_path: Path) -> Path:
    """A small lab in pyATS format: two stacks, a jump host, three testbeds sharing hardware."""
    root = tmp_path / "testbeds"
    write(
        root,
        "common/servers.yaml",
        """
        testbed:
          servers:
            jump1:
              address: 127.0.0.1
              credentials:
                default:
                  username: lab
                  password: s3cret-jump
        devices:
          jump1:
            os: linux
            type: rpi
            connections:
              linux:
                protocol: ssh
                ip: 10.0.0.10
        """,
    )
    write(
        root,
        "stacks/red.yaml",
        """
        extends:
          - ../common/servers.yaml
        devices:
          RED_01:
            type: meraki-ms
            os: linux
            connections:
              console:
                protocol: telnet
                ip: 127.0.0.1
                port: 9001
                proxy: jump1
                password: console-pass
              ssh:
                protocol: ssh
                ip: 10.0.1.1
            custom:
              meraki_device:
                stack: RED
                serial: Q2RR-0000-0001
                model: C9300-24UX
                location: LAB_A01_U10
          RED_02:
            type: meraki-ms
            os: linux
            connections:
              ssh:
                protocol: ssh
                ip: 10.0.1.2
            custom:
              meraki_device:
                stack: RED
                serial: Q2RR-0000-0002
                model: C9300-24UX
                location: LAB_A01_U09
          RED_C:
            type: meraki-ms
            chassis_type: stack
            connections:
              c1: {protocol: telnet, ip: 127.0.0.1, port: 9001, member: 1}
        """,
    )
    write(
        root,
        "stacks/blue.yaml",
        """
        extends:
          - ../common/servers.yaml
        devices:
          BLUE_01:
            type: meraki-ms
            os: linux
            connections:
              ssh: {protocol: ssh, ip: 10.0.2.1}
            custom:
              meraki_device:
                serial: Q2BB-0000-0003
                model: MS390-48UX2
                location: LAB_A02_U30
        """,
    )
    write(
        root,
        "smoke/red.yaml",
        """
        extends: [../stacks/red.yaml]
        testbed:
          name: red_smoke
          custom:
            type: Smoke
            dashboard_config: {org_id: "111"}
        topology:
          RED_01:
            interfaces:
              "24": {type: ethernet, tag: UPLINK_01}
        """,
    )
    write(
        root,
        "smoke/blue.yaml",
        """
        extends: [../stacks/blue.yaml]
        testbed:
          name: blue_smoke
          custom: {dashboard_config: {org_id: "111"}}
        """,
    )
    write(
        root,
        "regression/red_blue.yaml",
        """
        extends: [../stacks/red.yaml, ../stacks/blue.yaml]
        testbed:
          name: red_blue
        """,
    )
    return root


@pytest.fixture
def settings(tmp_path: Path, lab: Path) -> Settings:
    s = Settings(
        data_dir=tmp_path / "data",
        testbeds={"paths": [str(lab)], "watch": False},
        reachability={"enabled": False},
        meraki={"api_key": None},
    )
    s.data_dir.mkdir(parents=True, exist_ok=True)
    configure(s)
    return s


@pytest.fixture
def session(settings: Settings):
    db.init_engine(settings.resolved_database_url)
    Base.metadata.create_all(db.engine())
    s = db.new_session()
    yield s
    s.close()


@pytest.fixture
def imported(session, settings):
    from labkeeper.importer import apply_scan, run_scan

    summary = apply_scan(session, run_scan(settings), settings)
    session.commit()
    return summary
