"""Console connection tester.

Opens the console line exactly the way the testbed file describes it (telnet/ssh/raw TCP,
optionally through an SSH jump host given by ``proxy:``), nudges it with a carriage return and
classifies what comes back. The session is closed straight away so the line is free again.

States:
  ready         a CLI prompt was seen (``switch#``, ``root@host:~$`` ...)
  login         a username/login prompt was seen
  password      a password prompt was seen
  ssh           an SSH server banner answered (no login is attempted)
  connected     the port accepted the connection but printed nothing recognisable
  busy          the terminal server says the line is in use
  refused       connection refused (line in use, ser2net down, or wrong port)
  timeout       no TCP answer in time
  proxy_failed  the jump host could not be reached / authenticated
  error         anything else
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from ..secrets_store import ConnectionPlan

IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[()][A-Za-z0-9]|\x1b[=>]")
PROMPT_RE = re.compile(r"(?m)^[^\n\r]{0,80}?[\w\-\.\)\]~/:@]\s?[#>$%]\s*$")
LOGIN_RE = re.compile(r"(?im)(login|username|user name)\s*:\s*$")
PASSWORD_RE = re.compile(r"(?im)password\s*:\s*$")
BUSY_RE = re.compile(
    r"(?i)(port\s*[\w/]*\s*(is\s+)?(already\s+)?(in use|busy)|line\s*[\w/]*\s*is\s+busy|already\s+(connected|in use)"
    r"|session\s+in\s+use|in use by another|device or resource busy)"
)


@dataclass
class ConsoleTestResult:
    connection: str
    protocol: str
    state: str
    ok: bool
    message: str
    endpoint: str
    via: str | None = None
    duration_ms: int = 0
    output: str = ""
    hints: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _strip_telnet(data: bytes, writer: Any | None) -> bytes:
    """Remove telnet negotiation, refusing every option (keeps most terminal servers happy)."""
    out = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if b == IAC and i + 1 < len(data):
            cmd = data[i + 1]
            if cmd in (DO, DONT, WILL, WONT) and i + 2 < len(data):
                opt = data[i + 2]
                if writer is not None and cmd == DO:
                    writer.write(bytes([IAC, WONT, opt]))
                elif writer is not None and cmd == WILL:
                    # Accept ECHO(1) and SUPPRESS-GO-AHEAD(3) - typical for console servers.
                    writer.write(bytes([IAC, DO if opt in (1, 3) else DONT, opt]))
                i += 3
                continue
            if cmd == SB:
                end = data.find(bytes([IAC, SE]), i)
                i = len(data) if end == -1 else end + 2
                continue
            if cmd == IAC:
                out.append(IAC)
            i += 2
            continue
        out.append(b)
        i += 1
    return bytes(out)


def clean_output(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    text = ANSI_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or ch.isprintable())
    lines = [ln.rstrip() for ln in text.split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)[-1200:]


def classify(text: str) -> tuple[str, str]:
    tail = text[-400:]
    if BUSY_RE.search(tail):
        return "busy", "terminal server reports the line is already in use"
    if PASSWORD_RE.search(tail):
        return "password", "console is waiting for a password"
    if LOGIN_RE.search(tail):
        return "login", "console is at a login prompt"
    last = tail.strip().split("\n")[-1] if tail.strip() else ""
    if last and PROMPT_RE.search(last):
        return "ready", f"CLI prompt: {last[-60:]}"
    if text.strip():
        return "connected", "line answered, but no prompt was recognised"
    return "connected", "port accepted the connection but stayed silent (device off, or wrong baud?)"


async def _read_until_idle(reader: asyncio.StreamReader, writer: Any | None, deadline: float, telnet: bool,
                           nudge: bool) -> bytes:
    buf = bytearray()
    nudged = False
    quiet_after = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        wait = min(remaining, 0.6 if not nudged else 0.9)
        try:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=wait)
        except TimeoutError:
            chunk = None
        if chunk is None:
            if nudge and not nudged and writer is not None:
                writer.write(b"\r\n")
                try:
                    await writer.drain()
                except Exception:
                    pass
                nudged = True
                continue
            if quiet_after is not None or nudged:
                break
            continue
        if not chunk:
            break  # EOF
        data = _strip_telnet(chunk, writer if telnet else None) if telnet else chunk
        buf.extend(data)
        if telnet and writer is not None:
            try:
                await writer.drain()
            except Exception:
                pass
        quiet_after = time.monotonic()
        text = clean_output(bytes(buf))
        state, _ = classify(text)
        if state in {"ready", "login", "password", "busy"}:
            # one short extra read to catch the rest of a banner
            try:
                more = await asyncio.wait_for(reader.read(4096), timeout=0.25)
                if more:
                    buf.extend(_strip_telnet(more, None) if telnet else more)
            except TimeoutError:
                pass
            break
        if len(buf) > 64_000:
            break
    return bytes(buf)


class _ChannelStreams:
    """Adapter so an asyncssh direct-tcpip channel looks like asyncio streams."""

    def __init__(self, reader, writer):
        self._reader = reader
        self._writer = writer

    async def read(self, n: int) -> bytes:
        data = await self._reader.read(n)
        return data if isinstance(data, bytes) else data.encode()

    def write(self, data: bytes) -> None:
        self._writer.write(data)

    async def drain(self) -> None:
        await self._writer.drain()

    def close(self) -> None:
        self._writer.close()


async def probe(plan: ConnectionPlan, *, timeout: float = 8.0, send_newline: bool = True,
                allow_proxy: bool = True, verify_host_keys: bool = False) -> ConsoleTestResult:
    started = time.monotonic()
    protocol = plan.protocol or "telnet"
    endpoint = f"{plan.host}:{plan.port}" if plan.port else f"{plan.host}"
    via = plan.proxy.name if plan.proxy else None
    result = ConsoleTestResult(
        connection=plan.name, protocol=protocol, state="error", ok=False, message="", endpoint=endpoint, via=via
    )

    def done(state: str, message: str, output: str = "", hints: list[str] | None = None) -> ConsoleTestResult:
        result.state = state
        result.ok = state in {"ready", "login", "password", "ssh", "connected"}
        result.message = message
        result.output = output
        result.hints = hints or []
        result.duration_ms = int((time.monotonic() - started) * 1000)
        return result

    if not plan.host:
        return done("error", "connection has no ip/host in the testbed file")
    port = plan.port or (22 if protocol == "ssh" else 23)
    endpoint = f"{plan.host}:{port}"
    result.endpoint = endpoint
    if plan.proxy_error:
        return done("proxy_failed", plan.proxy_error)
    if plan.proxy and not allow_proxy:
        return done("error", "jump-host connections are disabled (console.allow_proxy=false)")

    deadline = started + timeout
    ssh_conn = None
    reader = writer = None
    try:
        if plan.proxy:
            try:
                import asyncssh
            except ImportError:  # pragma: no cover
                return done("error", "asyncssh is required for jump-host consoles")
            if plan.proxy.password_encrypted:
                return done("proxy_failed", f"password for {plan.proxy.name} is %ENC{{}}-encrypted; use %ENV{{}} or a key")
            options: dict[str, Any] = {
                "port": plan.proxy.port,
                "login_timeout": max(2.0, timeout / 2),
                "connect_timeout": max(2.0, timeout / 2),
            }
            if not verify_host_keys:
                options["known_hosts"] = None
            if plan.proxy.username:
                options["username"] = plan.proxy.username
            if plan.proxy.password:
                options["password"] = plan.proxy.password
                options["client_keys"] = ()  # password given: don't try local keys first
            try:
                ssh_conn = await asyncio.wait_for(
                    asyncssh.connect(plan.proxy.host, **options),
                    timeout=max(2.5, timeout / 2 + 0.5),
                )
            except (TimeoutError, OSError) as exc:
                return done("proxy_failed", f"cannot reach jump host {plan.proxy.name} ({plan.proxy.host}): {exc or 'timeout'}")
            except Exception as exc:
                return done("proxy_failed", f"jump host {plan.proxy.name} rejected the login: {type(exc).__name__}: {exc}")
            try:
                ch_reader, ch_writer = await asyncio.wait_for(
                    ssh_conn.open_connection(plan.host, port), timeout=max(1.0, deadline - time.monotonic())
                )
                reader = writer = _ChannelStreams(ch_reader, ch_writer)
            except Exception as exc:
                # Port forwarding may be disabled on the jump host: fall back to a port check there.
                cmd = f"timeout 3 bash -c 'exec 3<>/dev/tcp/{plan.host}/{port}' && echo OPEN || echo CLOSED"
                try:
                    res = await asyncio.wait_for(ssh_conn.run(cmd, check=False), timeout=5)
                    out = str(res.stdout or "").strip()
                except Exception:
                    out = ""
                if out.endswith("OPEN"):
                    return done("connected", f"{endpoint} is open on {plan.proxy.name} (forwarding disabled, prompt not checked)")
                if out.endswith("CLOSED"):
                    return done("refused", f"{endpoint} refused on {plan.proxy.name}",
                                hints=["The console line may be in use, or ser2net/conserver is not running."])
                msg = str(exc) or type(exc).__name__
                if "refused" in msg.lower() or "connect failed" in msg.lower():
                    return done("refused", f"{endpoint} refused (via {plan.proxy.name})",
                                hints=["The console line may be in use, or ser2net/conserver is not running."])
                return done("error", f"could not open {endpoint} through {plan.proxy.name}: {msg}")
        else:
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(plan.host, port), timeout=max(1.0, deadline - time.monotonic())
                )
            except ConnectionRefusedError:
                return done("refused", f"{endpoint} refused the connection",
                            hints=["Someone may hold the console line, or the terminal server port is wrong."])
            except TimeoutError:
                return done("timeout", f"no answer from {endpoint} within {timeout:.0f}s",
                            hints=["Check VPN/routing to the terminal server."])
            except OSError as exc:
                return done("timeout" if "timed out" in str(exc) else "error", f"{endpoint}: {exc}")

        if protocol == "ssh":
            try:
                banner = await asyncio.wait_for(reader.read(256), timeout=max(0.5, deadline - time.monotonic()))
            except TimeoutError:
                banner = b""
            text = banner.decode("utf-8", errors="replace").strip()
            if text.startswith("SSH-"):
                return done("ssh", f"SSH server answered: {text.splitlines()[0][:80]}", text[:200])
            return done("connected", "port open, but no SSH banner", clean_output(banner))

        raw = await _read_until_idle(
            reader, writer, deadline, telnet=protocol == "telnet", nudge=send_newline
        )
        text = clean_output(raw)
        state, message = classify(text)
        hints = []
        if state == "connected" and not text.strip():
            hints.append("Try a hard power-cycle or check the console cable/baud rate.")
        return done(state, message, text, hints)
    except Exception as exc:  # pragma: no cover - defensive
        return done("error", f"{type(exc).__name__}: {exc}")
    finally:
        if writer is not None:
            try:
                writer.close()
            except Exception:
                pass
        if ssh_conn is not None:
            ssh_conn.close()
