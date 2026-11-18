"""Reserve / release API for scripts, CI jobs and pyATS runs.

Everything takes testbed and device *names* (ids and serials work too), so a job can do::

    POST /api/v1/reserve   {"testbed": "PoE", "duration_minutes": 120, "purpose": "nightly"}
    ... run tests ...
    POST /api/v1/release   {"booking_id": 42}          (or {"testbed": "PoE"})

Who acts is the ``X-Labkeeper-User`` header (or the configured SSO header), or ``owner`` / ``by`` in
the body. Releasing is a courtesy anyone may do (``booking.release_policy: anyone``); it is logged
under the name of whoever released it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import booking as bk
from .. import services as sv
from ..db import get_db
from ..models import Booking
from ..schemas import BookingOut
from ..timeutil import utcnow
from .deps import USER_RE, actor, http_error, state

router = APIRouter(tags=["reservations"])

WHO = "Who the booking is for. Default: the X-Labkeeper-User header (or the server's SSO header)."


class ReserveBody(BaseModel):
    devices: list[str] | None = Field(
        None, max_length=500,
        description="Reserve only these devices or stacks of the testbed (names, serials or ids). Omit for the whole testbed.",
        examples=[["2M Stack"], ["POE_DUT2"]],
    )
    duration_minutes: int | None = Field(None, ge=5, le=60 * 24 * 31, description="Default 120 (server setting)")
    start: datetime | None = Field(None, description="ISO 8601; omit to start now")
    end: datetime | None = Field(None, description="ISO 8601; alternative to duration_minutes")
    purpose: str | None = Field(None, max_length=500, examples=["nightly PoE regression"])
    owner: str | None = Field(None, max_length=120, description=WHO)


class ReserveRequest(ReserveBody):
    testbed: str = Field(description="Testbed name or id", examples=["PoE"])


class ReleaseBody(BaseModel):
    devices: list[str] | None = Field(None, max_length=500, description="Only bookings holding any of these devices or stacks")
    owner: str | None = Field(None, max_length=120, description="Only this person's bookings")
    include_upcoming: bool = Field(False, description="Also cancel bookings that have not started yet")
    by: str | None = Field(None, max_length=120, description="Who is releasing (logged). Default: the user header.")


class ReleaseRequest(ReleaseBody):
    booking_id: int | None = Field(None, description="Release exactly this booking (as returned by /reserve)")
    testbed: str | None = Field(None, description="Or: release this testbed's current bookings (name or id)")


class ReleaseResult(BaseModel):
    released: list[BookingOut]


class ExtendRequest(BaseModel):
    booking_id: int | None = None
    testbed: str | None = Field(None, description="Or: your current booking of this testbed")
    minutes: int = Field(ge=5, le=60 * 24 * 14)
    by: str | None = Field(None, max_length=120, description="Who is extending. Default: the user header.")


def _who(request: Request, explicit: str | None) -> str:
    if explicit is not None:
        name = explicit.strip()
        if not USER_RE.match(name):
            raise http_error(sv.Invalid(f"not a valid user name: {explicit!r}"))
        return name
    return actor(request)


def _device_ids(db: Session, names: list[str] | None) -> list[int] | None:
    return [sv.find_device(db, n).id for n in names] if names else None


def _reserve(testbed: str, body: ReserveBody, request: Request, response: Response, db: Session) -> BookingOut:
    try:
        tb = sv.find_testbed(db, testbed)
        b = bk.create_booking(
            db, tb, owner=_who(request, body.owner), starts_at=body.start, ends_at=body.end,
            duration_minutes=body.duration_minutes, purpose=body.purpose, via="api",
            settings=state(request).settings.booking, device_ids=_device_ids(db, body.devices),
        )
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise http_error(exc) from exc
    response.headers["Location"] = f"/api/v1/bookings/{b.id}"
    return sv.booking_out(b)


def _release(body: ReleaseRequest, request: Request, db: Session) -> ReleaseResult:
    who = _who(request, body.by)
    settings = state(request).settings.booking
    try:
        if body.booking_id is not None:
            b = db.get(Booking, body.booking_id)
            if b is None:
                raise sv.NotFound(f"no booking {body.booking_id}")
            released = [bk.release_booking(db, b, actor=who, force=True, settings=settings, via="api")]
        elif body.testbed:
            tb = sv.find_testbed(db, body.testbed)
            released = bk.release_testbed(
                db, tb, actor=who, settings=settings, via="api", owner=body.owner,
                device_ids=_device_ids(db, body.devices), include_upcoming=body.include_upcoming, force=True,
            )
        else:
            raise sv.Invalid("give booking_id or testbed")
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise http_error(exc) from exc
    return ReleaseResult(released=[sv.booking_out(b) for b in released])


@router.post("/reserve", response_model=BookingOut, status_code=201, summary="Reserve a testbed, or some of its devices")
def reserve(body: ReserveRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    """Fails with **409** and the conflicting bookings if the hardware is taken in that window."""
    return _reserve(body.testbed, body, request, response, db)


@router.post("/testbeds/{testbed}/reserve", response_model=BookingOut, status_code=201, summary="Reserve this testbed")
def reserve_testbed(testbed: str, request: Request, response: Response, body: ReserveBody | None = None,
                    db: Session = Depends(get_db)):
    return _reserve(testbed, body or ReserveBody(), request, response, db)


@router.post("/release", response_model=ReleaseResult, summary="Release a booking, or a testbed's current bookings")
def release(body: ReleaseRequest, request: Request, db: Session = Depends(get_db)):
    """Anyone may release anyone's booking (courtesy); the release is logged under the caller's name.
    A booking that has not started is cancelled instead."""
    return _release(body, request, db)


@router.post("/testbeds/{testbed}/release", response_model=ReleaseResult, summary="Release this testbed's current bookings")
def release_testbed(testbed: str, request: Request, body: ReleaseBody | None = None, db: Session = Depends(get_db)):
    b = body or ReleaseBody()
    return _release(ReleaseRequest(testbed=testbed, **b.model_dump()), request, db)


@router.post("/extend", response_model=BookingOut, summary="Extend your booking")
def extend(body: ExtendRequest, request: Request, db: Session = Depends(get_db)):
    who = _who(request, body.by)
    try:
        if body.booking_id is not None:
            b = db.get(Booking, body.booking_id)
            if b is None:
                raise sv.NotFound(f"no booking {body.booking_id}")
        elif body.testbed:
            tb = sv.find_testbed(db, body.testbed)
            now = utcnow()
            mine = [x for x in tb.bookings if x.owner == who and x.state(now) in {"active", "upcoming"}]
            if not mine:
                raise bk.BookingNotFound(f"{who} has no active or upcoming booking of {tb.name}")
            b = min(mine, key=lambda x: x.starts_at)
        else:
            raise sv.Invalid("give booking_id or testbed")
        bk.extend_booking(db, b, minutes=body.minutes, actor=who, force=False, settings=state(request).settings.booking, via="api")
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise http_error(exc) from exc
    return sv.booking_out(b)


@router.get("/reservations", response_model=list[BookingOut], summary="Current and upcoming reservations")
def reservations(
    testbed: str | None = Query(None, description="Testbed name or id"),
    owner: str | None = None,
    state_: Literal["current", "active", "upcoming", "all"] = Query("current", alias="state",
                                                                    description="current = active + upcoming"),
    db: Session = Depends(get_db),
):
    tb_id = None
    if testbed:
        try:
            tb_id = sv.find_testbed(db, testbed).id
        except Exception as exc:
            raise http_error(exc) from exc
    out = sv.list_bookings(db, testbed_id=tb_id, owner=owner, include_finished=state_ == "all")
    wanted = {"current": {"active", "upcoming"}, "active": {"active"}, "upcoming": {"upcoming"}}.get(state_)
    return [b for b in out if wanted is None or b.state in wanted]
