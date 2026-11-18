"""Query/command layer shared by the REST API, the in-app agent and the MCP server."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, object_session, selectinload

from . import booking as bk
from .events import record
from .models import Booking, Device, Event, FloorPlan, Network, Rack, Testbed, TestbedDevice
from .schemas import (
    BookingOut,
    ConsoleLine,
    DeviceDetail,
    DeviceSummary,
    EventOut,
    FloorPlanOut,
    FloorPlanSummary,
    FloorRackDevice,
    FloorRackLink,
    LinkEnd,
    NetworkOut,
    PortTag,
    RackDetail,
    RackDevice,
    RackOut,
    Ref,
    StatusCounts,
    TestbedDetail,
    TestbedMembership,
    TestbedSummary,
    TopologyLink,
    TrafficGenerator,
    TrafficPort,
)
from .schemas import (
    TestbedDevice as TestbedDeviceOut,
)
from .timeutil import utcnow

STATUSES = ("online", "alerting", "offline", "dormant", "unknown")


class NotFound(Exception):
    pass


class Invalid(Exception):
    pass


# ---------------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------------


def booking_out(b: Booking, now: datetime | None = None) -> BookingOut:
    now = now or utcnow()
    return BookingOut(
        id=b.id,
        testbed_id=b.testbed_id,
        testbed_name=b.testbed.name,
        owner=b.owner,
        purpose=b.purpose,
        starts_at=b.starts_at,
        ends_at=b.ends_at,
        effective_end=b.effective_end,
        state=b.state(now),
        created_via=b.created_via,
        created_at=b.created_at,
        released_at=b.released_at,
        device_ids=list(b.device_ids or []),
        devices_label=bk.device_label(object_session(b), b.device_ids) if b.device_ids and object_session(b) else None,
    )


def _counts(statuses: list[str]) -> StatusCounts:
    c = Counter(s if s in STATUSES else "unknown" for s in statuses)
    return StatusCounts(**{k: c.get(k, 0) for k in STATUSES})


def active_device_owners(session: Session, now: datetime | None = None) -> dict[int, tuple[str, str]]:
    now = now or utcnow()
    active = [
        b
        for b in session.scalars(
            select(Booking)
            .options(selectinload(Booking.testbed))
            .where(Booking.cancelled_at.is_(None), Booking.starts_at <= now, Booking.ends_at > now)
        )
        if b.is_active(now)
    ]
    if not active:
        return {}
    excl = bk.exclusive_devices(session, [b.testbed_id for b in active])
    out: dict[int, tuple[str, str]] = {}
    for b in active:
        for d in bk.booked_devices(b, excl):
            out[d] = (b.owner, b.testbed.name)
    return out


def _testbed_counts(session: Session, device_ids: list[int]) -> dict[int, int]:
    if not device_ids:
        return {}
    rows = session.execute(
        select(TestbedDevice.device_id, func.count())
        .join(Testbed, Testbed.id == TestbedDevice.testbed_id)
        .where(TestbedDevice.device_id.in_(device_ids), Testbed.removed_at.is_(None))
        .group_by(TestbedDevice.device_id)
    )
    return dict(rows.all())


# "alerting" is up: the device is online and checking in, it just has an alert. Down is offline/dormant.
UP_STATUSES = {"online", "alerting"}
DOWN_STATUSES = {"offline", "dormant"}
STATUS_SEVERITY = {"offline": 4, "dormant": 3, "unknown": 2, "alerting": 1, "online": 0}


def effective_status(d: Device) -> tuple[str, str | None]:
    """A logical stack has no status of its own: it is as healthy as its worst member."""
    if d.is_virtual and d.members:
        live = [m for m in d.members if m.removed_at is None]
        if live:
            worst = max(live, key=lambda m: STATUS_SEVERITY.get(m.status, 1))
            down = [m.name for m in live if m.status in DOWN_STATUSES]
            alerting = sum(m.status == "alerting" for m in live)
            up = sum(m.status in UP_STATUSES for m in live)
            if down:
                detail = f"{', '.join(down)} {'is' if len(down) == 1 else 'are'} down · {up}/{len(live)} up"
            elif up == len(live):
                detail = f"all {len(live)} members up" + (f" · {alerting} alerting" if alerting else "")
            else:
                detail = f"{up}/{len(live)} members up"
            return worst.status, detail
    return d.status, d.status_detail


def _summary(d: Device, tb_count: int, owners: dict[int, tuple[str, str]]) -> dict[str, Any]:
    status, status_detail = effective_status(d)
    stack = (d.live or {}).get("stack") if not d.is_virtual else None
    stack = stack if isinstance(stack, dict) else {}
    return dict(
        id=d.id,
        name=d.name,
        alias=d.alias,
        role=d.role,
        vendor=d.vendor,
        model=d.model,
        serial=d.serial,
        type=d.type,
        os=d.os,
        platform=d.platform,
        status=status,
        status_detail=status_detail,
        status_changed_at=d.status_changed_at,
        last_seen_at=d.last_seen_at,
        mgmt_ip=d.mgmt_ip,
        network=Ref(id=d.network.id, name=d.network.name) if d.network else None,
        rack=Ref(id=d.rack.id, name=d.rack.name) if d.rack else None,
        rack_position=d.rack_position,
        rack_face=d.rack_face or "front",
        u_height=d.u_height,
        is_shared=d.is_shared,
        is_virtual=d.is_virtual,
        parent_id=d.parent_id,
        stack_name=d.stack_name,
        provider=d.provider,
        has_console=any((c or {}).get("is_console") for c in (d.connections or {}).values()),
        testbed_count=tb_count,
        in_use_by=(owners.get(d.id) or next((owners[m.id] for m in d.members if m.id in owners), None) or (None,))[0]
        if d.is_virtual
        else (owners.get(d.id) or (None,))[0],
        removed=d.removed_at is not None,
        tags=[str(t) for t in (d.tags or [])],
        stack_role=stack.get("role"),
        stack_number=stack.get("number"),
        member_count=sum(1 for m in d.members if m.removed_at is None) if d.is_virtual else 0,
    )


def _stack_testbed_counts(session: Session, stack_ids: list[int]) -> dict[int, int]:
    """A stack is in every testbed one of its members is in."""
    if not stack_ids:
        return {}
    rows = session.execute(
        select(Device.parent_id, func.count(func.distinct(TestbedDevice.testbed_id)))
        .join(TestbedDevice, TestbedDevice.device_id == Device.id)
        .join(Testbed, Testbed.id == TestbedDevice.testbed_id)
        .where(Device.parent_id.in_(stack_ids), Device.removed_at.is_(None), Testbed.removed_at.is_(None))
        .group_by(Device.parent_id)
    )
    return dict(rows.all())


def testbed_counts(session: Session, devices: list[Device]) -> dict[int, int]:
    counts = _testbed_counts(session, [d.id for d in devices])
    for sid, n in _stack_testbed_counts(session, [d.id for d in devices if d.is_virtual]).items():
        counts[sid] = max(counts.get(sid, 0), n)
    return counts


def summarize_devices(session: Session, devices: list[Device]) -> list[DeviceSummary]:
    counts = testbed_counts(session, devices)
    owners = active_device_owners(session)
    return [DeviceSummary(**_summary(d, counts.get(d.id, 0), owners)) for d in devices]


def _device_query():
    return select(Device).options(selectinload(Device.network), selectinload(Device.rack))


def find_device(session: Session, ident: str | int) -> Device:
    if isinstance(ident, int) or (isinstance(ident, str) and ident.isdigit()):
        d = session.get(Device, int(ident))
        if d:
            return d
    text = str(ident).strip()
    d = session.scalar(
        _device_query()
        .where(or_(func.lower(Device.name) == text.lower(), func.upper(Device.serial) == text.upper()))
        .order_by(Device.removed_at.is_not(None), Device.id)
    )
    if d is None:
        raise NotFound(f"no device named or with serial '{text}'")
    return d


def find_testbed(session: Session, ident: str | int) -> Testbed:
    if isinstance(ident, int) or (isinstance(ident, str) and ident.isdigit()):
        t = session.get(Testbed, int(ident))
        if t:
            return t
    text = str(ident).strip()
    t = session.scalar(
        select(Testbed)
        .where(or_(func.lower(Testbed.name) == text.lower(), Testbed.key == text))
        .order_by(Testbed.removed_at.is_not(None), Testbed.id)
    )
    if t is None:
        raise NotFound(f"no testbed named '{text}'")
    return t


# ---------------------------------------------------------------------------------------------------
# devices
# ---------------------------------------------------------------------------------------------------


def list_devices(
    session: Session,
    *,
    q: str | None = None,
    status: str | None = None,
    role: str | None = None,
    network_id: int | None = None,
    testbed_id: int | None = None,
    rack_id: int | None = None,
    unracked: bool = False,
    include_removed: bool = False,
    include_virtual: bool = True,
    limit: int = 2000,
) -> list[DeviceSummary]:
    query = _device_query()
    if not include_removed:
        query = query.where(Device.removed_at.is_(None))
    if not include_virtual:
        query = query.where(Device.is_virtual.is_(False))
    if q:
        like = f"%{q.lower()}%"
        query = query.where(
            or_(
                func.lower(Device.name).like(like),
                func.lower(Device.serial).like(like),
                func.lower(Device.model).like(like),
                func.lower(Device.mgmt_ip).like(like),
                func.lower(Device.alias).like(like),
            )
        )
    if status:
        query = query.where(Device.status.in_(status.split(",")))
    if role:
        query = query.where(Device.role.in_(role.split(",")))
    if network_id:
        # devices a provider reports in it, plus the devices of testbeds that name it as their network
        net = session.get(Network, network_id)
        declared = [d.id for d in _testbed_devices(network_testbeds(session).get(net.key, []))] if net else []
        query = query.where(or_(Device.network_id == network_id, Device.id.in_(declared)) if declared
                            else Device.network_id == network_id)
    if rack_id:
        query = query.where(Device.rack_id == rack_id)
    if unracked:
        query = query.where(Device.rack_position.is_(None), Device.is_virtual.is_(False))
    if testbed_id:
        query = query.join(TestbedDevice, TestbedDevice.device_id == Device.id).where(
            TestbedDevice.testbed_id == testbed_id
        )
    devices = list(session.scalars(query.order_by(Device.name).limit(limit)))
    if include_virtual:
        # bring along the stack of every member listed, so the list can show members under their stack
        have = {d.id for d in devices}
        parent_ids = {d.parent_id for d in devices if d.parent_id and d.parent_id not in have}
        if parent_ids:
            devices += list(session.scalars(
                _device_query().where(Device.id.in_(parent_ids), Device.removed_at.is_(None)).order_by(Device.name)
            ))
    return summarize_devices(session, devices)


def device_detail(session: Session, device: Device) -> DeviceDetail:
    counts = testbed_counts(session, [device])
    owners = active_device_owners(session)
    members = sorted((m for m in device.members if m.removed_at is None),
                     key=lambda d: (((d.live or {}).get("stack") or {}).get("number") or 99, d.name))
    # A stack's page shows the testbeds, topology ports and history of all its members.
    covered = [device, *members] if device.is_virtual else [device]
    memberships: dict[int, TestbedDevice] = {}
    port_tags: list[PortTag] = []
    for dev in covered:
        for m in dev.memberships:
            if m.testbed.removed_at is not None:
                continue
            memberships.setdefault(m.testbed_id, m)
            topo = (m.testbed.topology or {}).get(dev.name) or {}
            for iface, spec in topo.items():
                port_tags.append(PortTag(
                    testbed=m.testbed.name, interface=str(iface), tag=spec.get("tag"), type=spec.get("type"),
                    device=dev.name if dev is not device else None,
                ))
    tbs = list(memberships.values())
    states = bk.compute_states(session, [m.testbed for m in tbs])
    events = session.scalars(
        select(Event)
        .where(Event.object_type == "device", Event.object_id.in_([d.id for d in covered]))
        .order_by(Event.ts.desc())
        .limit(40 if device.is_virtual else 25)
    )
    return DeviceDetail(
        **_summary(device, counts.get(device.id, 0), owners),
        connections=device.connections or {},
        custom=device.custom or {},
        live=device.live or {},
        location=device.location,
        source_file=device.source_file,
        notes=device.notes,
        org_id=device.org_id,
        placement_source=device.placement_source,
        status_checked_at=device.status_checked_at,
        created_at=device.created_at,
        updated_at=device.updated_at,
        testbeds=[
            TestbedMembership(id=m.testbed.id, name=m.testbed.name, status=states[m.testbed.id].status, exclusive=m.exclusive)
            for m in sorted(tbs, key=lambda m: m.testbed.name)
        ],
        members=summarize_devices(session, members),
        parent=Ref(id=device.parent.id, name=device.parent.name) if device.parent else None,
        port_tags=port_tags,
        events=[EventOut.model_validate(e) for e in events],
    )


def place_device(
    session: Session, device: Device, *, rack_id: int | None, position: int | None, face: str, actor: str
) -> Device:
    if rack_id is None:
        old = device.rack.name if device.rack else None
        device.rack = None
        device.rack_position = None
        device.placement_source = "manual"
        record(session, actor=actor, action="unracked", object_type="device", object_id=device.id,
               object_name=device.name, message=f"removed from {old}" if old else "removed from rack")
        return device
    rack = session.get(Rack, rack_id)
    if rack is None:
        raise NotFound("rack not found")
    if device.is_virtual:
        raise Invalid(f"{device.name} is a logical device (e.g. a stack) and cannot be racked")
    height = max(device.u_height or 1, 1)
    if position is not None:
        if position < 1 or position + height - 1 > rack.height:
            raise Invalid(f"{device.name} ({height}U) does not fit at U{position} in a {rack.height}U rack")
        top = position + height - 1
        for other in rack.devices:
            if other.id == device.id or other.rack_position is None or other.removed_at is not None:
                continue
            o_top = other.rack_position + max(other.u_height or 1, 1) - 1
            if other.rack_position <= top and position <= o_top:
                raise Invalid(f"U{position}-U{top} overlaps {other.name} (U{other.rack_position}-U{o_top})")
    device.rack = rack
    device.rack_position = position
    device.rack_face = face
    device.placement_source = "manual"
    where = f"{rack.name} U{position}" if position else rack.name
    record(session, actor=actor, action="racked", object_type="device", object_id=device.id,
           object_name=device.name, message=f"placed in {where} ({face})")
    return device


# ---------------------------------------------------------------------------------------------------
# testbeds
# ---------------------------------------------------------------------------------------------------


def _testbed_summary(tb: Testbed, st: bk.TestbedState, now: datetime) -> dict[str, Any]:
    exclusive = [m.device for m in tb.members if m.exclusive and m.device.removed_at is None]
    physical = [d for d in exclusive if not d.is_virtual]
    models = sorted({d.model for d in physical if d.model and d.role in {"switch", "router", "security_appliance"}})
    if not models:
        models = sorted({d.model for d in physical if d.model})
    custom = tb.custom or {}
    return dict(
        id=tb.id,
        name=tb.name,
        key=tb.key,
        group=tb.group,
        status=st.status,
        current=booking_out(st.current, now) if st.current else None,
        next=booking_out(st.next, now) if st.next else None,
        blocked_by=[booking_out(b, now) for b in st.blocked_by[:5]],
        partial=[booking_out(b, now) for b in st.partial],
        busy_count=len(st.busy & {d.id for d in physical}),
        device_count=sum(1 for m in tb.members if m.device.removed_at is None),
        exclusive_count=len(physical),  # a logical stack is not a device of its own
        health=_counts([d.status for d in physical]),
        models=models[:8],
        kind=str(custom.get("type")) if custom.get("type") else None,
        maintenance=tb.maintenance,
        maintenance_note=tb.maintenance_note,
        org_id=tb.org_id,
        tags=[str(t) for t in (tb.tags or [])],
        removed=tb.removed_at is not None,
    )


def _testbed_query():
    return select(Testbed).options(
        selectinload(Testbed.members).selectinload(TestbedDevice.device)
    )


def list_testbeds(
    session: Session,
    *,
    q: str | None = None,
    status: str | None = None,
    group: str | None = None,
    model: str | None = None,
    include_removed: bool = False,
) -> list[TestbedSummary]:
    query = _testbed_query()
    if not include_removed:
        query = query.where(Testbed.removed_at.is_(None))
    if q:
        like = f"%{q.lower()}%"
        query = query.where(or_(func.lower(Testbed.name).like(like), func.lower(Testbed.key).like(like)))
    if group:
        query = query.where(Testbed.group == group)
    testbeds = list(session.scalars(query.order_by(Testbed.name)))
    now = utcnow()
    states = bk.compute_states(session, testbeds, now)
    out = [TestbedSummary(**_testbed_summary(t, states[t.id], now)) for t in testbeds]
    if status:
        wanted = set(status.split(","))
        out = [t for t in out if t.status in wanted]
    if model:
        m = model.lower()
        out = [t for t in out if any(m in x.lower() for x in t.models)]
    return out


def sharing_testbeds(session: Session, testbed: Testbed) -> list[Testbed]:
    mine = {m.device_id for m in testbed.members if m.exclusive}
    if not mine:
        return []
    ids = session.scalars(
        select(TestbedDevice.testbed_id)
        .where(TestbedDevice.device_id.in_(mine), TestbedDevice.exclusive.is_(True), TestbedDevice.testbed_id != testbed.id)
        .distinct()
    )
    return list(
        session.scalars(select(Testbed).where(Testbed.id.in_(list(ids)), Testbed.removed_at.is_(None)).order_by(Testbed.name))
    )


def testbed_detail(session: Session, tb: Testbed) -> TestbedDetail:
    now = utcnow()
    st = bk.compute_states(session, [tb], now)[tb.id]
    members = [m for m in tb.members if m.device.removed_at is None]
    devices = [m.device for m in members]
    counts = _testbed_counts(session, [d.id for d in devices])
    owners = active_device_owners(session, now)
    excl = {m.device_id: m.exclusive for m in members}
    devs = [
        TestbedDeviceOut(**_summary(d, counts.get(d.id, 0), owners), exclusive=excl[d.id])
        for d in sorted(devices, key=lambda d: (not excl[d.id], d.is_virtual, d.name))
    ]
    bookings = session.scalars(
        select(Booking)
        .options(selectinload(Booking.testbed))
        .where(Booking.testbed_id == tb.id, Booking.ends_at > now - timedelta(days=14))
        .order_by(Booking.starts_at.desc())
        .limit(50)
    )
    stacks = sorted({d.parent.id: d.parent for d in devices if d.parent is not None and d.parent.removed_at is None
                     and d.parent.id not in excl}.values(), key=lambda s: s.name)
    links = topology_links(tb, devices)
    return TestbedDetail(
        **_testbed_summary(tb, st, now),
        description=tb.description,
        source_file=tb.source_file,
        custom=tb.custom or {},
        topology=tb.topology or {},
        devices=devs,
        stacks=summarize_devices(session, stacks),
        bookings=[booking_out(b, now) for b in bookings],
        shares_with=[Ref(id=t.id, name=t.name) for t in sharing_testbeds(session, tb)],
        consoles=console_lines(devices),
        links=links,
        traffic=traffic_generators(tb, devices, links),
    )


def console_lines(devices: list[Device]) -> list[ConsoleLine]:
    out = []
    for d in sorted(devices, key=lambda d: d.name):
        for name, c in sorted((d.connections or {}).items()):
            if not (c or {}).get("is_console"):
                continue
            out.append(ConsoleLine(
                device_id=d.id, device=d.name, connection=name, protocol=c.get("protocol"),
                host=c.get("ip") or c.get("host"), port=_int(c.get("port")), via=c.get("proxy"),
                console_server=c.get("console_server_ip"), member=_int(c.get("member")),
            ))
    return out


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _interfaces(tb: Testbed, device: str) -> dict[str, dict[str, Any]]:
    """A device's topology interfaces (stored without pyATS's ``interfaces:`` level)."""
    spec = (tb.topology or {}).get(device) or {}
    spec = spec.get("interfaces", spec) if isinstance(spec, dict) else {}
    return {str(k): (v if isinstance(v, dict) else {}) for k, v in spec.items()} if isinstance(spec, dict) else {}


def topology_links(tb: Testbed, devices: list[Device]) -> list[TopologyLink]:
    """pyATS topology links: interfaces that name the same ``link`` are cabled together."""
    ids = {d.name: d.id for d in devices}
    by_link: dict[str, list[LinkEnd]] = defaultdict(list)
    for dev in (tb.topology or {}):
        for iface, attrs in _interfaces(tb, str(dev)).items():
            if attrs.get("link"):
                by_link[str(attrs["link"])].append(
                    LinkEnd(device_id=ids.get(str(dev)), device=str(dev), interface=str(iface), tag=attrs.get("tag"))
                )
    return [TopologyLink(name=k, ends=v) for k, v in sorted(by_link.items())]


def traffic_generators(tb: Testbed, devices: list[Device], links: list[TopologyLink]) -> list[TrafficGenerator]:
    """Traffic generators (Ixia, Spirent, TRex) with their API endpoint, chassis and cabled ports."""
    peer: dict[tuple[str, str], LinkEnd] = {}
    for link in links:
        for a in link.ends:
            for b in link.ends:
                if a is not b:
                    peer[(a.device, a.interface)] = b
    out = []
    for d in sorted(devices, key=lambda d: d.name):
        if d.role != "traffic_generator":
            continue
        info = (d.custom or {}).get("traffic_generator") or {}
        info = info if isinstance(info, dict) else {}
        api = info.get("api")
        conn_name, conn = next(
            ((n, c) for n, c in (d.connections or {}).items() if (c or {}).get("protocol") in {"http", "https", "rest"}),
            (None, None),
        )
        if not api:
            if conn and (conn.get("ip") or conn.get("host")):
                proto = "https" if conn.get("protocol") == "https" else "http"
                api = f"{proto}://{conn.get('ip') or conn.get('host')}" + (f":{conn['port']}" if conn.get("port") else "")
        ifaces = _interfaces(tb, d.name)
        ports = [
            TrafficPort(
                interface=str(i), tag=(a or {}).get("tag"), media=(a or {}).get("media"),
                ipv4=(a or {}).get("ipv4"), peer=peer.get((d.name, str(i))),
            )
            for i, a in ifaces.items()
        ]
        out.append(TrafficGenerator(
            device_id=d.id, name=d.name, type=d.type, os=d.os, api=str(api) if api else None, api_connection=conn_name,
            chassis=info.get("chassis"), session=str(info["session"]) if info.get("session") is not None else None,
            details={k: v for k, v in info.items() if k not in {"api", "chassis", "session"}}, ports=ports,
        ))
    return out


def available_testbeds(
    session: Session, *, starts: datetime, ends: datetime, q: str | None = None, model: str | None = None
) -> list[TestbedSummary]:
    candidates = list_testbeds(session, q=q, model=model)
    out = []
    for summary in candidates:
        if summary.maintenance:
            continue
        tb = session.get(Testbed, summary.id)
        assert tb is not None
        if not bk.find_conflicts(session, tb, starts, ends):
            out.append(summary)
    return out


def list_bookings(
    session: Session,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    testbed_id: int | None = None,
    owner: str | None = None,
    include_finished: bool = True,
    limit: int = 1000,
) -> list[BookingOut]:
    now = utcnow()
    query = select(Booking).options(selectinload(Booking.testbed)).where(Booking.cancelled_at.is_(None))
    if start:
        query = query.where(Booking.ends_at > start)
    if end:
        query = query.where(Booking.starts_at < end)
    if testbed_id:
        query = query.where(Booking.testbed_id == testbed_id)
    if owner:
        query = query.where(Booking.owner == owner)
    if not include_finished:
        query = query.where(Booking.ends_at > now, or_(Booking.released_at.is_(None), Booking.released_at > now))
    rows = session.scalars(query.order_by(Booking.starts_at).limit(limit))
    return [booking_out(b, now) for b in rows if not start or b.effective_end > start]


# ---------------------------------------------------------------------------------------------------
# racks, networks
# ---------------------------------------------------------------------------------------------------


def _rack_out(r: Rack, owners: dict[int, tuple[str, str]]) -> dict[str, Any]:
    devs = [d for d in r.devices if d.removed_at is None]
    placed = [d for d in devs if d.rack_position is not None]
    return dict(
        id=r.id,
        name=r.name,
        site=r.site,
        height=r.height,
        description=r.description,
        device_count=len(devs),
        used_units=sum(max(d.u_height or 1, 1) for d in placed),
        floor_plan_id=r.floor_plan_id,
        floor_row=r.floor_row,
        floor_col=r.floor_col,
        health=_counts([d.status for d in devs]),
        in_use=sum(1 for d in devs if d.id in owners),
    )


def list_racks(session: Session) -> list[RackOut]:
    owners = active_device_owners(session)
    racks = session.scalars(select(Rack).options(selectinload(Rack.devices)).order_by(Rack.name))
    return [RackOut(**_rack_out(r, owners)) for r in racks]


def rack_detail(session: Session, rack: Rack) -> RackDetail:
    owners = active_device_owners(session)
    devs = sorted([d for d in rack.devices if d.removed_at is None], key=lambda d: -(d.rack_position or 0))
    counts = _testbed_counts(session, [d.id for d in devs])
    return RackDetail(
        **_rack_out(rack, owners),
        devices=[RackDevice(**_summary(d, counts.get(d.id, 0), owners)) for d in devs],
    )


def network_testbeds(session: Session) -> dict[str, list[Testbed]]:
    """Network key -> active testbeds, using declarations or actual device membership."""
    from .providers.base import declared_networks

    out: dict[str, list[Testbed]] = defaultdict(list)
    for tb in session.scalars(_testbed_query().where(Testbed.removed_at.is_(None)).order_by(Testbed.name)):
        own = [item.network.key for item in declared_networks(tb.custom or {}) if item.own]
        keys = own or sorted({
            member.device.network.key for member in tb.members
            if member.device.network and member.device.removed_at is None and not member.device.is_virtual
        })
        for key in keys:
            out[key].append(tb)
    return out


def _testbed_devices(testbeds: list[Testbed]) -> list[Device]:
    seen: dict[int, Device] = {}
    for tb in testbeds:
        for m in tb.members:
            d = m.device
            if m.exclusive and d.removed_at is None and not d.is_virtual:
                seen.setdefault(d.id, d)
    return list(seen.values())


def list_networks(session: Session, regions: dict[str, dict[str, Any]] | None = None) -> list[NetworkOut]:
    """``regions``: API host -> {"has_key", "key_env"} from the status provider, for the key status."""
    from .providers.base import network_external_host

    by_testbed = network_testbeds(session)
    nets = session.scalars(select(Network).options(selectinload(Network.devices)).order_by(Network.name))
    out = []
    for n in nets:
        reported = [d for d in n.devices if d.removed_at is None and not d.is_virtual]
        tbs = by_testbed.get(n.key, [])
        devs = {d.id: d for d in [*reported, *_testbed_devices(tbs)]}.values()
        region = (regions or {}).get(n.api_host or "") if n.api_host else None
        out.append(
            NetworkOut(
                id=n.id,
                key=n.key,
                name=n.name,
                provider=n.provider,
                org_id=n.org_id,
                url=n.url,
                product_types=[str(p) for p in (n.product_types or [])],
                tags=[str(t) for t in (n.tags or [])],
                device_count=len(devs),
                health=_counts([d.status for d in devs]),
                region=n.region,
                api_host=n.api_host,
                dashboard_host=network_external_host(n.provider, n.api_host, n.url),
                reported_count=len(reported),
                testbeds=[Ref(id=t.id, name=t.name) for t in tbs],
                has_key=region.get("has_key") if region else None,
                key_env=region.get("key_env") if region else None,
            )
        )
    return out


# ---------------------------------------------------------------------------------------------------
# floor plans
# ---------------------------------------------------------------------------------------------------


def rack_name_for_cell(plan: FloorPlan, row: str, col: str, label: str | None = None) -> str:
    if label and label.strip() and not label.replace(".", "", 1).isdigit():
        return f"{plan.rack_prefix}{label.strip()}"
    return f"{plan.rack_prefix}{row}{col}"


def link_floor_racks(session: Session, plan: FloorPlan) -> None:
    """Attach existing racks to the plan's rack cells by name (``<prefix><row><col>``)."""
    racks = {r.name.upper(): r for r in session.scalars(select(Rack))}
    for cell in plan.cells or []:
        if cell.get("type") not in {"rack", "empty_rack"}:
            continue
        name = rack_name_for_cell(plan, str(cell["row"]), str(cell["col"]), cell.get("label"))
        rack = racks.get(name.upper())
        if rack is None:
            # also accept zero-padded / unpadded column variants: V5 <-> V05
            col = str(cell["col"])
            alt = col.lstrip("0") if col.startswith("0") else col.zfill(2)
            rack = racks.get(f"{plan.rack_prefix}{cell['row']}{alt}".upper())
        if rack is not None and (rack.floor_plan_id in (None, plan.id)):
            rack.floor_plan = plan
            rack.floor_row = str(cell["row"])
            rack.floor_col = str(cell["col"])
            if not rack.site and plan.site:
                rack.site = plan.site


def floor_plan_out(session: Session, plan: FloorPlan) -> FloorPlanOut:
    owners = active_device_owners(session)
    by_cell = {(r.floor_row, r.floor_col): r for r in plan.racks}
    links = []
    for cell in plan.cells or []:
        if cell.get("type") not in {"rack", "empty_rack"}:
            continue
        key = (str(cell["row"]), str(cell["col"]))
        rack = by_cell.get(key)
        devices = []
        if rack is not None:
            devices = [
                FloorRackDevice(
                    id=d.id, name=d.name, status=d.status, position=d.rack_position,
                    u_height=max(d.u_height or 1, 1), in_use_by=(owners.get(d.id) or (None,))[0],
                )
                for d in rack.devices
                if d.removed_at is None and not d.is_virtual
            ]
        links.append(
            FloorRackLink(
                row=key[0],
                col=key[1],
                rack=RackOut(**_rack_out(rack, owners)) if rack else None,
                rack_name=rack.name if rack else rack_name_for_cell(plan, key[0], key[1], cell.get("label")),
                devices=devices,
            )
        )
    return FloorPlanOut(
        id=plan.id,
        name=plan.name,
        site=plan.site,
        rack_prefix=plan.rack_prefix or "",
        rows=[str(r) for r in plan.rows],
        columns=[str(c) for c in plan.columns],
        cells=plan.cells or [],
        metric_label=plan.metric_label,
        notes=plan.notes,
        source=plan.source,
        has_image=bool(plan.source_image),
        created_at=plan.created_at,
        updated_at=plan.updated_at,
        racks=links,
        source_ref=plan.source_ref,
        synced_at=plan.synced_at,
        sync_error=plan.sync_error,
        source_extra=plan.source_extra or {},
    )


def list_floor_plans(session: Session) -> list[FloorPlanSummary]:
    out = []
    for p in session.scalars(select(FloorPlan).options(selectinload(FloorPlan.racks)).order_by(FloorPlan.name)):
        out.append(
            FloorPlanSummary(
                id=p.id,
                name=p.name,
                site=p.site,
                source_ref=p.source_ref,
                synced_at=p.synced_at,
                rows=len(p.rows or []),
                columns=len(p.columns or []),
                rack_cells=sum(1 for c in p.cells or [] if c.get("type") in {"rack", "empty_rack"}),
                linked_racks=len(p.racks),
                updated_at=p.updated_at,
            )
        )
    return out


# ---------------------------------------------------------------------------------------------------
# dashboard & search
# ---------------------------------------------------------------------------------------------------


def stats(session: Session, actor: str) -> dict[str, Any]:
    now = utcnow()
    devices = list(session.scalars(select(Device).where(Device.removed_at.is_(None), Device.is_virtual.is_(False))))
    testbeds = list(session.scalars(_testbed_query().where(Testbed.removed_at.is_(None))))
    states = bk.compute_states(session, testbeds, now)
    tb_status = Counter(s.status for s in states.values())
    role_counts = Counter(d.role for d in devices)
    mine = list_bookings(session, owner=actor, include_finished=False)
    in_use = [
        booking_out(s.current, now)
        for s in sorted((s for s in states.values() if s.current), key=lambda s: s.current.effective_end)  # type: ignore[union-attr]
    ]
    upcoming = list_bookings(session, start=now, end=now + timedelta(hours=24), include_finished=False)
    upcoming = [b for b in upcoming if b.state == "upcoming"]
    recently_down = [
        d
        for d in sorted(
            (d for d in devices if d.status in DOWN_STATUSES),
            key=lambda d: d.status_changed_at or now,
            reverse=True,
        )
    ][:8]
    events = session.scalars(select(Event).order_by(Event.ts.desc()).limit(12))
    return {
        "devices": {
            "total": len(devices),
            "health": _counts([d.status for d in devices]).model_dump(),
            "roles": dict(role_counts.most_common()),
            "switches": role_counts.get("switch", 0),
            "unracked": sum(1 for d in devices if d.rack_position is None),
        },
        "testbeds": {
            "total": len(testbeds),
            "free": tb_status.get("free", 0),
            "in_use": tb_status.get("in_use", 0),
            "blocked": tb_status.get("blocked", 0),
            "maintenance": tb_status.get("maintenance", 0),
        },
        "racks": session.scalar(select(func.count(Rack.id))) or 0,
        "networks": session.scalar(select(func.count(Network.id))) or 0,
        "floor_plans": session.scalar(select(func.count(FloorPlan.id))) or 0,
        "my_bookings": [b.model_dump() for b in mine],
        "in_use": [b.model_dump() for b in in_use],
        "upcoming": [b.model_dump() for b in upcoming[:10]],
        "attention": [s.model_dump() for s in summarize_devices(session, recently_down)],
        "events": [EventOut.model_validate(e).model_dump() for e in events],
    }


def search(session: Session, q: str, limit: int = 8) -> dict[str, list[dict[str, Any]]]:
    like = f"%{q.lower()}%"
    devices = session.scalars(
        select(Device)
        .where(
            Device.removed_at.is_(None),
            or_(func.lower(Device.name).like(like), func.lower(Device.serial).like(like),
                func.lower(Device.mgmt_ip).like(like), func.lower(Device.model).like(like)),
        )
        .order_by(Device.name)
        .limit(limit)
    )
    testbeds = session.scalars(
        select(Testbed).where(Testbed.removed_at.is_(None), func.lower(Testbed.name).like(like)).order_by(Testbed.name).limit(limit)
    )
    racks = session.scalars(select(Rack).where(func.lower(Rack.name).like(like)).order_by(Rack.name).limit(limit))
    nets = session.scalars(select(Network).where(func.lower(Network.name).like(like)).order_by(Network.name).limit(limit))
    return {
        "devices": [
            {"id": d.id, "name": d.name, "hint": " · ".join(x for x in (d.model, d.serial, d.status) if x)}
            for d in devices
        ],
        "testbeds": [{"id": t.id, "name": t.name, "hint": t.group or ""} for t in testbeds],
        "racks": [{"id": r.id, "name": r.name, "hint": f"{r.height}U"} for r in racks],
        "networks": [{"id": n.id, "name": n.name, "hint": n.provider} for n in nets],
    }


def group_counts(items: list[str | None]) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for i in items:
        out[i or "—"] += 1
    return dict(out)
