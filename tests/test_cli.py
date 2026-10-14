from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from conftest import quote_episode
from twinmeup.cli import app

PASS = "correct horse battery staple\n"
HUMAN = ["--passphrase-stdin"]
AGENT = ["--actor", "agent:hermes"]


@pytest.fixture
def cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("TWINMEUP_HOME", str(tmp_path / "home"))
    for var in ("TWINMEUP_ACTOR", "TWINMEUP_PRINCIPAL"):
        monkeypatch.delenv(var, raising=False)
    runner = CliRunner()

    def invoke(*args: str, ok: bool = True, input: str | None = None) -> Any:
        result = runner.invoke(app, list(args), input=input)
        if ok:
            assert result.exit_code == 0, result.output
        else:
            assert result.exit_code == 1, result.output
        return result

    return invoke


def human(cli: Any, *args: str, ok: bool = True) -> Any:
    return cli(*HUMAN, *args, ok=ok, input=PASS)


def write(tmp_path: Path, name: str, data: Any) -> str:
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_end_to_end_pilot_flow(cli: Any, tmp_path: Path) -> None:
    init = json.loads(human(cli, "init", "--name", "Alex Example").output)
    assert init["human_id"] == "human:alex-example" and init["protected"] is True
    assert stat.S_IMODE(Path(init["settings"]).stat().st_mode) == 0o600

    episode = json.loads(
        cli(*AGENT, "episode", "add", write(tmp_path, "ep.json", quote_episode())).output
    )
    gaps = json.loads(cli("episode", "gaps", episode["id"]).output)
    assert gaps[0]["gap"] == "rejected"

    lesson = json.loads(
        cli(
            *AGENT,
            "lesson",
            "propose",
            "--workflow",
            "quote-review",
            "--kind",
            "criterion",
            "--statement",
            "Compare complete scope before headline price.",
            "--origin",
            "inferred",
            "--episode",
            episode["id"],
            "--source",
            "interview:session-1",
        ).output
    )
    denied = cli(*AGENT, "lesson", "confirm", lesson["id"], ok=False)
    assert "is an agent; only the principal can do this" in denied.output
    confirmed = json.loads(human(cli, "lesson", "confirm", lesson["id"], "--note", "Yes").output)
    assert confirmed["status"] == "confirmed"
    human(
        cli,
        "lesson",
        "propose",
        "--workflow",
        "quote-review",
        "--kind",
        "boundary",
        "--statement",
        "Never instruct a contractor without written approval.",
        "--origin",
        "written_rule",
        "--confirm",
    )

    human(cli, "grant", "add", "--workflow", "quote-review", "--action", "draft.prepare")
    packet = cli(
        *AGENT,
        "handoff",
        "--workflow",
        "quote-review",
        "--objective",
        "Compare three roofing quotes",
        "--action",
        "draft.prepare",
        "--action",
        "email.send",
    ).output
    assert "Permitted actions: `draft.prepare`" in packet
    assert "Requested but not authorised: `email.send`" in packet
    assert "Never instruct a contractor" in packet and "Compare complete scope" in packet
    brief = cli("brief", "--workflow", "quote-review", "--objective", "Compare quotes").output
    assert "Alex Example" in brief  # the principal's name comes from init

    eval_set = json.loads(
        human(cli, "eval", "create", "--name", "pilot", "--workflow", "quote-review").output
    )
    case = json.loads(
        human(
            cli,
            "eval",
            "add-case",
            eval_set["id"],
            write(
                tmp_path,
                "case.json",
                {
                    "situation": "Two complete quotes; the cheaper contractor "
                    "has no insurance certificate.",
                    "options": [
                        {"id": "cheaper", "label": "Cheaper"},
                        {"id": "insured", "label": "Insured"},
                    ],
                    "chosen": "insured",
                    "rationale": "Uninsured work is never acceptable.",
                },
            ),
        ).output
    )
    human(cli, "eval", "freeze", eval_set["id"])
    script = write(
        tmp_path, "script.json", {case["id"]: {"decision": "insured", "confidence": 0.8}}
    )
    run = json.loads(
        cli(
            *AGENT,
            "eval",
            "run",
            eval_set["id"],
            "--arm",
            "twin",
            "--responder",
            f"scripted:{script}",
        ).output
    )
    assert run["agreement"].startswith("1/1")
    report = cli("eval", "report", run["run"]["id"]).output
    assert "| twin |" in report and "Small sample" in report

    audit = json.loads(cli("audit", "verify").output)
    assert audit == {"ok": True, "problems": []}
    export_path = tmp_path / "exports" / "twin.bundle.json"
    cli(*AGENT, "export", str(export_path), ok=False)  # exports are human-only
    human(cli, "export", str(export_path))
    assert stat.S_IMODE(export_path.stat().st_mode) == 0o600
    bundle = json.loads(export_path.read_text(encoding="utf-8"))
    assert "eval_answer" not in bundle["records"] and len(bundle["records"]["lesson"]) == 2


def test_human_operations_need_setup_and_the_right_passphrase(cli: Any, tmp_path: Path) -> None:
    missing = cli("grant", "add", "--workflow", "quote-review", "--action", "crm.read", ok=False)
    assert "run `twinmeup init`" in missing.output
    human(cli, "init", "--name", "Alex")
    grant = ["grant", "add", "--workflow", "quote-review", "--action", "crm.read"]
    wrong = cli(*HUMAN, *grant, ok=False, input="not my passphrase\n")
    assert "passphrase did not match" in wrong.output
    impostor = cli("--actor", "human:someone-else", *HUMAN, *grant, ok=False, input=PASS)
    assert "belongs to human:alex" in impostor.output
    bad_file = write(tmp_path, "bad.json", {"workflow": "Bad Slug"})
    bad = cli(*AGENT, "episode", "add", bad_file, ok=False)
    assert bad.output.startswith("error: invalid input") and "Traceback" not in bad.output


def test_init_is_idempotent_and_force_needs_the_current_passphrase(cli: Any) -> None:
    human(cli, "init", "--name", "Alex")
    again = json.loads(cli("init").output)
    assert again["principal"] == "Alex" and "already set up" in again["note"]
    force = ["init", "--force", "--name", "Sam"]
    refused = cli(*HUMAN, *force, ok=False, input="wrong guess\nnew pass 123\n")
    assert "passphrase did not match" in refused.output
    changed = json.loads(cli(*HUMAN, *force, input=PASS + "new pass 123\n").output)
    assert changed["principal"] == "Sam"
    assert cli("--actor", "agent:x", "init", ok=False).output.startswith("error: agents cannot")


def test_agents_cannot_set_up_an_unprotected_twin(
    cli: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("twinmeup.identity.has_terminal", lambda: False)
    refused = cli("init", "--name", "Alex", "--no-passphrase", ok=False)
    assert "in your own terminal, not by an agent" in refused.output


def test_unprotected_twin_warns(cli: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("twinmeup.identity.has_terminal", lambda: True)
    result = cli("init", "--name", "Alex", "--no-passphrase")
    assert '"protected": false' in result.output
    granted = cli("grant", "add", "--workflow", "quote-review", "--action", "crm.read")
    assert "no passphrase" in granted.output  # the warning goes to stderr, mixed in by the runner
