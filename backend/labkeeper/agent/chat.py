"""In-app assistant: Claude + the Labkeeper tool registry, streamed to the browser as SSE events.

Why a hand-written loop instead of the SDK tool runner: the browser needs one event stream that
interleaves text deltas with tool start/finish cards, and conversation history lives server-side
(so thinking blocks are passed back unchanged on every turn, as the API requires).

Event types sent to the client:
  {"type": "conversation", "id"}                 first event
  {"type": "status", "text"}                     e.g. "thinking"
  {"type": "text", "delta"}                      streamed answer text
  {"type": "tool_start", "id", "name", "title", "input"}
  {"type": "tool_end", "id", "name", "ok", "summary", "error", "mutating"}
  {"type": "error", "message"}
  {"type": "done"}
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from ..config import AgentSettings
from .tools import Runtime, ToolContext, ToolRegistry

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are Labkeeper, the assistant built into a network lab management tool. Engineers use you to find devices, check their live health, book and release testbeds, test console lines, and organise racks and floor plans.

How the lab is modelled:
- Devices (switches, access points, cameras, servers, jump hosts and traffic generators) come from testbed YAML files. Live status comes from configured status providers: online, alerting, offline, dormant or unknown.
- A testbed is one leaf YAML file. Testbeds often share hardware (a combined testbed extends several single-stack files). Booking a testbed locks its exclusive devices, so every other testbed that shares one of them shows as "blocked" until the booking ends. Shared support devices (jump hosts, servers, traffic generators) never block anything.
- Racks hold devices at U positions. Floor plans are grids of racks, benches and pillars; rack cells join to racks by name (prefix + row + column, e.g. B1_ + C + 03 = B1_C03).

How to work:
- Get facts from tools; never invent device names, serials, statuses, owners or times. If a lookup fails, say what you searched for.
- Bookings: when the user asks to book without a duration, use 2 hours and say so. When a booking conflicts, explain who holds what and until when, and offer the next free slot or an alternative testbed from find_available_testbeds. Never release or override someone else's booking unless the user explicitly confirms it in this conversation; then use force=true.
- Times: tool results are UTC. Present times in the user's local time zone given in the context block, in a friendly form ("until 5:30 PM", "tomorrow 9 AM"). When you pass times to tools, use ISO 8601 with the user's UTC offset.
- Console tests open real console lines briefly; run them when asked or when diagnosing an unreachable device.
- Floor plans: when the user shares a floor map image, read it carefully (row labels top to bottom, column labels left to right, every rack/empty rack/bench/pillar/storage cell at its top-left position with spans), check existing rack names with list_racks to choose rack_prefix, tell the user what you found (grid size, rack count, anything ambiguous) and save it with save_floor_plan once they agree, or right away if they asked you to.
- Be brief and concrete. Lead with the answer. Use short markdown tables for lists of devices or testbeds (name, status, key fact), and link nothing you were not given. Mention the device or testbed names exactly as they appear so the UI can link them."""


@dataclass
class Conversation:
    id: str
    actor: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    updated: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ChatService:
    def __init__(self, settings: AgentSettings, registry: ToolRegistry, runtime: Runtime):
        self.settings = settings
        self.registry = registry
        self.runtime = runtime
        self._conversations: OrderedDict[str, Conversation] = OrderedDict()
        self._client = None
        self._unavailable: str | None = None

    # -- availability ------------------------------------------------------------------------------------
    def availability(self) -> tuple[bool, str | None]:
        if not self.settings.enabled:
            return False, "the assistant is disabled in the config (agent.enabled)"
        if not self.settings.model.strip():
            return False, "set agent.model to a model supported by your AI account"
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False, "the anthropic package is not installed"
        if self._client is None and not _has_anthropic_credentials():
            return False, "no Anthropic credentials: set ANTHROPIC_API_KEY on the Labkeeper server"
        if self._unavailable:
            return False, self._unavailable
        return True, None

    def client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.AsyncAnthropic()
        return self._client

    # -- conversations -----------------------------------------------------------------------------------
    def _get(self, conv_id: str | None, actor: str) -> Conversation:
        cutoff = time.monotonic() - 12 * 3600
        for cid in [c for c, v in self._conversations.items() if v.updated < cutoff]:
            self._conversations.pop(cid, None)
        if conv_id and conv_id in self._conversations and self._conversations[conv_id].actor == actor:
            conv = self._conversations[conv_id]
            self._conversations.move_to_end(conv_id)
            return conv
        conv = Conversation(id=conv_id if conv_id and conv_id not in self._conversations else uuid.uuid4().hex, actor=actor)
        self._conversations[conv.id] = conv
        while len(self._conversations) > 300:
            self._conversations.popitem(last=False)
        return conv

    def forget(self, conv_id: str, actor: str) -> None:
        conv = self._conversations.get(conv_id)
        if conv and conv.actor == actor:
            self._conversations.pop(conv_id, None)

    # -- main loop -----------------------------------------------------------------------------------------
    async def stream(
        self,
        *,
        conversation_id: str | None,
        text: str,
        actor: str,
        context: dict[str, Any],
        attachments: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        conv = self._get(conversation_id, actor)
        yield {"type": "conversation", "id": conv.id}
        ok, reason = self.availability()
        if not ok:
            yield {"type": "error", "message": f"Assistant unavailable: {reason}"}
            yield {"type": "done"}
            return
        import anthropic

        tz = int(context.get("tz_offset_minutes") or 0)
        ctx = ToolContext(actor=actor, via="agent", runtime=self.runtime, tz_offset_minutes=tz)
        sign = "+" if tz >= 0 else "-"
        offset = f"{sign}{abs(tz) // 60:02d}:{abs(tz) % 60:02d}"
        context_lines = [
            f"user: {actor}",
            f"local time: {context.get('local_time') or 'unknown'} (UTC{offset})",
        ]
        if context.get("page"):
            context_lines.append(f"page the user is looking at: {context['page']}")
        content: list[dict[str, Any]] = [{"type": "text", "text": "<context>\n" + "\n".join(context_lines) + "\n</context>"}]
        for att in attachments or []:
            block = _attachment_block(att)
            if block:
                content.append(block)
        content.append({"type": "text", "text": text.strip() or "(no text)"})

        async with conv.lock:
            conv.updated = time.monotonic()
            history_len = len(conv.messages)
            conv.messages.append({"role": "user", "content": content})
            tools = self.registry.anthropic_tools(eager_input_streaming=True)
            extra: dict[str, Any] = {}
            if self.settings.use_fallbacks:
                extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
            json_retries = 0
            try:
                for _round in range(self.settings.max_tool_rounds):
                    try:
                        async with self.client().beta.messages.stream(
                            model=self.settings.model,
                            max_tokens=self.settings.max_tokens,
                            system=SYSTEM_PROMPT,
                            tools=tools,
                            messages=conv.messages,
                            output_config={"effort": self.settings.effort},
                            cache_control={"type": "ephemeral"},
                            **extra,
                        ) as stream:
                            thinking_announced = False
                            async for event in stream:
                                if event.type == "text":
                                    yield {"type": "text", "delta": event.text}
                                elif event.type == "content_block_start":
                                    btype = getattr(event.content_block, "type", "")
                                    if btype == "thinking" and not thinking_announced:
                                        thinking_announced = True
                                        yield {"type": "status", "text": "thinking"}
                                    elif btype == "tool_use":
                                        yield {"type": "status", "text": f"preparing {event.content_block.name}"}
                            message = await stream.get_final_message()
                        json_retries = 0
                    except ValueError:
                        # Tool input JSON the SDK could not parse at all: no tool_use id to answer, re-issue.
                        json_retries += 1
                        if json_retries > 2:
                            raise
                        continue

                    conv.messages.append({"role": "assistant", "content": message.content})
                    if message.stop_reason == "refusal":
                        yield {"type": "text", "delta": "\n\nI can't help with that request."}
                        break
                    if message.stop_reason == "pause_turn":
                        continue
                    tool_uses = [b for b in message.content if b.type == "tool_use"]
                    if not tool_uses:
                        break
                    if message.stop_reason == "max_tokens":
                        yield {"type": "error", "message": "The response was cut off; please ask again more narrowly."}
                        # keep history valid: answer the dangling tool calls
                        conv.messages.append(
                            {
                                "role": "user",
                                "content": [
                                    {"type": "tool_result", "tool_use_id": b.id, "is_error": True,
                                     "content": "not run: response truncated"}
                                    for b in tool_uses
                                ],
                            }
                        )
                        break

                    results = []
                    for block in tool_uses:
                        spec = self.registry.tools.get(block.name)
                        yield {
                            "type": "tool_start",
                            "id": block.id,
                            "name": block.name,
                            "title": spec.title if spec else block.name,
                            "input": _preview_input(block.input),
                        }
                        outcome = await self.registry.execute(block.name, block.input, ctx)
                        yield {
                            "type": "tool_end",
                            "id": block.id,
                            "name": block.name,
                            "ok": outcome.ok,
                            "summary": outcome.summary,
                            "error": outcome.error,
                            "mutating": bool(spec and spec.mutating),
                        }
                        results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": outcome.as_content(),
                                **({"is_error": True} if not outcome.ok else {}),
                            }
                        )
                    conv.messages.append({"role": "user", "content": results})
                else:
                    yield {"type": "text", "delta": "\n\n(Stopped after too many steps. Ask me to continue if needed.)"}
            except anthropic.AuthenticationError:
                self._unavailable = "Anthropic credentials are missing or invalid (set ANTHROPIC_API_KEY)"
                del conv.messages[history_len:]
                yield {"type": "error", "message": self._unavailable}
            except anthropic.RateLimitError:
                del conv.messages[history_len:]
                yield {"type": "error", "message": "The Claude API is rate limiting requests. Try again in a minute."}
            except anthropic.BadRequestError as exc:
                del conv.messages[history_len:]
                yield {"type": "error", "message": f"Claude rejected the request: {exc.message}"}
            except anthropic.APIStatusError as exc:
                del conv.messages[history_len:]
                yield {"type": "error", "message": f"Claude API error ({exc.status_code}). Try again shortly."}
            except anthropic.APIConnectionError:
                del conv.messages[history_len:]
                yield {"type": "error", "message": "Cannot reach the Claude API from the Labkeeper server."}
            except ValueError:
                del conv.messages[history_len:]
                yield {"type": "error", "message": "The model produced malformed tool input twice; please rephrase."}
            _trim_history(conv)
        yield {"type": "done"}


def _has_anthropic_credentials() -> bool:
    """Mirror the SDK's credential sources: API key, auth token, `ant auth login` profile, or WIF."""
    import os
    from pathlib import Path

    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    if os.environ.get("ANTHROPIC_PROFILE") or os.environ.get("ANTHROPIC_FEDERATION_RULE_ID"):
        return True
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return (config_home / "anthropic").is_dir()


def _preview_input(data: Any) -> Any:
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            if k == "plan" and isinstance(v, dict):
                out[k] = {"name": v.get("name"), "cells": len(v.get("cells") or [])}
            elif isinstance(v, str) and len(v) > 200:
                out[k] = v[:200] + "…"
            else:
                out[k] = v
        return out
    return data


ALLOWED_ATTACHMENTS = {"image/png", "image/jpeg", "image/webp", "image/gif", "application/pdf"}


def _attachment_block(att: dict[str, Any]) -> dict[str, Any] | None:
    media = str(att.get("media_type") or "")
    data = str(att.get("data") or "")
    if media not in ALLOWED_ATTACHMENTS or not data:
        return None
    try:
        raw = base64.b64decode(data, validate=True)
    except Exception:
        return None
    if len(raw) > (30 if media == "application/pdf" else 5) * 1024 * 1024:
        return None
    if media == "application/pdf":
        return {"type": "document", "source": {"type": "base64", "media_type": media, "data": data}}
    return {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}


def _trim_history(conv: Conversation, max_user_turns: int = 30) -> None:
    """Keep the conversation bounded by dropping the oldest *complete* exchanges."""
    starts = [
        i
        for i, m in enumerate(conv.messages)
        if m["role"] == "user" and isinstance(m["content"], list) and m["content"]
        and isinstance(m["content"][0], dict) and m["content"][0].get("type") == "text"
        and str(m["content"][0].get("text", "")).startswith("<context>")
    ]
    if len(starts) > max_user_turns:
        cut = starts[len(starts) - max_user_turns]
        del conv.messages[:cut]
