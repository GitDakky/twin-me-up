"""Pre-action hook adapter: enforce twin authority outside the model.

Agent frameworks that run command hooks before a tool call (Hermes shell hooks, Claude
Code and Cursor PreToolUse hooks, and similar) pipe a JSON description of the call to
`twinmeup hook pre-tool-call`. An action map ties tool names to twin actions. Mapped calls
are checked against active grants; anything not granted is escalated for the principal's
approval or blocked, depending on what the host supports. Unmapped calls are left to the
host's own permission system: the twin governs the actions it knows about, nothing more.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator

from .models import Action, AuthorisationDecision, Model, Slug
from .service import RESERVED_ACTION_PREFIX, TwinService


class Dialect(StrEnum):
    # Escalate to the Hermes human-approval gate. Hermes auto-approves escalations when
    # approvals.mode is off or yolo is active; use BLOCK for such profiles.
    HERMES = "hermes"
    CLAUDE = "claude"  # Claude Code PreToolUse permissionDecision: ask
    BLOCK = "block"  # block JSON plus exit code 2: honoured by Hermes, Claude Code and Cursor


class Rule(Model):
    tool: str = Field(description="Regular expression matched against the full tool name.")
    action: Action
    workflow: Slug | None = None
    input_regex: str | None = Field(
        default=None, description="Optional regular expression searched in the JSON tool input."
    )

    @field_validator("tool", "input_regex")
    @classmethod
    def _compiles(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"invalid regular expression {value!r}: {exc}") from None
        return value


# The twin's own tools are never governed: an agent must always be able to ask.
TWIN_TOOLS = r"(?:.*__)?twin_[a-z_]+"


class ActionMap(Model):
    workflow: Slug | None = None
    rules: list[Rule] = Field(default_factory=list)
    ignore: list[str] = Field(
        default_factory=lambda: [TWIN_TOOLS],
        description="Regular expressions for tool names that are never governed.",
    )

    @classmethod
    def load(cls, path: Path) -> ActionMap:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def match(self, tool_name: str, tool_input: Any) -> tuple[str | None, str] | None:
        if any(re.fullmatch(pattern, tool_name) for pattern in self.ignore):
            return None
        blob = json.dumps(tool_input, sort_keys=True, default=str)
        for rule in self.rules:
            if re.fullmatch(rule.tool, tool_name) is None:
                continue
            if rule.input_regex is not None and re.search(rule.input_regex, blob) is None:
                continue
            return rule.workflow or self.workflow, rule.action
        return None


@dataclass(frozen=True)
class HookResponse:
    output: dict[str, Any]
    exit_code: int


def blocked(reason: str) -> HookResponse:
    return HookResponse({"decision": "block", "reason": reason}, 2)


def unavailable(exc: BaseException) -> HookResponse:
    """A governed call is blocked when the twin cannot decide: fail closed, never open."""
    return blocked(f"Twin Me Up unavailable, so this governed action is blocked: {exc}")


def _deny(dialect: Dialect, reason: str, rule_key: str) -> HookResponse:
    if dialect is Dialect.HERMES:
        return HookResponse({"action": "approve", "message": reason, "rule_key": rule_key}, 0)
    if dialect is Dialect.CLAUDE:
        decision = {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": reason,
        }
        return HookResponse({"hookSpecificOutput": decision}, 0)
    return blocked(reason)


def pre_tool_call(
    service: TwinService,
    actor: str,
    payload: dict[str, Any],
    action_map: ActionMap,
    dialect: Dialect,
    principal: str = "the principal",
) -> HookResponse:
    tool_name = str(payload.get("tool_name") or "")
    matched = action_map.match(tool_name, payload.get("tool_input"))
    if matched is None:
        return HookResponse({}, 0)
    workflow, action = matched
    rule_key = f"twinmeup:{workflow}:{action}"
    if workflow is None:
        return blocked(f"Twin Me Up: no workflow configured for '{action}'. Fix the action map.")
    result = service.authorise(workflow=workflow, action=action, actor=actor)
    if action.startswith(RESERVED_ACTION_PREFIX):
        return blocked(
            f"Twin Me Up: agents may not act as {principal} on the twin or change its store or "
            f"enforcement ('{action}'). Only {principal} can do this, directly."
        )
    if result.decision is AuthorisationDecision.ALLOW:
        return HookResponse({}, 0)
    reason = (
        f"Twin Me Up: '{action}' is not granted in the '{workflow}' workflow. "
        f"{principal} must approve this action."
    )
    return _deny(dialect, reason, rule_key)
