# Integrations

Every guide below is generated from `src/twinmeup/connect.py`, the same code behind
`twinmeup connect <system>`. Run that command instead of copying from here: it prints these
steps with the absolute paths of your own installation.

Agent identities (`agent:<system>`) are labels for the audit log, so you can see which system
did what. Use any `agent:` name you like with `twinmeup --actor agent:<name> connect <system>`.

| System | Key | Kind |
|---|---|---|
| Hermes Agent | `hermes` | host |
| pi coding agent | `pi` | host |
| Claude Code | `claude-code` | host |
| OpenAI Codex CLI | `codex` | host |
| Gemini CLI | `gemini` | host |
| Cursor and Cursor Agent | `cursor` | host |
| VS Code (Copilot agent mode) | `vscode` | host |
| OpenCode | `opencode` | host |
| Goose | `goose` | host |
| Microsoft Agent Framework | `ms-agent-framework` | framework |
| OpenAI Agents SDK | `openai-agents` | framework |
| LangChain and LangGraph | `langchain` | framework |
| Any other MCP host or framework | `generic` | framework |

## Connect Twin Me Up to Hermes Agent

```text
1. Register the server (answer Y to enable all tools):
     hermes mcp add twin-me-up --command twinmeup-mcp --args --actor agent:hermes
   If the profile's config.yaml lists MCP servers under platform_toolsets, add twin-me-up
   to that list: a list naming any MCP server is an allowlist.
2. Teach the agent the loop:
     twinmeup skill export <profile home>/skills      (default profile: ~/.hermes/skills)
3. Optional enforcement, outside the model (recommended):
     twinmeup action-map example --host hermes > ~/.twinmeup/action-map.json
   Review the map, then add to the profile's config.yaml:
     hooks:
       pre_tool_call:
         - matcher: "terminal|execute_code|write_file|patch|(?:(?:.*[_-])?(?:send|reply|forward|post)(?:[_-].*)?)|(?:(?:.*[_-])?(?:pay|purchase|transfer|order|checkout)(?:[_-].*)?)|(?:(?:.*[_-])?(?:publish|deploy|release|promote)(?:[_-].*)?)|(?:(?:.*[_-])?(?:delete|remove|drop|purge|destroy)(?:[_-].*)?)|(?:(?:.*[_-])?(?:merge|push)(?:[_-].*)?)"
           command: "twinmeup --actor agent:hermes hook pre-tool-call --map ~/.twinmeup/action-map.json --dialect block"
           timeout: 20
           fail_closed: true
   Use --dialect hermes instead of block only when approvals are on: with approvals.mode off
   or yolo, Hermes approves escalations automatically. Approve the hook once
   (hermes --accept-hooks chat) and check it with `hermes hooks list`.
4. Start a new session. Tools appear as mcp__twin_me_up__twin_brief and so on.
```

## Connect Twin Me Up to pi coding agent

```text
Extension (tools, the brief at session start, and enforcement):
1. twinmeup extension export pi ~/.pi/agent/extensions
2. cd ~/.pi/agent/extensions/twin-me-up && npm install
3. Set in the shell that launches pi:
     export TWINMEUP_MCP_COMMAND=twinmeup-mcp
     export TWINMEUP_ACTOR=agent:pi
     export TWINMEUP_WORKFLOW=general                          # optional: inject the brief
     export TWINMEUP_ACTION_MAP=~/.twinmeup/action-map.json    # optional: enforcement
   For enforcement: twinmeup action-map example --host pi > ~/.twinmeup/action-map.json
   Ungranted actions then ask you to approve once, or are blocked when there is no UI.
Skill only (no extension): twinmeup skill export ~/.pi/agent/skills
   The agent then uses the `twinmeup tools call` bridge through bash.
```

## Connect Twin Me Up to Claude Code

```text
1. claude mcp add --scope user twin-me-up -- twinmeup-mcp --actor agent:claude-code
2. twinmeup skill export ~/.claude/skills
3. Optional enforcement: twinmeup action-map example --host claude-code > ~/.twinmeup/action-map.json
   then merge into ~/.claude/settings.json:
     {
       "hooks": {
         "PreToolUse": [
           {
             "matcher": "Bash|Write|Edit|MultiEdit|NotebookEdit|(?:(?:.*[_-])?(?:send|reply|forward|post)(?:[_-].*)?)|(?:(?:.*[_-])?(?:pay|purchase|transfer|order|checkout)(?:[_-].*)?)|(?:(?:.*[_-])?(?:publish|deploy|release|promote)(?:[_-].*)?)|(?:(?:.*[_-])?(?:delete|remove|drop|purge|destroy)(?:[_-].*)?)|(?:(?:.*[_-])?(?:merge|push)(?:[_-].*)?)",
             "hooks": [
               {
                 "type": "command",
                 "command": "twinmeup --actor agent:claude-code hook pre-tool-call --map ~/.twinmeup/action-map.json --dialect claude"
               }
             ]
           }
         ]
       }
     }
   Ungranted actions then make Claude Code ask you (permissionDecision: ask).
4. Check: claude mcp list   (tools appear as mcp__twin-me-up__twin_brief and so on)
```

## Connect Twin Me Up to OpenAI Codex CLI

```text
1. codex mcp add twin-me-up -- twinmeup-mcp --actor agent:codex
   (or in ~/.codex/config.toml:
     [mcp_servers.twin-me-up]
     command = "twinmeup-mcp"
     args = ["--actor", "agent:codex"])
2. twinmeup skill export ~/.codex/skills
3. Check: codex mcp list
```

## Connect Twin Me Up to Gemini CLI

```text
1. gemini mcp add --scope user twin-me-up twinmeup-mcp --actor agent:gemini
2. Add to GEMINI.md (or your context file): "Use the twin-me-up tools: call twin_brief before
   work and twin_authorise before consequential actions; record my corrections."
3. Check: gemini mcp list
```

## Connect Twin Me Up to Cursor and Cursor Agent

```text
1. Add to ~/.cursor/mcp.json (or .cursor/mcp.json in a project):
     {
       "mcpServers": {
         "twin-me-up": {
           "command": "twinmeup-mcp",
           "args": [
             "--actor",
             "agent:cursor"
           ]
         }
       }
     }
2. Cursor Agent CLI: cursor-agent mcp enable twin-me-up
3. Check: cursor-agent mcp list-tools twin-me-up
4. Add a rule (.cursor/rules) telling the agent to call twin_brief and twin_authorise.
```

## Connect Twin Me Up to VS Code (Copilot agent mode)

```text
1. code --add-mcp '{"name": "twin-me-up", "command": "twinmeup-mcp", "args": ["--actor", "agent:vscode"]}'
   (or .vscode/mcp.json: {"servers": {"twin-me-up": {"type": "stdio", "command": "twinmeup-mcp",
   "args": ["--actor", "agent:vscode"]}}})
2. Add to .github/copilot-instructions.md: call twin_brief before work and twin_authorise
   before consequential actions.
```

## Connect Twin Me Up to OpenCode

```text
1. Add to opencode.json (project) or ~/.config/opencode/opencode.json:
     {
       "$schema": "https://opencode.ai/config.json",
       "mcp": {
         "twin-me-up": {
           "type": "local",
           "command": [
             "twinmeup-mcp",
             "--actor",
             "agent:opencode"
           ],
           "enabled": true
         }
       }
     }
2. twinmeup skill export ~/.config/opencode/skills    (or add the loop to AGENTS.md)
3. Check: opencode mcp list
```

## Connect Twin Me Up to Goose

```text
1. goose configure -> Add Extension -> Command-line Extension
     name: twin-me-up
     command: twinmeup-mcp --actor agent:goose
   (or in ~/.config/goose/config.yaml:
     extensions:
       twin-me-up:
         type: stdio
         name: twin-me-up
         cmd: twinmeup-mcp
         args: [--actor, agent:goose]
         enabled: true
         timeout: 300)
2. Add the loop to your goosehints: call twin_brief before work, twin_authorise before acting.
```

## Connect Twin Me Up to Microsoft Agent Framework

```text
pip install agent-framework   (Microsoft Agent Framework, Python)

import asyncio
from agent_framework import Agent, MCPStdioTool
from agent_framework.openai import OpenAIChatClient  # or any supported chat client

INSTRUCTIONS = (
    "You are my delegate. Call twin_brief before work and twin_authorise before any "
    "consequential action; obey 'ask'. Record my corrections with twin_record_correction."
)

async def main() -> None:
    twin = MCPStdioTool(name="twin", command="twinmeup-mcp", args=["--actor", "agent:ms-agent-framework"])
    async with twin:
        agent = Agent(OpenAIChatClient(), instructions=INSTRUCTIONS, tools=[twin])
        print(await agent.run("Get my twin brief for the general workflow."))

asyncio.run(main())

.NET: connect with the official ModelContextProtocol client (StdioClientTransport with
Command = "twinmeup-mcp", Arguments = ["--actor", "agent:ms-agent-framework"]) and pass the listed tools to the agent.
```

## Connect Twin Me Up to OpenAI Agents SDK

```text
pip install openai-agents

import asyncio
from agents import Agent, Runner
from agents.mcp import MCPServerStdio

async def main() -> None:
    params = {"command": "twinmeup-mcp", "args": ["--actor", "agent:openai-agents"]}
    async with MCPServerStdio(params=params) as twin:
        agent = Agent(
            name="Delegate",
            instructions="Call twin_brief before work and twin_authorise before acting.",
            mcp_servers=[twin],
        )
        result = await Runner.run(agent, "Get my twin brief for the general workflow.")
        print(result.final_output)

asyncio.run(main())
```

## Connect Twin Me Up to LangChain and LangGraph

```text
pip install langchain-mcp-adapters langgraph

from langchain_mcp_adapters.client import MultiServerMCPClient

client = MultiServerMCPClient(
    {"twin": {"command": "twinmeup-mcp", "args": ["--actor", "agent:langchain"], "transport": "stdio"}}
)
tools = await client.get_tools()   # pass to create_react_agent(model, tools) or any agent
```

## Connect Twin Me Up to Any other MCP host or framework

```text
Any MCP host (stdio):
  command: twinmeup-mcp
  args:    --actor agent:generic
No MCP: export function-calling schemas and dispatch calls through the CLI bridge:
  twinmeup tools schema --format openai > twin-tools.json
  twinmeup --actor agent:generic tools call twin_authorise '{"workflow":"general","action":"email.send"}'
In-process Python:
  from twinmeup.protocol import call_tool
  from twinmeup.service import TwinService
  twin = TwinService.open()
  call_tool(twin, "agent:generic", "twin_brief", {"workflow": "general", "objective": "..."})
Teach the loop with the skill: twinmeup skill export <your skills directory>
```
