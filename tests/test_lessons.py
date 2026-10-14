from __future__ import annotations

from datetime import date

import pytest

from conftest import AGENT, HUMAN, Clock, quote_episode
from twinmeup.errors import AuthorityError, NotFoundError, StateError
from twinmeup.models import LessonKind, LessonOrigin, LessonStatus
from twinmeup.service import TwinService


def propose(service: TwinService, actor: str = AGENT, **kw: object):  # type: ignore[no-untyped-def]
    args: dict[str, object] = {
        "workflow": "quote-review",
        "kind": LessonKind.CRITERION,
        "statement": "Compare quotes on complete scope before price.",
        "origin": LessonOrigin.INFERRED,
        "actor": actor,
    }
    args.update(kw)
    return service.propose_lesson(**args)  # type: ignore[arg-type]


def test_agent_proposes_but_cannot_confirm(service: TwinService) -> None:
    lesson = propose(service)
    assert lesson.status is LessonStatus.CANDIDATE
    with pytest.raises(AuthorityError):
        service.confirm_lesson(lesson.id, actor=AGENT)
    with pytest.raises(AuthorityError):
        propose(service, confirm=True)


def test_agent_cannot_author_a_written_rule(service: TwinService) -> None:
    with pytest.raises(AuthorityError):
        propose(service, origin=LessonOrigin.WRITTEN_RULE)
    rule = propose(service, actor=HUMAN, origin=LessonOrigin.WRITTEN_RULE, confirm=True)
    assert rule.status is LessonStatus.CONFIRMED and rule.decided_by == HUMAN


def test_human_confirms_once(service: TwinService) -> None:
    lesson = propose(service)
    confirmed = service.confirm_lesson(lesson.id, actor=HUMAN, note="Matches practice")
    assert confirmed.status is LessonStatus.CONFIRMED
    assert confirmed.decided_by == HUMAN
    with pytest.raises(StateError):
        service.confirm_lesson(lesson.id, actor=HUMAN)


def test_rejection_needs_a_reason(service: TwinService) -> None:
    lesson = propose(service)
    with pytest.raises(StateError):
        service.reject_lesson(lesson.id, actor=HUMAN, note="  ")
    rejected = service.reject_lesson(
        lesson.id, actor=HUMAN, note="Price matters more on small jobs"
    )
    assert rejected.status is LessonStatus.REJECTED


def test_revision_creates_a_confirmed_version(service: TwinService) -> None:
    lesson = propose(service)
    revised = service.revise_lesson(
        lesson.id,
        actor=HUMAN,
        statement="Compare complete scope before price on jobs over a set value.",
        conditions=["Job value above the delegated limit"],
    )
    assert revised.version == 2 and revised.status is LessonStatus.CONFIRMED
    assert revised.supersedes == lesson.id and revised.lineage_id == lesson.lineage_id
    history = service.lesson_history(lesson.lineage_id)
    assert [item.status for item in history] == [LessonStatus.SUPERSEDED, LessonStatus.CONFIRMED]
    with pytest.raises(StateError):
        service.revise_lesson(lesson.id, actor=HUMAN, statement="again")


def test_review_date_ends_authority(service: TwinService, clock: Clock) -> None:
    lesson = propose(service, actor=HUMAN, confirm=True, review_by=date(2026, 10, 20))
    assert lesson.is_authoritative(service.today())
    clock.advance(days=10)
    assert not lesson.is_authoritative(service.today())
    assert lesson.needs_review(service.today())


def test_unknown_episode_reference_is_refused(service: TwinService) -> None:
    with pytest.raises(NotFoundError):
        propose(service, episode_ids=["ep-missing"])
    episode = service.record_episode(quote_episode(), actor=AGENT)
    assert propose(service, episode_ids=[episode.id]).episode_ids == [episode.id]


def test_episode_cannot_be_overwritten_or_relabelled(service: TwinService) -> None:
    episode = service.record_episode(quote_episode(recorded_by=HUMAN), actor=AGENT)
    assert episode.recorded_by == AGENT  # the actor, not the payload, is the recorder
    with pytest.raises(StateError):
        service.record_episode(quote_episode(id=episode.id), actor=AGENT)
    with pytest.raises(StateError):
        service.amend_episode(episode.id, {"held_out": True}, actor=AGENT)
    amended = service.amend_episode(
        episode.id, {"outcome": "Repair held through winter"}, actor=AGENT
    )
    assert amended.outcome == "Repair held through winter"


def test_invalid_episode_rejected(service: TwinService) -> None:
    with pytest.raises(ValueError):
        service.record_episode(quote_episode(chosen="missing"), actor=AGENT)
    with pytest.raises(ValueError):
        service.record_episode(quote_episode(rejected={"mid": "contradiction"}), actor=AGENT)
