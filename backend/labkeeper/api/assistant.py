from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .deps import actor, state

router = APIRouter()


class Attachment(BaseModel):
    name: str | None = None
    media_type: str
    data: str = Field(max_length=42_000_000)  # base64


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    message: str = Field(max_length=20_000)
    page: str | None = Field(None, max_length=300)
    local_time: str | None = Field(None, max_length=60)
    tz_offset_minutes: int = Field(0, ge=-14 * 60, le=14 * 60)
    attachments: list[Attachment] = Field(default_factory=list, max_length=4)


@router.post("/assistant/chat")
async def chat(body: ChatRequest, request: Request) -> StreamingResponse:
    st = state(request)
    user = actor(request)

    async def events():
        async for ev in st.chat.stream(
            conversation_id=body.conversation_id,
            text=body.message,
            actor=user,
            context={"page": body.page, "local_time": body.local_time, "tz_offset_minutes": body.tz_offset_minutes},
            attachments=[a.model_dump() for a in body.attachments],
        ):
            yield f"data: {json.dumps(ev, default=str)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete("/assistant/conversations/{conversation_id}", status_code=204)
async def forget(conversation_id: str, request: Request) -> None:
    state(request).chat.forget(conversation_id, actor(request))


@router.get("/assistant/tools")
def tools(request: Request) -> list[dict[str, Any]]:
    from ..agent.tools import registry

    return [
        {"name": t.name, "title": t.title, "description": t.description, "mutating": t.mutating}
        for t in registry.tools.values()
    ]
