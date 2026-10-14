"""Evaluation: frozen held-out sets, three comparison arms, honest statistics.

The question every run answers: does the personalised twin beat the same model given the
human's explicitly written rules? Agreement with the human is reported separately from
escalation behaviour, and small samples are labelled as directional.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from .errors import IntegrityError, StateError
from .models import (
    GLOBAL_WORKFLOW,
    CasePrompt,
    Episode,
    EvalAnswer,
    EvalArm,
    EvalResult,
    EvalRun,
    EvalSet,
    Lesson,
    LessonOrigin,
    ResponderOutput,
    validate_actor,
)
from .responders import Responder
from .service import TwinService
from .store import sha256

SMALL_SAMPLE = 30


def _lesson_line(lesson: Lesson) -> str:
    when = f" (when: {'; '.join(lesson.conditions)})" if lesson.conditions else ""
    return f"- [{lesson.id} v{lesson.version}] {lesson.kind.value}: {lesson.statement}{when}"


def _episode_line(episode: Episode) -> str:
    labels = {option.id: option.label for option in episode.options}
    parts = [f"- Past decision: {episode.situation}"]
    if episode.chosen:
        parts.append(
            f"  Chose: {labels[episode.chosen]}"
            + (f", because {episode.rationale}" if episode.rationale else "")
        )
    for option_id, reason in episode.rejected.items():
        parts.append(f"  Rejected {labels[option_id]}: {reason}")
    if episode.reversal_conditions:
        parts.append("  Would decide otherwise if: " + "; ".join(episode.reversal_conditions))
    if episode.escalation_triggers:
        parts.append("  Escalate when: " + "; ".join(episode.escalation_triggers))
    return "\n".join(parts)


def build_context(
    service: TwinService, workflow: str, arm: EvalArm
) -> tuple[str, list[str], list[str]]:
    """Return the arm's context, the lessons it used and lessons excluded for leakage."""
    if arm is EvalArm.GENERIC:
        return "", [], []
    today = service.today()
    held_out = {e.id for e in service.store.select(Episode, lambda e: e.held_out)}
    authoritative = service.store.select(
        Lesson,
        lambda lesson: (
            lesson.workflow in (workflow, GLOBAL_WORKFLOW) and lesson.is_authoritative(today)
        ),
    )
    leaked = [lesson.id for lesson in authoritative if set(lesson.episode_ids) & held_out]
    clean = [lesson for lesson in authoritative if lesson.id not in leaked]
    if arm is EvalArm.RULES:
        clean = [lesson for lesson in clean if lesson.origin is LessonOrigin.WRITTEN_RULE]
    lines = [_lesson_line(lesson) for lesson in clean]
    if arm is EvalArm.TWIN:
        episodes = service.store.select(
            Episode, lambda e: e.workflow == workflow and not e.held_out
        )
        lines.extend(_episode_line(episode) for episode in episodes)
    return "\n".join(lines), [lesson.id for lesson in clean], leaked


def run_eval(
    service: TwinService, set_id: str, arm: EvalArm, responder: Responder, *, actor: str
) -> EvalRun:
    validate_actor(actor)
    eval_set = service.store.get(EvalSet, set_id)
    if eval_set.frozen_at is None or eval_set.manifest_hash is None:
        raise StateError("freeze the evaluation set before running it")
    if service.manifest(set_id) != eval_set.manifest_hash:
        raise IntegrityError("the evaluation set changed after it was frozen")
    context, used, leaked = build_context(service, eval_set.workflow, arm)
    run = EvalRun(
        set_id=set_id,
        arm=arm,
        responder_id=responder.id,
        manifest_hash=eval_set.manifest_hash,
        context_hash=sha256(context),
        lessons_used=used,
        leakage_excluded=leaked,
        started_by=actor,
        started_at=service.now(),
    )
    service.store.put(run, actor=actor, action="eval.run_started")
    for case in service.cases(set_id):
        prompt = CasePrompt(
            case_id=case.id,
            workflow=eval_set.workflow,
            situation=case.situation,
            options=case.options,
            context=context,
        )
        started = time.perf_counter()
        output: ResponderOutput | None = None
        error: str | None = None
        try:
            output = responder.respond(prompt)
            if output.decision is not None and output.decision not in {o.id for o in case.options}:
                error, output = f"decision {output.decision!r} is not a case option", None
        except Exception as exc:  # one failing case must not abort the run
            error = f"{type(exc).__name__}: {exc}"[:500]
        result = EvalResult(
            id=f"{run.id}:{case.id}",
            run_id=run.id,
            case_id=case.id,
            output=output,
            error=error,
            latency_ms=round((time.perf_counter() - started) * 1000),
        )
        service.store.put(result, actor=actor, action="eval.result_recorded")
    run = run.model_copy(update={"finished_at": service.now()})
    service.store.put(run, actor=actor, action="eval.run_finished")
    return run


# --- scoring ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Proportion:
    k: int
    n: int

    @property
    def rate(self) -> float | None:
        return self.k / self.n if self.n else None

    def wilson(self, z: float = 1.96) -> tuple[float, float] | None:
        if not self.n:
            return None
        p, n = self.k / self.n, self.n
        centre = (p + z * z / (2 * n)) / (1 + z * z / n)
        half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
        return max(0.0, centre - half), min(1.0, centre + half)

    def describe(self) -> str:
        if not self.n:
            return "n/a"
        low, high = self.wilson() or (0.0, 0.0)
        return f"{self.k}/{self.n} ({self.k / self.n:.0%}, 95% CI {low:.0%}-{high:.0%})"


def is_correct(answer: EvalAnswer, output: ResponderOutput | None) -> bool:
    if output is None:
        return False
    if answer.chosen is None:
        return output.escalate or output.abstain
    return output.decision is not None and output.decision in {answer.chosen, *answer.acceptable}


@dataclass
class RunScore:
    run_id: str
    arm: str
    responder_id: str
    cases: int
    errors: int
    abstained: int
    agreement: Proportion
    selective: Proportion
    escalation_recall: Proportion
    escalation_precision: Proportion
    brier: float | None
    correct: dict[str, bool] = field(default_factory=dict)


def score_run(service: TwinService, run_id: str) -> RunScore:
    run = service.store.get(EvalRun, run_id)
    answers = service.answers(run.set_id)
    results = service.store.select(EvalResult, lambda r: r.run_id == run_id)
    correct = {r.case_id: is_correct(answers[r.case_id], r.output) for r in results}
    answered = [
        r for r in results if r.output and r.output.decision is not None and not r.output.abstain
    ]
    flagged = [r for r in results if r.output and r.output.escalate]
    expected = [r for r in results if answers[r.case_id].expect_escalation]
    squared = [
        (r.output.confidence - float(correct[r.case_id])) ** 2
        for r in answered
        if r.output is not None and r.output.confidence is not None
    ]
    brier = sum(squared) / len(squared) if squared else None
    return RunScore(
        run_id=run.id,
        arm=run.arm.value,
        responder_id=run.responder_id,
        cases=len(results),
        errors=sum(1 for r in results if r.error),
        abstained=sum(1 for r in results if r.output and r.output.abstain),
        agreement=Proportion(sum(correct.values()), len(results)),
        selective=Proportion(sum(correct[r.case_id] for r in answered), len(answered)),
        escalation_recall=Proportion(
            sum(1 for r in expected if r.output and r.output.escalate), len(expected)
        ),
        escalation_precision=Proportion(
            sum(1 for r in flagged if answers[r.case_id].expect_escalation), len(flagged)
        ),
        brier=brier,
        correct=correct,
    )


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar test on the discordant pair counts."""
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(b, c) + 1)) / (1 << n)
    return min(1.0, 2 * tail)


@dataclass(frozen=True)
class Comparison:
    first: str
    second: str
    both: int
    first_only: int
    second_only: int
    neither: int
    p_value: float


def compare(first: RunScore, second: RunScore) -> Comparison:
    shared = sorted(first.correct.keys() & second.correct.keys())
    pairs = [(first.correct[c], second.correct[c]) for c in shared]
    first_only = sum(1 for a, b in pairs if a and not b)
    second_only = sum(1 for a, b in pairs if b and not a)
    return Comparison(
        first=first.arm,
        second=second.arm,
        both=sum(1 for a, b in pairs if a and b),
        first_only=first_only,
        second_only=second_only,
        neither=sum(1 for a, b in pairs if not a and not b),
        p_value=mcnemar_exact(first_only, second_only),
    )


def render_report(scores: list[RunScore], comparisons: list[Comparison]) -> str:
    lines = ["# Evaluation report", ""]
    if any(score.cases < SMALL_SAMPLE for score in scores):
        lines += [
            f"Small sample (under {SMALL_SAMPLE} cases): treat every figure as directional, "
            "not as evidence of reliability.",
            "",
        ]
    lines += [
        "| Arm | Responder | Agreement | Selective accuracy | Escalation recall "
        "| Escalation precision | Abstained | Errors | Brier |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in scores:
        brier = f"{s.brier:.3f}" if s.brier is not None else "n/a"
        lines.append(
            f"| {s.arm} | {s.responder_id} | {s.agreement.describe()} | {s.selective.describe()} | "
            f"{s.escalation_recall.describe()} | {s.escalation_precision.describe()} | "
            f"{s.abstained} | {s.errors} | {brier} |"
        )
    for c in comparisons:
        lines += [
            "",
            f"**{c.second} vs {c.first}:** both right {c.both}, "
            f"only {c.first} right {c.first_only}, "
            f"only {c.second} right {c.second_only}, both wrong {c.neither}. "
            f"Exact McNemar p = {c.p_value:.3f}.",
        ]
    lines += [
        "",
        "Agreement measures matching the human's recorded choice, not decision quality. "
        "Review disagreements before treating either as an error.",
    ]
    return "\n".join(lines) + "\n"
