"""Real MCP handshake: spawn the server over stdio and drive it with the official client."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest

from conftest import HUMAN
from twinmeup.models import LessonKind, LessonOrigin
from twinmeup.service import TwinService

mcp = pytest.importorskip("mcp")
from mcp.client.session import ClientSession  # noqa: E402
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402


def _env(home: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "TWINMEUP_ACTOR")}
    env["TWINMEUP_HOME"] = str(home)
    return env


def _seed(home: Path) -> None:
    service = TwinService.open(home)
    service.propose_lesson(
        workflow="quote-review",
        kind=LessonKind.ESCALATION,
        statement="Bring any quote above budget back to Alex.",
        origin=LessonOrigin.WRITTEN_RULE,
        actor=HUMAN,
        confirm=True,
    )
    service.grant(workflow="quote-review", actions=["draft.prepare"], actor=HUMAN)
    service.close()


async def _session(home: Path) -> dict[str, Any]:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "twinmeup.mcp_server", "--actor", "agent:hermes", "--principal", "Alex"],
        env=_env(home),
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        init = await session.initialize()
        listed = await session.list_tools()
        brief = await session.call_tool(
            "twin_brief",
            {"workflow": "quote-review", "objective": "Compare quotes", "actions": ["email.send"]},
        )
        ask = await session.call_tool(
            "twin_authorise", {"workflow": "quote-review", "action": "email.send"}
        )
        bad = await session.call_tool("twin_brief", {"workflow": "Bad Slug", "objective": "x"})
        proposed = await session.call_tool(
            "twin_propose_lesson",
            {"workflow": "quote-review", "kind": "criterion", "statement": "Check insurance."},
        )
        return {
            "init": init,
            "tools": listed.tools,
            "brief": brief,
            "ask": ask,
            "bad": bad,
            "proposed": proposed,
        }


def test_stdio_handshake_and_calls(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _seed(home)
    out = anyio.run(_session, home)
    assert out["init"].server_info.name == "twinmeup"
    assert "twin_brief" in (out["init"].instructions or "")
    names = {tool.name for tool in out["tools"]}
    assert "twin_brief" in names and not any("confirm" in n or "grant" in n for n in names)
    hints = {tool.name: tool.annotations.read_only_hint for tool in out["tools"]}
    assert hints["twin_brief"] is True and hints["twin_propose_lesson"] is False
    brief_text = out["brief"].content[0].text
    assert "delegate of Alex" in brief_text and "Bring any quote above budget" in brief_text
    assert "Not authorised, ask first: email.send." in brief_text
    assert out["ask"].structured_content["decision"] == "ask"
    assert out["bad"].is_error is True and "invalid input" in out["bad"].content[0].text
    proposed = json.loads(out["proposed"].content[0].text)
    assert proposed["status"] == "candidate"
    service = TwinService.open(home)
    assert service.store.verify_chain() == []
    actors = {e["actor"] for e in service.store.events() if e["action"] == "lesson.proposed"}
    assert actors == {"agent:hermes"}
    service.close()


def test_refuses_to_run_as_a_human(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "twinmeup.mcp_server", "--actor", HUMAN],
        env=_env(tmp_path / "home"),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 2 and "never runs as a human" in result.stderr


def test_health_check(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "twinmeup.mcp_server", "--actor", "agent:x", "--health-check"],
        env=_env(tmp_path / "home"),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0 and json.loads(result.stdout)["ok"] is True
