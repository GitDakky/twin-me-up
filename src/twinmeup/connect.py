"""Connection guides for agent systems, generated with this installation's real paths.

`twinmeup connect <host>` prints the exact steps for one host; `twinmeup action-map example`
prints a starting enforcement map using that host's tool names. Nothing here edits another
program's configuration: the user, or their agent with the user watching, applies the steps.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .hooks import ActionMap


def _script(name: str) -> str:
    """Absolute path of one of this package's console scripts, falling back to PATH."""
    exe = name + (".exe" if os.name == "nt" else "")
    sibling = Path(sys.executable).parent / exe
    if sibling.exists():
        return str(sibling)
    return shutil.which(name) or name


def server_command() -> str:
    return _script("twinmeup-mcp")


def cli_command() -> str:
    return _script("twinmeup")


# --- enforcement maps -----------------------------------------------------------------

# Tool names each hook-capable host uses for shells, code execution and file writes.
HOOK_TOOLS: dict[str, dict[str, str]] = {
    "hermes": {"shell": "terminal|execute_code", "write": "write_file|patch"},
    "claude-code": {"shell": "Bash", "write": "Write|Edit|MultiEdit|NotebookEdit"},
    "pi": {"shell": "bash", "write": "write|edit"},
}

# Human-only subcommands, matched outside quoted text so JSON payloads never trigger them.
# `twin-me-up` runs the same commands as `twinmeup`, so both names are guarded.
_HUMAN_ONLY = (
    r"(?:twinmeup|twin-me-up)\b[^'\"\n]*?\s(init|export|erase|grant\s+(add|revoke)|"
    r"approvals\s+(approve|decline)|"
    r"lesson\s+(confirm|reject|revise|retire)|eval\s+(create|add-case|from-episode|freeze))\b"
    r"|(?:twinmeup|twin-me-up)\b[^'\"\n]*?\s--(confirm|passphrase-stdin)\b"
)
_STORE = r"\.twinmeup\b"
# Where each host registers its hooks. Guarding these stops an agent disabling enforcement.
_HOST_CONFIG: dict[str, str] = {
    "hermes": (
        r"\.hermes/(?:profiles/[^/\s'\"]+/)?(?:config\.ya?ml|\.env)\b"
        r"|shell-hooks-allowlist\.json"
    ),
    "claude-code": r"\.claude/settings(?:\.local)?\.json|\.claude\.json",
    "pi": r"\.pi/agent/(?:settings\.json|extensions\b)",
}
_VERB = r"(?:.*[_-])?(?:{verbs})(?:[_-].*)?"


def example_map(host: str, workflow: str = "general") -> ActionMap:
    """A conservative starting map: guard the twin itself, then common consequential verbs."""
    tools = HOOK_TOOLS.get(host, HOOK_TOOLS["hermes"])
    shell, write = tools["shell"], tools["write"]
    rules = [
        {"tool": shell, "input_regex": _HUMAN_ONLY, "action": "twin.human-only"},
        {"tool": f"{shell}|{write}", "input_regex": _STORE, "action": "twin.store"},
        {
            "tool": f"{shell}|{write}",
            "input_regex": _HOST_CONFIG.get(host, _HOST_CONFIG["hermes"]),
            "action": "twin.host-config",
        },
        {"tool": shell, "input_regex": r"\bgit\s+push\b", "action": "repo.push"},
        {
            "tool": shell,
            "input_regex": r"\b(npm|pnpm|yarn|bun)\s+publish\b|\b(twine|uv)\s+(upload|publish)\b",
            "action": "package.publish",
        },
        {"tool": _VERB.format(verbs="send|reply|forward|post"), "action": "message.send"},
        {
            "tool": _VERB.format(verbs="pay|purchase|transfer|order|checkout"),
            "action": "money.spend",
        },
        {"tool": _VERB.format(verbs="publish|deploy|release|promote"), "action": "publish.change"},
        {"tool": _VERB.format(verbs="delete|remove|drop|purge|destroy"), "action": "data.delete"},
        {"tool": _VERB.format(verbs="merge|push"), "action": "repo.write"},
    ]
    return ActionMap.model_validate({"workflow": workflow, "rules": rules})


def matcher(action_map: ActionMap) -> str:
    """One regular expression covering every governed tool, for host hook matchers.

    Plain alternatives (tool names) are listed once each; regular expressions are kept whole.
    """
    names: list[str] = []
    patterns: list[str] = []
    for rule in action_map.rules:
        if any(ch in rule.tool for ch in "()[]*+?.\\"):
            if rule.tool not in patterns:
                patterns.append(rule.tool)
            continue
        for name in rule.tool.split("|"):
            if name not in names:
                names.append(name)
    return "|".join([*names, *(f"(?:{p})" for p in patterns)])


# --- guides ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Host:
    key: str
    title: str
    kind: str  # "host" (an agent application) or "framework" (a library you code against)
    guide: Callable[[str, str, str], str]


def _hook_cli(cli: str, actor: str, dialect: str) -> str:
    return (
        f"{cli} --actor {actor} hook pre-tool-call "
        f"--map ~/.twinmeup/action-map.json --dialect {dialect}"
    )


def _hermes(mcp: str, cli: str, actor: str) -> str:
    rx = matcher(example_map("hermes"))
    return f"""\
1. Register the server (answer Y to enable all tools):
     hermes mcp add twin-me-up --command {mcp} --args --actor {actor}
   If the profile's config.yaml lists MCP servers under platform_toolsets, add twin-me-up
   to that list: a list naming any MCP server is an allowlist.
2. Teach the agent the loop:
     {cli} skill export <profile home>/skills      (default profile: ~/.hermes/skills)
3. Optional enforcement, outside the model (recommended):
     {cli} action-map example --host hermes > ~/.twinmeup/action-map.json
   Review the map, then add to the profile's config.yaml:
     hooks:
       pre_tool_call:
         - matcher: "{rx}"
           command: "{_hook_cli(cli, actor, "block")}"
           timeout: 20
           fail_closed: true
   Use --dialect hermes instead of block only when approvals are on: with approvals.mode off
   or yolo, Hermes approves escalations automatically. Approve the hook once
   (hermes --accept-hooks chat) and check it with `hermes hooks list`.
4. Start a new session. Tools appear as mcp__twin_me_up__twin_brief and so on."""


def _pi(mcp: str, cli: str, actor: str) -> str:
    return f"""\
Extension (tools, the brief at session start, and enforcement):
1. {cli} extension export pi ~/.pi/agent/extensions
2. cd ~/.pi/agent/extensions/twin-me-up && npm install
3. Set in the shell that launches pi:
     export TWINMEUP_MCP_COMMAND={mcp}
     export TWINMEUP_ACTOR={actor}
     export TWINMEUP_WORKFLOW=general                          # optional: inject the brief
     export TWINMEUP_ACTION_MAP=~/.twinmeup/action-map.json    # optional: enforcement
   For enforcement: {cli} action-map example --host pi > ~/.twinmeup/action-map.json
   Ungranted actions then ask you to approve once, or are blocked when there is no UI.
Skill only (no extension): {cli} skill export ~/.pi/agent/skills
   The agent then uses the `{cli} tools call` bridge through bash."""


def _pi_durable(mcp: str, cli: str, actor: str) -> str:
    return f"""\
Extension for your own Pi Durable harness (experimental, tested with
@earendil-works/pi-durable 1.2.0): the twin's tools, the brief as a system prompt section,
and enforcement that holds through crashes and restarts. It talks to the twin through the
CLI, so this harness does not need the MCP server ({mcp}).
1. {cli} extension export pi-durable <your project>
2. In your project: npm install @earendil-works/pi-durable @earendil-works/pi-ai
3. Install it in your harness (TypeScript: the live test ran it with Node's
   --experimental-strip-types):
     import {{ createTwinMeUp }} from "./twin-me-up-durable/index.ts";
     registry.install(createTwinMeUp({{
       command: "{cli}",
       actor: "{actor}",
       workflow: "general",                    // the brief, in the system prompt
       actionMap: `${{process.env.HOME}}/.twinmeup/action-map.json`,
       approvalWaitMs: 15 * 60_000,            // optional: ask the principal and wait
     }}));
4. Build the action map. If your harness uses pi's coding tools (bash, write, edit):
     {cli} action-map example --host pi > ~/.twinmeup/action-map.json
   Otherwise write rules for your own tool names, and test them with `{cli} action-map check`.
   Ungranted calls are blocked. With approvalWaitMs the hook requests approval and waits,
   through restarts: decide with `twinmeup approvals list`, then `approve` or `decline`.
   Keep the extension and the map where your agents cannot write, or add rules that guard
   them."""


def _claude(mcp: str, cli: str, actor: str) -> str:
    rx = matcher(example_map("claude-code"))
    hook = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": rx,
                    "hooks": [{"type": "command", "command": _hook_cli(cli, actor, "claude")}],
                }
            ]
        }
    }
    return f"""\
1. claude mcp add --scope user twin-me-up -- {mcp} --actor {actor}
2. {cli} skill export ~/.claude/skills
3. Optional enforcement: {cli} action-map example --host claude-code > ~/.twinmeup/action-map.json
   then merge into ~/.claude/settings.json:
{_indent(json.dumps(hook, indent=2), 5)}
   Ungranted actions then make Claude Code ask you (permissionDecision: ask).
4. Check: claude mcp list   (tools appear as mcp__twin-me-up__twin_brief and so on)"""


def _codex(mcp: str, cli: str, actor: str) -> str:
    return f"""\
1. codex mcp add twin-me-up -- {mcp} --actor {actor}
   (or in ~/.codex/config.toml:
     [mcp_servers.twin-me-up]
     command = "{mcp}"
     args = ["--actor", "{actor}"])
2. {cli} skill export ~/.codex/skills
3. Check: codex mcp list"""


def _gemini(mcp: str, cli: str, actor: str) -> str:
    return f"""\
1. gemini mcp add --scope user twin-me-up {mcp} --actor {actor}
2. Add to GEMINI.md (or your context file): "Use the twin-me-up tools: call twin_brief before
   work and twin_authorise before consequential actions; record my corrections."
3. Check: gemini mcp list"""


def _cursor(mcp: str, cli: str, actor: str) -> str:
    cfg = {"mcpServers": {"twin-me-up": {"command": mcp, "args": ["--actor", actor]}}}
    return f"""\
1. Add to ~/.cursor/mcp.json (or .cursor/mcp.json in a project):
{_indent(json.dumps(cfg, indent=2), 5)}
2. Cursor Agent CLI: cursor-agent mcp enable twin-me-up
3. Check: cursor-agent mcp list-tools twin-me-up
4. Add a rule (.cursor/rules) telling the agent to call twin_brief and twin_authorise."""


def _vscode(mcp: str, cli: str, actor: str) -> str:
    cfg = {"name": "twin-me-up", "command": mcp, "args": ["--actor", actor]}
    return f"""\
1. code --add-mcp '{json.dumps(cfg)}'
   (or .vscode/mcp.json: {{"servers": {{"twin-me-up": {{"type": "stdio", "command": "{mcp}",
   "args": ["--actor", "{actor}"]}}}}}})
2. Add to .github/copilot-instructions.md: call twin_brief before work and twin_authorise
   before consequential actions."""


def _opencode(mcp: str, cli: str, actor: str) -> str:
    cfg = {
        "$schema": "https://opencode.ai/config.json",
        "mcp": {
            "twin-me-up": {"type": "local", "command": [mcp, "--actor", actor], "enabled": True}
        },
    }
    return f"""\
1. Add to opencode.json (project) or ~/.config/opencode/opencode.json:
{_indent(json.dumps(cfg, indent=2), 5)}
2. {cli} skill export ~/.config/opencode/skills    (or add the loop to AGENTS.md)
3. Check: opencode mcp list"""


def _goose(mcp: str, cli: str, actor: str) -> str:
    return f"""\
1. goose configure -> Add Extension -> Command-line Extension
     name: twin-me-up
     command: {mcp} --actor {actor}
   (or in ~/.config/goose/config.yaml:
     extensions:
       twin-me-up:
         type: stdio
         name: twin-me-up
         cmd: {mcp}
         args: [--actor, {actor}]
         enabled: true
         timeout: 300)
2. Add the loop to your goosehints: call twin_brief before work, twin_authorise before acting."""


def _maf(mcp: str, cli: str, actor: str) -> str:
    return f'''\
pip install agent-framework   (Microsoft Agent Framework, Python)

import asyncio
from agent_framework import Agent, MCPStdioTool
from agent_framework.openai import OpenAIChatClient  # or any supported chat client

INSTRUCTIONS = (
    "You are my delegate. Call twin_brief before work and twin_authorise before any "
    "consequential action; obey 'ask'. Record my corrections with twin_record_correction."
)

async def main() -> None:
    twin = MCPStdioTool(name="twin", command="{mcp}", args=["--actor", "{actor}"])
    async with twin:
        agent = Agent(OpenAIChatClient(), instructions=INSTRUCTIONS, tools=[twin])
        print(await agent.run("Get my twin brief for the general workflow."))

asyncio.run(main())

.NET: connect with the official ModelContextProtocol client (StdioClientTransport with
Command = "{mcp}", Arguments = ["--actor", "{actor}"]) and pass the listed tools to the agent.'''


def _openai_agents(mcp: str, cli: str, actor: str) -> str:
    return f'''\
pip install openai-agents

import asyncio
from agents import Agent, Runner
from agents.mcp import MCPServerStdio

async def main() -> None:
    params = {{"command": "{mcp}", "args": ["--actor", "{actor}"]}}
    async with MCPServerStdio(params=params) as twin:
        agent = Agent(
            name="Delegate",
            instructions="Call twin_brief before work and twin_authorise before acting.",
            mcp_servers=[twin],
        )
        result = await Runner.run(agent, "Get my twin brief for the general workflow.")
        print(result.final_output)

asyncio.run(main())'''


def _langchain(mcp: str, cli: str, actor: str) -> str:
    return f'''\
pip install langchain-mcp-adapters langgraph

from langchain_mcp_adapters.client import MultiServerMCPClient

client = MultiServerMCPClient(
    {{"twin": {{"command": "{mcp}", "args": ["--actor", "{actor}"], "transport": "stdio"}}}}
)
tools = await client.get_tools()   # pass to create_react_agent(model, tools) or any agent'''


def _generic(mcp: str, cli: str, actor: str) -> str:
    return f"""\
Any MCP host (stdio):
  command: {mcp}
  args:    --actor {actor}
No MCP: export function-calling schemas and dispatch calls through the CLI bridge:
  {cli} tools schema --format openai > twin-tools.json
  {cli} --actor {actor} tools call twin_authorise '{{"workflow":"general","action":"email.send"}}'
In-process Python:
  from twinmeup.protocol import call_tool
  from twinmeup.service import TwinService
  twin = TwinService.open()
  call_tool(twin, "{actor}", "twin_brief", {{"workflow": "general", "objective": "..."}})
Teach the loop with the skill: {cli} skill export <your skills directory>"""


def _indent(text: str, spaces: int) -> str:
    pad = " " * spaces
    return "\n".join(pad + line for line in text.splitlines())


HOSTS: dict[str, Host] = {
    h.key: h
    for h in (
        Host("hermes", "Hermes Agent", "host", _hermes),
        Host("pi", "pi coding agent", "host", _pi),
        Host("pi-durable", "Pi Durable (experimental)", "framework", _pi_durable),
        Host("claude-code", "Claude Code", "host", _claude),
        Host("codex", "OpenAI Codex CLI", "host", _codex),
        Host("gemini", "Gemini CLI", "host", _gemini),
        Host("cursor", "Cursor and Cursor Agent", "host", _cursor),
        Host("vscode", "VS Code (Copilot agent mode)", "host", _vscode),
        Host("opencode", "OpenCode", "host", _opencode),
        Host("goose", "Goose", "host", _goose),
        Host("ms-agent-framework", "Microsoft Agent Framework", "framework", _maf),
        Host("openai-agents", "OpenAI Agents SDK", "framework", _openai_agents),
        Host("langchain", "LangChain and LangGraph", "framework", _langchain),
        Host("generic", "Any other MCP host or framework", "framework", _generic),
    )
}


def guide(key: str, actor: str | None = None, *, portable: bool = False) -> str:
    """Steps for one host. portable=True uses bare command names, for documentation."""
    host = HOSTS[key]
    who = actor or f"agent:{key}"
    mcp, cli = ("twinmeup-mcp", "twinmeup") if portable else (server_command(), cli_command())
    body = host.guide(mcp, cli, who)
    return f"# Connect Twin Me Up to {host.title}\n\n{body}\n"
