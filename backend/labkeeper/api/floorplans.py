from __future__ import annotations

import json
import mimetypes
import secrets
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import services as sv
from ..agent.tools import apply_floor_plan
from ..db import get_db
from ..events import record
from ..db import run_in_session
from ..floorplan import FloorPlanError, check_media, extract_from_image, normalise, parse_plan_file
from ..floorsources import FloorSourceError, upsert_source_plan
from ..models import FloorPlan, Rack
from ..schemas import FloorPlanIn, FloorPlanOut, FloorPlanSummary, RackDetail
from .deps import actor, state

router = APIRouter()

IMAGE_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif", "application/pdf": ".pdf"}


def _dir(request: Request) -> Path:
    d = (state(request).settings.data_dir / "floorplans").resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


class FloorPlanSave(FloorPlanIn):
    image_token: str | None = None


@router.get("/floorplans", response_model=list[FloorPlanSummary])
def floorplans(db: Session = Depends(get_db)):
    return sv.list_floor_plans(db)


@router.get("/floorplans/{plan_id}", response_model=FloorPlanOut)
def floorplan(plan_id: int, db: Session = Depends(get_db)):
    p = db.get(FloorPlan, plan_id)
    if p is None:
        raise HTTPException(404, "floor plan not found")
    return sv.floor_plan_out(db, p)


def _attach_image(request: Request, plan: FloorPlan, token: str | None) -> None:
    if not token:
        return
    if not token.isalnum():
        raise HTTPException(422, "bad image token")
    folder = _dir(request)
    match = next(iter(folder.glob(f"draft-{token}.*")), None)
    if match is None:
        return
    final = folder / f"plan-{plan.id}{match.suffix}"
    match.replace(final)
    plan.source_image = final.name
    plan.source = "image"


@router.post("/floorplans", response_model=FloorPlanOut, status_code=201)
def create(body: FloorPlanSave, request: Request, db: Session = Depends(get_db)):
    data = normalise(FloorPlanIn.model_validate(body.model_dump(exclude={"image_token"})))
    if db.scalar(select(FloorPlan).where(FloorPlan.name == data.name)):
        raise HTTPException(409, f"a floor plan named {data.name} already exists")
    plan = FloorPlan(name=data.name, source="manual")
    apply_floor_plan(plan, data)
    db.add(plan)
    db.flush()
    _attach_image(request, plan, body.image_token)
    sv.link_floor_racks(db, plan)
    record(db, actor=actor(request), action="created", object_type="floor_plan", object_id=plan.id,
           object_name=plan.name, message=f"{len(data.rows)}x{len(data.columns)} grid, {len(data.cells)} cells")
    db.commit()
    db.refresh(plan)
    return sv.floor_plan_out(db, plan)


@router.put("/floorplans/{plan_id}", response_model=FloorPlanOut)
def update(plan_id: int, body: FloorPlanSave, request: Request, db: Session = Depends(get_db)):
    plan = db.get(FloorPlan, plan_id)
    if plan is None:
        raise HTTPException(404, "floor plan not found")
    data = normalise(FloorPlanIn.model_validate(body.model_dump(exclude={"image_token"})))
    clash = db.scalar(select(FloorPlan).where(FloorPlan.name == data.name, FloorPlan.id != plan.id))
    if clash:
        raise HTTPException(409, f"a floor plan named {data.name} already exists")
    apply_floor_plan(plan, data)
    _attach_image(request, plan, body.image_token)
    # unlink racks whose cell disappeared, then relink by name
    valid = {(str(c["row"]), str(c["col"])) for c in plan.cells if c.get("type") in {"rack", "empty_rack"}}
    for r in list(plan.racks):
        if (r.floor_row, r.floor_col) not in valid:
            r.floor_plan = None
            r.floor_row = r.floor_col = None
    sv.link_floor_racks(db, plan)
    record(db, actor=actor(request), action="updated", object_type="floor_plan", object_id=plan.id, object_name=plan.name,
           message=f"{len(data.cells)} cells")
    db.commit()
    db.refresh(plan)
    return sv.floor_plan_out(db, plan)


@router.delete("/floorplans/{plan_id}", status_code=204)
def delete(plan_id: int, request: Request, db: Session = Depends(get_db)):
    plan = db.get(FloorPlan, plan_id)
    if plan is None:
        raise HTTPException(404, "floor plan not found")
    for r in list(plan.racks):
        r.floor_plan = None
        r.floor_row = r.floor_col = None
    if plan.source_image:
        (_dir(request) / plan.source_image).unlink(missing_ok=True)
    record(db, actor=actor(request), action="deleted", object_type="floor_plan", object_id=plan.id, object_name=plan.name)
    db.delete(plan)
    db.commit()


@router.get("/floorplans/{plan_id}/image")
def image(plan_id: int, request: Request, db: Session = Depends(get_db)):
    plan = db.get(FloorPlan, plan_id)
    if plan is None or not plan.source_image:
        raise HTTPException(404, "no image")
    path = _dir(request) / Path(plan.source_image).name
    if not path.is_file():
        raise HTTPException(404, "image missing")
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream")


@router.post("/floorplans/extract")
async def extract(
    request: Request,
    file: UploadFile = File(...),
    hint: str | None = Form(None),
    name: str | None = Form(None),
) -> dict[str, Any]:
    """Read a floor map image/PDF with Claude, or parse a grid file. Returns a draft (not saved)."""
    content = await file.read()
    filename = file.filename or "floorplan"
    media = (file.content_type or mimetypes.guess_type(filename)[0] or "").lower()
    # An export file from a floor-plan source plugin (e.g. a DCIM bookmarklet) is authoritative:
    # create or update the plan straight away instead of returning a draft.
    if filename.lower().endswith(".json") or "json" in media:
        try:
            payload = json.loads(content.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = None
        if payload is not None:
            for source in state(request).floor_sync.sources.values():
                try:
                    if not source.matches_export(payload):
                        continue
                    plans = source.parse_export(payload)
                except FloorSourceError as exc:
                    raise HTTPException(422, str(exc)) from exc
                user = actor(request)

                def apply(s, plans=plans, source=source, user=user):
                    out = []
                    for sp in plans:
                        plan, created, changes = upsert_source_plan(s, source, sp, actor=user)
                        out.append({"id": plan.id, "name": plan.name, "created": created, "changes": changes})
                    return out

                return {"method": "source", "source": source.name, "label": source.label, "plans": await run_in_session(apply)}
    try:
        if media.startswith("image/") or media == "application/pdf":
            check_media(content, media)
            ok, reason = state(request).chat.availability()
            if not ok:
                raise FloorPlanError(f"reading images needs the assistant: {reason}")
            draft = await extract_from_image(content, media, state(request).settings.agent, hint=hint, name=name)
            token = secrets.token_hex(12)
            (_dir(request) / f"draft-{token}{IMAGE_EXT.get(media, '.bin')}").write_bytes(content)
            return {"draft": draft.model_dump(), "image_token": token, "method": "vision"}
        draft = parse_plan_file(filename, content)
        if name:
            draft = draft.model_copy(update={"name": name})
        return {"draft": draft.model_dump(), "image_token": None, "method": "grid"}
    except FloorPlanError as exc:
        raise HTTPException(422, str(exc)) from exc


class CellRackRequest(BaseModel):
    row: str
    col: str
    name: str | None = None
    height: int = 42


@router.post("/floorplans/{plan_id}/racks", response_model=RackDetail, status_code=201)
def create_rack_for_cell(plan_id: int, body: CellRackRequest, request: Request, db: Session = Depends(get_db)):
    plan = db.get(FloorPlan, plan_id)
    if plan is None:
        raise HTTPException(404, "floor plan not found")
    cell = next((c for c in plan.cells if str(c["row"]) == body.row and str(c["col"]) == body.col), None)
    if cell is None or cell.get("type") not in {"rack", "empty_rack"}:
        raise HTTPException(422, "that cell is not a rack position")
    name = body.name or sv.rack_name_for_cell(plan, body.row, body.col, cell.get("label"))
    rack = db.scalar(select(Rack).where(Rack.name == name))
    if rack is None:
        rack = Rack(name=name, height=body.height, site=plan.site, source="floorplan")
        db.add(rack)
    elif rack.floor_plan_id not in (None, plan.id):
        raise HTTPException(409, f"rack {name} already sits on another floor plan")
    rack.floor_plan = plan
    rack.floor_row, rack.floor_col = body.row, body.col
    db.flush()
    record(db, actor=actor(request), action="created", object_type="rack", object_id=rack.id, object_name=rack.name,
           message=f"placed on {plan.name} at {body.row}{body.col}")
    db.commit()
    return sv.rack_detail(db, rack)


# ---- floor-plan source plugins ----------------------------------------------------------------------


@router.get("/floorsources")
def floor_sources(request: Request) -> list[dict[str, Any]]:
    return [src.describe() for src in state(request).floor_sync.sources.values()]


@router.get("/floorsources/{name}/sites")
async def floor_source_sites(name: str, request: Request) -> list[dict[str, Any]]:
    source = state(request).floor_sync.sources.get(name)
    if source is None:
        raise HTTPException(404, "no such floor-plan source")
    if not source.can_sync:
        raise HTTPException(409, source.sync_disabled_reason or "this source cannot be reached from the server")
    try:
        sites = await source.list_sites()
    except FloorSourceError as exc:
        raise HTTPException(502, str(exc)) from exc
    return [{"id": x.id, "name": x.name, "group": x.group, "detail": x.detail} for x in sites]


class SourceImport(BaseModel):
    site_id: str


@router.post("/floorsources/{name}/import")
async def floor_source_import(name: str, body: SourceImport, request: Request) -> dict[str, Any]:
    try:
        return await state(request).floor_sync.import_site(name, body.site_id, actor(request))
    except FloorSourceError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post("/floorplans/{plan_id}/sync")
async def sync_plan(plan_id: int, request: Request) -> dict[str, Any]:
    try:
        return await state(request).floor_sync.sync_plan(plan_id, actor=actor(request))
    except FloorSourceError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/floorplans/{plan_id}/detach", response_model=FloorPlanOut)
def detach_plan(plan_id: int, request: Request, db: Session = Depends(get_db)):
    plan = db.get(FloorPlan, plan_id)
    if plan is None:
        raise HTTPException(404, "floor plan not found")
    old = plan.source_ref
    plan.source_ref = None
    plan.sync_error = None
    record(db, actor=actor(request), action="updated", object_type="floor_plan", object_id=plan.id, object_name=plan.name,
           message=f"stopped syncing from {old}")
    db.commit()
    return sv.floor_plan_out(db, plan)
