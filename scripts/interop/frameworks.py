"""Drive the Twin Me Up MCP server through third-party agent frameworks, with no model calls.

Checks that each framework's own MCP client can list the twin's tools and call one:
Microsoft Agent Framework (MCPStdioTool), OpenAI Agents SDK (MCPServerStdio) and LangChain
(langchain-mcp-adapters). Run in an environment that has those packages installed:

    TWINMEUP_MCP_COMMAND=$(which twinmeup-mcp) TWINMEUP_HOME=/tmp/twin python frameworks.py
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any

COMMAND = os.environ.get("TWINMEUP_MCP_COMMAND", "twinmeup-mcp")
ENV = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
ASK = {"workflow": "general", "action": "email.send"}


def _decision(raw: Any) -> str:
    """The decision field, however deeply the framework nests or escapes the result."""
    text = raw if isinstance(raw, str) else json.dumps(raw, default=str)
    found = re.search(r"decision\W+(allow|ask)", text)
    return found.group(1) if found else text[:80]


async def ms_agent_framework() -> dict[str, Any]:
    from agent_framework import MCPStdioTool

    tool = MCPStdioTool(
        name="twin", command=COMMAND, args=["--actor", "agent:ms-agent-framework"], env=ENV
    )
    async with tool:
        names = [f.name for f in tool.functions]
        target = next(f for f in tool.functions if f.name == "twin_authorise")
        result = await target.invoke(**ASK)
    return {"tools": len(names), "authorise": _decision(result)}


async def openai_agents() -> dict[str, Any]:
    from agents.mcp import MCPServerStdio

    params = {"command": COMMAND, "args": ["--actor", "agent:openai-agents"], "env": ENV}
    async with MCPServerStdio(params=params) as server:  # type: ignore[arg-type]
        tools = await server.list_tools()
        result = await server.call_tool("twin_authorise", ASK)
    return {"tools": len(tools), "authorise": _decision(result.model_dump())}


async def langchain() -> dict[str, Any]:
    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient(
        {
            "twin": {
                "command": COMMAND,
                "args": ["--actor", "agent:langchain"],
                "env": ENV,
                "transport": "stdio",
            }
        }
    )
    tools = await client.get_tools()
    target = next(t for t in tools if t.name == "twin_authorise")
    result = await target.ainvoke(ASK)
    return {"tools": len(tools), "authorise": _decision(result)}


async def main() -> None:
    report = {}
    for name, probe in (
        ("ms-agent-framework", ms_agent_framework),
        ("openai-agents", openai_agents),
        ("langchain", langchain),
    ):
        try:
            report[name] = await probe()
        except Exception as exc:
            report[name] = {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(report, indent=2))
    ok = all(r.get("tools") == 10 and r.get("authorise") == "ask" for r in report.values())
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
