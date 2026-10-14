"""Setup surfaces: identity, connection guides, action maps, assets and the doctor."""

from __future__ import annotations

import builtins
import json
import re
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from twinmeup import connect, identity
from twinmeup.cli import app
from twinmeup.errors import AuthorityError
from twinmeup.hooks import ActionMap

PASS = "correct horse battery staple\n"


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("TWINMEUP_HOME", str(tmp_path / "home"))
    for var in ("TWINMEUP_ACTOR", "TWINMEUP_PRINCIPAL"):
        monkeypatch.delenv(var, raising=False)
    runner = CliRunner()

    def invoke(*args: str, code: int = 0, input: str | None = None) -> Any:
        result = runner.invoke(app, list(args), input=input)
        assert result.exit_code == code, result.output
        return result

    return invoke


def test_passphrase_hashing_and_human_ids() -> None:
    stored = identity.hash_passphrase("long enough secret")
    assert identity.check_passphrase("long enough secret", stored)
    assert not identity.check_passphrase("long enough secreT", stored)
    assert "long enough" not in stored.model_dump_json()
    with pytest.raises(AuthorityError):
        identity.hash_passphrase("short")
    assert identity.human_id_for("Dr. Ada Lovelace") == "human:dr-ada-lovelace"
    assert identity.human_id_for("!!!") == "human:principal"


def test_agents_without_a_terminal_cannot_prove_they_are_human(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_open = builtins.open

    def no_tty(path: Any, *args: Any, **kwargs: Any) -> Any:
        if path == "/dev/tty":
            raise OSError("no controlling terminal")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", no_tty)
    with pytest.raises(AuthorityError, match="human-only operation"):
        identity.read_secret("Passphrase: ", from_stdin=False)


def test_connect_lists_and_renders_every_host(run: Any) -> None:
    listed = run("connect", "--list").output
    for key in connect.HOSTS:
        assert key in listed
        guide = run("connect", key).output
        assert "twinmeup-mcp" in guide and f"agent:{key}" in guide, key
    custom = run("--actor", "agent:my-bot", "connect", "claude-code").output
    assert "--actor agent:my-bot" in custom
    run("connect", "nonsense", code=1)
    run("--actor", "human:alex", "connect", "pi", code=1)


@pytest.mark.parametrize("host", sorted(connect.HOOK_TOOLS))
def test_example_maps_guard_the_twin_and_spare_harmless_calls(host: str) -> None:
    rules = connect.example_map(host)
    shell = connect.HOOK_TOOLS[host]["shell"].split("|")[0]
    write = connect.HOOK_TOOLS[host]["write"].split("|")[0]
    cmd = lambda c: {"command": c}  # noqa: E731

    assert rules.match(shell, cmd("twinmeup grant add --workflow general --action x.y")) == (
        "general",
        "twin.human-only",
    )
    assert rules.match(shell, cmd("twinmeup lesson confirm lsn-1"))[1] == "twin.human-only"  # type: ignore[index]
    harmless = 'twinmeup --actor agent:x tools call twin_brief \'{"objective": "export it"}\''
    assert rules.match(shell, cmd(harmless)) is None
    assert rules.match(write, {"path": "/home/u/.twinmeup/action-map.json"})[1] == "twin.store"  # type: ignore[index]
    host_config = {
        "hermes": "/home/u/.hermes/profiles/work/config.yaml",
        "claude-code": "/home/u/.claude/settings.json",
        "pi": "/home/u/.pi/agent/settings.json",
    }[host]
    assert rules.match(write, {"path": host_config})[1] == "twin.host-config"  # type: ignore[index]
    sneaky = cmd(f"sed -i.bak 's/hooks/x/' {host_config}")
    assert rules.match(shell, sneaky)[1] == "twin.host-config"  # type: ignore[index]
    assert rules.match(shell, cmd("git push origin main"))[1] == "repo.push"  # type: ignore[index]
    assert rules.match(shell, cmd("ls -la")) is None
    assert rules.match("openmail_send", {})[1] == "message.send"  # type: ignore[index]
    assert rules.match("mcp__shop__create_purchase_request", {})[1] == "money.spend"  # type: ignore[index]
    assert rules.match("mcp__docs__sender_info", {}) is None  # "send" only at word boundaries
    assert rules.match("mcp__shop__purchase_item", {})[1] == "money.spend"  # type: ignore[index]
    assert rules.match("mcp__db__postgres_query", {}) is None
    assert rules.match("mcp__twin-me-up__twin_authorise", {}) is None  # never governed
    assert rules.match("mcp__twin_me_up__twin_record_correction", {}) is None
    assert ActionMap.model_validate_json(rules.model_dump_json()) == rules


def test_action_map_commands(run: Any, tmp_path: Path) -> None:
    text = run("action-map", "example", "--host", "claude-code").output
    path = tmp_path / "map.json"
    path.write_text(text, encoding="utf-8")
    matcher = run("action-map", "matcher", "--map", str(path)).output.strip()
    for governed in ("Bash", "Write", "Edit", "mcp__shop__purchase_item", "openmail_send"):
        assert re.fullmatch(matcher, governed), governed
    for harmless in ("Read", "Grep", "mcp__docs__sender_info", "mcp__twin-me-up__twin_brief"):
        assert not ActionMap.load(path).match(harmless, {}), harmless
    check = json.loads(
        run(
            "action-map",
            "check",
            "--map",
            str(path),
            "--tool",
            "Bash",
            "--input",
            json.dumps({"command": "git push"}),
        ).output
    )
    assert check == {"governed": True, "workflow": "general", "action": "repo.push"}
    run("action-map", "example", "--host", "codex", code=1)


def test_skill_and_extension_export(run: Any, tmp_path: Path) -> None:
    skill = json.loads(run("skill", "export", str(tmp_path / "skills")).output)
    text = Path(skill["written"]).read_text(encoding="utf-8")
    assert text.startswith("---\nname: twin-me-up\n") and "twin_authorise" in text
    assert run("skill", "show").output == text
    ext = json.loads(run("extension", "export", "pi", str(tmp_path / "ext")).output)
    names = sorted(p.name for p in Path(ext["written"]).iterdir())
    assert names == ["index.ts", "package.json"]
    run("extension", "export", "cursor", str(tmp_path / "ext"), code=1)


def test_doctor_reports_each_check(run: Any) -> None:
    unset = json.loads(run("doctor", code=1).output)
    assert unset["ok"] is False and unset["checks"][0]["level"] == "fail"
    run("--passphrase-stdin", "init", "--name", "Alex", input=PASS)
    report = json.loads(run("doctor").output)
    levels = {c["check"]: c["level"] for c in report["checks"]}
    assert report["ok"] is True
    assert levels == {
        "settings": "ok",
        "passphrase": "ok",
        "store": "ok",
        "audit chain": "ok",
        "mcp server": "ok",
    }
    assert "10 tools" in next(c["detail"] for c in report["checks"] if c["check"] == "mcp server")
