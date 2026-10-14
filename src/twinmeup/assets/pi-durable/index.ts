/**
 * Twin Me Up for Pi Durable (experimental; built and tested against @earendil-works/pi-durable 1.2.0).
 *
 * Durable agents keep working when nobody is watching. This extension keeps them inside the
 * authority their principal has granted, through crashes and restarts:
 *
 * - Every twin protocol tool, from the twin's own schemas. All are replay-safe: reads change
 *   nothing, and each write carries a request_id kept in its task's durable memo, so a rerun
 *   after a crash gets the first result back instead of recording twice.
 * - The principal's operating brief for `workflow` as a system prompt section.
 * - Enforcement before mapped tool calls, failing closed. Each tool task gets its own attempt id,
 *   kept in its memo, so an approval covers exactly one call and a call replayed after a restart
 *   is not charged twice. Task ids are not used: they repeat across storages.
 *   With `approvalWaitMs`, the hook asks the principal itself and waits: approve with
 *   `twinmeup approvals approve <id>` and the call goes ahead; decline and it is blocked with
 *   the reason. The wait survives a restart: the same call finds the same request.
 *
 * Use it in your own Harness:
 *
 *   import { createTwinMeUp } from "./twin-me-up-durable/index.ts";
 *   registry.install(createTwinMeUp({ workflow: "release", actionMap: "./twin-map.json" }));
 *
 * It talks to the twin through the `twinmeup` CLI, so nothing else needs installing.
 */
import { execFile, execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { Type } from "@earendil-works/pi-ai";
import { defineExtension, defineTool, hook, section, ToolTask } from "@earendil-works/pi-durable";

type Rule = { tool: string; action: string; workflow?: string; input_regex?: string };
type ActionMap = { workflow?: string; rules: Rule[] };
type Governed = { workflow?: string; action: string };
type Approval = { status: string; approval_id?: string; decision_note?: string | null };

export type TwinMeUpOptions = {
  /** The twinmeup CLI. Default: TWINMEUP_COMMAND, else twinmeup on PATH. */
  command?: string;
  /** This agent's identity. Default: TWINMEUP_ACTOR, else agent:pi-durable. */
  actor?: string;
  /** How messages name the principal. Default: the name given at `twinmeup init`. */
  principal?: string;
  /** The twin store directory. Default: TWINMEUP_HOME, else ~/.twinmeup. */
  home?: string;
  /** Put this workflow's operating brief in the system prompt. Default: TWINMEUP_WORKFLOW. */
  workflow?: string;
  /** What the agent is for, as the brief states it. */
  objective?: string;
  /** How long a brief is reused before it is read again. Default five minutes. */
  briefTtlMs?: number;
  /** Enforcement: an action map file or object, as for `twinmeup hook pre-tool-call`. */
  actionMap?: string | ActionMap;
  /**
   * When a governed call is not granted: 0 blocks it at once, the default. More than 0 asks
   * the principal from the hook and waits up to this long for their decision.
   */
  approvalWaitMs?: number;
  /** How often a waiting hook checks for the decision. Default five seconds. */
  pollMs?: number;
};

const PREVIEW = 1500;

function childEnv(home?: string): Record<string, string> {
  const env: Record<string, string> = {};
  for (const [key, value] of Object.entries(process.env)) {
    // A foreign PYTHONPATH breaks the twin's virtual environment.
    if (value !== undefined && key !== "PYTHONPATH") env[key] = value;
  }
  if (home) env.TWINMEUP_HOME = home;
  return env;
}

function loadMap(source: string | ActionMap | undefined): ActionMap | undefined {
  if (source === undefined || source === "") return undefined;
  return typeof source === "string" ? (JSON.parse(readFileSync(source, "utf8")) as ActionMap) : source;
}

function match(map: ActionMap, toolName: string, input: unknown): Governed | undefined {
  // The twin's own tools are never governed by the map.
  if (/^twin_/.test(toolName)) return undefined;
  const blob = JSON.stringify(input ?? {});
  for (const rule of map.rules) {
    if (!new RegExp(`^(?:${rule.tool})$`).test(toolName)) continue;
    if (rule.input_regex && !new RegExp(rule.input_regex).test(blob)) continue;
    return { workflow: rule.workflow ?? map.workflow, action: rule.action };
  }
  return undefined;
}

function sleep(ms: number, context?: { abortSignal?: AbortSignal }): Promise<void> {
  return new Promise((resolve, reject) => {
    const signal = context?.abortSignal;
    if (signal?.aborted) return reject(signal.reason ?? new Error("aborted"));
    const onAbort = () => {
      clearTimeout(timer);
      reject(signal?.reason ?? new Error("aborted"));
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

export function createTwinMeUp(options: TwinMeUpOptions = {}) {
  const command = options.command ?? process.env.TWINMEUP_COMMAND ?? "twinmeup";
  const actor = options.actor ?? process.env.TWINMEUP_ACTOR ?? "agent:pi-durable";
  const principal = options.principal ?? process.env.TWINMEUP_PRINCIPAL;
  const who = principal ?? "your principal";
  const workflow = options.workflow ?? process.env.TWINMEUP_WORKFLOW;
  const map = loadMap(options.actionMap ?? process.env.TWINMEUP_ACTION_MAP);
  const waitMs = options.approvalWaitMs ?? 0;
  const pollMs = options.pollMs ?? 5000;
  const briefTtlMs = options.briefTtlMs ?? 300_000;
  const env = childEnv(options.home);
  const base = ["--actor", actor, ...(principal ? ["--principal", principal] : [])];

  const run = (args: string[]) =>
    new Promise<{ code: number; stdout: string; stderr: string }>((resolve) => {
      execFile(command, [...base, ...args], { env, maxBuffer: 16 << 20, timeout: 120_000 }, (error, stdout, stderr) => {
        const code = error ? (typeof (error as any).code === "number" ? (error as any).code : -1) : 0;
        resolve({ code, stdout: String(stdout), stderr: String(stderr) });
      });
    });

  const callTwin = async (name: string, args: Record<string, unknown>): Promise<any> => {
    const { code, stdout, stderr } = await run(["tools", "call", name, JSON.stringify(args)]);
    if (code !== 0) throw new Error(stderr.trim() || `twinmeup exited with ${code}`);
    return JSON.parse(stdout);
  };

  /** true when allowed, false when approval is needed; anything else throws, which blocks. */
  const authorise = async (governed: Governed, attempt: string): Promise<boolean> => {
    const args = ["authorise", "--workflow", governed.workflow!, "--action", governed.action, "--attempt", attempt];
    const { code, stderr } = await run(args);
    if (code === 0) return true;
    if (code === 3) return false;
    throw new Error(stderr.trim() || `twinmeup authorise exited with ${code}`);
  };

  const waitForDecision = async (governed: Governed, call: any, nonce: string, context: any): Promise<Approval> => {
    const preview = JSON.stringify(call.arguments ?? {});
    let approval: Approval = await callTwin("twin_request_approval", {
      workflow: governed.workflow,
      action: governed.action,
      summary: `${actor} wants to run ${call.name} for ${governed.action}.`,
      details: preview.length > PREVIEW ? `${preview.slice(0, PREVIEW)}...` : preview,
      // The same call finds the same request, also after a restart.
      request_id: `pd-approval-${nonce}`,
    });
    if (approval.status === "not_needed") return { status: "approved" };
    const deadline = Date.now() + waitMs;
    while (approval.status === "pending" && Date.now() < deadline) {
      await sleep(Math.max(1, Math.min(pollMs, deadline - Date.now())), context);
      approval = await callTwin("twin_approval_status", { approval_id: approval.approval_id });
    }
    return approval;
  };

  const notGranted = (governed: Governed, approval?: Approval): string => {
    const head = `Twin Me Up: '${governed.action}' is not granted in '${governed.workflow}'.`;
    if (approval === undefined) {
      return `${head} ${who} must approve. Request it with twin_request_approval and wait until twin_approval_status says approved.`;
    }
    if (approval.status === "declined") {
      return `${head} ${who} declined it${approval.decision_note ? `: ${approval.decision_note}` : "."}`;
    }
    if (approval.status === "pending") {
      return `${head} No decision yet on ${approval.approval_id}, so it is blocked for now. It stays pending: check twin_approval_status.`;
    }
    return `${head} The approval is ${approval.status}. Request a new one if the work still needs it.`;
  };

  // One source of truth for the tools: the twin's own schemas, read once.
  const specs = JSON.parse(
    execFileSync(command, [...base, "tools", "schema", "--format", "mcp"], { env, encoding: "utf8" }),
  ) as any[];

  const tools = specs.map((spec) => {
    const readOnly = Boolean(spec.annotations?.readOnlyHint);
    const takesRequestId = Boolean(spec.inputSchema?.properties?.request_id);
    return defineTool({
      name: spec.name,
      description: spec.description ?? spec.name,
      parameters: Type.Unsafe(spec.inputSchema),
      replay: "safe",
      execute: async (args: any, api: any, context: any) => {
        const payload: Record<string, unknown> = { ...(args ?? {}) };
        // attempt_id spends approvals; it belongs to enforcement, never to the model.
        delete payload.attempt_id;
        if (!readOnly && takesRequestId && payload.request_id === undefined) {
          // Stable across a replay of this task, unique across storages (task ids repeat).
          payload.request_id = `pd-${await api.memo("twin-me-up.request", randomUUID(), context)}`;
        }
        const result = await callTwin(spec.name, payload);
        return { output: [{ type: "text", text: JSON.stringify(result, null, 2) }], details: result };
      },
    });
  });

  let brief: { text: string; at: number } | undefined;
  const sections = workflow
    ? [
        section("twin-me-up", async () => {
          if (brief === undefined || Date.now() - brief.at > briefTtlMs) {
            const result = await callTwin("twin_brief", {
              workflow,
              objective: options.objective ?? `Work in the ${workflow} workflow as a delegate.`,
            });
            brief = { text: String(result.brief ?? ""), at: Date.now() };
          }
          return brief.text || undefined;
        }),
      ]
    : [];

  const hooks = map
    ? [
        hook(ToolTask, {
          beforeTool: async (call: any, api: any, context: any) => {
            const governed = match(map, call.name, call.arguments);
            if (governed === undefined) return undefined;
            if (!governed.workflow) {
              return { block: `Twin Me Up: no workflow configured for '${governed.action}'. Fix the action map.` };
            }
            // One attempt per tool task, kept in its memo: stable across a restart, unique across
            // storages. Pi Durable task ids repeat between storages, so they are never the key.
            const nonce: string = await api.memo("twin-me-up.attempt", randomUUID(), context);
            const attempt = `pd-${nonce}`;
            try {
              if (await authorise(governed, attempt)) return undefined;
              if (waitMs <= 0) return { block: notGranted(governed) };
              const approval = await waitForDecision(governed, call, nonce, context);
              if (approval.status === "approved" && (await authorise(governed, attempt))) return undefined;
              return { block: notGranted(governed, approval) };
            } catch (error) {
              // Fail closed: a governed call is blocked when the twin cannot decide.
              return { block: `Twin Me Up unavailable, so '${governed.action}' is blocked: ${String(error)}` };
            }
          },
        }),
      ]
    : [];

  return defineExtension({ name: "twin-me-up", tools, sections, hooks });
}

export default createTwinMeUp;
