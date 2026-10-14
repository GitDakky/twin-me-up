"""The portable twin protocol: one registry of agent-safe tools.

Every adapter derives from this registry: the MCP server, the CLI bridge
(`twinmeup tools call`) for any shell-capable agent, and exported function-calling
schemas for frameworks that register tools directly. One definition means identical
behaviour in Hermes, Pi, Claude Code, Codex or a custom runtime.

Human-only operations (confirming learning, granting authority, designing evaluations,
erasure) are deliberately absent. No agent surface can reach them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

from . import probes
from .errors import TwinMeUpError
from .models import (
    MAX_ITEMS,
    Action,
    Episode,
    LessonKind,
    LessonOrigin,
    LessonStatus,
    Model,
    Option,
    Short,
    Slug,
    SourceRef,
    Text,
)
from .render import brief_text
from .retrieval import rank
from .service import TwinService, lesson_text

PROTOCOL_VERSION = "1"

SERVER_INSTRUCTIONS = (
    "Twin Me Up protocol. At the start of work in a workflow, call twin_brief and follow it: it "
    "holds the principal's confirmed guidance, hard limits, your authority and stop conditions. "
    "Call twin_authorise before any consequential action and obey 'ask'. When the principal "
    "corrects you, call twin_record_correction. Propose new guidance with twin_propose_lesson; "
    "it is never authoritative until the principal confirms it. You cannot confirm learning or "
    "grant yourself authority."
)


# --- inputs -----------------------------------------------------------------------------


class BriefInput(Model):
    workflow: Slug = Field(description="Workflow slug, for example quote-review.")
    objective: Text = Field(description="What you are about to do, in one sentence.")
    actions: list[Action] = Field(
        default_factory=list, description="Actions you expect to need, for example email.send."
    )
    include_hypotheses: bool = Field(
        default=False, description="Also list unconfirmed proposals, clearly labelled."
    )


class AuthoriseInput(Model):
    workflow: Slug
    action: Action = Field(description="The action you intend to take, for example email.send.")


class SearchInput(Model):
    workflow: Slug
    query: Text = Field(description="What you need guidance on.")
    limit: int = Field(default=8, ge=1, le=MAX_ITEMS)


class WorkflowInput(Model):
    workflow: Slug


class QueueInput(Model):
    workflow: Slug | None = None


class NextQuestionsInput(Model):
    episode_id: str
    limit: int = Field(default=3, ge=1, le=10)


class RecordDecisionInput(Model):
    workflow: Slug
    situation: Text
    goal: Text | None = None
    options: list[Option] = Field(default_factory=list, max_length=MAX_ITEMS)
    chosen: str | None = Field(default=None, description="The option id the principal chose.")
    rationale: Text | None = None
    rejected: dict[str, Text] = Field(
        default_factory=dict, description="Option id to the principal's reason for rejecting it."
    )
    cues: list[Text] = Field(default_factory=list, max_length=MAX_ITEMS)
    missing_information: list[Text] = Field(default_factory=list, max_length=MAX_ITEMS)
    reversal_conditions: list[Text] = Field(default_factory=list, max_length=MAX_ITEMS)
    escalation_triggers: list[Text] = Field(default_factory=list, max_length=MAX_ITEMS)
    outcome: Text | None = None
    sources: list[SourceRef] = Field(default_factory=list, max_length=MAX_ITEMS)


class AmendDecisionInput(Model):
    episode_id: str
    changes: dict[str, Any] = Field(
        description="Fields to merge into the recorded decision, using record_decision names."
    )


class RecordCorrectionInput(Model):
    workflow: Slug
    situation: Text = Field(description="What the task was.")
    agent_proposal: Text = Field(description="What you proposed or did.")
    human_decision: Text = Field(description="What the principal chose instead.")
    reason: Text | None = Field(default=None, description="The principal's reason, if given.")
    sources: list[SourceRef] = Field(default_factory=list, max_length=MAX_ITEMS)


class ProposeLessonInput(Model):
    workflow: Slug
    kind: LessonKind
    statement: Text = Field(description="One clear, testable rule or fact.")
    origin: Literal["elicited", "inferred", "correction"] = Field(
        default="inferred",
        description="elicited: the principal said it. inferred: your generalisation. "
        "correction: drawn from a correction.",
    )
    conditions: list[Text] = Field(default_factory=list, max_length=MAX_ITEMS)
    episode_ids: list[str] = Field(default_factory=list, max_length=MAX_ITEMS)
    confidence: float | None = Field(default=None, ge=0, le=1)
    review_by: date | None = None


# --- handlers ---------------------------------------------------------------------------


def _brief(service: TwinService, actor: str, args: BriefInput, principal: str) -> dict[str, Any]:
    packet = service.compile_handoff(
        workflow=args.workflow,
        objective=args.objective,
        actor=actor,
        requested_actions=args.actions,
        include_hypotheses=args.include_hypotheses,
    )
    return {
        "brief": brief_text(packet, principal),
        "packet_id": packet.id,
        "content_hash": packet.content_hash,
        "permitted_actions": packet.permitted_actions,
        "withheld_actions": packet.withheld_actions,
    }


def _authorise(service: TwinService, actor: str, args: AuthoriseInput, _: str) -> dict[str, Any]:
    return service.authorise(workflow=args.workflow, action=args.action, actor=actor).model_dump(
        mode="json"
    )


def _search(service: TwinService, actor: str, args: SearchInput, _: str) -> dict[str, Any]:
    today = service.today()
    pool = [
        lesson
        for lesson in service.lessons()
        if lesson.workflow in (args.workflow, "all") and lesson.is_authoritative(today)
    ]
    found = rank(args.query, pool, lesson_text, limit=args.limit, keep_zero=False)
    return {
        "lessons": [
            {
                "id": lesson.id,
                "version": lesson.version,
                "kind": lesson.kind.value,
                "statement": lesson.statement,
                "conditions": lesson.conditions,
            }
            for lesson in found
        ]
    }


def _queue(service: TwinService, actor: str, args: QueueInput, principal: str) -> dict[str, Any]:
    queue = service.review_queue(args.workflow)
    return {
        "awaiting_review": [
            {
                "id": lesson.id,
                "workflow": lesson.workflow,
                "kind": lesson.kind.value,
                "statement": lesson.statement,
                "origin": lesson.origin.value,
                "proposed_by": lesson.proposed_by,
            }
            for lesson in queue
        ],
        "note": f"Present these to {principal} for confirmation. You cannot confirm them.",
    }


def _track(service: TwinService, actor: str, args: WorkflowInput, _: str) -> dict[str, Any]:
    return service.track_record(args.workflow)


def _questions(
    service: TwinService, actor: str, args: NextQuestionsInput, _: str
) -> dict[str, Any]:
    episode = service.store.get(Episode, args.episode_id)
    return {
        "questions": [
            {"gap": p.gap, "question": p.question, "why": p.why}
            for p in probes.next_questions(episode, args.limit)
        ]
    }


def _episode_reply(episode: Episode) -> dict[str, Any]:
    return {
        "episode_id": episode.id,
        "next_questions": [
            {"gap": p.gap, "question": p.question} for p in probes.next_questions(episode)
        ],
    }


def _record(service: TwinService, actor: str, args: RecordDecisionInput, _: str) -> dict[str, Any]:
    return _episode_reply(service.record_episode(args.model_dump(mode="json"), actor=actor))


def _amend(service: TwinService, actor: str, args: AmendDecisionInput, _: str) -> dict[str, Any]:
    return _episode_reply(service.amend_episode(args.episode_id, args.changes, actor=actor))


def _correct(
    service: TwinService, actor: str, args: RecordCorrectionInput, _: str
) -> dict[str, Any]:
    episode = service.record_correction(
        workflow=args.workflow,
        situation=args.situation,
        agent_proposal=args.agent_proposal,
        human_decision=args.human_decision,
        reason=args.reason,
        actor=actor,
        sources=args.sources,
    )
    return _episode_reply(episode)


def _propose(
    service: TwinService, actor: str, args: ProposeLessonInput, principal: str
) -> dict[str, Any]:
    lesson = service.propose_lesson(
        workflow=args.workflow,
        kind=args.kind,
        statement=args.statement,
        origin=LessonOrigin(args.origin),
        actor=actor,
        conditions=args.conditions,
        episode_ids=args.episode_ids,
        confidence=args.confidence,
        review_by=args.review_by,
    )
    assert lesson.status is LessonStatus.CANDIDATE
    return {
        "lesson_id": lesson.id,
        "status": lesson.status.value,
        "note": f"Unconfirmed until {principal} confirms it. Do not rely on it yet.",
    }


# --- registry ---------------------------------------------------------------------------

Handler = Callable[[TwinService, str, Any, str], dict[str, Any]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    title: Short
    description: str
    input_model: type[BaseModel]
    read_only: bool
    handler: Handler


TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "twin_brief",
        "Get the operating brief",
        "Call at the start of work in a workflow. Returns the principal's confirmed guidance, "
        "hard limits, your authority, stop conditions and how to report back. Embed and follow it.",
        BriefInput,
        True,
        _brief,
    ),
    ToolSpec(
        "twin_authorise",
        "Check authority for an action",
        "Call before any consequential action (sending, paying, committing, writing to live "
        "systems). 'allow' means an active grant covers it. 'ask' means stop and get approval.",
        AuthoriseInput,
        True,
        _authorise,
    ),
    ToolSpec(
        "twin_search",
        "Search confirmed guidance",
        "Find the principal's confirmed guidance relevant to a question within a workflow.",
        SearchInput,
        True,
        _search,
    ),
    ToolSpec(
        "twin_review_queue",
        "List proposals awaiting the principal",
        "Unconfirmed lessons to present to the principal for confirmation, revision or rejection.",
        QueueInput,
        True,
        _queue,
    ),
    ToolSpec(
        "twin_track_record",
        "Show how well the twin knows a workflow",
        "Evidence and stage for a workflow: confirmed learning, corrections, evaluation results, "
        "grants and the recommended next step. It recommends; only the principal promotes.",
        WorkflowInput,
        True,
        _track,
    ),
    ToolSpec(
        "twin_next_questions",
        "Get follow-up questions for a decision",
        "The most valuable unanswered questions about a recorded decision. Ask them, then amend.",
        NextQuestionsInput,
        True,
        _questions,
    ),
    ToolSpec(
        "twin_record_decision",
        "Record a decision the principal made",
        "Capture a real decision: situation, options, choice, reasons for rejected options, cues, "
        "what would change it and when to escalate. Returns follow-up questions.",
        RecordDecisionInput,
        False,
        _record,
    ),
    ToolSpec(
        "twin_amend_decision",
        "Add answers to a recorded decision",
        "Merge the principal's answers to follow-up questions into a recorded decision.",
        AmendDecisionInput,
        False,
        _amend,
    ),
    ToolSpec(
        "twin_record_correction",
        "Record a correction from the principal",
        "Call whenever the principal edits, overrides or rejects your work. Captures both versions "
        "and the reason as learning material.",
        RecordCorrectionInput,
        False,
        _correct,
    ),
    ToolSpec(
        "twin_propose_lesson",
        "Propose new guidance",
        "Propose a lesson for the principal to confirm. It is never authoritative until confirmed.",
        ProposeLessonInput,
        False,
        _propose,
    ),
)
BY_NAME = {spec.name: spec for spec in TOOLS}


def call_tool(
    service: TwinService,
    actor: str,
    name: str,
    arguments: dict[str, Any] | None,
    *,
    principal: str = "the principal",
) -> dict[str, Any]:
    spec = BY_NAME.get(name)
    if spec is None:
        raise TwinMeUpError(f"unknown tool {name!r}; available: {sorted(BY_NAME)}")
    args = spec.input_model.model_validate(arguments or {})
    return spec.handler(service, actor, args, principal)


# --- schemas ----------------------------------------------------------------------------


def _inline(schema: dict[str, Any]) -> dict[str, Any]:
    """Resolve local $refs so clients that reject $defs still accept the schema."""
    defs = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(dict(defs[node["$ref"].split("/")[-1]]))
            return {k: resolve(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    resolved: dict[str, Any] = resolve(schema)
    return resolved


def input_schema(spec: ToolSpec) -> dict[str, Any]:
    return _inline(spec.input_model.model_json_schema())


def schemas(fmt: Literal["mcp", "openai"] = "mcp") -> list[dict[str, Any]]:
    if fmt == "openai":
        return [
            {
                "type": "function",
                "function": {
                    "name": s.name,
                    "description": s.description,
                    "parameters": input_schema(s),
                },
            }
            for s in TOOLS
        ]
    return [
        {
            "name": s.name,
            "title": s.title,
            "description": s.description,
            "inputSchema": input_schema(s),
            "annotations": {
                "readOnlyHint": s.read_only,
                "destructiveHint": False,
                "idempotentHint": s.read_only,
                "openWorldHint": False,
            },
        }
        for s in TOOLS
    ]
