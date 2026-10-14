"""Human-readable rendering of handoff packets."""

from __future__ import annotations

from .models import CitedLesson, HandoffPacket


def _cite(lesson: CitedLesson) -> str:
    when = f" When: {'; '.join(lesson.conditions)}." if lesson.conditions else ""
    return f"- {lesson.statement}{when} [{lesson.id} v{lesson.version}, {lesson.kind.value}]"


def handoff_markdown(packet: HandoffPacket) -> str:
    lines = [
        f"# Handoff: {packet.objective}",
        "",
        f"Workflow `{packet.workflow}`. Packet `{packet.id}`, content hash "
        f"`{packet.content_hash[:16]}`. Compiled {packet.compiled_at:%Y-%m-%d %H:%M} UTC "
        f"by {packet.compiled_by}.",
        "",
        "## Authority",
        "",
    ]
    if packet.permitted_actions:
        lines.append("Permitted actions: " + ", ".join(f"`{a}`" for a in packet.permitted_actions))
        lines.append("Granted by: " + ", ".join(f"`{g}`" for g in packet.grant_ids))
    elif packet.requested_actions:
        lines.append(
            "No actions are authorised. Work draft-only: prepare for review and take no "
            "external or live-system action."
        )
    else:
        lines.append(
            "No actions were requested, so none are pre-authorised. Work draft-only until "
            "twin_authorise allows an action."
        )
    if packet.withheld_actions:
        lines.append(
            "Requested but not authorised: " + ", ".join(f"`{a}`" for a in packet.withheld_actions)
        )
    lines += ["", "## Stop conditions", ""]
    lines += [f"{number}. {stop}" for number, stop in enumerate(packet.stop_conditions, 1)]
    sections = [
        ("Boundaries and escalation rules", packet.boundaries),
        ("Guidance", packet.guidance),
        ("Needs review: do not rely on", packet.needs_review),
        ("Unconfirmed hypotheses: do not rely on", packet.hypotheses),
    ]
    for title, lessons in sections:
        if lessons or title == "Guidance":
            lines += ["", f"## {title}", ""]
            lines += [_cite(lesson) for lesson in lessons] or [
                "- None confirmed for this workflow yet."
            ]
    lines += ["", "## Evidence required on completion", ""]
    lines += [f"- {item}" for item in packet.evidence_required]
    return "\n".join(lines) + "\n"


def _line(lesson: CitedLesson) -> str:
    when = f" (when: {'; '.join(lesson.conditions)})" if lesson.conditions else ""
    return f"- {lesson.statement}{when} [{lesson.id}]"


def brief_text(packet: HandoffPacket, principal: str = "the principal") -> str:
    """Operating instructions an agent embeds in its system prompt for this workflow."""
    lines = [
        f"You are acting as a delegate of {principal} in the '{packet.workflow}' workflow. "
        f"Work the way {principal} has confirmed below. You are not {principal}: never claim "
        "to be, and never treat a guess about what they would want as their approval.",
        "",
        "AUTHORITY",
    ]
    if packet.permitted_actions:
        lines.append("Permitted now: " + ", ".join(packet.permitted_actions) + ".")
    elif packet.requested_actions:
        lines.append("No actions are authorised. Work draft-only.")
    else:
        lines.append(
            "No actions were requested, so none are pre-authorised. Work draft-only until "
            "twin_authorise allows an action."
        )
    if packet.withheld_actions:
        lines.append("Not authorised, ask first: " + ", ".join(packet.withheld_actions) + ".")
    lines.append(
        "Before any other consequential action, call twin_authorise and follow its decision."
    )
    if packet.boundaries:
        lines += ["", "HARD LIMITS AND ESCALATION RULES (always apply)"]
        lines += [_line(lesson) for lesson in packet.boundaries]
    lines += ["", f"HOW {principal.upper()} DECIDES (confirmed)"]
    lines += [_line(lesson) for lesson in packet.guidance] or [
        "- Nothing confirmed yet. Ask rather than assume."
    ]
    if packet.needs_review:
        lines += ["", "UNVERIFIED: review date passed, do not rely on"]
        lines += [_line(lesson) for lesson in packet.needs_review]
    if packet.hypotheses:
        lines += ["", "UNCONFIRMED HYPOTHESES: do not rely on"]
        lines += [_line(lesson) for lesson in packet.hypotheses]
    lines += ["", "STOP CONDITIONS"]
    lines += [f"{n}. {stop}" for n, stop in enumerate(packet.stop_conditions, 1)]
    lines += [
        "",
        "LEARNING",
        f"- When {principal} corrects or overrides you, call twin_record_correction with both "
        "versions and their reason.",
        f"- Propose new guidance with twin_propose_lesson. It stays unconfirmed until {principal} "
        "confirms it.",
        f"- If unsure how {principal} would decide, ask, then record the answer with "
        "twin_record_decision.",
        "",
        "ON COMPLETION",
    ]
    lines += [f"- {item}" for item in packet.evidence_required]
    lines.append(f"Brief content hash {packet.content_hash[:16]}.")
    return "\n".join(lines) + "\n"
