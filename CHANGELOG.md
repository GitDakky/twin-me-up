# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] - 2026-10-14

### Added

- Durable approvals. When `twin_authorise` says ask, an agent requests approval with
  `twin_request_approval` and waits on `twin_approval_status`. You decide with
  `twinmeup approvals approve` or `decline`, behind your passphrase. An approval grants exactly
  that action, once and for an hour unless you say otherwise (`--uses`, `--for`), and every
  approval or decline is recorded as a decision the twin learns from.
- Limited grants: `twinmeup grant add --uses N`. Enforcement hooks spend one use per governed
  tool call; advisory checks never spend anything.
- Safe retries. Every write tool takes an optional `request_id`, and a retry with the same id
  returns the first result instead of recording twice, also after a crash.
- A Pi Durable extension (experimental), from `twinmeup extension export pi-durable`: the twin's
  tools, your brief in the system prompt, and enforcement that can ask you and wait through a
  restart. Tested live against Pi Durable 1.2.0.
- A desktop extension bundle (`.mcpb`) for Claude Desktop, Smithery and other MCPB hosts. It
  uses the uv runtime, so one bundle works on every platform, and each release builds and
  attaches it.
- `twinmeup status` reports pending approvals.

### Changed

- The store schema is version 2. Older stores migrate when opened.
- An `ask` tells the agent how to request approval.
- The agent skill teaches the approval step and request ids, and is licensed Apache-2.0 like
  the rest of the project.

### Security

- Every governed tool call is its own attempt. Host tool-call ids are never used to recognise
  a repeat, because some providers reuse them across turns and sessions.

## [0.1.0] - 2026-10-14

First public release, under the Apache License 2.0.

### Added

- Local twin store: decisions, corrections, lessons with provenance and history, grants with
  expiry, and a hash-chained audit log.
- Ten agent tools over MCP (stdio), the same tools through a CLI bridge, and OpenAI and
  Anthropic function-calling schemas.
- Human-only operations protected by a passphrase, with agent actors refused.
- Pre-action enforcement hooks for Hermes, Claude Code and pi, failing closed, with example
  action maps that guard the twin and each host's hook configuration.
- `twinmeup connect` guides for thirteen agent systems and frameworks, and a copy-paste
  install prompt.
- A native pi extension and an agent skill that teaches the working loop.
- Held-out evaluation with generic, rules and twin arms, and sample-size warnings.
- Portable export with sealed evaluation answers, and erasure of records with everything
  derived from them.
- `twinmeup doctor` for a full health check, including a live MCP handshake.
- A `twin-me-up` command, so `uvx twin-me-up` starts the MCP server, and a listing in the
  official MCP Registry.
- Commands that ignore a foreign `PYTHONPATH`, so polluted environments cannot break them.
- Releases built and published from GitHub Actions with PyPI trusted publishing.
