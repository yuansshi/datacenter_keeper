"""Testbed booking rules.

* A booking reserves a testbed for ``[starts_at, ends_at)``: all of it, or only some of its devices
  (``device_ids``; picking a stack picks its members), so two people can use one testbed at once.
* Two bookings conflict when their windows overlap **and** they hold a common *exclusive* device
  (e.g. ``lab12`` = ``stack1`` + ``stack2`` blocks ``lab01``), or both are whole-testbed bookings of
  the same testbed. Shared support devices (jump hosts, servers, traffic generators) never conflict.
* Releasing a booking ends it now (cancels it if it has not started). By default anyone may release
  anyone's booking, as a courtesy that is logged under their name (``release_policy: anyone``).
  Extending pushes ``ends_at`` (re-checked for conflicts) and stays with the owner.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from .config import BookingSettings
from .events import record
from .models import Booking, Device, Testbed, TestbedDevice
from .timeutil import ensure_utc, utcnow


class BookingError(Exception):
    status_code = 400


class BookingConflict(BookingError):
    status_code = 409

    def __init__(self, message: str, conflicts: list[Booking]):
        super().__init__(message)
        self.conflicts = conflicts


class BookingForbidden(BookingError):
    status_code = 403


class BookingNotFound(BookingError):
    status_code = 404


@dataclass
class TestbedState:
    status: str  # free | in_use | partial | blocked | maintenance
    current: Booking | None = None  # active whole-testbed booking
    partial: list[Booking] = field(default_factory=list)  # active bookings of some of its devices
    blocked_by: list[Booking] = field(default_factory=list)  # other testbeds' bookings holding its devices
    next: Booking | None = None
    busy: set[int] = field(default_factory=set)  # its exclusive devices held by any active booking


def exclusive_devices(session: Session, testbed_ids: list[int] | None = None) -> dict[int, set[int]]:
    q = select(TestbedDevice.testbed_id, TestbedDevice.device_id).where(TestbedDevice.exclusive.is_(True))
    if testbed_ids is not None:
        q = q.where(TestbedDevice.testbed_id.in_(testbed_ids))
    out: dict[int, set[int]] = defaultdict(set)
    for tb_id, dev_id in session.execute(q):
        out[tb_id].add(dev_id)
    return out


def overlapping_bookings(
    session: Session, starts: datetime, ends: datetime, exclude_id: int | None = None
) -> list[Booking]:
    q = (
        select(Booking)
        .options(selectinload(Booking.testbed))
        .where(
            Booking.cancelled_at.is_(None),
            Booking.starts_at < ends,
            Booking.ends_at > starts,
            or_(Booking.released_at.is_(None), Booking.released_at > starts),
        )
    )
    if exclude_id is not None:
        q = q.where(Booking.id != exclude_id)
    return [b for b in session.scalars(q) if b.effective_end > starts]


def booked_devices(b: Booking, excl: dict[int, set[int]]) -> set[int]:
    """Exclusive devices a booking holds: its testbed's, or only the ones it names."""
    mine = excl.get(b.testbed_id, set())
    return mine & set(b.device_ids) if b.device_ids else set(mine)


def find_conflicts(
    session: Session,
    testbed: Testbed,
    starts: datetime,
    ends: datetime,
    exclude_id: int | None = None,
    device_ids: list[int] | None = None,
) -> list[Booking]:
    candidates = overlapping_bookings(session, starts, ends, exclude_id)
    if not candidates:
        return []
    ids = list({b.testbed_id for b in candidates} | {testbed.id})
    excl = exclusive_devices(session, ids)
    mine = excl.get(testbed.id, set()) & set(device_ids) if device_ids else excl.get(testbed.id, set())
    return [
        b
        for b in candidates
        if (b.testbed_id == testbed.id and not b.device_ids and not device_ids) or (mine & booked_devices(b, excl))
    ]


def resolve_devices(session: Session, testbed: Testbed, device_ids: list[int] | None) -> list[int] | None:
    """Turn a pick of devices and stacks into the exclusive member devices to hold.
    None (or every exclusive device) means the whole testbed."""
    if not device_ids:
        return None
    members = {m.device_id: m for m in testbed.members if m.device.removed_at is None}
    wanted: set[int] = set()
    for did in dict.fromkeys(device_ids):
        dev = session.get(Device, did)
        if dev is None:
            raise BookingError(f"device {did} not found")
        parts = [m.id for m in dev.members if m.removed_at is None and m.id in members] if dev.is_virtual else []
        if dev.is_virtual and dev.id not in members:
            if not parts:
                raise BookingError(f"stack {dev.name} has no members in {testbed.name}")
            wanted.update(parts)
        elif dev.id in members:
            wanted.add(dev.id)
            wanted.update(parts)
        else:
            raise BookingError(f"{dev.name} is not part of {testbed.name}")
    exclusive = {d for d in wanted if members[d].exclusive}
    if not exclusive:
        raise BookingError("shared support devices (jump hosts, servers, traffic generators) are never reserved; pick a device under test")
    all_exclusive = {d for d, m in members.items() if m.exclusive}
    return None if exclusive == all_exclusive else sorted(exclusive)


def device_label(session: Session, device_ids: list[int] | None) -> str | None:
    """'2M Stack, SW_7' for a device-level booking: a whole stack is named once."""
    if not device_ids:
        return None
    devs = list(session.scalars(select(Device).where(Device.id.in_(device_ids))))
    held = {d.id for d in devs}
    names: list[str] = []
    done: set[int] = set()
    for d in sorted(devs, key=lambda d: d.name):
        if d.id in done:
            continue
        parent = d.parent
        live = [m for m in parent.members if m.removed_at is None] if parent is not None else []
        if parent is not None and live and {m.id for m in live} <= held:
            names.append(parent.name)
            done.update(m.id for m in live)
        else:
            names.append(d.name)
            done.add(d.id)
    return ", ".join(sorted(dict.fromkeys(names)))


def _validate_window(starts: datetime, ends: datetime, settings: BookingSettings, now: datetime) -> None:
    if ends <= starts:
        raise BookingError("end time must be after start time")
    if ends <= now:
        raise BookingError("booking would already be over")
    if ends - starts > timedelta(days=settings.max_duration_days):
        raise BookingError(f"bookings are limited to {settings.max_duration_days} days")


def describe_conflict(b: Booking) -> str:
    if b.device_ids:
        n = len(b.device_ids)
        what = f"{n} devices of {b.testbed.name} are" if n != 1 else f"a device of {b.testbed.name} is"
        return f"{what} booked by {b.owner} until {b.effective_end.isoformat()}"
    return f"{b.testbed.name} is booked by {b.owner} until {b.effective_end.isoformat()}"


def create_booking(
    session: Session,
    testbed: Testbed,
    *,
    owner: str,
    starts_at: datetime | None,
    ends_at: datetime | None,
    duration_minutes: int | None,
    purpose: str | None,
    via: str,
    settings: BookingSettings,
    actor: str | None = None,
    device_ids: list[int] | None = None,
) -> Booking:
    now = utcnow()
    if testbed.removed_at is not None:
        raise BookingError(f"{testbed.name} was removed from the testbed files")
    if testbed.maintenance:
        raise BookingError(f"{testbed.name} is in maintenance: {testbed.maintenance_note or 'no note'}")
    starts = ensure_utc(starts_at) or now
    if starts < now - timedelta(minutes=5):
        raise BookingError("start time is in the past")
    starts = max(starts, now) if starts <= now + timedelta(minutes=1) else starts
    if ends_at is not None:
        ends = ensure_utc(ends_at)
    else:
        ends = starts + timedelta(minutes=duration_minutes or settings.default_duration_minutes)
    assert ends is not None
    _validate_window(starts, ends, settings, now)
    device_ids = resolve_devices(session, testbed, device_ids)
    conflicts = find_conflicts(session, testbed, starts, ends, device_ids=device_ids)
    if conflicts:
        raise BookingConflict(
            "; ".join(describe_conflict(b) for b in conflicts[:5]) or "conflicting booking", conflicts
        )
    booking = Booking(
        testbed=testbed,
        owner=owner,
        purpose=(purpose or None),
        starts_at=starts,
        ends_at=ends,
        created_via=via,
        device_ids=device_ids,
    )
    session.add(booking)
    session.flush()
    when = "now" if starts <= now + timedelta(minutes=1) else starts.isoformat()
    label = device_label(session, device_ids)
    record(
        session,
        actor=actor or owner,
        action="booked",
        object_type="testbed",
        object_id=testbed.id,
        object_name=testbed.name,
        message=(f"booked {label} " if label else "booked ") + f"from {when} until {ends.isoformat()}"
        + (f" — {purpose}" if purpose else ""),
        data={"booking_id": booking.id, "via": via, "device_ids": device_ids},
    )
    return booking


def release_booking(
    session: Session, booking: Booking, *, actor: str, force: bool, settings: BookingSettings, via: str
) -> Booking:
    now = utcnow()
    someone_else = booking.owner != actor
    if someone_else and settings.release_policy != "anyone":
        if not force:
            raise BookingForbidden(f"booking belongs to {booking.owner}; pass force to release it anyway")
        if not settings.allow_force_release:
            raise BookingForbidden("releasing someone else's booking is disabled on this server")
    state = booking.state(now)
    if state == "upcoming":
        booking.cancelled_at = now
        action, msg = "cancelled", "cancelled upcoming booking"
    elif state == "active":
        booking.released_at = now
        action, msg = "released", "released"
    else:
        raise BookingError(f"booking is already {state}")
    label = device_label(session, booking.device_ids)
    if label:
        msg += f" {label}"
    if someone_else:
        msg += f" ({booking.owner}'s booking, courtesy release)"
    record(
        session,
        actor=actor,
        action=action,
        object_type="testbed",
        object_id=booking.testbed_id,
        object_name=booking.testbed.name,
        message=msg,
        data={"booking_id": booking.id, "via": via, "courtesy": someone_else, "owner": booking.owner},
    )
    return booking


def release_testbed(
    session: Session,
    testbed: Testbed,
    *,
    actor: str,
    settings: BookingSettings,
    via: str,
    owner: str | None = None,
    device_ids: list[int] | None = None,
    include_upcoming: bool = False,
    force: bool = False,
) -> list[Booking]:
    """Release a testbed's current bookings (all of them, one owner's, or the ones holding some devices).
    With ``include_upcoming`` its upcoming bookings are cancelled too."""
    now = utcnow()
    states = {"active", "upcoming"} if include_upcoming else {"active"}
    excl = exclusive_devices(session, [testbed.id])
    held: set[int] | None = None
    if device_ids:
        picked = resolve_devices(session, testbed, device_ids)
        held = set(picked) if picked else set(excl.get(testbed.id, set()))
    matches = [
        b for b in sorted(testbed.bookings, key=lambda b: b.starts_at)
        if b.state(now) in states
        and (owner is None or b.owner == owner)
        and (held is None or booked_devices(b, excl) & held)
    ]
    if not matches:
        what = "active or upcoming" if include_upcoming else "active"
        who = f" by {owner}" if owner else ""
        raise BookingNotFound(f"{testbed.name} has no {what} booking{who}" + (" holding those devices" if held else ""))
    for b in matches:
        release_booking(session, b, actor=actor, force=force, settings=settings, via=via)
    return matches


def extend_booking(
    session: Session, booking: Booking, *, minutes: int, actor: str, force: bool, settings: BookingSettings, via: str
) -> Booking:
    now = utcnow()
    if minutes <= 0:
        raise BookingError("minutes must be positive")
    if booking.owner != actor and not force:
        raise BookingForbidden(f"booking belongs to {booking.owner}")
    state = booking.state(now)
    if state not in {"active", "upcoming"}:
        raise BookingError(f"booking is already {state}")
    new_end = booking.ends_at + timedelta(minutes=minutes)
    _validate_window(booking.starts_at, new_end, settings, now)
    conflicts = find_conflicts(session, booking.testbed, booking.ends_at, new_end, exclude_id=booking.id,
                               device_ids=booking.device_ids)
    if conflicts:
        raise BookingConflict("; ".join(describe_conflict(b) for b in conflicts[:5]), conflicts)
    booking.ends_at = new_end
    record(
        session,
        actor=actor,
        action="extended",
        object_type="testbed",
        object_id=booking.testbed_id,
        object_name=booking.testbed.name,
        message=f"extended by {minutes} min until {new_end.isoformat()}",
        data={"booking_id": booking.id, "via": via},
    )
    return booking


def compute_states(session: Session, testbeds: list[Testbed], now: datetime | None = None) -> dict[int, TestbedState]:
    """State of many testbeds at once (a handful of queries regardless of testbed count)."""
    now = now or utcnow()
    horizon = now + timedelta(days=30)
    relevant = list(
        session.scalars(
            select(Booking)
            .options(selectinload(Booking.testbed))
            .where(Booking.cancelled_at.is_(None), Booking.ends_at > now, Booking.starts_at < horizon)
            .order_by(Booking.starts_at)
        )
    )
    relevant = [b for b in relevant if b.effective_end > now]
    active = [b for b in relevant if b.starts_at <= now]
    upcoming = [b for b in relevant if b.starts_at > now]
    excl = exclusive_devices(session)
    device_to_tbs: dict[int, set[int]] = defaultdict(set)
    for tb_id, devs in excl.items():
        for d in devs:
            device_to_tbs[d].add(tb_id)

    states: dict[int, TestbedState] = {}
    for tb in testbeds:
        states[tb.id] = TestbedState(status="free")
    for b in active:
        held = booked_devices(b, excl)
        st = states.get(b.testbed_id)
        if st:
            if b.device_ids:
                st.partial.append(b)
            elif st.current is None:
                st.current = b
        touched: set[int] = set()
        for d in held:
            touched |= device_to_tbs[d]
        for tb_id in touched:
            other = states.get(tb_id)
            if other is None:
                continue
            other.busy |= held & excl.get(tb_id, set())
            if tb_id != b.testbed_id:
                other.blocked_by.append(b)
    for tb in testbeds:
        st = states[tb.id]
        st.status = (
            "maintenance" if tb.maintenance
            else "in_use" if st.current
            else "partial" if st.partial
            else "blocked" if st.blocked_by
            else "free"
        )
    for b in upcoming:
        st = states.get(b.testbed_id)
        if st and st.next is None:
            st.next = b
    return states
