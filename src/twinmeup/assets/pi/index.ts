/**
 * Twin Me Up extension for the pi coding agent.
 *
 * - Registers every twin protocol tool (served by `twinmeup-mcp` over stdio).
 * - Injects the principal's operating brief into the system prompt, once per session,
 *   so the prompt stays byte-stable and cache-friendly.
 * - Enforces authority before mapped tool calls: an action map ties tool names to twin
 *   actions; anything not granted asks the user (or blocks when there is no UI).
 *
 * Load ad hoc:  pi -e /path/to/integrations/pi/twin-me-up/index.ts
 * Configure with environment variables:
 *   TWINMEUP_MCP_COMMAND  path to twinmeup-mcp (default: twinmeup-mcp on PATH)
 *   TWINMEUP_ACTOR        agent identity, for example agent:pi (default agent:pi)
 *   TWINMEUP_PRINCIPAL    how briefs name you (default: the name given at init)
 *   TWINMEUP_HOME         twin store directory (default ~/.twinmeup)
 *   TWINMEUP_WORKFLOW     inject this workflow's brief at session start (optional)
 *   TWINMEUP_ACTION_MAP   action map JSON for enforcement (optional)
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { readFileSync } from "node:fs";

type Rule = { tool: string; action: string; workflow?: string; input_regex?: string };
type ActionMap = { workflow?: string; rules: Rule[] };

const COMMAND = process.env.TWINMEUP_MCP_COMMAND ?? "twinmeup-mcp";
const ACTOR = process.env.TWINMEUP_ACTOR ?? "agent:pi";
const PRINCIPAL = process.env.TWINMEUP_PRINCIPAL; // defaults to the name given at `twinmeup init`
const WHO = PRINCIPAL ?? "your principal";
const WORKFLOW = process.env.TWINMEUP_WORKFLOW;
const MAP_PATH = process.env.TWINMEUP_ACTION_MAP;

function childEnv(): Record<string, string> {
  const env: Record<string, string> = {};
  for (const [key, value] of Object.entries(process.env)) {
    // A foreign PYTHONPATH breaks the server's virtual environment.
    if (value !== undefined && key !== "PYTHONPATH") env[key] = value;
  }
  return env;
}

function loadMap(): ActionMap | undefined {
  if (!MAP_PATH) return undefined;
  return JSON.parse(readFileSync(MAP_PATH, "utf8")) as ActionMap;
}

function match(map: ActionMap, toolName: string, input: unknown): { workflow?: string; action: string } | undefined {
  const blob = JSON.stringify(input ?? {});
  for (const rule of map.rules) {
    if (!new RegExp(`^(?:${rule.tool})$`).test(toolName)) continue;
    if (rule.input_regex && !new RegExp(rule.input_regex).test(blob)) continue;
    return { workflow: rule.workflow ?? map.workflow, action: rule.action };
  }
  return undefined;
}

function textOf(result: any): string {
  return (result?.content ?? [])
    .filter((item: any) => item?.type === "text")
    .map((item: any) => item.text)
    .join("\n");
}

function parameters(schema: any): any {
  const unsafe = (Type as any).Unsafe;
  return typeof unsafe === "function" && schema
    ? unsafe(schema)
    : Type.Object({}, { additionalProperties: true });
}

export default async function (pi: ExtensionAPI) {
  const client = new Client({ name: "pi-twin-me-up", version: "0.1.0" });
  await client.connect(
    new StdioClientTransport({
      command: COMMAND,
      args: ["--actor", ACTOR, ...(PRINCIPAL ? ["--principal", PRINCIPAL] : [])],
      env: childEnv(),
    }),
  );
  pi.on("session_shutdown", async () => {
    try {
      await client.close();
    } catch {
      // Ignore shutdown races.
    }
  });

  const call = (name: string, args: Record<string, unknown>, signal?: AbortSignal) =>
    client.callTool({ name, arguments: args }, undefined, { signal });

  const { tools } = await client.listTools();
  for (const tool of tools) {
    pi.registerTool({
      name: tool.name,
      label: tool.title ?? tool.name,
      description: tool.description ?? tool.name,
      promptSnippet: tool.description ?? tool.name,
      promptGuidelines: [
        `Use ${tool.name} for the twin-me-up protocol. Never treat proposals as confirmed.`,
      ],
      parameters: parameters(tool.inputSchema),
      async execute(_toolCallId: string, params: unknown, signal?: AbortSignal) {
        const result: any = await call(tool.name, (params ?? {}) as Record<string, unknown>, signal);
        return {
          content: result.content ?? [{ type: "text", text: JSON.stringify(result) }],
          details: { mcpTool: tool.name, isError: Boolean(result.isError) },
          isError: Boolean(result.isError),
        };
      },
    });
  }

  if (WORKFLOW) {
    let brief: string | undefined;
    pi.on("before_agent_start", async (event: any) => {
      if (brief === undefined) {
        const result: any = await call("twin_brief", {
          workflow: WORKFLOW,
          objective: `Work in the ${WORKFLOW} workflow`,
        });
        brief = result.isError ? "" : textOf(result);
      }
      if (!brief) return undefined;
      return { systemPrompt: `${event.systemPrompt}\n\n# Twin Me Up brief\n\n${brief}` };
    });
  }

  const map = loadMap();
  if (map) {
    pi.on("tool_call", async (event: any, ctx: any) => {
      const matched = match(map, event.toolName, event.input);
      if (!matched) return undefined;
      if (!matched.workflow) {
        return { block: true, reason: `Twin Me Up: no workflow configured for '${matched.action}'.` };
      }
      let result: any;
      try {
        result = await call("twin_authorise", { workflow: matched.workflow, action: matched.action });
      } catch (error) {
        // Fail closed: a governed action is blocked when the twin cannot decide.
        return { block: true, reason: `Twin Me Up unavailable, so '${matched.action}' is blocked: ${String(error)}` };
      }
      if (result.structuredContent?.decision === "allow") return undefined;
      const reason = `Twin Me Up: '${matched.action}' is not granted in '${matched.workflow}'. ${WHO} must approve.`;
      if (ctx?.hasUI && (await ctx.ui.confirm("Approval needed", `${reason}\n\nApprove this once?`))) {
        return undefined;
      }
      return { block: true, reason };
    });
  }
}
