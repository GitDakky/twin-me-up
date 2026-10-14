from __future__ import annotations

import sqlite3
import stat

import pytest

from conftest import AGENT, HUMAN, quote_episode
from twinmeup.errors import AuthorityError
from twinmeup.models import Episode, LessonKind, LessonOrigin
from twinmeup.service import TwinService


def test_chain_verifies_after_normal_writes(service: TwinService) -> None:
    service.record_episode(quote_episode(), actor=AGENT)
    service.grant(workflow="quote-review", actions=["crm.read"], actor=HUMAN)
    assert service.store.verify_chain() == []
    assert len(service.store.events()) == 2


def _raw(service: TwinService) -> sqlite3.Connection:
    return sqlite3.connect(service.store.path, isolation_level=None)


def test_tampered_payload_is_detected(service: TwinService) -> None:
    service.record_episode(quote_episode(), actor=AGENT)
    with _raw(service) as conn:
        conn.execute("UPDATE events SET payload='{\"forged\":true}' WHERE seq=1")
    assert any("payload does not match" in p for p in service.store.verify_chain())


def test_rewritten_history_is_detected(service: TwinService) -> None:
    service.record_episode(quote_episode(), actor=AGENT)
    service.record_episode(quote_episode(), actor=AGENT)
    with _raw(service) as conn:
        conn.execute("UPDATE events SET actor='human:someone' WHERE seq=1")
    assert any("hash mismatch" in p for p in service.store.verify_chain())


def test_silently_removed_payload_is_detected(service: TwinService) -> None:
    service.record_episode(quote_episode(), actor=AGENT)
    with _raw(service) as conn:
        conn.execute("UPDATE events SET payload=NULL WHERE seq=1")
    assert any("without a redaction record" in p for p in service.store.verify_chain())


def test_erasure_cascades_and_keeps_the_chain_valid(service: TwinService) -> None:
    episode = service.record_episode(quote_episode(situation="Erase-me decision"), actor=AGENT)
    lesson = service.propose_lesson(
        workflow="quote-review",
        kind=LessonKind.CRITERION,
        statement="Derived from the erased decision.",
        origin=LessonOrigin.ELICITED,
        actor=AGENT,
        episode_ids=[episode.id],
    )
    revised = service.revise_lesson(lesson.id, actor=HUMAN, statement="Revised derived lesson.")
    packet = service.compile_handoff(
        workflow="quote-review", objective="derived lesson", actor=AGENT
    )
    assert revised.id in {g.id for g in packet.guidance}
    eval_set = service.create_eval_set(name="x", workflow="quote-review", actor=HUMAN)
    case = service.add_case_from_episode(eval_set.id, episode.id, actor=HUMAN)
    with pytest.raises(AuthorityError):
        service.erase_episode(episode.id, actor=AGENT, reason="request")
    result = service.erase_episode(episode.id, actor=HUMAN, reason="Subject request")
    assert result["lessons"] == sorted([lesson.id, revised.id])
    assert result["handoffs"] == [packet.id]
    assert result["eval_cases"] == [case.id]
    assert service.store.find(Episode, episode.id) is None and service.lessons() == []
    assert service.store.verify_chain() == []
    payloads = " ".join(str(e["payload"]) for e in service.store.events())
    assert "Erase-me" not in payloads and "erased decision" not in payloads
    assert "Revised derived lesson" not in payloads


def test_store_is_owner_only(service: TwinService) -> None:
    service.record_episode(quote_episode(), actor=AGENT)
    for suffix in ("", "-wal", "-shm"):
        path = service.store.path.with_name(service.store.path.name + suffix)
        if path.exists():
            assert stat.S_IMODE(path.stat().st_mode) == 0o600, suffix


def test_export_withholds_sealed_answers_by_default(service: TwinService) -> None:
    eval_set = service.create_eval_set(name="x", workflow="quote-review", actor=HUMAN)
    service.add_case(
        eval_set.id,
        situation="s",
        chosen="a",
        actor=HUMAN,
        options=[{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
    )
    bundle = service.export_bundle()
    assert "eval_answer" not in bundle["records"] and bundle["includes_sealed_answers"] is False
    assert len(service.export_bundle(include_answers=True)["records"]["eval_answer"]) == 1
    assert bundle["audit_head"] == service.store.head()
