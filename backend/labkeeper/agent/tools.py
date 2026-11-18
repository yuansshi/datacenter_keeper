"""Tools the assistant can call. The same registry backs the in-app chat and the MCP server.

Every tool:
  * validates its input with a pydantic model (also the source of the JSON schema),
  * runs as the requesting user (bookings are owned by them, the audit log records the channel),
  * returns compact JSON so the model gets facts, not page dumps.
"""

from __future__ import annotations

import inspect
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select

from .. import booking as bk
from .. import services as sv
from ..config import Settings
from ..console import probe
from ..db import run_in_session
from ..events import record
from ..floorplan import normalise
from ..models import Booking, Event, FloorPlan, Rack
from ..providers import Poller, device_ref
from ..schemas import FloorPlanIn
from ..secrets_store import secret_store
from ..timeutil import utcnow

log = logging.getLogger(__name__)


@dataclass
class Runtime:
    settings: Settings
    poller: Poller | None = None
    import_now: Callable[[str], Awaitable[dict[str, Any]]] | None = None
    floor_sync: Any = None  # floorsources.FloorSync


@dataclass
class ToolContext:
    actor: str
    via: str  # agent | mcp
    runtime: Runtime
    tz_offset_minutes: int = 0  # user's offset from UTC, for times given without a zone

    def to_utc(self, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone(timedelta(minutes=self.tz_offset_minutes)))
        return value.astimezone(UTC)


class ToolError(Exception):
    pass


@dataclass
class ToolSpec:
    name: str
    description: str
    model: type[BaseModel]
    handler: Callable[[ToolContext, Any], Awaitable[Any]]
    mutating: bool = False
    title: str = ""

    def input_schema(self) -> dict[str, Any]:
        schema = self.model.model_json_schema()
        _strip_titles(schema)
        schema.setdefault("properties", {})
        schema["type"] = "object"
        return schema


@dataclass
class ToolOutcome:
    ok: bool
    data: Any = None
    error: str | None = None
    summary: str = ""

    def as_content(self) -> str:
        if self.ok:
            return json.dumps(self.data, default=str, separators=(",", ":"))
        return json.dumps({"error": self.error}, separators=(",", ":"))


def _strip_titles(node: Any) -> None:
    if isinstance(node, dict):
        node.pop("title", None)
        for v in node.values():
            _strip_titles(v)
    elif isinstance(node, list):
        for v in node:
            _strip_titles(v)


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolRegistry:
    def __init__(self) -> None:
        self.tools: dict[str, ToolSpec] = {}

    def tool(self, name: str, description: str, model: type[BaseModel], *, mutating: bool = False, title: str = ""):
        def deco(fn: Callable[[ToolContext, Any], Awaitable[Any]]):
            if not inspect.iscoroutinefunction(fn):
                raise TypeError(f"tool {name} must be async")
            self.tools[name] = ToolSpec(name, description.strip(), model, fn, mutating, title or name.replace("_", " "))
            return fn

        return deco

    def anthropic_tools(self, eager_input_streaming: bool = True) -> list[dict[str, Any]]:
        out = []
        for spec in self.tools.values():
            t: dict[str, Any] = {"name": spec.name, "description": spec.description, "input_schema": spec.input_schema()}
            if eager_input_streaming:
                t["eager_input_streaming"] = True
            out.append(t)
        return out

    async def execute(self, name: str, raw: Any, ctx: ToolContext) -> ToolOutcome:
        spec = self.tools.get(name)
        if spec is None:
            return ToolOutcome(False, error=f"unknown tool {name}")
        try:
            args = spec.model.model_validate(raw if isinstance(raw, dict) else {})
        except ValidationError as exc:
            problems = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'input'}: {e['msg']}" for e in exc.errors()[:5])
            return ToolOutcome(False, error=f"invalid input: {problems}")
        try:
            data = await spec.handler(ctx, args)
        except (ToolError, sv.NotFound, sv.Invalid, bk.BookingError) as exc:
            return ToolOutcome(False, error=str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("tool %s failed", name)
            return ToolOutcome(False, error=f"{type(exc).__name__}: {exc}")
        summary = ""
        if isinstance(data, dict) and "_summary" in data:
            summary = str(data.pop("_summary"))
        return ToolOutcome(True, data=data, summary=summary)


registry = ToolRegistry()


# ---------------------------------------------------------------------------------------------------
# compact views
# ---------------------------------------------------------------------------------------------------


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _dev(d: Any) -> dict[str, Any]:
    out = {
        "name": d.name,
        "status": d.status,
        "model": d.model,
        "role": d.role,
        "serial": d.serial,
        "mgmt_ip": d.mgmt_ip,
        "rack": f"{d.rack.name}" + (f" U{d.rack_position}" if d.rack_position else "") if d.rack else None,
        "network": d.network.name if d.network else None,
        "in_use_by": d.in_use_by,
        "console": d.has_console or None,
        "shared": d.is_shared or None,
        "stack": d.is_virtual or None,
    }
    if d.status_detail and d.status != "online":
        out["status_detail"] = d.status_detail
    return {k: v for k, v in out.items() if v not in (None, "", [])}


def _booking(b: Any) -> dict[str, Any]:
    return {
        "id": b.id,
        "testbed": b.testbed_name,
        "owner": b.owner,
        "from": _iso(b.starts_at),
        "until": _iso(b.effective_end),
        "state": b.state,
        "purpose": b.purpose,
    }


def _tb(t: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": t.name,
        "status": t.status,
        "group": t.group,
        "models": t.models,
        "devices": t.exclusive_count,
        "health": {k: v for k, v in t.health.model_dump().items() if v},
    }
    if t.current:
        out["held_by"] = t.current.owner
        out["until"] = _iso(t.current.effective_end)
    if t.blocked_by:
        out["blocked_by"] = [f"{b.testbed_name} ({b.owner} until {_iso(b.effective_end)})" for b in t.blocked_by[:3]]
    if t.next:
        out["next_booking"] = f"{t.next.owner} from {_iso(t.next.starts_at)}"
    if t.maintenance:
        out["maintenance"] = t.maintenance_note or True
    return out


# ---------------------------------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------------------------------


class NoArgs(Args):
    pass


@registry.tool(
    "lab_overview",
    """Snapshot of the whole lab: device health counts, testbeds free / in use / blocked, the
    caller's own bookings, what is in use right now, upcoming bookings and devices needing attention.
    Call this first for broad questions ("what's going on in the lab?", "anything down?").""",
    NoArgs,
)
async def lab_overview(ctx: ToolContext, args: NoArgs) -> dict[str, Any]:
    data = await run_in_session(lambda s: sv.stats(s, ctx.actor))
    return {
        "devices": data["devices"],
        "testbeds": data["testbeds"],
        "racks": data["racks"],
        "my_bookings": [
            {"testbed": b["testbed_name"], "until": b["effective_end"], "state": b["state"]} for b in data["my_bookings"]
        ],
        "in_use": [{"testbed": b["testbed_name"], "owner": b["owner"], "until": b["effective_end"]} for b in data["in_use"]],
        "upcoming_24h": [{"testbed": b["testbed_name"], "owner": b["owner"], "from": b["starts_at"]} for b in data["upcoming"]],
        "attention": [{"name": d["name"], "status": d["status"], "detail": d["status_detail"]} for d in data["attention"]],
    }


class SearchDevicesArgs(Args):
    query: str | None = Field(None, description="Substring of name, serial, model, IP or alias")
    status: str | None = Field(None, description="Comma list: online,alerting,offline,dormant,unknown")
    role: str | None = Field(None, description="Comma list: switch,access_point,camera,router,server,jump_host,traffic_generator")
    testbed: str | None = Field(None, description="Only devices in this testbed")
    limit: int = Field(30, ge=1, le=200)


@registry.tool(
    "search_devices",
    """Find devices (switches, APs, cameras, servers...) by name, serial, model, IP, status, role or
    testbed. Use for "which switches are offline", "where is Q2XX-...", "show C9300 devices".""",
    SearchDevicesArgs,
)
async def search_devices(ctx: ToolContext, args: SearchDevicesArgs) -> dict[str, Any]:
    def work(s):
        tb_id = sv.find_testbed(s, args.testbed).id if args.testbed else None
        return sv.list_devices(s, q=args.query, status=args.status, role=args.role, testbed_id=tb_id, limit=500)

    devices = await run_in_session(work)
    return {"total": len(devices), "devices": [_dev(d) for d in devices[: args.limit]]}


class DeviceArgs(Args):
    device: str = Field(description="Device name, serial number or id")


@registry.tool(
    "get_device",
    """Full detail for one device: live status, model/serial/firmware, rack position, console and
    management connections (no passwords), testbeds it belongs to and who is using it.""",
    DeviceArgs,
)
async def get_device(ctx: ToolContext, args: DeviceArgs) -> dict[str, Any]:
    def work(s):
        return sv.device_detail(s, sv.find_device(s, args.device))

    d = await run_in_session(work)
    base = _dev(d)
    base.update(
        {
            "vendor": d.vendor,
            "os": d.os,
            "location": d.location,
            "status_changed_at": _iso(d.status_changed_at),
            "last_seen_at": _iso(d.last_seen_at),
            "live": {k: v for k, v in (d.live.get("inventory") or d.live.get(d.provider or "") or {}).items() if v},
            "connections": {
                name: {k: v for k, v in c.items() if k in {"protocol", "ip", "port", "proxy", "is_console"}}
                for name, c in d.connections.items()
            },
            "testbeds": [{"name": t.name, "status": t.status} for t in d.testbeds],
            "stack_members": [m.name for m in d.members] or None,
            "port_tags": [f"{p.interface}={p.tag}" for p in d.port_tags[:60]] or None,
            "notes": d.notes,
        }
    )
    return {k: v for k, v in base.items() if v not in (None, "", [], {})}


class ListTestbedsArgs(Args):
    status: str | None = Field(None, description="Comma list: free,in_use,blocked,maintenance")
    query: str | None = Field(None, description="Substring of the testbed name or file path")
    model: str | None = Field(None, description="Only testbeds containing this device model, e.g. C9300X")
    limit: int = Field(60, ge=1, le=300)


@registry.tool(
    "list_testbeds",
    """List testbeds with their booking state. "blocked" means a testbed sharing hardware with it is
    in use, so it cannot be booked now. Use for "what testbeds are free?", "who has the ca testbeds?".""",
    ListTestbedsArgs,
)
async def list_testbeds(ctx: ToolContext, args: ListTestbedsArgs) -> dict[str, Any]:
    tbs = await run_in_session(lambda s: sv.list_testbeds(s, q=args.query, status=args.status, model=args.model))
    return {"total": len(tbs), "testbeds": [_tb(t) for t in tbs[: args.limit]]}


class TestbedArgs(Args):
    testbed: str = Field(description="Testbed name (e.g. lab12) or id")


@registry.tool(
    "get_testbed",
    """One testbed in detail: devices and their health, current/next bookings, recent history, and
    which other testbeds share its hardware (booking one blocks the others).""",
    TestbedArgs,
)
async def get_testbed(ctx: ToolContext, args: TestbedArgs) -> dict[str, Any]:
    t = await run_in_session(lambda s: sv.testbed_detail(s, sv.find_testbed(s, args.testbed)))
    out = _tb(t)
    out.update(
        {
            "source_file": t.source_file,
            "devices": [_dev(d) | ({"exclusive": False} if not d.exclusive else {}) for d in t.devices],
            "bookings": [_booking(b) for b in t.bookings if b.state in {"active", "upcoming"}],
            "recent_bookings": [_booking(b) for b in t.bookings if b.state not in {"active", "upcoming"}][:5],
            "shares_hardware_with": [r.name for r in t.shares_with][:40],
            "type": t.kind,
        }
    )
    return out


class AvailabilityArgs(Args):
    start: datetime | None = Field(None, description="ISO 8601 start; default now")
    end: datetime | None = Field(None, description="ISO 8601 end; default start + duration")
    duration_minutes: int | None = Field(None, ge=5, le=60 * 24 * 14)
    model: str | None = Field(None, description="Required device model substring, e.g. MS390")
    query: str | None = Field(None, description="Testbed name substring")


@registry.tool(
    "find_available_testbeds",
    """Testbeds that can be booked for a time window without conflicts (including hardware shared
    with other testbeds). Call before booking when the user asks for "a free testbed with X".""",
    AvailabilityArgs,
)
async def find_available_testbeds(ctx: ToolContext, args: AvailabilityArgs) -> dict[str, Any]:
    start = ctx.to_utc(args.start) or utcnow()
    end = ctx.to_utc(args.end) or start + timedelta(
        minutes=args.duration_minutes or ctx.runtime.settings.booking.default_duration_minutes
    )
    tbs = await run_in_session(lambda s: sv.available_testbeds(s, starts=start, ends=end, q=args.query, model=args.model))
    return {"window": {"from": _iso(start), "until": _iso(end)}, "available": [_tb(t) for t in tbs[:60]], "total": len(tbs)}


class BookArgs(Args):
    testbed: str = Field(description="Testbed name or id")
    duration_minutes: int | None = Field(None, ge=5, le=60 * 24 * 14, description="Default 120")
    start: datetime | None = Field(None, description="ISO 8601; omit to start now")
    end: datetime | None = Field(None, description="ISO 8601; alternative to duration_minutes")
    purpose: str | None = Field(None, max_length=300, description="Short reason, e.g. 'MS 17.2 smoke run'")
    devices: list[str] | None = Field(
        None, max_length=100,
        description="Reserve only these devices or stacks of the testbed (names or serials); omit for the whole testbed",
    )


@registry.tool(
    "book_testbed",
    """Book (reserve) a testbed for the current user, starting now unless a start time is given.
    Pass `devices` to reserve only some switches or stacks of it, so others can use the rest at the same time.
    Fails with the conflicting booking if the testbed, or hardware it shares, is taken.
    Only call when the user asked to book/reserve/claim/grab a testbed or device.""",
    BookArgs,
    mutating=True,
)
async def book_testbed(ctx: ToolContext, args: BookArgs) -> dict[str, Any]:
    settings = ctx.runtime.settings

    def work(s):
        tb = sv.find_testbed(s, args.testbed)
        device_ids = [sv.find_device(s, name).id for name in args.devices] if args.devices else None
        b = bk.create_booking(
            s,
            tb,
            device_ids=device_ids,
            owner=ctx.actor,
            starts_at=ctx.to_utc(args.start),
            ends_at=ctx.to_utc(args.end),
            duration_minutes=args.duration_minutes,
            purpose=args.purpose,
            via=ctx.via,
            settings=settings.booking,
            actor=f"{ctx.actor} (via {ctx.via})",
        )
        return sv.booking_out(b)

    b = await run_in_session(work)
    return _booking(b) | {"_summary": f"Booked {b.testbed_name} until {b.effective_end.isoformat()}"}


class ReleaseArgs(Args):
    testbed: str | None = Field(None, description="Testbed whose active/upcoming booking to release")
    booking_id: int | None = None
    force: bool = Field(False, description="Only needed on servers with release_policy=owner")


def _pick_booking(s, ctx: ToolContext, testbed: str | None, booking_id: int | None) -> Booking:
    now = utcnow()
    if booking_id:
        b = s.get(Booking, booking_id)
        if b is None:
            raise ToolError(f"no booking {booking_id}")
        return b
    if not testbed:
        raise ToolError("give a testbed or booking_id")
    tb = sv.find_testbed(s, testbed)
    candidates = [b for b in tb.bookings if b.state(now) in {"active", "upcoming"}]
    candidates.sort(key=lambda b: (b.owner != ctx.actor, b.state(now) != "active", b.starts_at))
    if not candidates:
        raise ToolError(f"{tb.name} has no active or upcoming booking")
    return candidates[0]


@registry.tool(
    "release_testbed",
    """End the current booking of a testbed (or cancel an upcoming one) so others can use it.
    Defaults to the caller's own booking. Anyone may release anyone's booking as a courtesy (it is logged
    under the caller's name), but if it belongs to someone else, say whose it is and get the user's
    confirmation in the conversation first.""",
    ReleaseArgs,
    mutating=True,
)
async def release_testbed(ctx: ToolContext, args: ReleaseArgs) -> dict[str, Any]:
    settings = ctx.runtime.settings

    def work(s):
        b = _pick_booking(s, ctx, args.testbed, args.booking_id)
        bk.release_booking(s, b, actor=ctx.actor, force=args.force, settings=settings.booking, via=ctx.via)
        return sv.booking_out(b)

    b = await run_in_session(work)
    whose = "" if b.owner == ctx.actor else f" ({b.owner}'s booking)"
    return _booking(b) | {"_summary": f"Released {b.testbed_name}{whose}"}


class ExtendArgs(Args):
    testbed: str | None = None
    booking_id: int | None = None
    minutes: int = Field(ge=5, le=60 * 24 * 7)


@registry.tool(
    "extend_booking",
    "Extend the caller's active or upcoming booking of a testbed by N minutes (conflicts are checked).",
    ExtendArgs,
    mutating=True,
)
async def extend_booking(ctx: ToolContext, args: ExtendArgs) -> dict[str, Any]:
    settings = ctx.runtime.settings

    def work(s):
        b = _pick_booking(s, ctx, args.testbed, args.booking_id)
        bk.extend_booking(s, b, minutes=args.minutes, actor=ctx.actor, force=False, settings=settings.booking, via=ctx.via)
        return sv.booking_out(b)

    b = await run_in_session(work)
    return _booking(b) | {"_summary": f"Extended {b.testbed_name} until {b.effective_end.isoformat()}"}


class BookingsArgs(Args):
    owner: str | None = Field(None, description="Owner name, or 'me' for the current user")
    testbed: str | None = None
    hours_ahead: int = Field(72, ge=1, le=24 * 31)
    include_past_hours: int = Field(0, ge=0, le=24 * 31)


@registry.tool(
    "list_bookings",
    "Bookings in a time window (default: now to +72h), optionally for one owner or testbed.",
    BookingsArgs,
)
async def list_bookings(ctx: ToolContext, args: BookingsArgs) -> dict[str, Any]:
    now = utcnow()
    owner = ctx.actor if (args.owner or "").lower() in {"me", "my", "mine"} else args.owner

    def work(s):
        tb_id = sv.find_testbed(s, args.testbed).id if args.testbed else None
        return sv.list_bookings(
            s,
            start=now - timedelta(hours=args.include_past_hours),
            end=now + timedelta(hours=args.hours_ahead),
            testbed_id=tb_id,
            owner=owner,
        )

    rows = await run_in_session(work)
    return {"bookings": [_booking(b) for b in rows[:100]], "total": len(rows)}


class ConsoleArgs(Args):
    device: str = Field(description="Device name, serial or id")
    connection: str | None = Field(None, description="Connection name from the testbed file; default: all consoles")


@registry.tool(
    "test_console",
    """Open each console line of a device briefly (through its jump host if configured), send a
    carriage return and report what answered: CLI prompt, login prompt, line busy, refused, timeout.
    Use when the user asks whether a console works or a device is unreachable.""",
    ConsoleArgs,
)
async def test_console(ctx: ToolContext, args: ConsoleArgs) -> dict[str, Any]:
    results = await run_console_test(ctx.runtime, args.device, args.connection, actor=f"{ctx.actor} (via {ctx.via})")
    ok = sum(1 for r in results["results"] if r["ok"])
    results["_summary"] = f"Console test on {results['device']}: {ok}/{len(results['results'])} reachable"
    return results


async def run_console_test(runtime: Runtime, device_ident: str, connection: str | None, actor: str) -> dict[str, Any]:
    cfg = runtime.settings.console

    def lookup(s):
        d = sv.find_device(s, device_ident)
        return d.id, d.name, d.key

    dev_id, dev_name, dev_key = await run_in_session(lookup)
    plans = secret_store.plans_for(dev_key)
    if connection:
        plans = {k: v for k, v in plans.items() if k == connection}
        if not plans:
            raise ToolError(f"{dev_name} has no connection named '{connection}'")
    else:
        plans = {k: v for k, v in plans.items() if v.is_console}
    if not plans:
        raise ToolError(f"{dev_name} has no console connections in its testbed file")
    import asyncio

    outcomes = await asyncio.gather(
        *(
            probe(p, timeout=cfg.timeout, send_newline=cfg.send_newline, allow_proxy=cfg.allow_proxy,
                  verify_host_keys=cfg.verify_host_keys)
            for p in plans.values()
        )
    )
    results = [o.as_dict() for o in outcomes]

    def log_it(s):
        summary = ", ".join(f"{r['connection']}: {r['state']}" for r in results)
        record(s, actor=actor, action="console_test", object_type="device", object_id=dev_id, object_name=dev_name,
               message=summary, data={"results": [{k: r[k] for k in ("connection", "state", "endpoint", "via")} for r in results]})

    await run_in_session(log_it)
    return {"device": dev_name, "results": results}


@registry.tool(
    "get_switch_ports",
    """Live per-port state of a switch from its configured status provider: link status,
    speed, PoE, VLAN, neighbour, errors, plus which ports the testbed topology uses (tags).""",
    DeviceArgs,
)
async def get_switch_ports(ctx: ToolContext, args: DeviceArgs) -> dict[str, Any]:
    live = await device_live(ctx.runtime, args.device)
    ports = live.get("ports") or []
    compact = [
        {k: v for k, v in p.items() if k in {"id", "status", "speed", "poe", "vlan", "neighbor", "errors", "tags", "is_uplink"} and v not in (None, [], False, "")}
        for p in ports
    ]
    return {"device": live.get("device"), "source": live.get("source"), "error": live.get("error"), "ports": compact}


async def device_live(runtime: Runtime, device_ident: str) -> dict[str, Any]:
    def lookup(s):
        d = sv.find_device(s, device_ident)
        tags: dict[str, list[str]] = {}
        for m in d.memberships:
            for iface, spec in ((m.testbed.topology or {}).get(d.name) or {}).items():
                if spec.get("tag"):
                    tags.setdefault(str(iface), []).append(f"{m.testbed.name}:{spec['tag']}")
        return device_ref(d), d.provider, d.name, tags

    ref, provider_name, name, tags = await run_in_session(lookup)
    providers = runtime.poller.providers if runtime.poller else {}
    provider = providers.get(provider_name or "")
    if provider is None or not provider.enabled:
        return {"device": name, "source": provider_name, "ports": [], "error": "no live data source for this device"}
    data = await provider.device_live(ref) or {"ports": []}
    ports = [dict(p) | ({"tags": sorted(set(tags.get(str(p.get("id")), [])))} if tags.get(str(p.get("id"))) else {})
             for p in data.get("ports", [])]
    return {**data, "device": name, "ports": ports}


class RefreshArgs(Args):
    device: str | None = Field(None, description="Device to re-poll now; omit for all devices")


@registry.tool(
    "refresh_status",
    "Ask the status providers to poll now (one device, or everything) instead of waiting for the next cycle.",
    RefreshArgs,
)
async def refresh_status(ctx: ToolContext, args: RefreshArgs) -> dict[str, Any]:
    poller = ctx.runtime.poller
    if poller is None:
        raise ToolError("status polling is not running")
    if args.device:
        def lookup(s):
            d = sv.find_device(s, args.device)
            return d.id, d.provider, d.name

        dev_id, provider, name = await run_in_session(lookup)
        if not provider or provider not in poller.providers:
            raise ToolError(f"{name} has no status provider")
        changed = await poller.poll_once(provider, [dev_id])
        d = await run_in_session(lambda s: sv.find_device(s, name))
        return {"device": name, "status": d.status, "detail": d.status_detail, "changed": bool(changed)}
    poller.trigger()
    return {"triggered": [n for n, p in poller.providers.items() if p.enabled]}


class ActivityArgs(Args):
    object_name: str | None = Field(None, description="Device or testbed name to filter on")
    actor: str | None = None
    limit: int = Field(20, ge=1, le=100)


@registry.tool(
    "recent_activity",
    "Audit log: bookings, releases, status changes, console tests, imports, rack moves (newest first).",
    ActivityArgs,
)
async def recent_activity(ctx: ToolContext, args: ActivityArgs) -> dict[str, Any]:
    def work(s):
        q = select(Event).order_by(Event.ts.desc()).limit(args.limit)
        if args.object_name:
            q = q.where(Event.object_name == args.object_name)
        if args.actor:
            q = q.where(Event.actor.like(f"{args.actor}%"))
        return [
            {"ts": _iso(e.ts), "actor": e.actor, "action": e.action, "object": e.object_name, "message": e.message}
            for e in s.scalars(q)
        ]

    return {"events": await run_in_session(work)}


@registry.tool(
    "rescan_testbed_files",
    "Re-read the pyATS testbed YAML folders now (normally automatic on file change) and report what changed.",
    NoArgs,
    mutating=True,
)
async def rescan_testbed_files(ctx: ToolContext, args: NoArgs) -> dict[str, Any]:
    if ctx.runtime.import_now is None:
        raise ToolError("importer not available")
    summary = await ctx.runtime.import_now(f"{ctx.actor} (via {ctx.via})")
    return {k: v for k, v in summary.items() if k != "errors"} | {"errors": summary.get("errors", [])[:10]}


@registry.tool("list_racks", "Racks with device counts, used rack units and health.", NoArgs)
async def list_racks(ctx: ToolContext, args: NoArgs) -> dict[str, Any]:
    racks = await run_in_session(sv.list_racks)
    return {
        "racks": [
            {"name": r.name, "height": r.height, "devices": r.device_count, "used_u": r.used_units,
             "floor": f"{r.floor_row}{r.floor_col}" if r.floor_row else None, "offline": r.health.offline or None}
            for r in racks
        ]
    }


class RackArgs(Args):
    rack: str = Field(description="Rack name")


@registry.tool("get_rack", "Devices in a rack, top to bottom, with U positions and status.", RackArgs)
async def get_rack(ctx: ToolContext, args: RackArgs) -> dict[str, Any]:
    def work(s):
        r = s.scalar(select(Rack).where(Rack.name.ilike(args.rack)))
        if r is None:
            raise sv.NotFound(f"no rack named {args.rack}")
        return sv.rack_detail(s, r)

    r = await run_in_session(work)
    return {
        "name": r.name,
        "height": r.height,
        "devices": [_dev(d) | {"u": d.rack_position, "u_height": d.u_height} for d in r.devices],
    }


class PlaceArgs(Args):
    device: str
    rack: str | None = Field(None, description="Rack name; null removes the device from its rack")
    position: int | None = Field(None, ge=1, le=100, description="Lowest U the device occupies")
    face: str = Field("front", pattern="^(front|rear)$")


@registry.tool(
    "place_device",
    "Put a device in a rack at a U position (or take it out). Checks the rack height and overlaps.",
    PlaceArgs,
    mutating=True,
)
async def place_device(ctx: ToolContext, args: PlaceArgs) -> dict[str, Any]:
    def work(s):
        d = sv.find_device(s, args.device)
        rack_id = None
        if args.rack:
            r = s.scalar(select(Rack).where(Rack.name.ilike(args.rack)))
            if r is None:
                raise sv.NotFound(f"no rack named {args.rack}")
            rack_id = r.id
        sv.place_device(s, d, rack_id=rack_id, position=args.position, face=args.face, actor=f"{ctx.actor} (via {ctx.via})")
        return d.name, d.rack.name if d.rack else None, d.rack_position

    name, rack, pos = await run_in_session(work)
    return {"device": name, "rack": rack, "position": pos, "_summary": f"Placed {name} in {rack or 'no rack'}"}


@registry.tool("list_floor_plans", "Floor plans (rooms drawn as grids of racks, benches, pillars).", NoArgs)
async def list_floor_plans(ctx: ToolContext, args: NoArgs) -> dict[str, Any]:
    plans = await run_in_session(sv.list_floor_plans)
    return {"floor_plans": [p.model_dump(mode="json") for p in plans]}


class SaveFloorPlanArgs(Args):
    plan: FloorPlanIn
    replace: bool = Field(False, description="Overwrite an existing plan with the same name")


@registry.tool(
    "save_floor_plan",
    """Save a floor plan the user gave you (usually an image of a floor map you have read).
    rows = row labels top->bottom, columns = labels left->right, cells = every occupied position
    (rack, empty_rack, bench, open_space, pillar, storage, unassigned) at its top-left cell with
    span_rows/span_cols. Set rack_prefix so cell row+col matches existing rack names, e.g. "B1_"
    makes cell C/03 the rack B1_C03 (call list_racks to see the naming). Confirm the name first.""",
    SaveFloorPlanArgs,
    mutating=True,
)
async def save_floor_plan(ctx: ToolContext, args: SaveFloorPlanArgs) -> dict[str, Any]:
    plan_in = normalise(args.plan)

    def work(s):
        plan = s.scalar(select(FloorPlan).where(FloorPlan.name == plan_in.name))
        if plan is not None and not args.replace:
            raise ToolError(f"a floor plan named '{plan_in.name}' exists; set replace=true to overwrite")
        if plan is None:
            plan = FloorPlan(name=plan_in.name, source="agent")
            s.add(plan)
        apply_floor_plan(plan, plan_in)
        s.flush()
        sv.link_floor_racks(s, plan)
        record(s, actor=f"{ctx.actor} (via {ctx.via})", action="saved", object_type="floor_plan",
               object_id=plan.id, object_name=plan.name, message=f"{len(plan_in.rows)}x{len(plan_in.columns)} grid, {len(plan_in.cells)} cells")
        s.flush()
        return plan.id, plan.name, len(plan.racks)

    pid, name, linked = await run_in_session(work)
    return {"id": pid, "name": name, "linked_racks": linked, "url": f"/floor/{pid}",
            "_summary": f"Saved floor plan {name} ({linked} racks linked)"}


class FloorImportArgs(Args):
    source: str = Field(description="Floor-plan source plugin name, from list_floor_sources")
    site_id: str = Field(description="Site / lab id in that source")


@registry.tool(
    "list_floor_sources",
    "Floor-plan source plugins (DCIM / lab-tracking systems) and whether the server can sync from them.",
    NoArgs,
)
async def list_floor_sources(ctx: ToolContext, args: NoArgs) -> dict[str, Any]:
    fs = ctx.runtime.floor_sync
    if fs is None:
        return {"sources": []}
    return {
        "sources": [
            {k: v for k, v in s.describe().items() if k != "browser_export"} | {"browser_export": s.browser_export() is not None}
            for s in fs.sources.values()
        ]
    }


@registry.tool(
    "import_floor_plan_from_source",
    """Create or refresh a floor plan from a source plugin (e.g. a DCIM system) by site/lab id.
    Only works when list_floor_sources says can_sync; otherwise tell the user to use the source's
    browser export button on the Floor plans page.""",
    FloorImportArgs,
    mutating=True,
)
async def import_floor_plan_from_source(ctx: ToolContext, args: FloorImportArgs) -> dict[str, Any]:
    fs = ctx.runtime.floor_sync
    if fs is None:
        raise ToolError("floor-plan sources are not available")
    from ..floorsources import FloorSourceError

    try:
        out = await fs.import_site(args.source, args.site_id, f"{ctx.actor} (via {ctx.via})")
    except FloorSourceError as exc:
        raise ToolError(str(exc)) from exc
    out["url"] = f"/floor/{out['id']}"
    out["_summary"] = f"{'Imported' if out.get('created') else 'Refreshed'} floor plan {out['name']}"
    return out


def apply_floor_plan(plan: FloorPlan, data: FloorPlanIn) -> None:
    plan.name = data.name
    plan.site = data.site
    plan.rack_prefix = data.rack_prefix or ""
    plan.rows = list(data.rows)
    plan.columns = list(data.columns)
    plan.cells = [c.model_dump() for c in data.cells]
    plan.metric_label = data.metric_label
    plan.notes = data.notes


__all__ = [
    "Runtime",
    "ToolContext",
    "ToolOutcome",
    "ToolRegistry",
    "apply_floor_plan",
    "device_live",
    "registry",
    "run_console_test",
]
