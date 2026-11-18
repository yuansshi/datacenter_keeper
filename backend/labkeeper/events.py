"""Audit log helpers."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .models import Event


def record(
    session: Session,
    *,
    actor: str,
    action: str,
    object_type: str,
    object_id: int | None = None,
    object_name: str | None = None,
    message: str | None = None,
    data: dict[str, Any] | None = None,
) -> Event:
    ev = Event(
        actor=actor or "system",
        action=action,
        object_type=object_type,
        object_id=object_id,
        object_name=object_name,
        message=message,
        data=data or {},
    )
    session.add(ev)
    return ev
