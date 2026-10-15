"""The installed commands: they survive a foreign PYTHONPATH, and twin-me-up runs commands too."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from twinmeup._entry import wants_cli
from twinmeup.protocol import TOOLS

SCRIPTS = Path(sys.executable).parent
RUN = "import sys; from twinmeup._entry import twin_me_up; sys.argv[0] = 'twin-me-up'; twin_me_up()"


@pytest.mark.parametrize(
    ("command", "args"),
    [
        ("twinmeup", ["version"]),
        ("twinmeup-mcp", ["--health-check"]),
        ("twin-me-up", ["--health-check"]),
        ("twin-me-up", ["version"]),
    ],
)
def test_commands_ignore_a_poisoned_pythonpath(
    command: str, args: list[str], tmp_path: Path
) -> None:
    script = SCRIPTS / command
    if not script.exists():
        pytest.skip(f"{command} is not installed next to {sys.executable}")
    poison = tmp_path / "poison"
    for module in ("pydantic", "pydantic_core", "typer", "mcp"):
        (poison / module).mkdir(parents=True)
        (poison / module / "__init__.py").write_text("raise ImportError('shadowed')\n")
    env = {**os.environ, "PYTHONPATH": str(poison), "TWINMEUP_HOME": str(tmp_path / "home")}
    result = subprocess.run(
        [str(script), *args], env=env, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert "shadowed" not in result.stderr


def _twin_me_up(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "TWINMEUP_ACTOR")}
    env["TWINMEUP_HOME"] = str(home)
    return subprocess.run(
        [sys.executable, "-c", RUN, *args], env=env, capture_output=True, text=True, timeout=60
    )


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ([], False),
        (["--actor", "agent:vscode"], False),
        (["--home", "/tmp/twin", "--health-check"], False),
        (["--home=/tmp/twin", "--principal", "Alex"], False),
        (["--help"], False),
        (["doctor"], True),
        (["--actor", "agent:x", "tools", "list"], True),
        (["--passphrase-stdin", "approvals", "list"], True),
    ],
)
def test_a_command_word_means_the_cli(args: list[str], expected: bool) -> None:
    assert wants_cli(args) is expected


def test_twin_me_up_runs_commands(tmp_path: Path) -> None:
    for args in (["tools", "list"], ["--actor", "agent:x", "tools", "list"]):
        result = _twin_me_up(tmp_path / "home", *args)
        assert result.returncode == 0, result.stderr
        assert len(json.loads(result.stdout)) == len(TOOLS)


def test_twin_me_up_without_a_command_is_still_the_server(tmp_path: Path) -> None:
    result = _twin_me_up(tmp_path / "home", "--actor", "agent:x", "--health-check")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["ok"] is True
