"""Floor plans: parse grid files, or read a floor-map image/PDF with Claude vision.

A floor plan is a grid. Rows are labelled top -> bottom (e.g. ``Z..R``), columns left -> right
(e.g. ``01..20``). Every non-background cell has a type: ``rack``, ``empty_rack``, ``bench``,
``open_space``, ``pillar``, ``storage``, ``unassigned``, ``wall`` or ``door``; benches and big
blocks can span several cells. Rack cells join to Labkeeper racks by name: ``<rack_prefix><row><col>``.

Grid text format (CSV/TSV, first row = column labels, first column = row labels)::

    ,01,02,03,04
    Z,R:1.7,R,E,.
    Y,B*2,,P,S

Codes: ``R`` rack, ``E``/``X`` empty rack, ``B`` bench, ``O`` open space, ``P`` pillar, ``S`` storage,
``U`` unassigned, ``W`` wall, ``D`` door, ``.``/blank = nothing. ``:value`` sets the metric (e.g. kW)
or a label, ``*N`` spans N columns and ``*NxM`` spans N columns by M rows.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import logging
import re
from typing import Any

import yaml
from pydantic import ValidationError

from .config import AgentSettings
from .schemas import FloorCell, FloorPlanIn

log = logging.getLogger(__name__)

CELL_CODES = {
    "R": "rack",
    "E": "empty_rack",
    "X": "empty_rack",
    "B": "bench",
    "O": "open_space",
    "P": "pillar",
    "S": "storage",
    "U": "unassigned",
    "W": "wall",
    "D": "door",
}
WORD_TYPES = {
    "rack": "rack",
    "empty": "empty_rack",
    "empty_rack": "empty_rack",
    "empty rack": "empty_rack",
    "bench": "bench",
    "open": "open_space",
    "open_space": "open_space",
    "open space": "open_space",
    "pillar": "pillar",
    "column": "pillar",
    "storage": "storage",
    "unassigned": "unassigned",
    "wall": "wall",
    "door": "door",
}

IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_PDF_BYTES = 30 * 1024 * 1024


class FloorPlanError(Exception):
    pass


def merge_cells(plan: FloorPlanIn, types: tuple[str, ...] = ("unassigned", "open_space")) -> FloorPlanIn:
    """Merge adjacent 1x1 cells of the same type into rectangles (greedy: widest run, then down).

    Sources such as DCIM tools often report big blocked-off areas one square at a time; merged
    blocks render as one shape instead of a checkerboard.
    """
    ri = {r: i for i, r in enumerate(plan.rows)}
    ci = {c: i for i, c in enumerate(plan.columns)}
    keep: list[FloorCell] = []
    pools: dict[str, set[tuple[int, int]]] = {t: set() for t in types}
    for cell in plan.cells:
        pos = (ri.get(cell.row), ci.get(cell.col))
        if cell.type in pools and cell.span_rows == 1 and cell.span_cols == 1 and None not in pos and not cell.label and cell.metric is None:
            pools[cell.type].add(pos)  # type: ignore[arg-type]
        else:
            keep.append(cell)
    for type_, free in pools.items():
        for r in range(len(plan.rows)):
            for c in range(len(plan.columns)):
                if (r, c) not in free:
                    continue
                w = 1
                while (r, c + w) in free:
                    w += 1
                h = 1
                while all((r + h, c + k) in free for k in range(w)):
                    h += 1
                for dr in range(h):
                    for dc in range(w):
                        free.discard((r + dr, c + dc))
                keep.append(FloorCell(row=plan.rows[r], col=plan.columns[c], type=type_, span_cols=w, span_rows=h))
    return plan.model_copy(update={"cells": keep})


RACK_NAME_RE = re.compile(r"^(.*?)([A-Za-z]{1,2})(\d{1,3})$")


def suggest_rack_prefix(rows: list[str], columns: list[str], rack_names: list[str]) -> str | None:
    """The prefix that makes ``<prefix><row><col>`` match the most existing racks (e.g. ``SJ_``)."""
    row_set = {r.upper() for r in rows}
    col_set: set[str] = set()
    for c in columns:
        col_set.add(c)
        if c.isdigit():
            col_set.update({str(int(c)), c.zfill(2)})
    tally: dict[str, int] = {}
    for name in rack_names:
        m = RACK_NAME_RE.match(name)
        if m and m.group(2).upper() in row_set and m.group(3) in col_set:
            tally[m.group(1)] = tally.get(m.group(1), 0) + 1
    if not tally:
        return None
    return max(tally.items(), key=lambda kv: kv[1])[0]


def normalise(plan: FloorPlanIn) -> FloorPlanIn:
    """Drop cells outside the grid, de-duplicate positions, clamp spans."""
    rows = [str(r).strip() for r in plan.rows if str(r).strip()]
    cols = [str(c).strip() for c in plan.columns if str(c).strip()]
    rows = list(dict.fromkeys(rows))
    cols = list(dict.fromkeys(cols))
    r_index = {r.upper(): i for i, r in enumerate(rows)}
    c_index = {c.upper(): i for i, c in enumerate(cols)}
    # tolerate "5" vs "05"
    for i, c in enumerate(cols):
        if c.isdigit():
            c_index.setdefault(str(int(c)), i)
    seen: set[tuple[int, int]] = set()
    cells: list[FloorCell] = []
    for cell in plan.cells:
        ri = r_index.get(str(cell.row).strip().upper())
        key = str(cell.col).strip()
        ci = c_index.get(key.upper(), c_index.get(str(int(key)) if key.isdigit() else key))
        if ri is None or ci is None or (ri, ci) in seen:
            continue
        seen.add((ri, ci))
        cells.append(
            cell.model_copy(
                update={
                    "row": rows[ri],
                    "col": cols[ci],
                    "span_rows": min(cell.span_rows, len(rows) - ri),
                    "span_cols": min(cell.span_cols, len(cols) - ci),
                    "label": (cell.label or None),
                }
            )
        )
    return plan.model_copy(update={"rows": rows, "columns": cols, "cells": cells})


# ---------------------------------------------------------------------------------------------------
# grid files
# ---------------------------------------------------------------------------------------------------


def _parse_token(token: str) -> tuple[str | None, str | None, float | None, int, int]:
    t = token.strip()
    if not t or t in {".", "-", "_"}:
        return None, None, None, 1, 1
    span, rows = 1, 1
    m = re.search(r"\*(\d+)(?:x(\d+))?$", t)
    if m:
        span = int(m.group(1))
        rows = int(m.group(2) or 1)
        t = t[: m.start()]
    value = None
    if ":" in t:
        t, value = t.split(":", 1)
    kind = CELL_CODES.get(t.strip().upper()) or WORD_TYPES.get(t.strip().lower())
    if kind is None:
        return None, None, None, 1, 1
    metric = None
    label = None
    if value is not None and value.strip():
        try:
            metric = float(value)
        except ValueError:
            label = value.strip()
    return kind, label, metric, span, rows


def parse_grid_text(text: str, name: str) -> FloorPlanIn:
    sample = text[:2000]
    delimiter = "\t" if sample.count("\t") > sample.count(",") else ","
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delimiter)]
    rows = [r for r in rows if any(c.strip() for c in r)]
    if len(rows) < 2:
        raise FloorPlanError("grid needs a header row of column labels and at least one row")
    header = [c.strip() for c in rows[0][1:]]
    columns = [c for c in header if c]
    cells: list[FloorCell] = []
    row_labels: list[str] = []
    for r in rows[1:]:
        label = r[0].strip()
        if not label:
            continue
        row_labels.append(label)
        for ci, token in enumerate(r[1:]):
            if ci >= len(header) or not header[ci]:
                continue
            kind, cell_label, metric, span, span_rows = _parse_token(token)
            if kind:
                cells.append(
                    FloorCell(
                        row=label, col=header[ci], type=kind, label=cell_label, metric=metric,
                        span_cols=max(span, 1), span_rows=max(span_rows, 1),
                    )
                )
    return normalise(FloorPlanIn(name=name, rows=row_labels, columns=columns, cells=cells))


def parse_plan_file(filename: str, content: bytes) -> FloorPlanIn:
    name = re.sub(r"\.[A-Za-z0-9]+$", "", filename.rsplit("/", 1)[-1]) or "Floor plan"
    text = content.decode("utf-8-sig", errors="replace")
    lower = filename.lower()
    if lower.endswith((".yaml", ".yml", ".json")):
        try:
            data = json.loads(text) if lower.endswith(".json") else yaml.safe_load(text)
        except Exception as exc:
            raise FloorPlanError(f"could not parse {filename}: {exc}") from exc
        if not isinstance(data, dict):
            raise FloorPlanError("floor plan file must be a mapping")
        data.setdefault("name", name)
        try:
            return normalise(FloorPlanIn.model_validate(data))
        except ValidationError as exc:
            raise FloorPlanError(f"invalid floor plan: {exc.errors()[:3]}") from exc
    return parse_grid_text(text, name)


# ---------------------------------------------------------------------------------------------------
# images (Claude vision + structured output)
# ---------------------------------------------------------------------------------------------------

EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Room or lab name shown on the plan, else empty"},
        "site": {"type": "string", "description": "Site/building/room identifier if shown, else empty"},
        "rows": {"type": "array", "items": {"type": "string"}, "description": "Row labels, top to bottom"},
        "columns": {"type": "array", "items": {"type": "string"}, "description": "Column labels, left to right"},
        "metric_label": {"type": "string", "description": "What numbers inside racks mean (e.g. kW), else empty"},
        "cells": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "row": {"type": "string"},
                    "col": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": ["rack", "empty_rack", "bench", "open_space", "pillar", "storage", "unassigned", "wall", "door"],
                    },
                    "label": {"type": "string", "description": "Name written on the cell, else empty"},
                    "span_rows": {"type": "integer"},
                    "span_cols": {"type": "integer"},
                    "metric": {"type": ["number", "null"]},
                },
                "required": ["row", "col", "type", "label", "span_rows", "span_cols", "metric"],
                "additionalProperties": False,
            },
        },
        "notes": {"type": "string", "description": "Anything ambiguous the user should double-check"},
    },
    "required": ["name", "site", "rows", "columns", "metric_label", "cells", "notes"],
    "additionalProperties": False,
}

EXTRACTION_PROMPT = """You are reading a lab or data-center floor map. Convert it into a grid description.

How to read it:
- Find the row labels (usually letters down one side) and column labels (usually numbers across the top or bottom). List them in on-screen order: rows top to bottom, columns left to right. Include every label, even for columns that are empty aisles.
- Emit one entry per occupied cell at the top-left cell it covers. Do not emit entries for plain aisles or background.
- Use the plan's legend when there is one. Typical meanings: a solid outlined box is a rack; a box with an X is an empty rack; a hatched wide shape is a bench (use span_cols for its width); a small dark square is a pillar; a double-outlined box is storage; a large plain grey block is an unassigned area (one entry with span_rows/span_cols covering it).
- If a rack shows a number (power, kW, utilisation), put it in "metric" and say what it is in "metric_label". If it shows "-" or nothing, metric is null. If it shows a name, put it in "label".
- span_rows and span_cols are 1 unless the shape clearly covers more cells.
- Be exhaustive and precise: count positions against the column labels; don't shift cells across aisles.
- Put anything you are unsure about in "notes" (e.g. "row U, columns 11-14 partly hidden").
"""


def _image_block(content: bytes, media_type: str) -> dict[str, Any]:
    data = base64.standard_b64encode(content).decode("ascii")
    if media_type == "application/pdf":
        return {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data}}
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}


def check_media(content: bytes, media_type: str) -> None:
    if media_type == "application/pdf":
        if len(content) > MAX_PDF_BYTES:
            raise FloorPlanError("PDF is larger than 30 MB")
        return
    if media_type not in IMAGE_TYPES:
        raise FloorPlanError(f"unsupported file type {media_type}; use PNG, JPEG, WebP, GIF or PDF")
    if len(content) > MAX_IMAGE_BYTES:
        raise FloorPlanError("image is larger than 5 MB; crop it to the floor map or save as JPEG")


async def extract_from_image(
    content: bytes, media_type: str, agent_settings: AgentSettings, *, hint: str | None = None, name: str | None = None
) -> FloorPlanIn:
    check_media(content, media_type)
    if not agent_settings.enabled or not agent_settings.model.strip():
        raise FloorPlanError("AI image import is optional; enable agent.enabled and configure agent.model to use it")
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise FloorPlanError("the anthropic package is not installed") from exc

    client = anthropic.AsyncAnthropic()
    text_prompt = EXTRACTION_PROMPT + (f"\nExtra context from the user: {hint}\n" if hint else "")
    kwargs: dict[str, Any] = {}
    if agent_settings.use_fallbacks:
        kwargs = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    try:
        async with client.beta.messages.stream(
            model=agent_settings.model,
            max_tokens=32000,
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": EXTRACTION_SCHEMA}},
            messages=[{"role": "user", "content": [_image_block(content, media_type), {"type": "text", "text": text_prompt}]}],
            **kwargs,
        ) as stream:
            message = await stream.get_final_message()
    except anthropic.AuthenticationError as exc:
        raise FloorPlanError("Anthropic credentials are missing or invalid (set ANTHROPIC_API_KEY)") from exc
    except anthropic.RateLimitError as exc:
        raise FloorPlanError("Claude API rate limit reached; try again in a minute") from exc
    except anthropic.BadRequestError as exc:
        raise FloorPlanError(f"Claude rejected the request: {exc.message}") from exc
    except anthropic.APIStatusError as exc:
        raise FloorPlanError(f"Claude API error {exc.status_code}") from exc
    except anthropic.APIConnectionError as exc:
        raise FloorPlanError("cannot reach the Claude API") from exc

    if message.stop_reason == "refusal":
        raise FloorPlanError("Claude declined to read this image")
    if message.stop_reason == "max_tokens":
        raise FloorPlanError("floor plan is too large to read in one pass; crop it into sections")
    text = next((b.text for b in message.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise FloorPlanError("could not read a grid from the image") from exc
    data["name"] = name or data.get("name") or "Floor plan"
    data["site"] = data.get("site") or None
    data["metric_label"] = data.get("metric_label") or None
    for cell in data.get("cells", []):
        cell["label"] = cell.get("label") or None
        cell["span_rows"] = max(1, int(cell.get("span_rows") or 1))
        cell["span_cols"] = max(1, int(cell.get("span_cols") or 1))
    try:
        plan = FloorPlanIn.model_validate(data)
    except ValidationError as exc:
        raise FloorPlanError(f"the grid read from the image was invalid: {exc.errors()[:3]}") from exc
    return normalise(plan)
