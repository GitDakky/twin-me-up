"""Domain model for governed professional twins.

Everything a twin knows is an explicit record with provenance. Nothing here models
personality or emotion: the unit of learning is a real professional decision and the
guidance a human principal has confirmed from it.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Annotated, Any
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

SCHEMA_VERSION = 2
GLOBAL_WORKFLOW = "all"
MAX_TEXT = 4000
MAX_ITEMS = 50

_SLUG = r"^[a-z0-9][a-z0-9._-]{0,63}$"
_ACTOR = r"^(human|agent):[a-z0-9][a-z0-9._-]{0,63}$"
_ACTION = r"^[a-z][a-z0-9_.:-]{0,63}$"
# Idempotency keys and tool-call ids from hosts (toolu_..., call_..., parent/1): letters,
# digits and a few separators.
_REQUEST_ID = r"^[A-Za-z0-9][A-Za-z0-9_.:@/|+=-]{0,255}$"

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TEXT)]
Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Slug = Annotated[str, StringConstraints(pattern=_SLUG)]
ActorId = Annotated[str, StringConstraints(pattern=_ACTOR)]
Action = Annotated[str, StringConstraints(pattern=_ACTION)]
RequestId = Annotated[str, StringConstraints(pattern=_REQUEST_ID)]


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


def validate_actor(actor: str) -> str:
    if re.fullmatch(_ACTOR, actor) is None:
        raise ValueError(f"actor must look like 'human:<name>' or 'agent:<name>', got {actor!r}")
    return actor


def safe_request_id(raw: str) -> str:
    """Use a host's call id as an attempt key, hashing any id this format does not allow."""
    if re.fullmatch(_REQUEST_ID, raw):
        return raw
    return "h-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def is_human(actor: str) -> bool:
    return re.fullmatch(_ACTOR, actor) is not None and actor.startswith("human:")


def as_utc(value: datetime | None) -> datetime | None:
    """Treat naive datetimes as UTC so comparisons never mix naive and aware values."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --- provenance -------------------------------------------------------------------------


class SourceKind(StrEnum):
    INTERVIEW = "interview"
    DOCUMENT = "document"
    DECISION_RECORD = "decision_record"
    CORRECTION = "correction"
    SYSTEM_RECORD = "system_record"
    OTHER = "other"


class SourceRef(Model):
    """Where knowledge came from. A locator points at the source; it does not copy it."""

    kind: SourceKind
    locator: Short
    note: Text | None = None


class Option(Model):
    id: Slug
    label: Short


def _check_options(options: list[Option], *referenced: str | None) -> None:
    ids = [option.id for option in options]
    if len(ids) != len(set(ids)):
        raise ValueError("option ids must be unique")
    for ref in referenced:
        if ref is not None and ref not in ids:
            raise ValueError(f"{ref!r} is not one of the option ids {ids}")


# --- capture ----------------------------------------------------------------------------


class Episode(Model):
    """One real professional decision, captured with cognitive-task-analysis probes."""

    id: str = Field(default_factory=lambda: new_id("ep"))
    workflow: Slug
    situation: Text
    goal: Text | None = None
    options: list[Option] = Field(default_factory=list, max_length=MAX_ITEMS)
    chosen: str | None = None
    rationale: Text | None = None
    rejected: dict[str, Text] = Field(default_factory=dict)
    cues: list[Text] = Field(default_factory=list, max_length=MAX_ITEMS)
    missing_information: list[Text] = Field(default_factory=list, max_length=MAX_ITEMS)
    reversal_conditions: list[Text] = Field(default_factory=list, max_length=MAX_ITEMS)
    escalation_triggers: list[Text] = Field(default_factory=list, max_length=MAX_ITEMS)
    outcome: Text | None = None
    sources: list[SourceRef] = Field(default_factory=list, max_length=MAX_ITEMS)
    held_out: bool = False
    recorded_by: ActorId
    recorded_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _consistent(self) -> Episode:
        _check_options(self.options, self.chosen, *self.rejected)
        if self.chosen is not None and self.chosen in self.rejected:
            raise ValueError("the chosen option cannot also be rejected")
        return self


# --- learning ---------------------------------------------------------------------------


class LessonKind(StrEnum):
    FACT = "fact"
    CRITERION = "criterion"
    EXCEPTION = "exception"
    ESCALATION = "escalation"
    PROCEDURE = "procedure"
    BOUNDARY = "boundary"
    PREFERENCE = "preference"


# Safety-relevant kinds are always carried into a handoff, never ranked out by retrieval.
ALWAYS_CARRIED = frozenset({LessonKind.BOUNDARY, LessonKind.ESCALATION})


class LessonOrigin(StrEnum):
    WRITTEN_RULE = "written_rule"  # authored directly by the human as a rule
    ELICITED = "elicited"  # drawn from an interview or decision episode
    INFERRED = "inferred"  # proposed by a model from episodes
    CORRECTION = "correction"  # learned from reviewing a twin error


class LessonStatus(StrEnum):
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    RETIRED = "retired"


class Lesson(Model):
    """A unit of learned guidance. Authoritative only once a human confirms it."""

    id: str = Field(default_factory=lambda: new_id("ls"))
    lineage_id: str = ""
    version: int = Field(default=1, ge=1)
    workflow: Slug
    kind: LessonKind
    statement: Text
    conditions: list[Text] = Field(default_factory=list, max_length=MAX_ITEMS)
    origin: LessonOrigin
    status: LessonStatus = LessonStatus.CANDIDATE
    confidence: float | None = Field(default=None, ge=0, le=1)
    episode_ids: list[str] = Field(default_factory=list, max_length=MAX_ITEMS)
    sources: list[SourceRef] = Field(default_factory=list, max_length=MAX_ITEMS)
    review_by: date | None = None
    proposed_by: ActorId
    proposed_at: datetime = Field(default_factory=utcnow)
    decided_by: ActorId | None = None
    decided_at: datetime | None = None
    decision_note: Text | None = None
    supersedes: str | None = None

    @model_validator(mode="after")
    def _lineage(self) -> Lesson:
        if not self.lineage_id:
            self.lineage_id = self.id
        return self

    def is_authoritative(self, today: date) -> bool:
        return self.status is LessonStatus.CONFIRMED and (
            self.review_by is None or self.review_by >= today
        )

    def needs_review(self, today: date) -> bool:
        return (
            self.status is LessonStatus.CONFIRMED
            and self.review_by is not None
            and self.review_by < today
        )


# --- authority --------------------------------------------------------------------------


class GrantUse(Model):
    """One enforced attempt that relied on a limited grant."""

    attempt_id: RequestId
    actor: ActorId
    at: datetime

    @field_validator("at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return as_utc(value) or value


class Grant(Model):
    """Explicit authority from a human principal: the only source of permission to act."""

    id: str = Field(default_factory=lambda: new_id("gr"))
    workflow: Slug
    actions: list[Action] = Field(min_length=1, max_length=MAX_ITEMS)
    granted_by: ActorId
    granted_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    revoked_by: ActorId | None = None
    note: Text | None = None
    # None means unlimited until expiry or revocation. Approvals issue single-use grants.
    max_uses: int | None = Field(default=None, ge=1)
    uses: list[GrantUse] = Field(default_factory=list)
    approval_id: str | None = None

    @field_validator("expires_at", "revoked_at", "granted_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return as_utc(value)

    @property
    def exhausted(self) -> bool:
        return self.max_uses is not None and len(self.uses) >= self.max_uses

    @property
    def uses_left(self) -> int | None:
        return None if self.max_uses is None else max(self.max_uses - len(self.uses), 0)

    def in_date(self, now: datetime) -> bool:
        if self.revoked_at is not None:
            return False
        return self.expires_at is None or self.expires_at > now

    def used_by(self, attempt_id: str) -> bool:
        return any(use.attempt_id == attempt_id for use in self.uses)

    def is_active(self, now: datetime) -> bool:
        return self.in_date(now) and not self.exhausted


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DECLINED = "declined"


class ApprovalRequest(Model):
    """An agent's request for the principal to approve one action.

    Approving it issues a grant for exactly that action, single-use by default and short-lived,
    so a waiting agent can act once and no more. Declining it is recorded with the reason.
    """

    id: str = Field(default_factory=lambda: new_id("ap"))
    workflow: Slug
    action: Action
    summary: Text
    details: Text | None = None
    sources: list[SourceRef] = Field(default_factory=list, max_length=MAX_ITEMS)
    requested_by: ActorId
    requested_at: datetime = Field(default_factory=utcnow)
    status: ApprovalStatus = ApprovalStatus.PENDING
    decided_by: ActorId | None = None
    decided_at: datetime | None = None
    decision_note: Text | None = None
    grant_id: str | None = None

    @field_validator("requested_at", "decided_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return as_utc(value)


class Receipt(Model):
    """The first result of a write made with a request_id. Retries get this result back."""

    id: str
    actor: ActorId
    tool: str
    request_id: RequestId
    arguments_hash: str
    entity_id: str | None = None
    result: dict[str, Any]
    created_at: datetime = Field(default_factory=utcnow)


# --- handoff ----------------------------------------------------------------------------


class CitedLesson(Model):
    id: str
    version: int
    kind: LessonKind
    origin: LessonOrigin
    statement: str
    conditions: list[str] = Field(default_factory=list)

    @classmethod
    def of(cls, lesson: Lesson) -> CitedLesson:
        return cls(
            id=lesson.id,
            version=lesson.version,
            kind=lesson.kind,
            origin=lesson.origin,
            statement=lesson.statement,
            conditions=list(lesson.conditions),
        )


class HandoffPacket(Model):
    """What a receiving agent may rely on, what it may do, and where it must stop."""

    id: str = Field(default_factory=lambda: new_id("ho"))
    workflow: Slug
    objective: Text
    compiled_by: ActorId
    compiled_at: datetime = Field(default_factory=utcnow)
    guidance: list[CitedLesson] = Field(default_factory=list)
    boundaries: list[CitedLesson] = Field(default_factory=list)
    needs_review: list[CitedLesson] = Field(default_factory=list)
    hypotheses: list[CitedLesson] = Field(default_factory=list)
    requested_actions: list[Action] = Field(default_factory=list)
    permitted_actions: list[Action] = Field(default_factory=list)
    withheld_actions: list[Action] = Field(default_factory=list)
    grant_ids: list[str] = Field(default_factory=list)
    stop_conditions: list[str] = Field(default_factory=list)
    evidence_required: list[str] = Field(default_factory=list)
    content_hash: str = ""


class AuthorisationDecision(StrEnum):
    ALLOW = "allow"
    ASK = "ask"  # not granted: stop and request the principal's approval


class Authorisation(Model):
    """The answer to 'may I do this now?'. Recorded in the audit log."""

    workflow: Slug
    action: Action
    decision: AuthorisationDecision
    grant_ids: list[str] = Field(default_factory=list)
    reason: str
    checked_by: ActorId
    checked_at: datetime = Field(default_factory=utcnow)
    # Set by enforcement hooks: the tool call about to run. It spends limited grants once.
    attempt_id: str | None = None


# --- evaluation -------------------------------------------------------------------------


class EvalArm(StrEnum):
    GENERIC = "generic"  # task and options only
    RULES = "rules"  # plus the human's explicitly written rules
    TWIN = "twin"  # plus all confirmed learning and non-held-out episodes


class EvalSet(Model):
    id: str = Field(default_factory=lambda: new_id("es"))
    name: Short
    workflow: Slug
    created_by: ActorId
    created_at: datetime = Field(default_factory=utcnow)
    frozen_at: datetime | None = None
    frozen_by: ActorId | None = None
    manifest_hash: str | None = None


class EvalCase(Model):
    """What a responder sees. Never contains the human's answer."""

    id: str = Field(default_factory=lambda: new_id("ec"))
    set_id: str
    situation: Text
    options: list[Option] = Field(min_length=2, max_length=MAX_ITEMS)
    episode_id: str | None = None

    @model_validator(mode="after")
    def _unique_options(self) -> EvalCase:
        _check_options(self.options)
        return self


class EvalAnswer(Model):
    """The sealed human answer. Used only for scoring, never for prompting."""

    id: str
    case_id: str
    set_id: str
    chosen: str | None = None  # None means the right move is to escalate or abstain
    acceptable: list[str] = Field(default_factory=list, max_length=MAX_ITEMS)
    expect_escalation: bool = False
    rationale: Text | None = None


class CasePrompt(Model):
    case_id: str
    workflow: str
    situation: str
    options: list[Option]
    context: str = ""


class ResponderOutput(Model):
    decision: str | None = None
    escalate: bool = False
    abstain: bool = False
    confidence: float | None = Field(default=None, ge=0, le=1)
    rationale: str = Field(default="", max_length=MAX_TEXT)


class EvalRun(Model):
    id: str = Field(default_factory=lambda: new_id("er"))
    set_id: str
    arm: EvalArm
    responder_id: Short
    manifest_hash: str
    context_hash: str
    lessons_used: list[str] = Field(default_factory=list)
    leakage_excluded: list[str] = Field(default_factory=list)
    started_by: ActorId
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None


class EvalResult(Model):
    id: str
    run_id: str
    case_id: str
    output: ResponderOutput | None = None
    error: str | None = None
    latency_ms: int | None = None
