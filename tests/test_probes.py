from __future__ import annotations

from conftest import AGENT, quote_episode
from twinmeup.models import Episode
from twinmeup.probes import gaps, next_questions


def test_sparse_episode_asks_for_options_first() -> None:
    episode = Episode.model_validate(
        {"workflow": "quote-review", "situation": "A quote arrived.", "recorded_by": AGENT}
    )
    assert [p.gap for p in next_questions(episode)] == [
        "options",
        "reversal_conditions",
        "escalation_triggers",
    ]


def test_each_unexplained_rejected_option_gets_a_probe() -> None:
    episode = Episode.model_validate({**quote_episode(), "recorded_by": AGENT})
    found = gaps(episode)
    rejected = [p for p in found if p.gap == "rejected"]
    assert [p.question for p in rejected] == ["Why did you rule out 'Retender with a fixed scope'?"]
    assert found[0].gap == "rejected"  # the most valuable remaining gap leads
    assert {p.gap for p in found} == {"rejected", "missing_information", "outcome"}


def test_complete_episode_has_no_gaps() -> None:
    data = quote_episode(
        rejected={"cheapest": "Omits scaffolding.", "retender": "Too slow before winter."},
        missing_information=["Contractor insurance expiry"],
        outcome="Repair held.",
    )
    assert gaps(Episode.model_validate({**data, "recorded_by": AGENT})) == []
