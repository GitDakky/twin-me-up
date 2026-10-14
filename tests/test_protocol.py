from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from conftest import AGENT, HUMAN
from twinmeup.errors import TwinMeUpError
from twinmeup.evals import run_eval
from twinmeup.models import EvalArm, LessonKind, LessonOrigin, LessonStatus
from twinmeup.protocol import BY_NAME, TOOLS, call_tool, schemas
from twinmeup.responders import ScriptedResponder
from twinmeup.service import TwinService

HUMAN_ONLY_WORDS = ("confirm", "revise", "reject", "retire", "grant", "freeze", "erase", "case")


def test_registry_has_no_human_only_operations() -> None:
    for spec in TOOLS:
        assert not any(word in spec.name for word in HUMAN_ONLY_WORDS), spec.name
    origin = BY_NAME["twin_propose_lesson"].input_model.model_json_schema()["properties"]["origin"]
    assert "written_rule" not in json.dumps(origin)


def test_schemas_are_self_contained_and_annotated() -> None:
    mcp = schemas("mcp")
    openai = schemas("openai")
    assert len(mcp) == len(openai) == len(TOOLS)
    blob = json.dumps(mcp) + json.dumps(openai)
    assert "$ref" not in blob and "$defs" not in blob
    read_only = {t["name"] for t in mcp if t["annotations"]["readOnlyHint"]}
    assert {"twin_brief", "twin_authorise", "twin_search"} <= read_only
    assert "twin_propose_lesson" not in read_only and "twin_record_correction" not in read_only
    assert all(t["inputSchema"]["type"] == "object" for t in mcp)
    assert openai[0]["type"] == "function" and "parameters" in openai[0]["function"]


def _rules(service: TwinService) -> None:
    service.propose_lesson(
        workflow="quote-review",
        kind=LessonKind.BOUNDARY,
        statement="Never instruct a contractor without written approval.",
        origin=LessonOrigin.WRITTEN_RULE,
        actor=HUMAN,
        confirm=True,
    )
    service.propose_lesson(
        workflow="quote-review",
        kind=LessonKind.CRITERION,
        statement="Compare complete scope before headline price.",
        origin=LessonOrigin.ELICITED,
        actor=HUMAN,
        confirm=True,
    )


def test_brief_and_authorise(service: TwinService) -> None:
    _rules(service)
    grant = service.grant(workflow="quote-review", actions=["draft.prepare"], actor=HUMAN)
    out = call_tool(
        service,
        AGENT,
        "twin_brief",
        {
            "workflow": "quote-review",
            "objective": "Compare roofing quotes",
            "actions": ["draft.prepare", "email.send"],
        },
        principal="Alex",
    )
    brief = out["brief"]
    assert "delegate of Alex" in brief and "Permitted now: draft.prepare." in brief
    assert "Not authorised, ask first: email.send." in brief
    assert "Never instruct a contractor" in brief and "Compare complete scope" in brief
    assert out["withheld_actions"] == ["email.send"]
    ask = call_tool(
        service, AGENT, "twin_authorise", {"workflow": "quote-review", "action": "email.send"}
    )
    allow = call_tool(
        service, AGENT, "twin_authorise", {"workflow": "quote-review", "action": "draft.prepare"}
    )
    assert ask["decision"] == "ask" and ask["grant_ids"] == []
    assert allow["decision"] == "allow" and allow["grant_ids"] == [grant.id]
    actions = [e["action"] for e in service.store.events()]
    assert "authorise.ask" in actions and "authorise.allow" in actions


def test_correction_becomes_reviewable_learning(service: TwinService) -> None:
    corrected = call_tool(
        service,
        AGENT,
        "twin_record_correction",
        {
            "workflow": "quote-review",
            "situation": "Draft reply to a contractor asking for a deposit.",
            "agent_proposal": "Agree a 30 percent deposit.",
            "human_decision": "Offer staged payments against completed work.",
            "reason": "Deposits on small jobs have gone unrecovered.",
        },
    )
    episode_id = corrected["episode_id"]
    assert "rejected" not in {q["gap"] for q in corrected["next_questions"]}
    proposed = call_tool(
        service,
        AGENT,
        "twin_propose_lesson",
        {
            "workflow": "quote-review",
            "kind": "criterion",
            "origin": "correction",
            "statement": "Prefer staged payments over deposits on small jobs.",
            "episode_ids": [episode_id],
        },
    )
    assert proposed["status"] == "candidate"
    queue = call_tool(service, AGENT, "twin_review_queue", {"workflow": "quote-review"})
    assert [item["id"] for item in queue["awaiting_review"]] == [proposed["lesson_id"]]
    found = call_tool(
        service, AGENT, "twin_search", {"workflow": "quote-review", "query": "deposit"}
    )
    assert found["lessons"] == []  # not confirmed, so not guidance
    service.confirm_lesson(proposed["lesson_id"], actor=HUMAN)
    found = call_tool(
        service, AGENT, "twin_search", {"workflow": "quote-review", "query": "deposit"}
    )
    assert [item["id"] for item in found["lessons"]] == [proposed["lesson_id"]]
    record = call_tool(service, AGENT, "twin_track_record", {"workflow": "quote-review"})
    assert record["corrections"] == 1 and record["lessons_confirmed"] == 1
    assert record["stage"] == "observe"  # no boundaries or escalation rules yet


def test_track_record_recommends_but_never_promotes(service: TwinService) -> None:
    _rules(service)
    assert service.track_record("quote-review")["stage"] == "draft"
    eval_set = service.create_eval_set(name="held", workflow="quote-review", actor=HUMAN)
    options = [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]
    ids = [
        service.add_case(
            eval_set.id, situation=f"Case {i}", options=options, chosen="a", actor=HUMAN
        ).id
        for i in range(30)
    ]
    service.freeze_eval_set(eval_set.id, actor=HUMAN)
    run_eval(
        service,
        eval_set.id,
        EvalArm.RULES,
        ScriptedResponder({c: {"decision": "a" if i % 2 else "b"} for i, c in enumerate(ids)}),
        actor=AGENT,
    )
    run_eval(
        service,
        eval_set.id,
        EvalArm.TWIN,
        ScriptedResponder({c: {"decision": "a"} for c in ids}),
        actor=AGENT,
    )
    record = service.track_record("quote-review")
    assert record["stage"] == "supervised"
    assert record["latest_evaluation"]["twin"]["agreement"] == 1.0
    assert record["active_grants"] == []  # evidence alone grants nothing


def test_bad_calls_fail_cleanly(service: TwinService) -> None:
    with pytest.raises(TwinMeUpError):
        call_tool(service, AGENT, "twin_confirm_lesson", {})
    with pytest.raises(ValidationError):
        call_tool(service, AGENT, "twin_brief", {"workflow": "Not A Slug", "objective": "x"})
    with pytest.raises(ValidationError):
        call_tool(
            service,
            AGENT,
            "twin_propose_lesson",
            {
                "workflow": "quote-review",
                "kind": "boundary",
                "statement": "x",
                "origin": "written_rule",
            },
        )
    assert service.lessons(status=LessonStatus.CANDIDATE) == []
