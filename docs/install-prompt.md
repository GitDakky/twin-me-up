# Install with one prompt

Paste the prompt below into any agent that can run shell commands: Hermes, pi, Claude Code,
Codex, Gemini CLI, Cursor, OpenCode, Goose and others. It installs Twin Me Up, connects the
agent to your twin and checks the result.

Two steps always stay with you, and the prompt tells the agent so:

1. **Choosing your passphrase.** You run `twinmeup init` yourself. The agent never sees the
   passphrase, and the twin refuses to let an agent set one up for you.
2. **Approving enforcement.** If the agent's system supports pre-action hooks, it shows you the
   action map and the hook configuration before installing anything.

## The prompt

```text
Install Twin Me Up for me and connect it to yourself. Twin Me Up is a local, open-source
work twin: it learns how I make decisions, briefs you at the start of work and tells you when
you need my approval. Repository: https://github.com/GitDakky/twin-me-up

Work step by step and show me each command you run.

1. Install it. Prefer uv:
     uv tool install twin-me-up
   If uv is not available, use pipx:
     pipx install twin-me-up
   If PyPI cannot be reached, install from GitHub instead:
     uv tool install git+https://github.com/GitDakky/twin-me-up
   Then check with: twinmeup version

2. Ask me to run `twinmeup init` myself, in my own terminal, and wait until I say it is done.
   It sets my name and a passphrase that only I know. Do not run it for me, do not ask me for
   the passphrase, and never type a passphrase anywhere.

3. Run `twinmeup connect --list`, pick the entry that matches you (or `generic`), then run
   `twinmeup connect <entry>` and carry out the steps it prints to register the MCP server and
   install the skill. If it offers optional enforcement hooks, show me the action map and the
   hook configuration first, and install them only if I agree.

4. Run `twinmeup doctor` and report the result.

5. If your system needs a new session to load MCP tools, tell me. Otherwise call twin_brief
   for workflow "general" and summarise what it says.

While you work with my twin:
- Call twin_brief at the start of a piece of work.
- Call twin_authorise before anything consequential: sending, paying, publishing, deleting or
  committing on my behalf. If the answer is "ask", prepare a draft, request my approval with
  twin_request_approval, and act only once twin_approval_status says it is approved.
- Give every write a request_id, so a retry never records anything twice.
- When I correct you, record it with twin_record_correction.
- Never try to confirm lessons, approve requests, grant authority, export or erase. Those are
  mine, and the twin will refuse you anyway.
```

## Agents without a shell

Chat assistants that cannot run commands can still use your twin through MCP if their app
supports MCP servers. Install and initialise it yourself:

```bash
uv tool install twin-me-up
twinmeup init
twinmeup connect generic
```

Then add the MCP server shown by `twinmeup connect generic` in the app's settings.

## After installing

- Run `twinmeup probes onboarding` and answer the questions for your first workflow.
- Write down your red lines as confirmed boundary lessons. They appear in every brief.
- Grant authority narrowly, with an expiry, and only once the twin's track record supports it.

See the [README](https://github.com/GitDakky/twin-me-up#teach-your-twin) for the full walkthrough.
