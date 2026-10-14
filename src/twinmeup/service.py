"""Service layer: the single home of business rules.

CLI and agent-facing surfaces are thin adapters over this module. The invariants:

1. Only a human principal can confirm, revise, reject or retire learning, author a
   written rule, grant authority, design evaluations or erase records.
2. Authority to act comes only from active grants. A twin's prediction never grants it.
3. A handoff relies only on confirmed, in-date lessons. Boundaries and escalation rules
   are always carried. Everything else is labelled as unverified.
4. Every write is audited in a tamper-evident chain.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from .errors import AuthorityError, StateError
from .models import (
    ALWAYS_CARRIED,
    GLOBAL_WORKFLOW,
    MAX_TEXT,
    SCHEMA_VERSION,
    ApprovalRequest,
    ApprovalStatus,
    Authorisation,
    AuthorisationDecision,
    CitedLesson,
    Episode,
    EvalAnswer,
    EvalCase,
    EvalResult,
    EvalRun,
    EvalSet,
    Grant,
    GrantUse,
    HandoffPacket,
    Lesson,
    LessonKind,
    LessonOrigin,
    LessonStatus,
    Option,
    Receipt,
    SourceRef,
    as_utc,
    is_human,
    safe_request_id,
    utcnow,
    validate_actor,
)
from .retrieval import rank
from .store import Store, canonical, sha256

DEFAULT_STOP_CONDITIONS: tuple[str, ...] = (
    "Stop before any external send, payment, signature, commitment or live-system write "
    "unless that exact action is listed as permitted.",
    "Stop and ask when required evidence is missing, sources conflict or the situation "
    "falls outside the guidance below.",
    "Treat anything listed as needing review or as an unconfirmed hypothesis as unverified. "
    "Do not rely on it.",
)
DEFAULT_EVIDENCE: tuple[str, ...] = (
    "Cite the source or lesson id for every factual claim and recommendation.",
    "List open questions and assumptions explicitly.",
    "State which stop condition, if any, was reached.",
)


# Actions that govern the twin itself (impersonating the principal, editing its store or
# its enforcement). Hooks map them so they are always refused; they can never be granted.
RESERVED_ACTION_PREFIX = "twin."

# How long an approval stays usable when the principal does not say otherwise.
APPROVAL_TTL = timedelta(hours=1)
ASK_REASON = "Not granted. Prepare a draft, then request approval with twin_request_approval."
# Result keys that name the record a write created, so erasure can find its receipt.
_ENTITY_KEYS = ("episode_id", "lesson_id", "approval_id")
_LATEST = datetime.max.replace(tzinfo=UTC)


def default_home() -> Path:
    return Path(os.environ.get("TWINMEUP_HOME") or Path.home() / ".twinmeup")


def require_human(actor: str, operation: str) -> None:
    validate_actor(actor)
    if not is_human(actor):
        raise AuthorityError(f"{operation} requires a human principal; {actor} is not one")


def lesson_text(lesson: Lesson) -> str:
    return " ".join([lesson.statement, *lesson.conditions, lesson.kind.value])


class TwinService:
    def __init__(self, store: Store, clock: Callable[[], datetime] | None = None) -> None:
        self.store = store
        self._clock = clock or utcnow

    @classmethod
    def open(cls, home: Path | None = None) -> TwinService:
        return cls(Store((home or default_home()) / "twin.db"))

    def close(self) -> None:
        self.store.close()

    def now(self) -> datetime:
        return self._clock()

    def today(self) -> date:
        return self.now().date()

    # --- episodes ---------------------------------------------------------------------

    def record_episode(self, data: dict[str, Any], *, actor: str) -> Episode:
        validate_actor(actor)
        payload = {**data, "recorded_by": actor}
        payload.setdefault("recorded_at", self.now().isoformat())
        episode = Episode.model_validate(payload)
        if self.store.find(Episode, episode.id) is not None:
            raise StateError(f"episode {episode.id} already exists; amend it instead")
        self.store.put(episode, actor=actor, action="episode.recorded")
        return episode

    def amend_episode(self, episode_id: str, changes: dict[str, Any], *, actor: str) -> Episode:
        validate_actor(actor)
        locked = {"id", "recorded_by", "recorded_at", "held_out"}
        if locked & changes.keys():
            raise StateError(f"these fields cannot be amended: {sorted(locked & changes.keys())}")
        current = self.store.get(Episode, episode_id)
        episode = Episode.model_validate({**current.model_dump(mode="json"), **changes})
        self.store.put(episode, actor=actor, action="episode.amended")
        return episode

    def episodes(self, workflow: str | None = None) -> list[Episode]:
        return self.store.select(Episode, lambda e: workflow is None or e.workflow == workflow)

    # --- lessons ----------------------------------------------------------------------

    def propose_lesson(
        self,
        *,
        workflow: str,
        kind: LessonKind,
        statement: str,
        origin: LessonOrigin,
        actor: str,
        conditions: Sequence[str] = (),
        episode_ids: Sequence[str] = (),
        sources: Sequence[SourceRef] = (),
        confidence: float | None = None,
        review_by: date | None = None,
        confirm: bool = False,
    ) -> Lesson:
        """Propose learning. A human may confirm in the same step; an agent never can."""
        validate_actor(actor)
        if origin is LessonOrigin.WRITTEN_RULE:
            require_human(actor, "authoring a written rule")
        if confirm:
            require_human(actor, "confirming a lesson")
        for episode_id in episode_ids:
            self.store.get(Episode, episode_id)
        now = self.now()
        lesson = Lesson(
            workflow=workflow,
            kind=kind,
            statement=statement,
            origin=origin,
            conditions=list(conditions),
            episode_ids=list(episode_ids),
            sources=list(sources),
            confidence=confidence,
            review_by=review_by,
            proposed_by=actor,
            proposed_at=now,
        )
        if confirm:
            lesson = lesson.model_copy(
                update={"status": LessonStatus.CONFIRMED, "decided_by": actor, "decided_at": now}
            )
        self.store.put(
            lesson, actor=actor, action="lesson.confirmed" if confirm else "lesson.proposed"
        )
        return lesson

    def _decide(
        self,
        lesson_id: str,
        *,
        actor: str,
        allowed: Iterable[LessonStatus],
        status: LessonStatus,
        note: str | None,
        action: str,
    ) -> Lesson:
        require_human(actor, f"marking a lesson {status.value}")
        lesson = self.store.get(Lesson, lesson_id)
        if lesson.status not in set(allowed):
            raise StateError(
                f"lesson {lesson_id} is {lesson.status.value}; cannot mark {status.value}"
            )
        updated = lesson.model_copy(
            update={
                "status": status,
                "decided_by": actor,
                "decided_at": self.now(),
                "decision_note": note,
            }
        )
        self.store.put(updated, actor=actor, action=action)
        return updated

    def confirm_lesson(self, lesson_id: str, *, actor: str, note: str | None = None) -> Lesson:
        return self._decide(
            lesson_id,
            actor=actor,
            allowed=[LessonStatus.CANDIDATE],
            status=LessonStatus.CONFIRMED,
            note=note,
            action="lesson.confirmed",
        )

    def reject_lesson(self, lesson_id: str, *, actor: str, note: str) -> Lesson:
        if not note.strip():
            raise StateError("say why the lesson is rejected; the reason is itself learning")
        return self._decide(
            lesson_id,
            actor=actor,
            allowed=[LessonStatus.CANDIDATE],
            status=LessonStatus.REJECTED,
            note=note,
            action="lesson.rejected",
        )

    def retire_lesson(self, lesson_id: str, *, actor: str, note: str) -> Lesson:
        return self._decide(
            lesson_id,
            actor=actor,
            allowed=[LessonStatus.CONFIRMED],
            status=LessonStatus.RETIRED,
            note=note,
            action="lesson.retired",
        )

    def revise_lesson(
        self,
        lesson_id: str,
        *,
        actor: str,
        statement: str | None = None,
        conditions: Sequence[str] | None = None,
        kind: LessonKind | None = None,
        review_by: date | None = None,
        note: str | None = None,
    ) -> Lesson:
        """A human revision creates a confirmed new version and supersedes the old one."""
        require_human(actor, "revising a lesson")
        old = self.store.get(Lesson, lesson_id)
        if old.status not in (LessonStatus.CANDIDATE, LessonStatus.CONFIRMED):
            raise StateError(
                f"lesson {lesson_id} is {old.status.value}; only live lessons can be revised"
            )
        now = self.now()
        new = Lesson(
            lineage_id=old.lineage_id,
            version=old.version + 1,
            workflow=old.workflow,
            kind=kind or old.kind,
            statement=statement or old.statement,
            conditions=list(old.conditions if conditions is None else conditions),
            origin=old.origin,
            status=LessonStatus.CONFIRMED,
            confidence=old.confidence,
            episode_ids=list(old.episode_ids),
            sources=list(old.sources),
            review_by=review_by if review_by is not None else old.review_by,
            proposed_by=old.proposed_by,
            proposed_at=old.proposed_at,
            decided_by=actor,
            decided_at=now,
            decision_note=note,
            supersedes=old.id,
        )
        retired = old.model_copy(
            update={"status": LessonStatus.SUPERSEDED, "decided_by": actor, "decided_at": now}
        )
        with self.store.transaction():
            self.store.put(retired, actor=actor, action="lesson.superseded")
            self.store.put(new, actor=actor, action="lesson.revised")
        return new

    def lessons(
        self, workflow: str | None = None, status: LessonStatus | None = None
    ) -> list[Lesson]:
        return self.store.select(
            Lesson,
            lambda lesson: (
                (workflow is None or lesson.workflow == workflow)
                and (status is None or lesson.status is status)
            ),
        )

    def lesson_history(self, lineage_id: str) -> list[Lesson]:
        history = self.store.select(Lesson, lambda lesson: lesson.lineage_id == lineage_id)
        return sorted(history, key=lambda lesson: lesson.version)

    # --- authority --------------------------------------------------------------------

    def grant(
        self,
        *,
        workflow: str,
        actions: Sequence[str],
        actor: str,
        expires_at: datetime | None = None,
        note: str | None = None,
        max_uses: int | None = None,
    ) -> Grant:
        require_human(actor, "granting authority")
        reserved = sorted(a for a in actions if a.startswith(RESERVED_ACTION_PREFIX))
        if reserved:
            raise StateError(
                f"{', '.join(reserved)}: '{RESERVED_ACTION_PREFIX}' actions govern the twin itself "
                "and can never be granted"
            )
        expires_at = as_utc(expires_at)
        if expires_at is not None and expires_at <= self.now():
            raise StateError("a grant cannot expire in the past")
        grant = Grant(
            workflow=workflow,
            actions=sorted(set(actions)),
            granted_by=actor,
            granted_at=self.now(),
            expires_at=expires_at,
            note=note,
            max_uses=max_uses,
        )
        self.store.put(grant, actor=actor, action="grant.created")
        return grant

    def revoke_grant(self, grant_id: str, *, actor: str, note: str | None = None) -> Grant:
        """Any actor may reduce authority; only a human may create it."""
        validate_actor(actor)
        grant = self.store.get(Grant, grant_id)
        if grant.revoked_at is not None:
            raise StateError(f"grant {grant_id} is already revoked")
        revoked = grant.model_copy(
            update={"revoked_at": self.now(), "revoked_by": actor, "note": note or grant.note}
        )
        self.store.put(revoked, actor=actor, action="grant.revoked")
        return revoked

    def active_grants(self, workflow: str) -> list[Grant]:
        now = self.now()
        return self.store.select(
            Grant,
            lambda g: g.workflow in (workflow, GLOBAL_WORKFLOW) and g.is_active(now),
        )

    # --- approvals --------------------------------------------------------------------

    def request_approval(
        self,
        *,
        workflow: str,
        action: str,
        summary: str,
        actor: str,
        details: str | None = None,
        sources: Sequence[SourceRef] = (),
    ) -> dict[str, Any]:
        """Ask the principal to approve one action. Agents call this when authorise says ask.

        Nothing is created when a grant already covers the action, and a second request for
        the same pending action returns the first, so a waiting agent never queues duplicates.
        """
        validate_actor(actor)
        if action.startswith(RESERVED_ACTION_PREFIX):
            raise StateError(
                f"'{action}': '{RESERVED_ACTION_PREFIX}' actions govern the twin itself and can "
                "never be approved"
            )
        with self.store.transaction():
            covering = [grant for grant in self.active_grants(workflow) if action in grant.actions]
            if covering:
                return {
                    "status": "not_needed",
                    "workflow": workflow,
                    "action": action,
                    "grant_ids": sorted(grant.id for grant in covering),
                }
            waiting = self.store.select(
                ApprovalRequest,
                lambda a: (
                    a.status is ApprovalStatus.PENDING
                    and a.workflow == workflow
                    and a.action == action
                    and a.requested_by == actor
                ),
            )
            if waiting:
                return self.approval_view(waiting[0])
            request = ApprovalRequest(
                workflow=workflow,
                action=action,
                summary=summary,
                details=details,
                sources=list(sources),
                requested_by=actor,
                requested_at=self.now(),
            )
            self.store.put(request, actor=actor, action="approval.requested")
        return self.approval_view(request)

    def approvals(
        self, *, workflow: str | None = None, pending_only: bool = False
    ) -> list[ApprovalRequest]:
        return self.store.select(
            ApprovalRequest,
            lambda a: (
                (workflow is None or a.workflow == workflow)
                and (not pending_only or a.status is ApprovalStatus.PENDING)
            ),
        )

    def approval_state(self, approval: ApprovalRequest) -> str:
        """pending, declined, or for an approval: approved, used, expired or revoked."""
        grant = self.store.find(Grant, approval.grant_id) if approval.grant_id else None
        if approval.status is not ApprovalStatus.APPROVED or grant is None:
            return approval.status.value
        if grant.revoked_at is not None:
            return "revoked"
        if grant.exhausted:
            return "used"
        if not grant.in_date(self.now()):
            return "expired"
        return "approved"

    def approval_view(self, approval: ApprovalRequest) -> dict[str, Any]:
        view: dict[str, Any] = {
            "approval_id": approval.id,
            "status": self.approval_state(approval),
            "workflow": approval.workflow,
            "action": approval.action,
            "summary": approval.summary,
            "requested_by": approval.requested_by,
            "requested_at": approval.requested_at.isoformat(),
        }
        if approval.details:
            view["details"] = approval.details
        if approval.decided_by is not None and approval.decided_at is not None:
            view["decided_by"] = approval.decided_by
            view["decided_at"] = approval.decided_at.isoformat()
            view["decision_note"] = approval.decision_note
        grant = self.store.find(Grant, approval.grant_id) if approval.grant_id else None
        if grant is not None:
            view["grant_id"] = grant.id
            view["expires_at"] = grant.expires_at.isoformat() if grant.expires_at else None
            view["uses_left"] = grant.uses_left
        return view

    def _pending(self, approval_id: str) -> ApprovalRequest:
        approval = self.store.get(ApprovalRequest, approval_id)
        if approval.status is not ApprovalStatus.PENDING:
            raise StateError(
                f"approval {approval_id} is {self.approval_state(approval)}; only pending "
                "requests can be decided"
            )
        return approval

    def approve(
        self,
        approval_id: str,
        *,
        actor: str,
        expires_at: datetime | None = None,
        max_uses: int = 1,
        note: str | None = None,
    ) -> ApprovalRequest:
        """Approve a pending request: a grant for exactly that action, single-use by default."""
        require_human(actor, "approving an action")
        now = self.now()
        expires_at = as_utc(expires_at) or now + APPROVAL_TTL
        if expires_at <= now:
            raise StateError("an approval cannot expire in the past")
        if max_uses < 1:
            raise StateError("an approval must allow at least one use")
        with self.store.transaction():
            approval = self._pending(approval_id)
            grant = Grant(
                workflow=approval.workflow,
                actions=[approval.action],
                granted_by=actor,
                granted_at=now,
                expires_at=expires_at,
                note=note or f"Approval {approval.id}",
                max_uses=max_uses,
                approval_id=approval.id,
            )
            self.store.put(grant, actor=actor, action="grant.created")
            approved = approval.model_copy(
                update={
                    "status": ApprovalStatus.APPROVED,
                    "decided_by": actor,
                    "decided_at": now,
                    "decision_note": note,
                    "grant_id": grant.id,
                }
            )
            self.store.put(approved, actor=actor, action="approval.approved")
            self._record_approval_decision(approved, chosen="approve", actor=actor)
        return approved

    def decline(self, approval_id: str, *, actor: str, note: str) -> ApprovalRequest:
        require_human(actor, "declining an approval")
        if not note.strip():
            raise StateError("say why you are declining; the reason is itself learning")
        with self.store.transaction():
            approval = self._pending(approval_id)
            declined = approval.model_copy(
                update={
                    "status": ApprovalStatus.DECLINED,
                    "decided_by": actor,
                    "decided_at": self.now(),
                    "decision_note": note,
                }
            )
            self.store.put(declined, actor=actor, action="approval.declined")
            self._record_approval_decision(declined, chosen="decline", actor=actor)
        return declined

    def _record_approval_decision(
        self, approval: ApprovalRequest, *, chosen: str, actor: str
    ) -> Episode:
        """Approving or declining is a real decision, so it becomes learning material."""
        situation = f"{approval.requested_by} asked to {approval.action}: {approval.summary}"
        return self.record_episode(
            {
                "workflow": approval.workflow,
                "situation": situation[:MAX_TEXT],
                "options": [
                    {"id": "approve", "label": f"Approve {approval.action}"[:200]},
                    {"id": "decline", "label": "Decline"},
                ],
                "chosen": chosen,
                "rationale": approval.decision_note,
                "sources": [{"kind": "decision_record", "locator": f"approval {approval.id}"}],
            },
            actor=actor,
        )

    # --- idempotency ------------------------------------------------------------------

    def once(
        self,
        *,
        actor: str,
        tool: str,
        request_id: str,
        arguments: dict[str, Any],
        run: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        """Run a write at most once per actor, tool and request_id.

        The write and its receipt commit together, so a retry after a crash or a dropped
        connection gets the first result back instead of recording twice. A failed write
        stores nothing, so it can be retried.
        """
        validate_actor(actor)
        receipt_id = "rc-" + sha256(canonical([actor, tool, request_id]))[:32]
        arguments_hash = sha256(canonical(arguments))
        with self.store.transaction():
            existing = self.store.find(Receipt, receipt_id)
            if existing is not None:
                replay: dict[str, Any] = {**existing.result, "replayed": True}
                if existing.arguments_hash != arguments_hash:
                    replay["warning"] = (
                        "This request_id was first used with different arguments. "
                        "This is the original result; use a new request_id for new work."
                    )
                return replay
            result = run()
            entity = next((str(result[key]) for key in _ENTITY_KEYS if result.get(key)), None)
            receipt = Receipt(
                id=receipt_id,
                actor=actor,
                tool=tool,
                request_id=request_id,
                arguments_hash=arguments_hash,
                entity_id=entity,
                result=result,
                created_at=self.now(),
            )
            self.store.put(receipt, actor=actor, action="receipt.recorded")
        return result

    # --- handoff ----------------------------------------------------------------------

    def compile_handoff(
        self,
        *,
        workflow: str,
        objective: str,
        actor: str,
        requested_actions: Sequence[str] = (),
        include_hypotheses: bool = False,
        limit: int = 20,
    ) -> HandoffPacket:
        validate_actor(actor)
        today = self.today()
        scoped = self.store.select(
            Lesson, lambda lesson: lesson.workflow in (workflow, GLOBAL_WORKFLOW)
        )
        authoritative = [lesson for lesson in scoped if lesson.is_authoritative(today)]
        carried = [lesson for lesson in authoritative if lesson.kind in ALWAYS_CARRIED]
        rankable = [lesson for lesson in authoritative if lesson.kind not in ALWAYS_CARRIED]
        local = rank(
            objective,
            [lesson for lesson in rankable if lesson.workflow == workflow],
            lesson_text,
            limit=limit,
            keep_zero=True,
        )
        shared = rank(
            objective,
            [lesson for lesson in rankable if lesson.workflow == GLOBAL_WORKFLOW],
            lesson_text,
            limit=limit,
            keep_zero=False,
        )
        guidance = (local + shared)[:limit]
        needs_review = [lesson for lesson in scoped if lesson.needs_review(today)]
        hypotheses = (
            [lesson for lesson in scoped if lesson.status is LessonStatus.CANDIDATE]
            if include_hypotheses
            else []
        )

        grants = self.active_grants(workflow)
        allowed = {action for grant in grants for action in grant.actions}
        requested = sorted(set(requested_actions))
        permitted = [action for action in requested if action in allowed]
        withheld = [action for action in requested if action not in allowed]
        grant_ids = sorted(grant.id for grant in grants if set(grant.actions) & set(permitted))

        stops = list(DEFAULT_STOP_CONDITIONS)
        if withheld:
            stops.append(
                "Not authorised: " + ", ".join(withheld) + ". Prepare a draft and request approval."
            )
        packet = HandoffPacket(
            workflow=workflow,
            objective=objective,
            compiled_by=actor,
            compiled_at=self.now(),
            guidance=[CitedLesson.of(lesson) for lesson in guidance],
            boundaries=[CitedLesson.of(lesson) for lesson in carried],
            needs_review=[CitedLesson.of(lesson) for lesson in needs_review],
            hypotheses=[CitedLesson.of(lesson) for lesson in hypotheses],
            requested_actions=requested,
            permitted_actions=permitted,
            withheld_actions=withheld,
            grant_ids=grant_ids,
            stop_conditions=stops,
            evidence_required=list(DEFAULT_EVIDENCE),
        )
        content = packet.model_dump(mode="json", exclude={"id", "compiled_at", "content_hash"})
        packet = packet.model_copy(update={"content_hash": sha256(canonical(content))})
        self.store.put(packet, actor=actor, action="handoff.compiled")
        return packet

    # --- evaluation design ------------------------------------------------------------

    def create_eval_set(self, *, name: str, workflow: str, actor: str) -> EvalSet:
        require_human(actor, "designing an evaluation")
        eval_set = EvalSet(name=name, workflow=workflow, created_by=actor, created_at=self.now())
        self.store.put(eval_set, actor=actor, action="eval.set_created")
        return eval_set

    def add_case(
        self,
        set_id: str,
        *,
        situation: str,
        options: Sequence[Option | dict[str, str]],
        chosen: str | None,
        actor: str,
        acceptable: Sequence[str] = (),
        expect_escalation: bool = False,
        rationale: str | None = None,
        episode_id: str | None = None,
    ) -> EvalCase:
        require_human(actor, "adding evaluation cases")
        eval_set = self.store.get(EvalSet, set_id)
        if eval_set.frozen_at is not None:
            raise StateError(f"evaluation set {set_id} is frozen")
        case = EvalCase(
            set_id=set_id,
            situation=situation,
            options=[Option.model_validate(o) for o in options],
            episode_id=episode_id,
        )
        ids = {option.id for option in case.options}
        for ref in [chosen, *acceptable]:
            if ref is not None and ref not in ids:
                raise StateError(f"answer {ref!r} is not one of the case options {sorted(ids)}")
        if chosen is None and not expect_escalation:
            raise StateError("a case with no chosen option must expect escalation")
        answer = EvalAnswer(
            id=f"ans-{case.id}",
            case_id=case.id,
            set_id=set_id,
            chosen=chosen,
            acceptable=sorted(set(acceptable) - {chosen} if chosen else set(acceptable)),
            expect_escalation=expect_escalation,
            rationale=rationale,
        )
        with self.store.transaction():
            self.store.put(case, actor=actor, action="eval.case_added")
            self.store.put(answer, actor=actor, action="eval.answer_sealed")
            if episode_id is not None:
                episode = self.store.get(Episode, episode_id)
                if not episode.held_out:
                    self.store.put(
                        episode.model_copy(update={"held_out": True}),
                        actor=actor,
                        action="episode.held_out",
                    )
        return case

    def add_case_from_episode(
        self, set_id: str, episode_id: str, *, actor: str, expect_escalation: bool = False
    ) -> EvalCase:
        episode = self.store.get(Episode, episode_id)
        if len(episode.options) < 2 or episode.chosen is None:
            raise StateError("an episode needs at least two options and a chosen option")
        return self.add_case(
            set_id,
            situation=episode.situation,
            options=episode.options,
            chosen=episode.chosen,
            actor=actor,
            expect_escalation=expect_escalation or bool(episode.escalation_triggers),
            rationale=episode.rationale,
            episode_id=episode.id,
        )

    def cases(self, set_id: str) -> list[EvalCase]:
        return self.store.select(EvalCase, lambda case: case.set_id == set_id)

    def answers(self, set_id: str) -> dict[str, EvalAnswer]:
        return {a.case_id: a for a in self.store.select(EvalAnswer, lambda a: a.set_id == set_id)}

    def manifest(self, set_id: str) -> str:
        cases = sorted(
            (c.model_dump(mode="json") for c in self.cases(set_id)), key=lambda c: c["id"]
        )
        answers = sorted(
            (a.model_dump(mode="json") for a in self.answers(set_id).values()),
            key=lambda a: a["id"],
        )
        return sha256(canonical({"cases": cases, "answers": answers}))

    def freeze_eval_set(self, set_id: str, *, actor: str) -> EvalSet:
        require_human(actor, "freezing an evaluation")
        eval_set = self.store.get(EvalSet, set_id)
        if eval_set.frozen_at is not None:
            raise StateError(f"evaluation set {set_id} is already frozen")
        if not self.cases(set_id):
            raise StateError("add at least one case before freezing")
        frozen = eval_set.model_copy(
            update={
                "frozen_at": self.now(),
                "frozen_by": actor,
                "manifest_hash": self.manifest(set_id),
            }
        )
        self.store.put(frozen, actor=actor, action="eval.set_frozen")
        return frozen

    # --- delegation loop --------------------------------------------------------------

    def authorise(
        self, *, workflow: str, action: str, actor: str, attempt_id: str | None = None
    ) -> Authorisation:
        """May this actor take this action now? Only active grants say yes. Always audited.

        A check without attempt_id changes nothing. Enforcement hooks pass the id of the tool
        call about to run: that spends one use of a limited grant (an approval), in the same
        transaction as the decision, and repeating the same attempt is allowed again without
        spending more. Unlimited grants are never spent.
        """
        validate_actor(actor)
        now = self.now()
        if attempt_id is not None:
            attempt_id = safe_request_id(attempt_id)
        with self.store.transaction():
            in_date = [
                grant
                for grant in self.store.select(
                    Grant,
                    lambda g: g.workflow in (workflow, GLOBAL_WORKFLOW) and action in g.actions,
                )
                if grant.in_date(now)
            ]
            repeats = [g for g in in_date if attempt_id is not None and g.used_by(attempt_id)]
            usable = [g for g in in_date if not g.exhausted and g not in repeats]
            unlimited = [g for g in usable if g.max_uses is None]
            covering: list[Grant] = []
            if repeats:
                covering, reason = repeats, "Already allowed for this attempt."
            elif unlimited:
                covering, reason = unlimited, "Covered by an active grant."
            elif usable:
                grant = min(usable, key=lambda g: (g.expires_at or _LATEST, g.granted_at))
                if attempt_id is None:
                    reason = (
                        f"Covered by an approval with {grant.uses_left} use(s) left. The tool "
                        "call that acts on it spends a use."
                    )
                else:
                    use = GrantUse(attempt_id=attempt_id, actor=actor, at=now)
                    grant = grant.model_copy(update={"uses": [*grant.uses, use]})
                    self.store.put(grant, actor=actor, action="grant.used")
                    reason = (
                        f"Covered by an approval. This attempt used it: {grant.uses_left} left."
                    )
                covering = [grant]
            else:
                reason = ASK_REASON
            result = Authorisation(
                workflow=workflow,
                action=action,
                decision=AuthorisationDecision.ALLOW if covering else AuthorisationDecision.ASK,
                grant_ids=sorted(g.id for g in covering),
                reason=reason,
                checked_by=actor,
                checked_at=now,
                attempt_id=attempt_id,
            )
            self.store.record_event(
                actor=actor,
                action=f"authorise.{result.decision.value}",
                entity_type="authorisation",
                entity_id=f"{workflow}:{action}",
                payload=result.model_dump(mode="json"),
            )
        return result

    def record_correction(
        self,
        *,
        workflow: str,
        situation: str,
        agent_proposal: str,
        human_decision: str,
        actor: str,
        reason: str | None = None,
        sources: Sequence[SourceRef] = (),
    ) -> Episode:
        """A correction is a decision episode: the human chose their version over the agent's."""
        validate_actor(actor)
        data: dict[str, Any] = {
            "workflow": workflow,
            "situation": situation,
            "options": [
                {"id": "agent", "label": agent_proposal[:200]},
                {"id": "principal", "label": human_decision[:200]},
            ],
            "chosen": "principal",
            "rationale": reason,
            "rejected": {"agent": reason} if reason else {},
            "sources": [
                {"kind": "correction", "locator": f"correction by {actor}"},
                *[s.model_dump(mode="json") for s in sources],
            ],
        }
        episode = self.record_episode(data, actor=actor)
        return episode

    def review_queue(self, workflow: str | None = None) -> list[Lesson]:
        """Candidates awaiting the principal, oldest first."""
        return sorted(
            self.lessons(workflow, LessonStatus.CANDIDATE), key=lambda lesson: lesson.proposed_at
        )

    def track_record(self, workflow: str) -> dict[str, Any]:
        """Evidence about how well the twin knows this workflow. Recommends; never promotes."""
        from .evals import score_run  # local import: evals depends on this module

        today = self.today()
        lessons = self.lessons(workflow)
        confirmed = [lesson for lesson in lessons if lesson.is_authoritative(today)]
        decided = [
            lesson
            for lesson in lessons
            if lesson.status
            in (LessonStatus.CONFIRMED, LessonStatus.REJECTED, LessonStatus.SUPERSEDED)
        ]
        rejected = [lesson for lesson in lessons if lesson.status is LessonStatus.REJECTED]
        episodes = self.episodes(workflow)
        corrections = [e for e in episodes if any(s.kind.value == "correction" for s in e.sources)]
        sets = self.store.select(
            EvalSet, lambda s: s.workflow == workflow and s.frozen_at is not None
        )
        latest: dict[str, dict[str, Any]] = {}
        runs = self.store.select(EvalRun)
        for eval_set in sets:
            for run in (r for r in runs if r.set_id == eval_set.id):
                score = score_run(self, run.id)
                latest[run.arm.value] = {
                    "run_id": run.id,
                    "set_id": eval_set.id,
                    "cases": score.cases,
                    "agreement": score.agreement.rate,
                    "escalation_recall": score.escalation_recall.rate,
                }
        grants = self.active_grants(workflow)
        carried = [lesson for lesson in confirmed if lesson.kind in ALWAYS_CARRIED]
        twin, rules = latest.get("twin"), latest.get("rules")
        if not carried:
            stage, next_step = "observe", "Capture the principal's boundaries and escalation rules."
        elif not sets:
            stage, next_step = "draft", "Freeze a held-out evaluation set before tuning further."
        elif not (twin and rules):
            stage, next_step = "draft", "Run the rules and twin arms on the frozen set."
        elif (twin["agreement"] or 0) <= (rules["agreement"] or 0) or twin["cases"] < 30:
            stage, next_step = (
                "draft",
                "The twin has not yet beaten the rules baseline on 30+ cases.",
            )
        else:
            stage = "supervised"
            next_step = "Evidence supports asking the principal whether to grant more authority."
        return {
            "workflow": workflow,
            "stage": stage,
            "recommended_next_step": next_step,
            "promotion_requires": "an explicit grant from the principal",
            "episodes": len(episodes),
            "corrections": len(corrections),
            "lessons_confirmed": len(confirmed),
            "lessons_awaiting_review": len(self.review_queue(workflow)),
            "rejection_rate": round(len(rejected) / len(decided), 3) if decided else None,
            "latest_evaluation": latest,
            "active_grants": [{"id": g.id, "actions": g.actions} for g in grants],
        }

    # --- portability and erasure ------------------------------------------------------

    def export_bundle(self, *, include_answers: bool = False) -> dict[str, Any]:
        records = self.store.dump()
        if not include_answers:
            records.pop("eval_answer", None)
        return {
            "format": "twinmeup.bundle",
            "schema_version": SCHEMA_VERSION,
            "exported_at": self.now().isoformat(),
            "audit_head": self.store.head(),
            "includes_sealed_answers": include_answers,
            "records": records,
        }

    def erase_episode(self, episode_id: str, *, actor: str, reason: str) -> dict[str, Any]:
        """Erase an episode and everything derived from it, keeping the audit chain valid."""
        require_human(actor, "erasing records")
        self.store.get(Episode, episode_id)
        lineages = {
            lesson.lineage_id
            for lesson in self.store.select(Lesson, lambda lesson: episode_id in lesson.episode_ids)
        }
        lessons = self.store.select(Lesson, lambda lesson: lesson.lineage_id in lineages)
        lesson_ids = {lesson.id for lesson in lessons}
        packets = self.store.select(
            HandoffPacket,
            lambda packet: bool(
                lesson_ids
                & {
                    cited.id
                    for cited in (
                        *packet.guidance,
                        *packet.boundaries,
                        *packet.needs_review,
                        *packet.hypotheses,
                    )
                }
            ),
        )
        cases = self.store.select(EvalCase, lambda case: case.episode_id == episode_id)
        case_ids = {case.id for case in cases}
        results = self.store.select(EvalResult, lambda result: result.case_id in case_ids)
        receipts = self.store.select(
            Receipt, lambda receipt: receipt.entity_id in {episode_id, *lesson_ids}
        )
        redacted = 0
        with self.store.transaction():
            for receipt in receipts:
                redacted += self.store.erase(Receipt, receipt.id, actor=actor, reason=reason)
            for packet in packets:
                redacted += self.store.erase(HandoffPacket, packet.id, actor=actor, reason=reason)
            for lesson in lessons:
                redacted += self.store.erase(Lesson, lesson.id, actor=actor, reason=reason)
            for result in results:
                redacted += self.store.erase(EvalResult, result.id, actor=actor, reason=reason)
            for case in cases:
                redacted += self.store.erase(
                    EvalAnswer, f"ans-{case.id}", actor=actor, reason=reason
                )
                redacted += self.store.erase(EvalCase, case.id, actor=actor, reason=reason)
            redacted += self.store.erase(Episode, episode_id, actor=actor, reason=reason)
        return {
            "episode": episode_id,
            "lessons": sorted(lesson_ids),
            "handoffs": sorted(packet.id for packet in packets),
            "eval_cases": sorted(case_ids),
            "receipts": len(receipts),
            "events_redacted": redacted,
        }
