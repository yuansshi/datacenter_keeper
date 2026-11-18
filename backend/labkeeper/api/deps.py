from __future__ import annotations

import re
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request
from sqlalchemy.orm import object_session

from .. import booking as bk
from .. import services as sv

if TYPE_CHECKING:
    from ..app import AppState

USER_RE = re.compile(r"^[\w .@+\-()]{1,120}$")


def state(request: Request) -> AppState:
    return request.app.state.lk


def actor(request: Request) -> str:
    st = state(request)
    raw = request.headers.get(st.settings.auth.user_header) or ""
    raw = raw.strip()
    if raw and USER_RE.match(raw):
        return raw
    return st.settings.auth.default_user


def http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, sv.NotFound):
        return HTTPException(404, str(exc))
    if isinstance(exc, sv.Invalid):
        return HTTPException(422, str(exc))
    if isinstance(exc, bk.BookingConflict):
        return HTTPException(409, {
            "message": str(exc),
            "conflicts": [
                {"booking_id": c.id, "testbed": c.testbed.name, "owner": c.owner, "until": c.effective_end.isoformat(),
                 "devices": bk.device_label(object_session(c), c.device_ids) if c.device_ids and object_session(c) else None}
                for c in exc.conflicts
            ],
        })
    if isinstance(exc, bk.BookingError):
        return HTTPException(exc.status_code, str(exc))
    return HTTPException(500, str(exc))
