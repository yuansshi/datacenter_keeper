from __future__ import annotations

import asyncio
import socket

import pytest

from labkeeper.console import classify, probe
from labkeeper.secrets_store import ConnectionPlan, ProxyPlan


async def _server(behaviour: str):
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            if behaviour == "busy":
                writer.write(b"Port 7 is in use by another session\r\n")
                await writer.drain()
                return
            if behaviour == "silent":
                await asyncio.sleep(5)
                return
            writer.write(b"\xff\xfd\x18\xff\xfb\x01")  # IAC DO TTYPE, IAC WILL ECHO
            await writer.drain()
            while True:
                data = await asyncio.wait_for(reader.read(100), 5)
                if not data:
                    return
                if b"\r" in data or b"\n" in data:
                    prompt = {"prompt": b"\r\nswitch-01#", "login": b"\r\nUsername: ", "password": b"\r\nPassword: "}[behaviour]
                    writer.write(prompt)
                    await writer.drain()
        except (TimeoutError, ConnectionError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.mark.parametrize(("behaviour", "state"), [("prompt", "ready"), ("login", "login"), ("password", "password"), ("busy", "busy")])
async def test_telnet_states(behaviour, state):
    server, port = await _server(behaviour)
    async with server:
        res = await probe(ConnectionPlan(name="console", protocol="telnet", host="127.0.0.1", port=port), timeout=4)
    assert res.state == state, res
    assert res.duration_ms < 4000


async def test_silent_line_is_connected_but_flagged():
    server, port = await _server("silent")
    async with server:
        res = await probe(ConnectionPlan(name="c", protocol="telnet", host="127.0.0.1", port=port), timeout=2.5)
    assert res.state == "connected" and res.ok
    assert res.hints


async def test_refused():
    res = await probe(ConnectionPlan(name="c", protocol="telnet", host="127.0.0.1", port=_free_port()), timeout=2)
    assert res.state == "refused" and not res.ok


async def test_unreachable_jump_host():
    plan = ConnectionPlan(
        name="c", protocol="telnet", host="127.0.0.1", port=9000,
        proxy=ProxyPlan(name="jump", host="127.0.0.1", port=_free_port(), username="lab", password="x"),
    )
    res = await probe(plan, timeout=3)
    assert res.state == "proxy_failed"
    assert "jump" in res.message


async def test_missing_proxy_definition_is_reported_without_connecting():
    plan = ConnectionPlan(name="c", protocol="telnet", host="127.0.0.1", port=1, proxy_error="jump host 'x' is not defined")
    res = await probe(plan)
    assert res.state == "proxy_failed" and "not defined" in res.message


def test_classify_prompts():
    assert classify("Cisco IOS\nswitch#")[0] == "ready"
    assert classify("root@ms-switch:~$ ")[0] == "ready"
    assert classify("login: ")[0] == "login"
    assert classify("")[0] == "connected"
