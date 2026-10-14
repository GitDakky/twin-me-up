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
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .errors import AuthorityError, StateError
from .models import (
    ALWAYS_CARRIED,
    GLOBAL_WORKFLOW,
    SCHEMA_VERSION,
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
    HandoffPacket,
    Lesson,
    LessonKind,
    LessonOrigin,
    LessonStatus,
    Option,
    SourceRef,
    as_utc,
    is_human,
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

    def authorise(self, *, workflow: str, action: str, actor: str) -> Authorisation:
        """May this actor take this action now? Only active grants say yes. Always audited."""
        validate_actor(actor)
        grants = [g for g in self.active_grants(workflow) if action in g.actions]
        if grants:
            decision, reason = AuthorisationDecision.ALLOW, "Covered by an active grant."
        else:
            decision = AuthorisationDecision.ASK
            reason = "Not granted. Prepare the work as a draft and ask the principal to approve."
        result = Authorisation(
            workflow=workflow,
            action=action,
            decision=decision,
            grant_ids=sorted(g.id for g in grants),
            reason=reason,
            checked_by=actor,
            checked_at=self.now(),
        )
        self.store.record_event(
            actor=actor,
            action=f"authorise.{decision.value}",
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
        redacted = 0
        with self.store.transaction():
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
            "events_redacted": redacted,
        }
