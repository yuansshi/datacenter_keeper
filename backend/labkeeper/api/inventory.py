from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import __version__
from .. import services as sv
from ..agent.tools import device_live, run_console_test
from ..db import get_db
from ..events import record
from ..models import Event, Network, Rack
from ..schemas import (
    ConsoleTestRequest,
    DeviceDetail,
    DevicePatch,
    DeviceSummary,
    EventOut,
    NetworkOut,
    Placement,
    RackCreate,
    RackDetail,
    RackOut,
    RackPatch,
)
from ..timeutil import utcnow
from .deps import actor, http_error, state

router = APIRouter()


@router.get("/system")
def system_info(request: Request) -> dict[str, Any]:
    st = state(request)
    ok, reason = st.chat.availability()
    providers = st.poller.status()
    return {
        "server_time": utcnow().isoformat(),
        "version": __version__,
        "demo": st.settings.demo,
        "user": actor(request),
        "user_header": st.settings.auth.user_header,
        "providers": providers,
        "assistant": {"available": ok, "reason": reason, "model": st.settings.agent.model},
        "testbed_paths": [str(p) for p in st.settings.testbed_roots()],
        "last_import": st.importer.last_summary,
        "mcp_url": "/mcp/",
        "booking": st.settings.booking.model_dump(),
    }


@router.get("/stats")
def stats(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    return sv.stats(db, actor(request))


@router.get("/search")
def search(q: str = Query(min_length=1, max_length=100), db: Session = Depends(get_db)):
    return sv.search(db, q)


# ---- devices ----------------------------------------------------------------------------------------


@router.get("/devices", response_model=list[DeviceSummary])
def devices(
    q: str | None = None,
    status: str | None = None,
    role: str | None = None,
    network_id: int | None = None,
    testbed_id: int | None = None,
    rack_id: int | None = None,
    unracked: bool = False,
    include_removed: bool = False,
    include_virtual: bool = True,
    db: Session = Depends(get_db),
):
    return sv.list_devices(
        db, q=q, status=status, role=role, network_id=network_id, testbed_id=testbed_id, rack_id=rack_id,
        unracked=unracked, include_removed=include_removed, include_virtual=include_virtual,
    )


@router.get("/devices/{device_id}", response_model=DeviceDetail)
def device(device_id: int, db: Session = Depends(get_db)):
    try:
        return sv.device_detail(db, sv.find_device(db, device_id))
    except Exception as exc:
        raise http_error(exc) from exc


@router.patch("/devices/{device_id}", response_model=DeviceDetail)
def patch_device(device_id: int, body: DevicePatch, request: Request, db: Session = Depends(get_db)):
    try:
        d = sv.find_device(db, device_id)
    except Exception as exc:
        raise http_error(exc) from exc
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(d, k, v)
    if changes:
        record(db, actor=actor(request), action="updated", object_type="device", object_id=d.id, object_name=d.name,
               message=", ".join(f"{k} changed" for k in changes))
    db.commit()
    return sv.device_detail(db, d)


@router.put("/devices/{device_id}/placement", response_model=DeviceSummary)
def place(device_id: int, body: Placement, request: Request, db: Session = Depends(get_db)):
    try:
        d = sv.find_device(db, device_id)
        sv.place_device(db, d, rack_id=body.rack_id, position=body.position, face=body.face, actor=actor(request))
        db.commit()
        return sv.summarize_devices(db, [d])[0]
    except Exception as exc:
        db.rollback()
        raise http_error(exc) from exc


@router.post("/devices/{device_id}/placement/reset", response_model=DeviceSummary)
async def reset_placement(device_id: int, request: Request, db: Session = Depends(get_db)):
    """Forget the manual placement; the next import puts the device where its YAML says."""
    try:
        d = sv.find_device(db, device_id)
    except Exception as exc:
        raise http_error(exc) from exc
    d.placement_source = None
    record(db, actor=actor(request), action="updated", object_type="device", object_id=d.id, object_name=d.name,
           message="placement reset to the testbed file")
    db.commit()
    await state(request).importer.run(actor(request))
    db.expire_all()
    return sv.summarize_devices(db, [sv.find_device(db, device_id)])[0]


@router.get("/devices/{device_id}/live")
async def live(device_id: int, request: Request) -> dict[str, Any]:
    try:
        return await device_live(state(request).runtime, str(device_id))
    except Exception as exc:
        raise http_error(exc) from exc


@router.post("/devices/{device_id}/console-test")
async def console_test(device_id: int, body: ConsoleTestRequest, request: Request) -> dict[str, Any]:
    try:
        return await run_console_test(state(request).runtime, str(device_id), body.connection, actor=actor(request))
    except Exception as exc:
        if exc.__class__.__name__ == "ToolError":
            raise HTTPException(422, str(exc)) from exc
        raise http_error(exc) from exc


@router.post("/devices/{device_id}/refresh", response_model=DeviceSummary)
async def refresh(device_id: int, request: Request, db: Session = Depends(get_db)):
    st = state(request)
    try:
        d = sv.find_device(db, device_id)
    except Exception as exc:
        raise http_error(exc) from exc
    # a stack has no status of its own: refresh its members
    targets = [m for m in d.members if m.removed_at is None] if d.is_virtual else [d]
    by_provider: dict[str, list[int]] = {}
    for t in targets:
        if t.provider and t.provider in st.poller.providers:
            by_provider.setdefault(t.provider, []).append(t.id)
    for name, ids in by_provider.items():
        await st.poller.poll_once(name, ids)
    db.expire_all()
    return sv.summarize_devices(db, [sv.find_device(db, device_id)])[0]


# ---- racks ---------------------------------------------------------------------------------------------


@router.get("/racks", response_model=list[RackOut])
def racks(db: Session = Depends(get_db)):
    return sv.list_racks(db)


@router.post("/racks", response_model=RackDetail, status_code=201)
def create_rack(body: RackCreate, request: Request, db: Session = Depends(get_db)):
    if db.scalar(select(Rack).where(Rack.name == body.name)):
        raise HTTPException(409, f"rack {body.name} already exists")
    rack = Rack(**body.model_dump(), source="manual")
    db.add(rack)
    db.flush()
    record(db, actor=actor(request), action="created", object_type="rack", object_id=rack.id, object_name=rack.name,
           message=f"{rack.height}U rack")
    db.commit()
    return sv.rack_detail(db, rack)


@router.get("/racks/{rack_id}", response_model=RackDetail)
def rack(rack_id: int, db: Session = Depends(get_db)):
    r = db.get(Rack, rack_id)
    if r is None:
        raise HTTPException(404, "rack not found")
    return sv.rack_detail(db, r)


@router.patch("/racks/{rack_id}", response_model=RackDetail)
def patch_rack(rack_id: int, body: RackPatch, request: Request, db: Session = Depends(get_db)):
    r = db.get(Rack, rack_id)
    if r is None:
        raise HTTPException(404, "rack not found")
    changes = body.model_dump(exclude_unset=True)
    if "height" in changes:
        top = max((d.rack_position + max(d.u_height, 1) - 1 for d in r.devices if d.rack_position), default=0)
        if changes["height"] < top:
            raise HTTPException(422, f"devices occupy up to U{top}")
    for k, v in changes.items():
        setattr(r, k, v)
    record(db, actor=actor(request), action="updated", object_type="rack", object_id=r.id, object_name=r.name,
           message=", ".join(changes))
    db.commit()
    return sv.rack_detail(db, r)


@router.delete("/racks/{rack_id}", status_code=204)
def delete_rack(rack_id: int, request: Request, db: Session = Depends(get_db)):
    r = db.get(Rack, rack_id)
    if r is None:
        raise HTTPException(404, "rack not found")
    if any(d.removed_at is None for d in r.devices):
        raise HTTPException(409, "rack still holds devices")
    record(db, actor=actor(request), action="deleted", object_type="rack", object_id=r.id, object_name=r.name)
    db.delete(r)
    db.commit()


# ---- networks & events -----------------------------------------------------------------------------


def _regions(request: Request) -> dict[str, dict[str, Any]]:
    regions = [
        region for provider in state(request).poller.providers.values()
        for region in provider.details().get("regions", [])
    ]
    return {r["host"]: r for r in regions}


@router.get("/networks", response_model=list[NetworkOut])
def networks(request: Request, db: Session = Depends(get_db)):
    return sv.list_networks(db, _regions(request))


@router.get("/networks/{network_id}", response_model=NetworkOut)
def network(network_id: int, request: Request, db: Session = Depends(get_db)):
    n = db.get(Network, network_id)
    if n is None:
        raise HTTPException(404, "network not found")
    return next(x for x in sv.list_networks(db, _regions(request)) if x.id == n.id)


@router.get("/events", response_model=list[EventOut])
def events(
    limit: int = Query(100, ge=1, le=1000),
    object_type: str | None = None,
    object_id: int | None = None,
    actor_name: str | None = Query(None, alias="actor"),
    action: str | None = None,
    before_id: int | None = None,
    db: Session = Depends(get_db),
):
    q = select(Event).order_by(Event.id.desc()).limit(limit)
    if object_type:
        q = q.where(Event.object_type == object_type)
    if object_id is not None:
        q = q.where(Event.object_id == object_id)
    if actor_name:
        q = q.where(Event.actor.like(f"{actor_name}%"))
    if action:
        q = q.where(Event.action.in_(action.split(",")))
    if before_id:
        q = q.where(Event.id < before_id)
    return list(db.scalars(q))
