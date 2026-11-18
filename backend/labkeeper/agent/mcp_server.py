"""MCP server exposing the Labkeeper tool registry to external agents (Claude Code, Claude Desktop...).

Two transports:
  * stdio:            ``labkeeper mcp`` (runs against the local database; identity from $LABKEEPER_USER)
  * streamable HTTP:  mounted at ``/mcp`` on the Labkeeper server (identity from the user header)
"""

from __future__ import annotations

import json
import os
from typing import Any

import mcp_types as types
from mcp.server.lowlevel import Server

from .. import __version__
from .tools import Runtime, ToolContext, ToolRegistry

INSTRUCTIONS = (
    "Labkeeper manages network lab devices and testbed bookings. Use lab_overview for a snapshot, "
    "find_available_testbeds before book_testbed, and get_device / test_console to diagnose devices. "
    "Booking a testbed blocks every testbed that shares its hardware. Times are UTC ISO 8601."
)


def build_server(registry: ToolRegistry, runtime: Runtime, default_actor: str | None = None) -> Server:
    actor_default = default_actor or os.environ.get("LABKEEPER_USER") or "mcp"
    header = runtime.settings.auth.user_header.lower()

    def actor_for(ctx: Any) -> str:
        request = getattr(ctx, "request", None)
        headers = getattr(request, "headers", None)
        if headers is not None:
            value = headers.get(header)
            if value:
                return str(value)[:200]
        return actor_default

    async def on_list_tools(ctx, params) -> types.ListToolsResult:
        tools = []
        for spec in registry.tools.values():
            tools.append(
                types.Tool(
                    name=spec.name,
                    title=spec.title.capitalize(),
                    description=spec.description,
                    input_schema=spec.input_schema(),
                    annotations=types.ToolAnnotations(
                        read_only_hint=not spec.mutating,
                        destructive_hint=spec.name in {"release_testbed"},
                        idempotent_hint=not spec.mutating,
                        open_world_hint=spec.name in {"test_console", "get_switch_ports", "refresh_status"},
                    ),
                )
            )
        return types.ListToolsResult(tools=tools)

    async def on_call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        tctx = ToolContext(actor=actor_for(ctx), via="mcp", runtime=runtime)
        outcome = await registry.execute(params.name, params.arguments or {}, tctx)
        if outcome.ok:
            text = json.dumps(outcome.data, default=str, indent=1)
            return types.CallToolResult(content=[types.TextContent(type="text", text=text)])
        return types.CallToolResult(content=[types.TextContent(type="text", text=f"Error: {outcome.error}")], is_error=True)

    return Server(
        "labkeeper",
        version=__version__,
        title="Labkeeper",
        description="Testbed inventory, live device status and bookings",
        instructions=INSTRUCTIONS,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


async def run_stdio(registry: ToolRegistry, runtime: Runtime) -> None:
    from mcp.server.stdio import stdio_server

    server = build_server(registry, runtime)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
