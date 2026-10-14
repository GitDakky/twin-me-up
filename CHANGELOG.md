# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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
