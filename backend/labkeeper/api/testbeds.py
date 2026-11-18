from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import booking as bk
from .. import services as sv
from ..db import get_db
from ..events import record
from ..importer import masked_source
from ..models import Booking, ImportSource, Testbed
from ..models import TestbedDevice as TestbedDeviceModel
from ..schemas import (
    BookingCreate,
    BookingExtend,
    BookingOut,
    BookingRelease,
    TestbedDetail,
    TestbedPatch,
    TestbedSummary,
)
from ..timeutil import ensure_utc, utcnow
from .deps import actor, http_error, state

router = APIRouter()


@router.get("/testbeds", response_model=list[TestbedSummary])
def testbeds(
    q: str | None = None,
    status: str | None = None,
    group: str | None = None,
    model: str | None = None,
    include_removed: bool = False,
    db: Session = Depends(get_db),
):
    return sv.list_testbeds(db, q=q, status=status, group=group, model=model, include_removed=include_removed)


@router.get("/testbeds/memberships")
def memberships(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """Testbed -> device ids, for grouping device lists by testbed."""
    rows = db.execute(
        select(TestbedDeviceModel.testbed_id, TestbedDeviceModel.device_id, TestbedDeviceModel.exclusive)
        .join(Testbed, Testbed.id == TestbedDeviceModel.testbed_id)
        .where(Testbed.removed_at.is_(None))
    ).all()
    names = {t.id: (t.name, t.group) for t in db.scalars(select(Testbed).where(Testbed.removed_at.is_(None)))}
    out: dict[int, dict[str, Any]] = {}
    for tb_id, dev_id, exclusive in rows:
        entry = out.setdefault(tb_id, {"testbed_id": tb_id, "name": names[tb_id][0], "group": names[tb_id][1], "device_ids": [], "shared_ids": []})
        entry["device_ids" if exclusive else "shared_ids"].append(dev_id)
    return sorted(out.values(), key=lambda e: e["name"])


@router.get("/testbeds/{testbed_id}", response_model=TestbedDetail)
def testbed(testbed_id: str, db: Session = Depends(get_db)):
    try:
        return sv.testbed_detail(db, sv.find_testbed(db, testbed_id))
    except Exception as exc:
        raise http_error(exc) from exc


@router.patch("/testbeds/{testbed_id}", response_model=TestbedDetail)
def patch_testbed(testbed_id: int, body: TestbedPatch, request: Request, db: Session = Depends(get_db)):
    try:
        tb = sv.find_testbed(db, testbed_id)
    except Exception as exc:
        raise http_error(exc) from exc
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(tb, k, v)
    if "maintenance" in changes:
        record(db, actor=actor(request), action="maintenance" if tb.maintenance else "updated", object_type="testbed",
               object_id=tb.id, object_name=tb.name,
               message=("in maintenance: " + (tb.maintenance_note or "")) if tb.maintenance else "back in service")
    elif changes:
        record(db, actor=actor(request), action="updated", object_type="testbed", object_id=tb.id, object_name=tb.name,
               message=", ".join(changes))
    db.commit()
    return sv.testbed_detail(db, tb)


@router.get("/testbeds/{testbed_id}/source")
def testbed_source(testbed_id: int, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        tb = sv.find_testbed(db, testbed_id)
    except Exception as exc:
        raise http_error(exc) from exc
    if not tb.source_file:
        raise HTTPException(404, "no source file")
    path = Path(tb.source_file)
    roots = state(request).settings.testbed_roots()
    if not any(path.is_relative_to(r) for r in roots) or not path.is_file():
        raise HTTPException(404, "source file not available")
    files = []
    seen: set[Path] = set()

    def add(p: Path, depth: int = 0) -> None:
        if p in seen or depth > 8 or not p.is_file() or not any(p.is_relative_to(r) for r in roots):
            return
        seen.add(p)
        text = p.read_text(encoding="utf-8", errors="replace")
        files.append({"path": str(p), "name": p.name, "text": masked_source(text)})
        for m in re.finditer(r"^\s*-\s*['\"]?([^'\"\s#]+\.ya?ml)", text.split("devices:")[0], re.M):
            add((p.parent / m.group(1)).resolve(), depth + 1)

    add(path.resolve())
    return {"files": files}


@router.get("/testbeds/{testbed_id}/availability")
def availability(
    testbed_id: str,
    start: datetime | None = None,
    end: datetime | None = None,
    duration_minutes: int = Query(120, ge=5, le=60 * 24 * 31),
    devices: str | None = Query(None, description="Comma-separated device or stack ids; omit for the whole testbed"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        tb = sv.find_testbed(db, testbed_id)
    except Exception as exc:
        raise http_error(exc) from exc
    s = ensure_utc(start) or utcnow()
    e = ensure_utc(end) or s + timedelta(minutes=duration_minutes)
    try:
        ids = bk.resolve_devices(db, tb, [int(x) for x in devices.split(",") if x.strip()] if devices else None)
    except (ValueError, bk.BookingError) as exc:
        return {"available": False, "conflicts": [], "error": str(exc), "device_ids": []}
    conflicts = bk.find_conflicts(db, tb, s, e, device_ids=ids)
    return {
        "available": not conflicts and not tb.maintenance,
        "conflicts": [sv.booking_out(b) for b in conflicts],
        "device_ids": ids or [],
        "devices_label": bk.device_label(db, ids),
    }


@router.post("/testbeds/{testbed_id}/bookings", response_model=BookingOut, status_code=201)
def book(testbed_id: str, body: BookingCreate, request: Request, db: Session = Depends(get_db)):
    st = state(request)
    try:
        tb = sv.find_testbed(db, testbed_id)
        b = bk.create_booking(
            db, tb, owner=actor(request), starts_at=body.starts_at, ends_at=body.ends_at,
            duration_minutes=body.duration_minutes, purpose=body.purpose, via="ui", settings=st.settings.booking,
            device_ids=body.device_ids,
        )
        db.commit()
        return sv.booking_out(b)
    except Exception as exc:
        db.rollback()
        raise http_error(exc) from exc


@router.get("/bookings", response_model=list[BookingOut])
def bookings(
    start: datetime | None = None,
    end: datetime | None = None,
    testbed_id: int | None = None,
    owner: str | None = None,
    active_only: bool = False,
    db: Session = Depends(get_db),
):
    return sv.list_bookings(
        db, start=ensure_utc(start), end=ensure_utc(end), testbed_id=testbed_id, owner=owner,
        include_finished=not active_only,
    )


def _booking(db: Session, booking_id: int) -> Booking:
    b = db.get(Booking, booking_id)
    if b is None:
        raise HTTPException(404, "booking not found")
    return b


@router.post("/bookings/{booking_id}/release", response_model=BookingOut)
def release(booking_id: int, body: BookingRelease, request: Request, db: Session = Depends(get_db)):
    b = _booking(db, booking_id)
    try:
        bk.release_booking(db, b, actor=actor(request), force=body.force, settings=state(request).settings.booking, via="ui")
        db.commit()
        return sv.booking_out(b)
    except Exception as exc:
        db.rollback()
        raise http_error(exc) from exc


@router.post("/bookings/{booking_id}/extend", response_model=BookingOut)
def extend(booking_id: int, body: BookingExtend, request: Request, db: Session = Depends(get_db)):
    b = _booking(db, booking_id)
    try:
        bk.extend_booking(db, b, minutes=body.minutes, actor=actor(request), force=body.force,
                          settings=state(request).settings.booking, via="ui")
        db.commit()
        return sv.booking_out(b)
    except Exception as exc:
        db.rollback()
        raise http_error(exc) from exc


# ---- imports -----------------------------------------------------------------------------------------


@router.get("/imports")
def imports(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    st = state(request)
    sources = db.scalars(select(ImportSource).order_by(ImportSource.kind, ImportSource.path))
    return {
        "roots": [str(r) for r in st.settings.testbed_roots()],
        "uploads_dir": str(st.settings.uploads_dir),
        "watching": st.importer.watching,
        "last_summary": st.importer.last_summary,
        "sources": [
            {
                "path": s.path,
                "root": s.root,
                "relative": str(Path(s.path).relative_to(s.root)) if Path(s.path).is_relative_to(s.root) else s.path,
                "kind": s.kind,
                "testbed_id": s.testbed_id,
                "device_count": s.device_count,
                "error": s.error,
                "last_imported_at": s.last_imported_at,
            }
            for s in sources
        ],
    }


@router.post("/imports/rescan")
async def rescan(request: Request) -> dict[str, Any]:
    return await state(request).importer.run(actor(request))


SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


@router.post("/imports/upload")
async def upload(request: Request, files: list[UploadFile] = File(...)) -> dict[str, Any]:
    st = state(request)
    target = st.settings.uploads_dir
    target.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in files[:50]:
        name = SAFE_NAME.sub("_", Path(f.filename or "testbed.yaml").name)
        if not name.lower().endswith((".yaml", ".yml")):
            raise HTTPException(422, f"{f.filename}: only .yaml/.yml testbed files")
        content = await f.read()
        if len(content) > 2 * 1024 * 1024:
            raise HTTPException(413, f"{f.filename}: larger than 2 MB")
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(422, f"{f.filename}: not UTF-8 text") from exc
        (target / name).write_bytes(content)
        saved.append(name)
    summary = await st.importer.run(actor(request))
    return {"saved": saved, "summary": summary}
