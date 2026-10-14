// Cross-language check: the TypeScript MCP SDK (as used by the pi extension) driving the
// Python twinmeup-mcp server. Run from this directory after `npm install`:
//   TWINMEUP_MCP_COMMAND=/path/to/twinmeup-mcp TWINMEUP_HOME=/tmp/twin node smoke.mjs
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

const env = Object.fromEntries(
  Object.entries(process.env).filter(([key, value]) => value !== undefined && key !== "PYTHONPATH"),
);
const client = new Client({ name: "twin-me-up-smoke", version: "0.1.0" });
await client.connect(
  new StdioClientTransport({
    command: process.env.TWINMEUP_MCP_COMMAND ?? "twinmeup-mcp",
    args: ["--actor", "agent:pi-smoke", "--principal", "Alex"],
    env,
  }),
);
const { tools } = await client.listTools();
const brief = await client.callTool({
  name: "twin_brief",
  arguments: { workflow: "quote-review", objective: "Compare quotes", actions: ["email.send"] },
});
const ask = await client.callTool({
  name: "twin_authorise",
  arguments: { workflow: "quote-review", action: "email.send" },
});
const report = {
  protocol: client.getServerVersion(),
  tools: tools.map((t) => `${t.name}${t.annotations?.readOnlyHint ? " (read-only)" : ""}`),
  brief_first_line: brief.content[0].text.split("\n")[0],
  authorise: ask.structuredContent?.decision,
};
console.log(JSON.stringify(report, null, 2));
await client.close();
if (report.authorise !== "ask" || tools.length === 0) process.exit(1);
