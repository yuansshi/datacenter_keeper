"""Demo mode: sample bookings, a floor plan, and fake console servers on 127.0.0.1:17001-17010."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import services as sv
from .config import Settings
from .events import record
from .models import Booking, FloorPlan, Testbed
from .timeutil import utcnow

log = logging.getLogger(__name__)

# port -> (behaviour, prompt)
CONSOLES = {
    17001: ("prompt", "ALPHA_01#"),
    17002: ("prompt", "ALPHA_02#"),
    17003: ("prompt", "BRAVO_01>"),
    17005: ("prompt", "DELTA_01#"),
    17006: ("prompt", "CORE_01#"),
    17007: ("login", "Username: "),
    17008: ("password", "Password: "),
    17009: ("busy", "Port 9 is in use by another session\r\n"),
    17010: ("silent", ""),
    # 17004 and 17011 are left closed on purpose -> "refused"
}


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, behaviour: str, text: str) -> None:
    try:
        if behaviour == "busy":
            writer.write(text.encode())
            await writer.drain()
            return
        if behaviour == "silent":
            await asyncio.sleep(10)
            return
        writer.write(b"\xff\xfb\x01\xff\xfb\x03")  # IAC WILL ECHO, IAC WILL SGA
        await writer.drain()
        for _ in range(20):
            data = await asyncio.wait_for(reader.read(256), timeout=10)
            if not data:
                return
            if b"\r" in data or b"\n" in data:
                writer.write(b"\r\n" + text.encode())
                await writer.drain()
    except (TimeoutError, ConnectionError):
        pass
    finally:
        writer.close()


async def start_demo_consoles() -> list[asyncio.AbstractServer]:
    servers = []
    for port, (behaviour, text) in CONSOLES.items():
        try:
            server = await asyncio.start_server(
                lambda r, w, b=behaviour, t=text: _handle(r, w, b, t), host="127.0.0.1", port=port
            )
            servers.append(server)
        except OSError as exc:
            log.warning("demo console on port %s not started: %s", port, exc)
    return servers


DEMO_FLOOR = {
    "name": "Example lab floor",
    "site": "Example site",
    "rack_prefix": "EX_",
    "metric_label": "kW",
    "rows": ["A", "B", "C", "D", "E", "F"],
    "columns": [f"{i:02d}" for i in range(1, 13)],
}


def _demo_cells() -> list[dict]:
    cells: list[dict] = []

    def add(row, col, type_, span_cols=1, span_rows=1, metric=None, label=None):
        cells.append({"row": row, "col": f"{col:02d}", "type": type_, "span_cols": span_cols, "span_rows": span_rows,
                      "metric": metric, "label": label})

    metrics = {"A": [1.2, 2.4, None, 3.1, 0.8, None], "B": [2.2, None, 1.6, 4.2, 2.9, 1.1]}
    for row in ("A", "B"):
        for i, col in enumerate(range(1, 7)):
            add(row, col, "rack" if metrics[row][i] is not None else "empty_rack", metric=metrics[row][i])
        add(row, 9, "bench", span_cols=3)
    for i, col in enumerate(range(1, 7)):
        add("C", col, "rack", metric=[1.4, 1.9, 3.3, 2.6, 0.9, 1.7][i])
    add("C", 8, "pillar")
    add("C", 9, "storage", span_cols=2)
    for i, col in enumerate(range(1, 7)):
        add("D", col, "rack" if col != 5 else "empty_rack", metric=[4.4, 3.8, 2.7, None, None, 1.5][i])
    add("D", 9, "rack", metric=2.0)
    add("D", 10, "rack", metric=0.6)
    add("E", 1, "unassigned", span_cols=4, span_rows=2)
    add("E", 6, "pillar")
    add("E", 9, "bench", span_cols=4)
    add("F", 9, "open_space", span_cols=4)
    return cells


def seed_demo(session: Session, settings: Settings) -> None:
    if session.scalar(select(func.count(Booking.id))):
        return
    now = utcnow()
    tbs = {t.name: t for t in session.scalars(select(Testbed))}

    def book(name: str, owner: str, start_min: int, minutes: int, purpose: str, released: bool = False):
        tb = tbs.get(name)
        if tb is None:
            return
        b = Booking(testbed=tb, owner=owner, purpose=purpose, starts_at=now + timedelta(minutes=start_min),
                    ends_at=now + timedelta(minutes=start_min + minutes), created_via="ui")
        if released:
            b.released_at = b.ends_at - timedelta(minutes=10)
        session.add(b)
        session.flush()
        record(session, actor=owner, action="booked", object_type="testbed", object_id=tb.id, object_name=tb.name,
               message=purpose)

    book("alpha_bravo", "alice", -40, 180, "Switch-stack regression")
    book("echo_smoke", "carol", -90, 135, "PoE budget soak")
    book("delta_perf", "bob", 120, 240, "Throughput baseline")
    book("core_routing", "dave", 60 * 20, 180, "OSPF interop")
    book("charlie_smoke", "erin", -60 * 26, 120, "nightly smoke", released=True)
    charlie = tbs.get("charlie_smoke")
    if charlie:
        charlie.maintenance = True
        charlie.maintenance_note = "CHARLIE_02 PSU replacement"
        record(session, actor="erin", action="maintenance", object_type="testbed", object_id=charlie.id,
               object_name=charlie.name, message="in maintenance: CHARLIE_02 PSU replacement")

    if not session.scalar(select(FloorPlan).where(FloorPlan.name == DEMO_FLOOR["name"])):
        plan = FloorPlan(**DEMO_FLOOR, cells=_demo_cells(), source="manual")
        session.add(plan)
        session.flush()
        sv.link_floor_racks(session, plan)
