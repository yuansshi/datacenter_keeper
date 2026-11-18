"""Tool registry + the chat loop, with a scripted stand-in for the Claude API."""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import select

from labkeeper.agent import ChatService, Runtime, ToolContext, registry
from labkeeper.models import Booking


def ctx(settings, actor="alice"):
    return ToolContext(actor=actor, via="agent", runtime=Runtime(settings=settings))


async def test_tool_input_is_validated(settings, imported):
    out = await registry.execute("book_testbed", {"testbed": "red_smoke", "duration_minutes": 1}, ctx(settings))
    assert not out.ok and "duration_minutes" in out.error
    out = await registry.execute("book_testbed", {"testbed": "red_smoke", "bogus": 1}, ctx(settings))
    assert not out.ok and "bogus" in out.error


async def test_book_and_conflict_through_tools(settings, imported, session):
    ok = await registry.execute("book_testbed", {"testbed": "red_smoke", "duration_minutes": 30}, ctx(settings))
    assert ok.ok and ok.summary.startswith("Booked red_smoke")
    clash = await registry.execute("book_testbed", {"testbed": "red_blue"}, ctx(settings, "bob"))
    assert not clash.ok and "booked by alice" in clash.error
    avail = await registry.execute("find_available_testbeds", {"duration_minutes": 60}, ctx(settings, "bob"))
    assert [t["name"] for t in avail.data["available"]] == ["blue_smoke"]
    # courtesy release: bob may release alice's booking; the tool says whose it was
    freed = await registry.execute("release_testbed", {"testbed": "red_smoke"}, ctx(settings, "bob"))
    assert freed.ok and "alice" in freed.summary
    settings.booking.release_policy = "owner"
    await registry.execute("book_testbed", {"testbed": "red_smoke"}, ctx(settings, "alice"))
    denied = await registry.execute("release_testbed", {"testbed": "red_smoke"}, ctx(settings, "bob"))
    assert not denied.ok and "belongs to alice" in denied.error


async def test_device_and_overview_tools(settings, imported):
    dev = await registry.execute("get_device", {"device": "Q2RR-0000-0001"}, ctx(settings))
    assert dev.ok and dev.data["name"] == "RED_01" and dev.data["rack"] == "LAB_A01 U10"
    assert "password" not in str(dev.data)
    overview = await registry.execute("lab_overview", {}, ctx(settings))
    assert overview.data["testbeds"]["total"] == 3


def test_tool_schemas_are_anthropic_ready():
    tools = registry.anthropic_tools()
    assert len(tools) == len(registry.tools) >= 20
    for t in tools:
        assert t["input_schema"]["type"] == "object" and t["eager_input_streaming"] is True
        assert "title" not in t["input_schema"]


class FakeStream:
    def __init__(self, message):
        self.message = message

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        async def gen():
            for block in self.message.content:
                if block.type == "text":
                    yield SimpleNamespace(type="text", text=block.text)

        return gen()

    async def get_final_message(self):
        return self.message


class FakeClient:
    """Turn 1: call book_testbed. Turn 2: answer in text."""

    def __init__(self):
        self.requests = []
        messages = SimpleNamespace(stream=self._stream)
        self.beta = SimpleNamespace(messages=messages)

    def _stream(self, **kwargs):
        self.requests.append(kwargs | {"messages": list(kwargs["messages"])})  # snapshot: the list keeps growing
        if len(self.requests) == 1:
            block = SimpleNamespace(type="tool_use", id="toolu_1", name="book_testbed", input={"testbed": "blue_smoke", "duration_minutes": 60})
            return FakeStream(SimpleNamespace(content=[block], stop_reason="tool_use"))
        text = SimpleNamespace(type="text", text="Booked `blue_smoke` for an hour.")
        return FakeStream(SimpleNamespace(content=[text], stop_reason="end_turn"))


async def test_chat_loop_runs_tools_and_streams_text(settings, imported, session, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    settings.agent.enabled = True
    settings.agent.model = "example-model"
    settings.agent.use_fallbacks = True
    svc = ChatService(settings.agent, registry, Runtime(settings=settings))
    fake = FakeClient()
    svc._client = fake
    events = [ev async for ev in svc.stream(conversation_id=None, text="book blue for an hour", actor="carol", context={"tz_offset_minutes": -480})]
    types = [e["type"] for e in events]
    assert types[0] == "conversation" and types[-1] == "done"
    assert "tool_start" in types and "tool_end" in types
    tool_end = next(e for e in events if e["type"] == "tool_end")
    assert tool_end["ok"] and tool_end["mutating"]
    assert "".join(e["delta"] for e in events if e["type"] == "text") == "Booked `blue_smoke` for an hour."
    # second request carried the tool result back, with fallbacks + caching on
    second = fake.requests[1]
    assert second["messages"][-1]["content"][0]["type"] == "tool_result"
    assert second["fallbacks"] == "default" and second["cache_control"] == {"type": "ephemeral"}
    assert second["model"] == "example-model"
    session.expire_all()
    b = session.scalar(select(Booking).where(Booking.owner == "carol"))
    assert b is not None and b.created_via == "agent"


async def test_chat_reports_missing_credentials(settings, monkeypatch, tmp_path):
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE", "ANTHROPIC_FEDERATION_RULE_ID"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    settings.agent.enabled = True
    settings.agent.model = "example-model"
    settings.agent.use_fallbacks = True
    svc = ChatService(settings.agent, registry, Runtime(settings=settings))
    events = [ev async for ev in svc.stream(conversation_id=None, text="hi", actor="a", context={})]
    assert any(e["type"] == "error" and "ANTHROPIC_API_KEY" in e["message"] for e in events)
