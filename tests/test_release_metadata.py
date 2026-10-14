"""Release metadata stays coherent: one version, one server name, one licence."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

import twinmeup
from twinmeup.protocol import TOOLS

ROOT = Path(__file__).resolve().parents[1]


def _project() -> dict[str, Any]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project: dict[str, Any] = data["project"]
    return project


def _server() -> dict[str, Any]:
    server: dict[str, Any] = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    return server


def test_every_version_agrees() -> None:
    version = _project()["version"]
    server = _server()
    assert twinmeup.__version__ == version == server["version"]
    assert {package["version"] for package in server["packages"]} == {version}
    citation = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    assert re.search(rf'^version: "?{re.escape(version)}"?$', citation, re.MULTILINE)
    assert f"## [{version}]" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def test_desktop_extension_bundle_matches_the_package() -> None:
    version = _project()["version"]
    manifest = json.loads((ROOT / "mcpb" / "manifest.json").read_text(encoding="utf-8"))
    bundle_toml = (ROOT / "mcpb" / "pyproject.toml").read_text(encoding="utf-8")
    bundle = tomllib.loads(bundle_toml)["project"]
    assert manifest["version"] == bundle["version"] == version
    assert bundle["dependencies"] == [f"{_project()['name']}=={version}"]
    assert manifest["server"]["type"] == "uv"
    assert (ROOT / "mcpb" / manifest["server"]["entry_point"]).is_file()
    assert (ROOT / "mcpb" / manifest["icon"]).is_file()
    assert manifest["license"] == _project()["license"]
    assert {tool["name"] for tool in manifest["tools"]} == {tool.name for tool in TOOLS}


def test_registry_ownership_marker_matches_the_server_name() -> None:
    server = _server()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"mcp-name: {server['name']}" in readme
    assert server["packages"][0]["identifier"] == _project()["name"]
    assert len(server["description"]) <= 100


def test_uvx_runs_the_mcp_server() -> None:
    scripts = _project()["scripts"]
    assert scripts[_project()["name"]] == scripts["twinmeup-mcp"]


def test_licence_and_notice_ship_with_the_package() -> None:
    project = _project()
    assert project["license"] == "Apache-2.0"
    assert set(project["license-files"]) == {"LICENSE", "NOTICE"}
    licence = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "Apache License" in licence and "Version 2.0, January 2004" in licence
    assert "GitDakky" in (ROOT / "NOTICE").read_text(encoding="utf-8")
