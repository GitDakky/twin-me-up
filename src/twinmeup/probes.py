"""Cognitive-task-analysis probes and episode gap analysis.

Follows the critical decision method: anchor on a real incident, then probe the cues,
the options, the basis for choice, what would have changed the decision and when the
expert wants the matter brought back to them. Experts omit steps they perform
automatically, so the probes target exactly what free narration leaves out.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Episode


@dataclass(frozen=True)
class Probe:
    gap: str
    question: str
    why: str


ONBOARDING: tuple[str, ...] = (
    "Describe the last time you handled this workflow from start to finish. What triggered it?",
    "Which decisions in that work did only you make, and why could nobody else make them?",
    "What do you check first, and what makes you stop and look harder?",
    "Where do capable newcomers most often get this wrong?",
    "Which actions in this workflow must always come back to you for approval?",
    "What information do you usually wish you had, and where would it come from?",
)

_ORDER: tuple[Probe, ...] = (
    Probe(
        "options",
        "What other options did you consider, even briefly?",
        "A choice only makes sense against its alternatives.",
    ),
    Probe(
        "chosen",
        "Which option did you choose in the end?",
        "The decision itself anchors every other probe.",
    ),
    Probe(
        "rejected",
        "Why did you rule out '{option}'?",
        "Rejected options carry the criteria experts rarely state.",
    ),
    Probe(
        "reversal_conditions",
        "What would have had to be different for you to choose otherwise?",
        "Reversal conditions mark the boundary of the rule.",
    ),
    Probe(
        "escalation_triggers",
        "At what point would you want this brought back to you "
        "rather than decided by someone else?",
        "Escalation triggers become the stop conditions for delegated agents.",
    ),
    Probe(
        "cues",
        "What did you notice first that told you what kind of situation this was?",
        "Cues are the tacit knowledge newcomers miss.",
    ),
    Probe(
        "missing_information",
        "What information did you lack, and how did you work around it?",
        "Gaps show what an agent must obtain or flag.",
    ),
    Probe(
        "rationale",
        "In one or two sentences, why was that the right choice here?",
        "A stated basis lets others test the decision rather than copy it.",
    ),
    Probe(
        "goal",
        "What were you trying to achieve, and what were you protecting against?",
        "Goals explain trade-offs that look inconsistent from outside.",
    ),
    Probe(
        "sources",
        "Is there a document, record or message that shows this decision or its context?",
        "Provenance lets others verify the decision and lets the twin cite it.",
    ),
    Probe(
        "outcome",
        "How did it turn out, and would you decide the same way again?",
        "Outcomes separate good decisions from good luck.",
    ),
)


def gaps(episode: Episode) -> list[Probe]:
    """Every unanswered probe for an episode, most valuable first."""
    found: list[Probe] = []
    for probe in _ORDER:
        gap = probe.gap
        if gap == "options":
            if len(episode.options) < 2:
                found.append(probe)
        elif gap == "chosen":
            if episode.options and episode.chosen is None:
                found.append(probe)
        elif gap == "rejected":
            if episode.chosen is None:
                continue
            for option in episode.options:
                if option.id != episode.chosen and option.id not in episode.rejected:
                    found.append(Probe(gap, probe.question.format(option=option.label), probe.why))
        elif not getattr(episode, gap):
            found.append(probe)
    return found


def next_questions(episode: Episode, limit: int = 3) -> list[Probe]:
    return gaps(episode)[:limit]
