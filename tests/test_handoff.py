from __future__ import annotations

from datetime import timedelta

import pytest

from conftest import AGENT, HUMAN, Clock
from twinmeup.errors import AuthorityError, StateError
from twinmeup.models import GLOBAL_WORKFLOW, LessonKind, LessonOrigin
from twinmeup.render import brief_text, handoff_markdown
from twinmeup.service import TwinService


def confirmed(
    service: TwinService,
    statement: str,
    kind: LessonKind = LessonKind.CRITERION,
    workflow: str = "quote-review",
):  # type: ignore[no-untyped-def]
    return service.propose_lesson(
        workflow=workflow,
        kind=kind,
        statement=statement,
        origin=LessonOrigin.ELICITED,
        actor=HUMAN,
        confirm=True,
    )


def test_no_grant_means_draft_only(service: TwinService) -> None:
    packet = service.compile_handoff(
        workflow="quote-review",
        objective="Review roofing quotes",
        actor=AGENT,
        requested_actions=["email.send"],
    )
    assert packet.permitted_actions == [] and packet.withheld_actions == ["email.send"]
    assert any("Not authorised: email.send" in stop for stop in packet.stop_conditions)
    assert "No actions are authorised" in handoff_markdown(packet)


def test_permitted_actions_are_requested_intersect_granted(service: TwinService) -> None:
    grant = service.grant(
        workflow="quote-review", actions=["draft.prepare", "crm.read"], actor=HUMAN
    )
    packet = service.compile_handoff(
        workflow="quote-review",
        objective="Review quotes",
        actor=AGENT,
        requested_actions=["crm.read", "payment.approve"],
    )
    assert packet.permitted_actions == ["crm.read"]
    assert packet.withheld_actions == ["payment.approve"]
    assert packet.grant_ids == [grant.id]


def test_only_humans_grant_but_anyone_revokes(service: TwinService, clock: Clock) -> None:
    with pytest.raises(AuthorityError):
        service.grant(workflow="quote-review", actions=["crm.read"], actor=AGENT)
    with pytest.raises(StateError):
        service.grant(
            workflow="quote-review",
            actions=["crm.read"],
            actor=HUMAN,
            expires_at=clock.now - timedelta(minutes=1),
        )
    expiring = service.grant(
        workflow="quote-review",
        actions=["crm.read"],
        actor=HUMAN,
        expires_at=clock.now + timedelta(hours=1),
    )
    revocable = service.grant(workflow="quote-review", actions=["draft.prepare"], actor=HUMAN)
    service.revoke_grant(revocable.id, actor=AGENT, note="Task finished")
    with pytest.raises(StateError):
        service.revoke_grant(revocable.id, actor=HUMAN)
    clock.advance(hours=2)
    assert service.active_grants("quote-review") == []
    packet = service.compile_handoff(
        workflow="quote-review",
        objective="x",
        actor=AGENT,
        requested_actions=["crm.read", "draft.prepare"],
    )
    assert packet.permitted_actions == [] and expiring.id not in packet.grant_ids


def test_boundaries_are_always_carried(service: TwinService) -> None:
    boundary = confirmed(
        service, "Never instruct a contractor without written approval.", LessonKind.BOUNDARY
    )
    escalation = confirmed(service, "Escalate any quote above budget.", LessonKind.ESCALATION)
    for i in range(5):
        confirmed(service, f"Roofing detail number {i} about flashing and gutters.")
    packet = service.compile_handoff(
        workflow="quote-review", objective="flashing gutters", actor=AGENT, limit=1
    )
    assert {b.id for b in packet.boundaries} == {boundary.id, escalation.id}
    assert len(packet.guidance) == 1


def test_candidates_and_stale_lessons_are_never_guidance(
    service: TwinService, clock: Clock
) -> None:
    candidate = service.propose_lesson(
        workflow="quote-review",
        kind=LessonKind.CRITERION,
        statement="Prefer local contractors.",
        origin=LessonOrigin.INFERRED,
        actor=AGENT,
    )
    stale = service.propose_lesson(
        workflow="quote-review",
        kind=LessonKind.FACT,
        statement="Scaffolding costs about the same everywhere.",
        origin=LessonOrigin.ELICITED,
        actor=HUMAN,
        confirm=True,
        review_by=clock.now.date(),
    )
    clock.advance(days=2)
    plain = service.compile_handoff(workflow="quote-review", objective="contractors", actor=AGENT)
    assert candidate.id not in {g.id for g in plain.guidance} and plain.hypotheses == []
    assert stale.id not in {g.id for g in plain.guidance}
    assert [n.id for n in plain.needs_review] == [stale.id]
    with_hypotheses = service.compile_handoff(
        workflow="quote-review", objective="contractors", actor=AGENT, include_hypotheses=True
    )
    assert [h.id for h in with_hypotheses.hypotheses] == [candidate.id]
    assert "Unconfirmed hypotheses: do not rely on" in handoff_markdown(with_hypotheses)


def test_global_lessons_join_only_when_relevant(service: TwinService) -> None:
    relevant = confirmed(
        service, "Quotes must name the contractor's insurer.", workflow=GLOBAL_WORKFLOW
    )
    confirmed(service, "Board packs go out five days ahead.", workflow=GLOBAL_WORKFLOW)
    other = confirmed(service, "Tenant deposits are protected.", workflow="lettings")
    packet = service.compile_handoff(
        workflow="quote-review", objective="check quotes insurer", actor=AGENT
    )
    ids = {g.id for g in packet.guidance}
    assert relevant.id in ids and other.id not in ids and len(ids) == 1


def test_packet_is_hashed_and_audited(service: TwinService) -> None:
    confirmed(service, "Compare complete scope before price.")
    packet = service.compile_handoff(workflow="quote-review", objective="compare", actor=AGENT)
    again = service.compile_handoff(workflow="quote-review", objective="compare", actor=AGENT)
    assert len(packet.content_hash) == 64 and packet.content_hash == again.content_hash
    actions = [e["action"] for e in service.store.events(packet.id)]
    assert actions == ["handoff.compiled"]


def test_a_brief_without_requested_actions_does_not_claim_nothing_is_granted(
    service: TwinService,
) -> None:
    service.grant(workflow="quote-review", actions=["draft.prepare"], actor=HUMAN)
    packet = service.compile_handoff(workflow="quote-review", objective="Reply", actor=AGENT)
    for text in (brief_text(packet), handoff_markdown(packet)):
        assert "No actions are authorised" not in text
        assert "none are pre-authorised" in text and "twin_authorise" in text
