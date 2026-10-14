"""The installed commands survive a foreign PYTHONPATH that shadows their dependencies."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(sys.executable).parent


@pytest.mark.parametrize("command", ["twinmeup", "twinmeup-mcp", "twin-me-up"])
def test_commands_ignore_a_poisoned_pythonpath(command: str, tmp_path: Path) -> None:
    script = SCRIPTS / command
    if not script.exists():
        pytest.skip(f"{command} is not installed next to {sys.executable}")
    poison = tmp_path / "poison"
    for module in ("pydantic", "pydantic_core", "typer", "mcp"):
        (poison / module).mkdir(parents=True)
        (poison / module / "__init__.py").write_text("raise ImportError('shadowed')\n")
    env = {**os.environ, "PYTHONPATH": str(poison), "TWINMEUP_HOME": str(tmp_path / "home")}
    args = ["version"] if command == "twinmeup" else ["--health-check"]
    result = subprocess.run(
        [str(script), *args], env=env, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert "shadowed" not in result.stderr
