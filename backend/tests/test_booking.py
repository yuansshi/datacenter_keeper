from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from labkeeper import booking as bk
from labkeeper.models import Testbed
from labkeeper.timeutil import utcnow


def tb(session, name):
    return session.scalar(select(Testbed).where(Testbed.name == name))


def book(session, settings, name, owner="alice", start=None, minutes=60):
    return bk.create_booking(
        session, tb(session, name), owner=owner, starts_at=start, ends_at=None, duration_minutes=minutes,
        purpose=None, via="test", settings=settings.booking,
    )


def test_booking_blocks_testbeds_sharing_exclusive_devices(session, settings, imported):
    book(session, settings, "red_smoke")
    with pytest.raises(bk.BookingConflict) as exc:
        book(session, settings, "red_blue", owner="bob")
    assert "red_smoke is booked by alice" in str(exc.value)
    # blue shares only the jump host (non-exclusive) with red -> no conflict
    book(session, settings, "blue_smoke", owner="bob")


def test_states(session, settings, imported):
    book(session, settings, "red_smoke")
    tbs = list(session.scalars(select(Testbed)))
    states = {t.name: s for t, s in ((t, bk.compute_states(session, tbs)[t.id]) for t in tbs)}
    assert states["red_smoke"].status == "in_use"
    assert states["red_blue"].status == "blocked"
    assert states["red_blue"].blocked_by[0].testbed.name == "red_smoke"
    assert states["blue_smoke"].status == "free"


def test_future_window_does_not_conflict_with_now(session, settings, imported):
    book(session, settings, "red_smoke", minutes=60)
    later = book(session, settings, "red_blue", owner="bob", start=utcnow() + timedelta(hours=2))
    assert later.state(utcnow()) == "upcoming"


def test_release_rules(session, settings, imported):
    b = book(session, settings, "red_smoke")
    bk.release_booking(session, b, actor="bob", force=False, settings=settings.booking, via="test")  # courtesy
    assert b.state(utcnow()) == "released"
    settings.booking.release_policy = "owner"
    b2 = book(session, settings, "red_smoke")
    with pytest.raises(bk.BookingForbidden):
        bk.release_booking(session, b2, actor="bob", force=False, settings=settings.booking, via="test")
    bk.release_booking(session, b2, actor="bob", force=True, settings=settings.booking, via="test")
    assert b2.state(utcnow()) == "released"
    # released -> the shared testbed is bookable again
    book(session, settings, "red_blue", owner="bob")


def test_cancel_upcoming(session, settings, imported):
    b = book(session, settings, "red_smoke", start=utcnow() + timedelta(hours=3))
    bk.release_booking(session, b, actor="alice", force=False, settings=settings.booking, via="test")
    assert b.state(utcnow()) == "cancelled"


def test_extend_checks_conflicts(session, settings, imported):
    mine = book(session, settings, "red_smoke", minutes=60)
    book(session, settings, "red_blue", owner="bob", start=utcnow() + timedelta(minutes=90))
    bk.extend_booking(session, mine, minutes=20, actor="alice", force=False, settings=settings.booking, via="test")
    with pytest.raises(bk.BookingConflict):
        bk.extend_booking(session, mine, minutes=60, actor="alice", force=False, settings=settings.booking, via="test")


def test_maintenance_and_limits(session, settings, imported):
    t = tb(session, "blue_smoke")
    t.maintenance = True
    with pytest.raises(bk.BookingError, match="maintenance"):
        book(session, settings, "blue_smoke")
    with pytest.raises(bk.BookingError, match="limited"):
        book(session, settings, "red_smoke", minutes=60 * 24 * 30)


def book_devices(session, settings, name, device_ids, owner="alice"):
    return bk.create_booking(
        session, tb(session, name), owner=owner, starts_at=None, ends_at=None, duration_minutes=60,
        purpose=None, via="test", settings=settings.booking, device_ids=device_ids,
    )


def dev(session, name):
    from labkeeper.models import Device

    return session.scalar(select(Device).where(Device.name == name))


def test_device_level_booking_holds_only_those_devices(session, settings, imported):
    from labkeeper import services as sv

    blue = dev(session, "BLUE_01")
    b = book_devices(session, settings, "red_blue", [blue.id])
    assert b.device_ids == [blue.id] and bk.device_label(session, b.device_ids) == "BLUE_01"
    # the rest of red_blue's hardware is still free: red_smoke can be booked whole by someone else
    book(session, settings, "red_smoke", owner="bob")
    with pytest.raises(bk.BookingConflict) as exc:
        book(session, settings, "blue_smoke", owner="carol")  # needs BLUE_01
    assert "a device of red_blue is booked by alice" in str(exc.value)
    with pytest.raises(bk.BookingConflict):
        book_devices(session, settings, "red_blue", [blue.id], owner="eve")
    with pytest.raises(bk.BookingConflict):
        book(session, settings, "red_blue", owner="dave")  # whole testbed overlaps both bookings

    states = bk.compute_states(session, [tb(session, n) for n in ("red_blue", "blue_smoke", "red_smoke")])
    rb, bs, rs = (states[tb(session, n).id] for n in ("red_blue", "blue_smoke", "red_smoke"))
    assert rb.status == "partial" and [x.owner for x in rb.partial] == ["alice"] and len(rb.blocked_by) == 1
    assert rb.busy == {d.device_id for d in tb(session, "red_blue").members if d.exclusive}
    assert bs.status == "blocked" and rs.status == "in_use"
    owners = sv.active_device_owners(session)
    assert owners[blue.id] == ("alice", "red_blue") and owners[dev(session, "RED_01").id][0] == "bob"


def test_device_pick_rules(session, settings, imported):
    red_blue = tb(session, "red_blue")
    exclusive = sorted(m.device_id for m in red_blue.members if m.exclusive)
    jump = dev(session, "jump1")
    with pytest.raises(bk.BookingError, match="never reserved"):
        bk.resolve_devices(session, red_blue, [jump.id])
    assert bk.resolve_devices(session, red_blue, exclusive) is None  # everything = whole testbed
    with pytest.raises(bk.BookingError, match="not part of"):
        bk.resolve_devices(session, tb(session, "blue_smoke"), [dev(session, "RED_01").id])
    # picking a logical stack picks its members
    red_c = dev(session, "RED_C")
    picked = bk.resolve_devices(session, red_blue, [red_c.id])
    assert set(picked) >= {dev(session, "RED_01").id, dev(session, "RED_02").id}
