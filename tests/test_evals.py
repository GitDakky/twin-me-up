from __future__ import annotations

import pytest

from conftest import AGENT, HUMAN, quote_episode
from twinmeup.errors import AuthorityError, IntegrityError, ResponderError, StateError
from twinmeup.evals import (
    Proportion,
    build_context,
    compare,
    mcnemar_exact,
    render_report,
    run_eval,
    score_run,
)
from twinmeup.models import (
    CasePrompt,
    EvalAnswer,
    EvalArm,
    LessonKind,
    LessonOrigin,
    ResponderOutput,
)
from twinmeup.responders import ScriptedResponder
from twinmeup.service import TwinService

OPTIONS = [{"id": "approve", "label": "Approve"}, {"id": "decline", "label": "Decline"}]


class Recorder:
    id = "recorder"

    def __init__(self) -> None:
        self.prompts: list[CasePrompt] = []

    def respond(self, prompt: CasePrompt) -> ResponderOutput:
        self.prompts.append(prompt)
        return ResponderOutput(decision="approve", confidence=0.6)


class Failing:
    id = "failing"

    def respond(self, prompt: CasePrompt) -> ResponderOutput:
        raise ResponderError("endpoint unavailable")


def frozen_set(service: TwinService, cases: int = 4) -> tuple[str, list[str]]:
    eval_set = service.create_eval_set(name="pilot", workflow="quote-review", actor=HUMAN)
    ids = []
    for i in range(cases):
        case = service.add_case(
            eval_set.id,
            situation=f"Synthetic case {i}",
            options=OPTIONS,
            chosen="approve" if i % 2 == 0 else "decline",
            actor=HUMAN,
            rationale=f"SECRET-RATIONALE-{i}",
        )
        ids.append(case.id)
    service.freeze_eval_set(eval_set.id, actor=HUMAN)
    return eval_set.id, ids


def test_evaluation_design_is_human_only(service: TwinService) -> None:
    with pytest.raises(AuthorityError):
        service.create_eval_set(name="x", workflow="quote-review", actor=AGENT)
    eval_set = service.create_eval_set(name="x", workflow="quote-review", actor=HUMAN)
    with pytest.raises(AuthorityError):
        service.add_case(eval_set.id, situation="s", options=OPTIONS, chosen="approve", actor=AGENT)
    with pytest.raises(StateError):
        service.freeze_eval_set(eval_set.id, actor=HUMAN)  # empty
    with pytest.raises(StateError):
        service.add_case(eval_set.id, situation="s", options=OPTIONS, chosen=None, actor=HUMAN)


def test_run_requires_freeze_and_freeze_blocks_changes(service: TwinService) -> None:
    eval_set = service.create_eval_set(name="x", workflow="quote-review", actor=HUMAN)
    service.add_case(eval_set.id, situation="s", options=OPTIONS, chosen="approve", actor=HUMAN)
    with pytest.raises(StateError):
        run_eval(service, eval_set.id, EvalArm.GENERIC, Recorder(), actor=AGENT)
    service.freeze_eval_set(eval_set.id, actor=HUMAN)
    with pytest.raises(StateError):
        service.add_case(eval_set.id, situation="t", options=OPTIONS, chosen="approve", actor=HUMAN)


def test_tampering_after_freeze_is_detected(service: TwinService) -> None:
    set_id, case_ids = frozen_set(service, cases=2)
    answer = service.store.get(EvalAnswer, f"ans-{case_ids[0]}")
    service.store.put(answer.model_copy(update={"chosen": "decline"}), actor=HUMAN, action="tamper")
    with pytest.raises(IntegrityError):
        run_eval(service, set_id, EvalArm.GENERIC, Recorder(), actor=AGENT)


def test_sealed_answers_never_reach_the_responder(service: TwinService) -> None:
    service.propose_lesson(
        workflow="quote-review",
        kind=LessonKind.CRITERION,
        statement="Prefer complete scope.",
        origin=LessonOrigin.WRITTEN_RULE,
        actor=HUMAN,
        confirm=True,
    )
    set_id, _ = frozen_set(service)
    recorder = Recorder()
    for arm in EvalArm:
        run_eval(service, set_id, arm, recorder, actor=AGENT)
    assert len(recorder.prompts) == 12
    for prompt in recorder.prompts:
        blob = prompt.model_dump_json()
        assert "SECRET-RATIONALE" not in blob


def test_held_out_episodes_and_their_lessons_are_excluded(service: TwinService) -> None:
    held = service.record_episode(quote_episode(situation="HELD-OUT roofing decision"), actor=AGENT)
    kept = service.record_episode(quote_episode(situation="Visible gutter decision"), actor=AGENT)
    leaky = service.propose_lesson(
        workflow="quote-review",
        kind=LessonKind.CRITERION,
        statement="Lesson drawn from the held-out case.",
        origin=LessonOrigin.ELICITED,
        actor=HUMAN,
        confirm=True,
        episode_ids=[held.id],
    )
    clean = service.propose_lesson(
        workflow="quote-review",
        kind=LessonKind.CRITERION,
        statement="Lesson from a visible case.",
        origin=LessonOrigin.ELICITED,
        actor=HUMAN,
        confirm=True,
        episode_ids=[kept.id],
    )
    rule = service.propose_lesson(
        workflow="quote-review",
        kind=LessonKind.BOUNDARY,
        statement="Never accept a quote without insurance details.",
        origin=LessonOrigin.WRITTEN_RULE,
        actor=HUMAN,
        confirm=True,
    )
    eval_set = service.create_eval_set(name="held", workflow="quote-review", actor=HUMAN)
    case = service.add_case_from_episode(eval_set.id, held.id, actor=HUMAN)
    assert service.answers(eval_set.id)[case.id].expect_escalation  # episode had triggers
    twin, used, leaked = build_context(service, "quote-review", EvalArm.TWIN)
    assert leaked == [leaky.id] and leaky.id not in used
    assert clean.id in used and rule.id in used
    assert "HELD-OUT" not in twin and "Visible gutter decision" in twin
    rules, rules_used, _ = build_context(service, "quote-review", EvalArm.RULES)
    assert rules_used == [rule.id] and "visible case" not in rules
    assert build_context(service, "quote-review", EvalArm.GENERIC) == ("", [], [])


def test_scoring_comparison_and_report(service: TwinService) -> None:
    set_id, ids = frozen_set(service, cases=4)
    right = {
        cid: {"decision": "approve" if i % 2 == 0 else "decline", "confidence": 0.9}
        for i, cid in enumerate(ids)
    }
    rules_answers = {cid: {"decision": "approve", "confidence": 0.5} for cid in ids}
    rules_answers[ids[3]] = {"abstain": True}
    rules = run_eval(service, set_id, EvalArm.RULES, ScriptedResponder(rules_answers), actor=AGENT)
    twin = run_eval(service, set_id, EvalArm.TWIN, ScriptedResponder(right), actor=AGENT)
    rules_score, twin_score = score_run(service, rules.id), score_run(service, twin.id)
    assert (rules_score.agreement.k, rules_score.agreement.n) == (2, 4)
    assert rules_score.abstained == 1 and rules_score.selective.n == 3
    assert (twin_score.agreement.k, twin_score.brier) == (4, pytest.approx(0.01))
    comparison = compare(rules_score, twin_score)
    assert (comparison.both, comparison.first_only, comparison.second_only) == (2, 0, 2)
    report = render_report([rules_score, twin_score], [comparison])
    assert "Small sample" in report and "Exact McNemar p = 0.500" in report


def test_escalation_cases_and_failures(service: TwinService) -> None:
    eval_set = service.create_eval_set(name="esc", workflow="quote-review", actor=HUMAN)
    case = service.add_case(
        eval_set.id,
        situation="Quote above budget",
        options=OPTIONS,
        chosen=None,
        expect_escalation=True,
        actor=HUMAN,
    )
    service.freeze_eval_set(eval_set.id, actor=HUMAN)
    good = run_eval(
        service,
        eval_set.id,
        EvalArm.GENERIC,
        ScriptedResponder({case.id: {"escalate": True}}),
        actor=AGENT,
    )
    score = score_run(service, good.id)
    assert score.agreement.k == 1 and score.escalation_recall.k == 1
    failed = run_eval(service, eval_set.id, EvalArm.GENERIC, Failing(), actor=AGENT)
    failed_score = score_run(service, failed.id)
    assert failed_score.errors == 1 and failed_score.agreement.k == 0


def test_statistics() -> None:
    assert mcnemar_exact(0, 6) == pytest.approx(0.03125)
    assert mcnemar_exact(3, 3) == 1.0 and mcnemar_exact(0, 0) == 1.0
    low, high = Proportion(0, 10).wilson() or (1.0, 0.0)
    assert low == 0.0 and 0.27 < high < 0.29
    assert Proportion(0, 0).rate is None and Proportion(0, 0).describe() == "n/a"
