"""Durable approvals, limited-use grants and idempotent writes."""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from conftest import AGENT, HUMAN, Clock, quote_episode
from twinmeup.cli import app
from twinmeup.errors import AuthorityError, StateError
from twinmeup.hooks import ActionMap, Dialect, pre_tool_call
from twinmeup.models import SCHEMA_VERSION, ApprovalRequest, Episode, Grant, Receipt
from twinmeup.protocol import call_tool
from twinmeup.service import TwinService
from twinmeup.store import Store

PUSH_MAP = ActionMap.model_validate(
    {
        "workflow": "release",
        "rules": [{"tool": "terminal", "action": "repo.push", "input_regex": "git\\s+push"}],
    }
)


def _request(service: TwinService, **overrides: Any) -> dict[str, Any]:
    arguments = {"workflow": "release", "action": "repo.push", "summary": "Push the hotfix."}
    return call_tool(service, AGENT, "twin_request_approval", arguments | overrides)


def _push(service: TwinService, call_id: str | None) -> int:
    payload: dict[str, Any] = {"tool_name": "terminal", "tool_input": {"command": "git push"}}
    if call_id is not None:
        payload["tool_use_id"] = call_id
    return pre_tool_call(service, AGENT, payload, PUSH_MAP, Dialect.BLOCK).exit_code


def test_an_approval_covers_one_enforced_attempt(service: TwinService, clock: Clock) -> None:
    pending = _request(service)
    assert pending["status"] == "pending"
    assert "twinmeup approvals approve" in pending["note"]
    service.approve(pending["approval_id"], actor=HUMAN)

    status = call_tool(
        service, AGENT, "twin_approval_status", {"approval_id": pending["approval_id"]}
    )
    assert status["status"] == "approved" and status["uses_left"] == 1
    grant = service.store.get(Grant, status["grant_id"])
    assert grant.actions == ["repo.push"] and grant.max_uses == 1
    assert grant.expires_at == clock() + timedelta(hours=1)

    # Advisory checks never spend the approval.
    for _ in range(3):
        check = service.authorise(workflow="release", action="repo.push", actor=AGENT)
        assert check.decision.value == "allow"
    # The enforced attempt spends it once; the same attempt may be re-checked safely.
    first = service.authorise(workflow="release", action="repo.push", actor=AGENT, attempt_id="a1")
    assert first.decision.value == "allow" and first.attempt_id == "a1"
    again = service.authorise(workflow="release", action="repo.push", actor=AGENT, attempt_id="a1")
    assert again.decision.value == "allow" and again.reason == "Already allowed for this attempt."
    second = service.authorise(workflow="release", action="repo.push", actor=AGENT, attempt_id="a2")
    assert second.decision.value == "ask" and "twin_request_approval" in second.reason
    assert service.store.get(Grant, grant.id).uses_left == 0
    assert service.approval_view(service.approvals()[0])["status"] == "used"


def test_hook_spends_an_approval_once_per_tool_call(service: TwinService) -> None:
    assert _push(service, "toolu_01A") == 2
    service.approve(_request(service)["approval_id"], actor=HUMAN)
    assert _push(service, "toolu_01A") == 0
    assert _push(service, "toolu_01B") == 2  # a new call needs a new approval
    service.approve(_request(service)["approval_id"], actor=HUMAN)
    assert _push(service, None) == 0  # hosts without call ids work the same way
    assert _push(service, None) == 2


def test_a_reused_call_id_never_rides_on_a_spent_approval(service: TwinService) -> None:
    service.approve(_request(service)["approval_id"], actor=HUMAN)
    first = {
        "tool_name": "terminal",
        "tool_input": {"command": "git push"},
        "tool_call_id": "call_0",
    }
    assert pre_tool_call(service, AGENT, first, PUSH_MAP, Dialect.BLOCK).exit_code == 0
    # Some providers reuse call_0 every turn and every session. Each hook run is a new
    # attempt, so neither the same push again nor a different one can use the spent approval.
    assert pre_tool_call(service, AGENT, first, PUSH_MAP, Dialect.BLOCK).exit_code == 2
    forced = {**first, "tool_input": {"command": "git push --force"}}
    assert pre_tool_call(service, AGENT, forced, PUSH_MAP, Dialect.BLOCK).exit_code == 2


def test_extension_attempt_ids_spend_once_and_replay_safely(service: TwinService) -> None:
    """The pi and Pi Durable extensions send their own pi- and pd- attempt ids over MCP."""
    service.approve(_request(service)["approval_id"], actor=HUMAN)
    durable = {"workflow": "release", "action": "repo.push", "attempt_id": "pd-6f1c2a9e-4b7d"}
    assert call_tool(service, AGENT, "twin_authorise", durable)["decision"] == "allow"
    assert call_tool(service, AGENT, "twin_authorise", durable)["decision"] == "allow"  # replay
    fresh = {**durable, "attempt_id": "pi-0d3e5b71-9a2c"}
    assert call_tool(service, AGENT, "twin_authorise", fresh)["decision"] == "ask"


def test_only_the_principal_decides_approvals(service: TwinService) -> None:
    approval_id = _request(service)["approval_id"]
    with pytest.raises(AuthorityError):
        service.approve(approval_id, actor=AGENT)
    with pytest.raises(AuthorityError):
        service.decline(approval_id, actor=AGENT, note="Not now.")
    with pytest.raises(StateError, match="say why"):
        service.decline(approval_id, actor=HUMAN, note="  ")


def test_decisions_on_approvals_become_learning(service: TwinService) -> None:
    approval_id = _request(service)["approval_id"]
    service.decline(approval_id, actor=HUMAN, note="Wait for the freeze to lift.")
    view = call_tool(service, AGENT, "twin_approval_status", {"approval_id": approval_id})
    assert view["status"] == "declined" and view["decision_note"] == "Wait for the freeze to lift."
    with pytest.raises(StateError, match="only pending"):
        service.approve(approval_id, actor=HUMAN)
    episode = service.store.select(Episode)[0]
    assert episode.chosen == "decline" and episode.recorded_by == HUMAN
    assert episode.rationale == "Wait for the freeze to lift."
    assert episode.sources[0].locator == f"approval {approval_id}"


def test_requests_are_not_duplicated_and_not_needed_when_granted(service: TwinService) -> None:
    first = _request(service)
    assert _request(service, summary="Push it, please.")["approval_id"] == first["approval_id"]
    assert len(service.approvals(pending_only=True)) == 1
    service.grant(workflow="release", actions=["repo.push"], actor=HUMAN)
    covered = _request(service, action="repo.push")
    assert covered["status"] == "not_needed" and covered["grant_ids"]
    with pytest.raises(StateError, match="can never be approved"):
        _request(service, action="twin.confirm")


def test_approvals_expire_and_can_be_revoked(service: TwinService, clock: Clock) -> None:
    expiring = _request(service)["approval_id"]
    service.approve(expiring, actor=HUMAN)
    clock.advance(hours=2)
    assert (
        service.approval_view(service.store.get(ApprovalRequest, expiring))["status"] == "expired"
    )
    assert service.authorise(workflow="release", action="repo.push", actor=AGENT).decision == "ask"

    revoked = _request(service)["approval_id"]
    grant_id = service.approval_view(service.approve(revoked, actor=HUMAN))["grant_id"]
    service.revoke_grant(grant_id, actor=HUMAN)
    assert service.approval_view(service.store.get(ApprovalRequest, revoked))["status"] == "revoked"


def test_writes_with_a_request_id_happen_once(service: TwinService) -> None:
    arguments = {**quote_episode(), "request_id": "call_1"}
    first = call_tool(service, AGENT, "twin_record_decision", arguments)
    retry = call_tool(service, AGENT, "twin_record_decision", arguments)
    assert retry["episode_id"] == first["episode_id"] and retry["replayed"] is True
    assert "warning" not in retry
    assert len(service.store.select(Episode)) == 1

    changed = call_tool(
        service, AGENT, "twin_record_decision", {**arguments, "situation": "Something else."}
    )
    assert (
        changed["episode_id"] == first["episode_id"] and "different arguments" in changed["warning"]
    )
    # Another agent, or no request_id at all, is separate work.
    call_tool(service, "agent:other", "twin_record_decision", arguments)
    call_tool(service, AGENT, "twin_record_decision", quote_episode())
    assert len(service.store.select(Episode)) == 3


def test_a_failed_write_leaves_nothing_to_replay(service: TwinService) -> None:
    broken = {**quote_episode(), "chosen": "no-such-option", "request_id": "call_2"}
    with pytest.raises(Exception, match="option"):
        call_tool(service, AGENT, "twin_record_decision", broken)
    assert service.store.select(Receipt) == []
    fixed = call_tool(service, AGENT, "twin_record_decision", {**broken, "chosen": "mid"})
    assert "replayed" not in fixed


def test_erasing_an_episode_erases_its_receipts(service: TwinService) -> None:
    recorded = call_tool(
        service, AGENT, "twin_record_decision", {**quote_episode(), "request_id": "call_3"}
    )
    assert len(service.store.select(Receipt)) == 1
    report = service.erase_episode(recorded["episode_id"], actor=HUMAN, reason="Asked to forget.")
    assert report["receipts"] == 1 and service.store.select(Receipt) == []
    assert not service.store.verify_chain()


def test_stores_from_schema_one_are_migrated(tmp_path: Path) -> None:
    path = tmp_path / "twin.db"
    Store(path).close()
    old_grant = {
        "id": "gr-old",
        "workflow": "release",
        "actions": ["repo.push"],
        "granted_by": HUMAN,
        "granted_at": "2026-01-01T00:00:00Z",
        "expires_at": None,
        "revoked_at": None,
        "revoked_by": None,
        "note": None,
    }
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE meta SET value='1' WHERE key='schema_version'")
        conn.execute(
            "INSERT INTO entities(type, id, data, created_at, updated_at) VALUES(?, ?, ?, ?, ?)",
            ("grant", "gr-old", json.dumps(old_grant), "2026-01-01", "2026-01-01"),
        )
    store = Store(path)
    grant = store.get(Grant, "gr-old")
    assert grant.max_uses is None and grant.uses == [] and grant.approval_id is None
    store.close()
    with sqlite3.connect(path) as conn:
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        assert meta["schema_version"] == str(SCHEMA_VERSION) and meta["migrated_from"] == "1"
        conn.execute("UPDATE meta SET value='99' WHERE key='schema_version'")
    with pytest.raises(StateError, match="newer than this package"):
        Store(path)


def test_cli_approvals_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TWINMEUP_HOME", str(tmp_path / "home"))
    for var in ("TWINMEUP_ACTOR", "TWINMEUP_PRINCIPAL"):
        monkeypatch.delenv(var, raising=False)
    runner = CliRunner()
    secret = "correct horse battery staple\n"

    def run(*args: str, code: int = 0, input: str | None = None) -> Any:
        result = runner.invoke(app, list(args), input=input)
        assert result.exit_code == code, result.output
        return result

    run("--passphrase-stdin", "init", "--name", "Alex", input=secret)
    request = {"workflow": "release", "action": "repo.push", "summary": "Push the hotfix."}
    asked = json.loads(
        run("--actor", AGENT, "tools", "call", "twin_request_approval", json.dumps(request)).output
    )
    listed = json.loads(run("approvals", "list").output)
    assert [a["approval_id"] for a in listed] == [asked["approval_id"]]
    assert json.loads(run("status").output)["pending_approvals"] == 1

    run("--actor", AGENT, "approvals", "approve", asked["approval_id"], code=1)
    run("approvals", "approve", asked["approval_id"], "--for", "soon", code=1, input=secret)
    approved = json.loads(
        run(
            "--passphrase-stdin",
            "approvals",
            "approve",
            asked["approval_id"],
            "--for",
            "30m",
            "--note",
            "Hotfix only.",
            input=secret,
        ).output
    )
    assert approved["status"] == "approved" and approved["uses_left"] == 1
    check = ["--actor", AGENT, "authorise", "--workflow", "release", "--action", "repo.push"]
    run(*check, "--attempt", "call_9")
    run(*check, "--attempt", "call_10", code=3)
    assert json.loads(run("approvals", "list").output) == []
    assert json.loads(run("approvals", "list", "--all").output)[0]["status"] == "used"
