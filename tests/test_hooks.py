from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from conftest import AGENT, HUMAN
from twinmeup.cli import app
from twinmeup.hooks import ActionMap, Dialect, pre_tool_call
from twinmeup.service import TwinService

MAP = ActionMap.model_validate(
    {
        "workflow": "quote-review",
        "rules": [
            {"tool": "mcp_crm_send_email", "action": "email.send"},
            {"tool": "terminal", "input_regex": r"git push", "action": "repo.push"},
            {"tool": "mcp_bank_.*", "action": "payment.make", "workflow": "finance"},
        ],
    }
)


def payload(tool: str, tool_input: Any = None) -> dict[str, Any]:
    return {"hook_event_name": "pre_tool_call", "tool_name": tool, "tool_input": tool_input or {}}


def test_matching_rules() -> None:
    assert MAP.match("mcp_crm_send_email", {}) == ("quote-review", "email.send")
    assert MAP.match("terminal", {"command": "git push origin main"}) == (
        "quote-review",
        "repo.push",
    )
    assert MAP.match("terminal", {"command": "ls"}) is None
    assert MAP.match("mcp_bank_transfer", {}) == ("finance", "payment.make")
    assert MAP.match("mcp_crm_send_email_draft", {}) is None  # full match, not prefix
    with pytest.raises(ValidationError):
        ActionMap.model_validate({"rules": [{"tool": "(", "action": "x.y"}]})


def test_unmapped_calls_are_left_to_the_host(service: TwinService) -> None:
    response = pre_tool_call(service, AGENT, payload("read_file"), MAP, Dialect.HERMES)
    assert response.output == {} and response.exit_code == 0
    assert service.store.events() == []  # nothing checked, nothing recorded


def test_ungranted_actions_escalate_in_each_dialect(service: TwinService) -> None:
    hermes = pre_tool_call(
        service, AGENT, payload("mcp_crm_send_email"), MAP, Dialect.HERMES, "Alex"
    )
    assert hermes.exit_code == 0 and hermes.output["action"] == "approve"
    assert "Alex must approve" in hermes.output["message"]
    assert hermes.output["rule_key"] == "twinmeup:quote-review:email.send"
    claude = pre_tool_call(service, AGENT, payload("mcp_crm_send_email"), MAP, Dialect.CLAUDE)
    assert claude.output["hookSpecificOutput"]["permissionDecision"] == "ask"
    block = pre_tool_call(service, AGENT, payload("mcp_crm_send_email"), MAP, Dialect.BLOCK)
    assert block.exit_code == 2 and block.output["decision"] == "block"


def test_granted_actions_pass_and_are_audited(service: TwinService) -> None:
    service.grant(workflow="quote-review", actions=["email.send"], actor=HUMAN)
    response = pre_tool_call(service, AGENT, payload("mcp_crm_send_email"), MAP, Dialect.HERMES)
    assert response.output == {} and response.exit_code == 0
    assert [e["action"] for e in service.store.events()][-1] == "authorise.allow"


def test_reserved_twin_actions_can_never_be_granted(service: TwinService) -> None:
    from twinmeup.errors import TwinMeUpError

    with pytest.raises(TwinMeUpError, match="can never be granted"):
        service.grant(
            workflow="quote-review", actions=["email.send", "twin.human-only"], actor=HUMAN
        )
    assert service.store.events() == []  # nothing partially granted
    guard = ActionMap.model_validate(
        {
            "workflow": "general",
            "rules": [
                {
                    "tool": "terminal",
                    "input_regex": r"twinmeup\b[^\n]*--actor[= ]+human:",
                    "action": "twin.human-only",
                }
            ],
        }
    )
    forged = payload("terminal", {"command": "twinmeup --actor human:alex grant --actions x.y"})
    response = pre_tool_call(service, AGENT, forged, guard, Dialect.BLOCK)
    assert response.exit_code == 2 and "twin.human-only" in response.output["reason"]
    assert "may not act as" in response.output["reason"]


def test_missing_workflow_fails_closed(service: TwinService) -> None:
    loose = ActionMap.model_validate({"rules": [{"tool": "x", "action": "a.b"}]})
    response = pre_tool_call(service, AGENT, payload("x"), loose, Dialect.HERMES)
    assert response.exit_code == 2 and "no workflow" in response.output["reason"]


def test_cli_hook_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TWINMEUP_HOME", str(tmp_path / "home"))
    map_path = tmp_path / "map.json"
    map_path.write_text(MAP.model_dump_json(), encoding="utf-8")
    runner = CliRunner()
    args = ["--actor", "agent:hermes", "hook", "pre-tool-call", "--map", str(map_path)]
    escalated = runner.invoke(app, args, input=json.dumps(payload("mcp_crm_send_email")))
    assert escalated.exit_code == 0 and json.loads(escalated.output)["action"] == "approve"
    blocked = runner.invoke(
        app, [*args, "--dialect", "block"], input=json.dumps(payload("mcp_crm_send_email"))
    )
    assert blocked.exit_code == 2
    passed = runner.invoke(app, args, input=json.dumps(payload("read_file")))
    assert passed.exit_code == 0 and passed.output == ""


def test_cli_hook_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    broken = tmp_path / "not-a-directory"
    broken.write_text("x", encoding="utf-8")
    monkeypatch.setenv("TWINMEUP_HOME", str(broken))  # the store cannot open
    map_path = tmp_path / "map.json"
    map_path.write_text(MAP.model_dump_json(), encoding="utf-8")
    runner = CliRunner()
    args = ["--actor", "agent:hermes", "hook", "pre-tool-call", "--map", str(map_path)]
    governed = runner.invoke(app, args, input=json.dumps(payload("mcp_crm_send_email")))
    assert governed.exit_code == 2
    assert '"decision": "block"' in governed.output and "unavailable" in governed.output
    ungoverned = runner.invoke(app, args, input=json.dumps(payload("read_file")))
    assert ungoverned.exit_code == 0 and ungoverned.output == ""  # store never touched
    no_actor = runner.invoke(
        app,
        ["hook", "pre-tool-call", "--map", str(map_path)],
        input=json.dumps(payload("mcp_crm_send_email")),
    )
    assert no_actor.exit_code == 2
    bad_map = runner.invoke(
        app,
        [*args[:4], "--map", str(tmp_path / "missing.json")],
        input=json.dumps(payload("read_file")),
    )
    assert bad_map.exit_code == 2  # cannot tell what is governed, so block
