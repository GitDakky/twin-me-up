# Security

## Reporting a vulnerability

Please report vulnerabilities privately using GitHub's
[private vulnerability reporting](https://github.com/GitDakky/twin-me-up/security/advisories/new)
for this repository. Do not open a public issue. We aim to acknowledge reports within a week.

## Threat model

Twin Me Up holds a person's confirmed guidance, recorded decisions and grants of authority.
It is designed for agents that are capable but fallible: they misread instructions, follow
injected text, or try to be helpful in ways the person did not want.

### What it protects

| Risk | Protection |
|---|---|
| An agent confirms its own lessons or grants itself authority | Human-only operations require the principal's passphrase, read from a terminal. Agent surfaces (MCP, the CLI bridge, hooks) expose no human-only operations. |
| An agent sets up a twin before the principal does | `twinmeup init` refuses agent actors, and an unprotected twin can only be chosen from a real terminal. |
| A grant hands over control of the twin itself | Actions in the reserved `twin.` namespace can never be granted. |
| An agent acts without asking | `twin_authorise` returns `ask` for anything not covered by a live, scoped grant. Optional hooks enforce this outside the model. |
| An agent approves its own request | Approving and declining are human-only: they need the principal's passphrase, read from a terminal. Agents can request approval and read its status, nothing more. |
| One approval is stretched to cover more | An approval grants exactly the action requested, for one use and an hour by default. Enforcement spends it on the call that acts. Every hook run is a new attempt, and the Pi Durable extension keeps its attempt ids in durable memos, so ids a host reuses cannot ride on a spent approval. |
| A retry records something twice | Write tools take a `request_id`. A retry with the same id returns the first result. |
| A hook crashes or times out | Hooks fail closed: no decision means the call is blocked. |
| An agent disables enforcement | The example action maps guard the twin's store, human-only commands under either command name, and each host's own hook configuration. |
| Records are altered after the fact | Every write is appended to a hash-chained audit log. `twinmeup audit verify` detects breaks. |
| Exports leak | Bundles are written readable by the owner only. Held-out evaluation answers are sealed unless explicitly included. |
| Another program loads the wrong libraries | The commands ignore a foreign `PYTHONPATH`, so a polluted environment cannot swap in different code. |

### What it does not protect against

- **Code running as you with intent to cause harm.** Twin Me Up runs as your operating system
  user. Anything else running as that user can read and modify your files, including the store.
  The passphrase and hooks stop agents using the twin's own interfaces to act as you; they are
  not a sandbox. For hard isolation, run agents as a different user or in a container.
- **Agents that are not connected to hooks.** Without hooks the twin is advisory.
- **Approvals given without reading them.** An approval covers what the request names. Read
  the summary, and the draft where there is one, before you approve.
- **Tool names the action map does not cover.** Review and extend the map for your tools. Use
  `twinmeup action-map check` to test it.
- **Compromise of the model provider your agents use.** The twin never calls a model itself,
  but your agents send their context, including briefs, to whichever provider they use.

### Passphrases

Passphrases are stored as salted scrypt hashes in `settings.json`, readable by the owner only.
Choose one you do not use elsewhere. If you skip the passphrase with `--no-passphrase`, any
program running as you can perform human-only operations, and the twin warns you each time.

## Supply chain

- Releases are built in GitHub Actions from a tagged commit and published to PyPI with trusted
  publishing, so no long-lived upload token exists. PyPI records attestations for each file.
- Every GitHub Action is pinned to a full commit hash, and Dependabot proposes updates weekly.
- CodeQL scans the Python, TypeScript and workflow code on every change, and OpenSSF Scorecard
  reports on the project's security practices.
- The runtime has three dependencies: `mcp`, `pydantic` and `typer`.

## Supported versions

Security fixes go into the latest release only while the project is in alpha.
