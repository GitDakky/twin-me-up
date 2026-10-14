"""MCP adapter: serves the twin protocol over stdio.

Works with any MCP host: Hermes, Claude Code, Codex, Cursor, or a Pi extension bridge.
Read-only tools carry readOnlyHint so hosts can auto-approve them; writes only ever
create records or candidate lessons. The server always runs as an agent actor and
refuses to start as a human principal, so no agent surface can confirm or grant.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any

import anyio
from mcp import types
from mcp.server.lowlevel.server import Server
from mcp.server.stdio import stdio_server
from pydantic import ValidationError

from . import __version__
from .errors import TwinMeUpError
from .identity import load_settings
from .models import is_human, validate_actor
from .protocol import BY_NAME, SERVER_INSTRUCTIONS, TOOLS, call_tool, input_schema
from .service import TwinService, default_home

log = logging.getLogger("twinmeup.mcp")


def _error(message: str) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=message)], is_error=True
    )


def build_server(service: TwinService, actor: str, principal: str) -> Server[Any]:
    write_lock = threading.Lock()
    tools = [
        types.Tool(
            name=spec.name,
            title=spec.title,
            description=spec.description,
            input_schema=input_schema(spec),
            annotations=types.ToolAnnotations(
                readOnlyHint=spec.read_only,
                destructiveHint=False,
                idempotentHint=spec.read_only,
                openWorldHint=False,
            ),
        )
        for spec in TOOLS
    ]

    def run(name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        spec = BY_NAME.get(name)
        if spec is not None and not spec.read_only:
            with write_lock:
                return call_tool(service, actor, name, arguments, principal=principal)
        return call_tool(service, actor, name, arguments, principal=principal)

    async def on_list_tools(_ctx: Any, _params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tools)

    async def on_call_tool(_ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        try:
            result = await anyio.to_thread.run_sync(run, params.name, params.arguments)
        except ValidationError as exc:
            return _error("invalid input: " + "; ".join(e["msg"] for e in exc.errors()[:5]))
        except (TwinMeUpError, ValueError) as exc:
            return _error(str(exc))
        text = (
            result["brief"]
            if params.name == "twin_brief"
            else json.dumps(result, ensure_ascii=False, default=str)
        )
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)],
            structured_content=json.loads(json.dumps(result, default=str)),
        )

    return Server(
        "twinmeup",
        version=__version__,
        instructions=SERVER_INSTRUCTIONS,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="twinmeup-mcp", description="Serve the twin-me-up protocol over MCP stdio."
    )
    parser.add_argument("--home", default=os.environ.get("TWINMEUP_HOME"), help="Store directory.")
    parser.add_argument(
        "--actor",
        default=os.environ.get("TWINMEUP_ACTOR", "agent:assistant"),
        help="Agent identity, agent:<name>. Default agent:assistant.",
    )
    parser.add_argument(
        "--principal",
        default=os.environ.get("TWINMEUP_PRINCIPAL"),
        help="How briefs name the principal. Defaults to the name given at `twinmeup init`.",
    )
    parser.add_argument("--health-check", action="store_true", help="Verify the store and exit.")
    args = parser.parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=os.environ.get("TWINMEUP_LOG_LEVEL", "WARNING"))

    try:
        validate_actor(args.actor)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if is_human(args.actor):
        print("error: the agent surface never runs as a human principal", file=sys.stderr)
        return 2

    home = Path(args.home) if args.home else default_home()
    settings = load_settings(home)
    principal = args.principal or (settings.principal if settings else "the principal")
    service = TwinService.open(home)
    try:
        if args.health_check:
            problems = service.store.verify_chain()
            print(json.dumps({"ok": not problems, "store": str(service.store.path)}))
            return 0 if not problems else 1
        server = build_server(service, args.actor, principal)

        async def serve() -> None:
            async with stdio_server() as (read, write):
                await server.run(read, write, server.create_initialization_options())

        anyio.run(serve)
    except KeyboardInterrupt:
        pass
    finally:
        service.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
