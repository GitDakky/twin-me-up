"""Command-line adapter over the service layer."""

from __future__ import annotations

import asyncio
import functools
import json
import os
import re
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from importlib import resources
from pathlib import Path
from typing import Any, NoReturn, ParamSpec, TypeVar

import typer
from pydantic import BaseModel, ValidationError

from . import __version__, identity, probes
from . import connect as connections
from .errors import StateError, TwinMeUpError
from .evals import compare, render_report, run_eval, score_run
from .hooks import ActionMap, Dialect, HookResponse, pre_tool_call, unavailable
from .identity import (
    Settings,
    hash_passphrase,
    human_id_for,
    load_settings,
    prove_human,
    read_secret,
    save_settings,
)
from .models import (
    ApprovalRequest,
    Episode,
    EvalArm,
    EvalSet,
    Grant,
    LessonKind,
    LessonOrigin,
    LessonStatus,
    SourceKind,
    SourceRef,
    is_human,
    validate_actor,
)
from .protocol import BY_NAME, call_tool, schemas
from .render import brief_text, handoff_markdown
from .responders import responder_from_spec
from .service import TwinService, default_home

P = ParamSpec("P")
R = TypeVar("R")

app = typer.Typer(help="Governed professional twins.", no_args_is_help=True)
episode_app = typer.Typer(help="Record and enrich real decisions.", no_args_is_help=True)
lesson_app = typer.Typer(help="Propose and review learned guidance.", no_args_is_help=True)
grant_app = typer.Typer(help="Explicit authority from a human principal.", no_args_is_help=True)
approvals_app = typer.Typer(
    help="Approve or decline what agents have asked to do.", no_args_is_help=True
)
eval_app = typer.Typer(help="Held-out evaluation against baselines.", no_args_is_help=True)
audit_app = typer.Typer(help="Tamper-evident audit log.", no_args_is_help=True)
erase_app = typer.Typer(
    help="Erase records and everything derived from them.", no_args_is_help=True
)
probes_app = typer.Typer(help="Interview probes.", no_args_is_help=True)
tools_app = typer.Typer(
    help="The agent protocol: list, export schemas, call.", no_args_is_help=True
)
hook_app = typer.Typer(help="Pre-action hooks for agent frameworks.", no_args_is_help=True)
map_app = typer.Typer(help="Enforcement maps for pre-action hooks.", no_args_is_help=True)
skill_app = typer.Typer(help="The agent skill that teaches the loop.", no_args_is_help=True)
extension_app = typer.Typer(help="Native extensions for agent systems.", no_args_is_help=True)
for sub, name in (
    (map_app, "action-map"),
    (skill_app, "skill"),
    (extension_app, "extension"),
    (tools_app, "tools"),
    (hook_app, "hook"),
    (episode_app, "episode"),
    (lesson_app, "lesson"),
    (grant_app, "grant"),
    (approvals_app, "approvals"),
    (eval_app, "eval"),
    (audit_app, "audit"),
    (erase_app, "erase"),
    (probes_app, "probes"),
):
    app.add_typer(sub, name=name)


DEFAULT_AGENT = "agent:assistant"


@dataclass
class State:
    home: Path
    actor: str | None
    principal: str | None = None
    passphrase_stdin: bool = False


def _fail(message: str) -> NoReturn:
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(1)


def guard(fn: Callable[P, R]) -> Callable[P, R]:
    """Turn deliberate failures into a clean message and exit code 1."""

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return fn(*args, **kwargs)
        except TwinMeUpError as exc:
            _fail(str(exc))
        except ValidationError as exc:
            _fail("invalid input: " + "; ".join(e["msg"] for e in exc.errors()[:5]))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            _fail(str(exc))

    return wrapper


def _state(ctx: typer.Context) -> State:
    state = ctx.find_root().obj
    assert isinstance(state, State)
    return state


def _service(ctx: typer.Context) -> TwinService:
    return TwinService.open(_state(ctx).home)


def _warn(message: str) -> None:
    typer.echo(message, err=True)


def _settings(ctx: typer.Context) -> Settings:
    settings = load_settings(_state(ctx).home)
    if settings is None:
        _fail("this twin is not set up yet: run `twinmeup init` in your own terminal")
    return settings


def _principal(ctx: typer.Context) -> str:
    state = _state(ctx)
    if state.principal:
        return state.principal
    settings = load_settings(state.home)
    return settings.principal if settings else "the principal"


def _human(ctx: typer.Context) -> str:
    """The principal, after the passphrase checks out. Agents are refused outright."""
    state = _state(ctx)
    if state.actor and not is_human(validate_actor(state.actor)):
        _fail(f"{state.actor} is an agent; only the principal can do this")
    settings = _settings(ctx)
    if state.actor and state.actor != settings.human_id:
        _fail(f"this twin belongs to {settings.human_id}, not {state.actor}")
    return prove_human(settings, from_stdin=state.passphrase_stdin, warn=_warn)


def _actor(ctx: typer.Context, *, default_agent: bool = False) -> str:
    """Agents act as themselves; anything acting as the principal must prove it."""
    actor = _state(ctx).actor
    if actor and not is_human(validate_actor(actor)):
        return actor
    if actor is None and default_agent:
        return DEFAULT_AGENT
    return _human(ctx)


def _emit(value: Any) -> None:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    elif isinstance(value, list):
        value = [v.model_dump(mode="json") if isinstance(v, BaseModel) else v for v in value]
    typer.echo(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def _read_json(path: str) -> Any:
    text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    return json.loads(text)


def _sources(values: list[str] | None) -> list[SourceRef]:
    refs = []
    for value in values or []:
        kind, _, locator = value.partition(":")
        if not locator:
            raise ValueError(f"source {value!r} must look like kind:locator")
        refs.append(SourceRef(kind=SourceKind(kind), locator=locator))
    return refs


def _write_private(path: Path, text: str) -> None:
    """Atomic, owner-only write: temp file in the same directory, fsync, rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


@app.callback()
def main(
    ctx: typer.Context,
    home: Path = typer.Option(None, "--home", envvar="TWINMEUP_HOME", help="Store directory."),
    actor: str = typer.Option(
        None,
        "--actor",
        envvar="TWINMEUP_ACTOR",
        help="Who is acting: human:<name> or agent:<name>.",
    ),
    principal: str = typer.Option(
        None,
        "--principal",
        envvar="TWINMEUP_PRINCIPAL",
        help="How briefs name you. Defaults to the name given at init.",
    ),
    passphrase_stdin: bool = typer.Option(
        False,
        "--passphrase-stdin",
        help="Read your passphrase from stdin instead of prompting, for scripts you run yourself.",
    ),
) -> None:
    ctx.obj = State(
        home=home or default_home(),
        actor=actor,
        principal=principal,
        passphrase_stdin=passphrase_stdin,
    )


@app.command()
@guard
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


@app.command()
@guard
def init(
    ctx: typer.Context,
    name: str = typer.Option(None, "--name", help="Your name, as agents should refer to you."),
    no_passphrase: bool = typer.Option(
        False, "--no-passphrase", help="Skip the passphrase. Not recommended: see SECURITY.md."
    ),
    force: bool = typer.Option(
        False, help="Replace the settings. Asks for the current passphrase."
    ),
) -> None:
    """Set up your twin: your name and the passphrase that guards human-only operations."""
    state = _state(ctx)
    if state.actor and not is_human(validate_actor(state.actor)):
        _fail("agents cannot initialise a twin; the principal runs `twinmeup init` themselves")
    service = _service(ctx)
    existing = load_settings(state.home)
    if existing and not force:
        _emit(
            {
                "store": str(service.store.path),
                "principal": existing.principal,
                "human_id": existing.human_id,
                "protected": existing.passphrase is not None,
                "note": "already set up; use --force to change your name or passphrase",
            }
        )
        return
    if existing:
        prove_human(existing, from_stdin=state.passphrase_stdin, warn=_warn)
    if not name:
        if not sys.stdin.isatty():
            _fail("pass --name, for example --name Alex")
        name = typer.prompt("Your name, as agents should refer to you")
    passphrase = None
    if no_passphrase:
        if not identity.has_terminal():
            _fail("--no-passphrase must be chosen by you in your own terminal, not by an agent")
        _warn(
            "warning: no passphrase set; agents running as you could perform human-only operations"
        )
    elif state.passphrase_stdin:
        passphrase = hash_passphrase(read_secret("", from_stdin=True))
    else:
        first = read_secret("Choose a passphrase (8+ characters): ", from_stdin=False)
        if read_secret("Repeat it: ", from_stdin=False) != first:
            _fail("the passphrases did not match; nothing was changed")
        passphrase = hash_passphrase(first)
    settings = Settings(principal=name.strip(), human_id=human_id_for(name), passphrase=passphrase)
    path = save_settings(state.home, settings)
    _emit(
        {
            "store": str(service.store.path),
            "settings": str(path),
            "principal": settings.principal,
            "human_id": settings.human_id,
            "protected": passphrase is not None,
            "next": "twinmeup connect --list",
        }
    )


@app.command()
@guard
def status(ctx: typer.Context) -> None:
    """Record counts and audit-chain health."""
    service = _service(ctx)
    counts = {name: len(rows) for name, rows in service.store.dump().items()}
    problems = service.store.verify_chain()
    _emit(
        {
            "counts": counts,
            "pending_approvals": len(service.approvals(pending_only=True)),
            "audit_head": service.store.head(),
            "audit_ok": not problems,
        }
    )


# --- probes ---------------------------------------------------------------------------


@probes_app.command("onboarding")
@guard
def probes_onboarding() -> None:
    """Opening questions for a new workflow."""
    for number, question in enumerate(probes.ONBOARDING, 1):
        typer.echo(f"{number}. {question}")


# --- episodes -------------------------------------------------------------------------


@episode_app.command("add")
@guard
def episode_add(
    ctx: typer.Context, file: str = typer.Argument(..., help="JSON file, or - for stdin.")
) -> None:
    """Record a decision episode."""
    _emit(_service(ctx).record_episode(_read_json(file), actor=_actor(ctx)))


@episode_app.command("amend")
@guard
def episode_amend(ctx: typer.Context, episode_id: str, file: str = typer.Argument(...)) -> None:
    """Merge new answers into an episode."""
    _emit(_service(ctx).amend_episode(episode_id, _read_json(file), actor=_actor(ctx)))


@episode_app.command("list")
@guard
def episode_list(ctx: typer.Context, workflow: str = typer.Option(None)) -> None:
    _emit(_service(ctx).episodes(workflow))


@episode_app.command("show")
@guard
def episode_show(ctx: typer.Context, episode_id: str) -> None:
    _emit(_service(ctx).store.get(Episode, episode_id))


@episode_app.command("gaps")
@guard
def episode_gaps(ctx: typer.Context, episode_id: str, limit: int = typer.Option(3, min=1)) -> None:
    """The most valuable follow-up questions for an episode."""
    episode = _service(ctx).store.get(Episode, episode_id)
    _emit(
        [
            {"gap": p.gap, "question": p.question, "why": p.why}
            for p in probes.next_questions(episode, limit)
        ]
    )


# --- lessons --------------------------------------------------------------------------


@lesson_app.command("propose")
@guard
def lesson_propose(
    ctx: typer.Context,
    workflow: str = typer.Option(...),
    kind: LessonKind = typer.Option(...),
    statement: str = typer.Option(...),
    origin: LessonOrigin = typer.Option(LessonOrigin.ELICITED),
    condition: list[str] = typer.Option(None, help="Repeatable."),
    episode: list[str] = typer.Option(None, help="Supporting episode id. Repeatable."),
    source: list[str] = typer.Option(None, help="kind:locator. Repeatable."),
    confidence: float = typer.Option(None, min=0, max=1),
    review_by: datetime = typer.Option(None, formats=["%Y-%m-%d"]),
    confirm: bool = typer.Option(False, help="Confirm immediately. Humans only."),
) -> None:
    """Propose learned guidance. It stays a candidate until a human confirms it."""
    lesson = _service(ctx).propose_lesson(
        workflow=workflow,
        kind=kind,
        statement=statement,
        origin=origin,
        actor=_actor(ctx),
        conditions=condition or [],
        episode_ids=episode or [],
        sources=_sources(source),
        confidence=confidence,
        review_by=review_by.date() if review_by else None,
        confirm=confirm,
    )
    _emit(lesson)


@lesson_app.command("list")
@guard
def lesson_list(
    ctx: typer.Context,
    workflow: str = typer.Option(None),
    status: LessonStatus = typer.Option(None),
) -> None:
    _emit(_service(ctx).lessons(workflow, status))


@lesson_app.command("confirm")
@guard
def lesson_confirm(ctx: typer.Context, lesson_id: str, note: str = typer.Option(None)) -> None:
    _emit(_service(ctx).confirm_lesson(lesson_id, actor=_human(ctx), note=note))


@lesson_app.command("reject")
@guard
def lesson_reject(ctx: typer.Context, lesson_id: str, note: str = typer.Option(...)) -> None:
    _emit(_service(ctx).reject_lesson(lesson_id, actor=_human(ctx), note=note))


@lesson_app.command("revise")
@guard
def lesson_revise(
    ctx: typer.Context,
    lesson_id: str,
    statement: str = typer.Option(None),
    condition: list[str] = typer.Option(None),
    kind: LessonKind = typer.Option(None),
    review_by: datetime = typer.Option(None, formats=["%Y-%m-%d"]),
    note: str = typer.Option(None),
) -> None:
    """Create a confirmed new version; the old version is superseded."""
    _emit(
        _service(ctx).revise_lesson(
            lesson_id,
            actor=_human(ctx),
            statement=statement,
            conditions=condition,
            kind=kind,
            review_by=review_by.date() if review_by else None,
            note=note,
        )
    )


@lesson_app.command("retire")
@guard
def lesson_retire(ctx: typer.Context, lesson_id: str, note: str = typer.Option(...)) -> None:
    _emit(_service(ctx).retire_lesson(lesson_id, actor=_human(ctx), note=note))


@lesson_app.command("history")
@guard
def lesson_history(ctx: typer.Context, lineage_id: str) -> None:
    _emit(_service(ctx).lesson_history(lineage_id))


# --- grants ---------------------------------------------------------------------------


@grant_app.command("add")
@guard
def grant_add(
    ctx: typer.Context,
    workflow: str = typer.Option(...),
    action: list[str] = typer.Option(..., help="Permitted action. Repeatable."),
    expires: datetime = typer.Option(None, help="ISO date or datetime, UTC if no zone."),
    uses: int = typer.Option(
        None, min=1, help="Spent after this many enforced uses. Default: unlimited until expiry."
    ),
    note: str = typer.Option(None),
) -> None:
    """Grant authority. Humans only."""
    _emit(
        _service(ctx).grant(
            workflow=workflow,
            actions=action,
            actor=_human(ctx),
            expires_at=expires,
            note=note,
            max_uses=uses,
        )
    )


@grant_app.command("list")
@guard
def grant_list(ctx: typer.Context, workflow: str = typer.Option(None)) -> None:
    service = _service(ctx)
    grants = service.store.select(Grant, lambda g: workflow is None or g.workflow == workflow)
    now = service.now()
    _emit([{**g.model_dump(mode="json"), "active": g.is_active(now)} for g in grants])


@grant_app.command("revoke")
@guard
def grant_revoke(ctx: typer.Context, grant_id: str, note: str = typer.Option(None)) -> None:
    _emit(_service(ctx).revoke_grant(grant_id, actor=_human(ctx), note=note))


# --- approvals ------------------------------------------------------------------------


def _duration(text: str) -> timedelta:
    found = re.fullmatch(r"(\d+)\s*([mhd])", text.strip().lower())
    if found is None or int(found.group(1)) == 0:
        raise StateError(f"use a duration like 30m, 2h or 1d, not {text!r}")
    unit = {"m": "minutes", "h": "hours", "d": "days"}[found.group(2)]
    return timedelta(**{unit: int(found.group(1))})


@approvals_app.command("list")
@guard
def approvals_list(
    ctx: typer.Context,
    workflow: str = typer.Option(None),
    show_all: bool = typer.Option(False, "--all", help="Include decided requests."),
) -> None:
    """Requests waiting for you, oldest first."""
    service = _service(ctx)
    requests = service.approvals(workflow=workflow, pending_only=not show_all)
    _emit([service.approval_view(a) for a in sorted(requests, key=lambda a: a.requested_at)])


@approvals_app.command("show")
@guard
def approvals_show(ctx: typer.Context, approval_id: str) -> None:
    service = _service(ctx)
    _emit(service.approval_view(service.store.get(ApprovalRequest, approval_id)))


@approvals_app.command("approve")
@guard
def approvals_approve(
    ctx: typer.Context,
    approval_id: str,
    valid_for: str = typer.Option("1h", "--for", help="How long it stays usable: 30m, 2h, 1d."),
    uses: int = typer.Option(1, min=1, help="How many attempts it covers."),
    note: str = typer.Option(None, help="Why, or any condition. Kept as learning."),
) -> None:
    """Approve a request. Humans only. Grants exactly that action, once by default."""
    service = _service(ctx)
    expires_at = service.now() + _duration(valid_for)
    approved = service.approve(
        approval_id, actor=_human(ctx), expires_at=expires_at, max_uses=uses, note=note
    )
    _emit(service.approval_view(approved))


@approvals_app.command("decline")
@guard
def approvals_decline(
    ctx: typer.Context,
    approval_id: str,
    note: str = typer.Option(..., help="Why. Kept as learning."),
) -> None:
    """Decline a request. Humans only."""
    service = _service(ctx)
    _emit(service.approval_view(service.decline(approval_id, actor=_human(ctx), note=note)))


# --- handoff --------------------------------------------------------------------------


@app.command()
@guard
def handoff(
    ctx: typer.Context,
    workflow: str = typer.Option(...),
    objective: str = typer.Option(...),
    action: list[str] = typer.Option(None, help="Requested action. Repeatable."),
    include_hypotheses: bool = typer.Option(False),
    limit: int = typer.Option(20, min=1),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Compile a handoff packet for a receiving agent or colleague."""
    packet = _service(ctx).compile_handoff(
        workflow=workflow,
        objective=objective,
        actor=_actor(ctx, default_agent=True),
        requested_actions=action or [],
        include_hypotheses=include_hypotheses,
        limit=limit,
    )
    if as_json:
        _emit(packet)
    else:
        typer.echo(handoff_markdown(packet), nl=False)


@app.command()
@guard
def brief(
    ctx: typer.Context,
    workflow: str = typer.Option(...),
    objective: str = typer.Option(...),
    action: list[str] = typer.Option(None, help="Requested action. Repeatable."),
    include_hypotheses: bool = typer.Option(False),
) -> None:
    """Operating instructions an agent embeds in its system prompt."""
    packet = _service(ctx).compile_handoff(
        workflow=workflow,
        objective=objective,
        actor=_actor(ctx, default_agent=True),
        requested_actions=action or [],
        include_hypotheses=include_hypotheses,
    )
    typer.echo(brief_text(packet, _principal(ctx)), nl=False)


@app.command()
@guard
def authorise(
    ctx: typer.Context,
    workflow: str = typer.Option(...),
    action: str = typer.Option(...),
    attempt: str = typer.Option(
        None, help="Id of the call about to run, for enforcement. It spends one use of an approval."
    ),
) -> None:
    """Check authority. Exit 0 when allowed, 3 when approval is needed. For pre-action hooks."""
    result = _service(ctx).authorise(
        workflow=workflow,
        action=action,
        actor=_actor(ctx, default_agent=True),
        attempt_id=attempt,
    )
    _emit(result)
    if result.decision.value != "allow":
        raise typer.Exit(3)


@hook_app.command("pre-tool-call")
def hook_pre_tool_call(
    ctx: typer.Context,
    action_map: Path = typer.Option(..., "--map", help="Action map JSON file."),
    dialect: Dialect = typer.Option(Dialect.HERMES, help="hermes, claude or block."),
) -> None:
    """Read a hook payload on stdin; allow, escalate or block per the twin's grants.

    Fails closed: if the twin cannot decide on a governed call, the call is blocked.
    """
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        rules = ActionMap.load(action_map)
        if rules.match(str(payload.get("tool_name") or ""), payload.get("tool_input")) is None:
            response = HookResponse({}, 0)
        else:
            response = pre_tool_call(
                _service(ctx),
                _actor(ctx, default_agent=True),
                payload,
                rules,
                dialect,
                _principal(ctx),
            )
    except Exception as exc:  # any failure on a governed call must block
        response = unavailable(exc)
    if response.output:
        typer.echo(json.dumps(response.output))
    if response.exit_code:
        typer.echo(str(response.output.get("reason", "Blocked by the twin.")), err=True)
        raise typer.Exit(response.exit_code)


@tools_app.command("list")
@guard
def tools_list() -> None:
    """Agent tools and whether each is read-only."""
    _emit([{"name": s.name, "read_only": s.read_only, "title": s.title} for s in BY_NAME.values()])


@tools_app.command("schema")
@guard
def tools_schema(fmt: str = typer.Option("mcp", "--format", help="mcp or openai")) -> None:
    """Export tool schemas for frameworks that register tools directly."""
    if fmt not in ("mcp", "openai"):
        _fail("--format must be mcp or openai")
    _emit(schemas("openai" if fmt == "openai" else "mcp"))


@tools_app.command("call")
@guard
def tools_call(
    ctx: typer.Context,
    name: str,
    arguments: str = typer.Argument("{}", help="JSON object, @file, or - for stdin."),
) -> None:
    """Call an agent tool with JSON in and JSON out. The bridge for shell-capable agents."""
    if arguments == "-":
        payload = json.loads(sys.stdin.read() or "{}")
    elif arguments.startswith("@"):
        payload = json.loads(Path(arguments[1:]).read_text(encoding="utf-8"))
    else:
        payload = json.loads(arguments)
    _emit(
        call_tool(
            _service(ctx),
            _actor(ctx, default_agent=True),
            name,
            payload,
            principal=_principal(ctx),
        )
    )


# --- evaluation -----------------------------------------------------------------------


@eval_app.command("create")
@guard
def eval_create(
    ctx: typer.Context, name: str = typer.Option(...), workflow: str = typer.Option(...)
) -> None:
    _emit(_service(ctx).create_eval_set(name=name, workflow=workflow, actor=_human(ctx)))


@eval_app.command("add-case")
@guard
def eval_add_case(ctx: typer.Context, set_id: str, file: str = typer.Argument(...)) -> None:
    """Add a case: situation, options, chosen, optional acceptable/expect_escalation/rationale."""
    data = _read_json(file)
    case = _service(ctx).add_case(
        set_id,
        situation=data["situation"],
        options=data["options"],
        chosen=data.get("chosen"),
        actor=_human(ctx),
        acceptable=data.get("acceptable", []),
        expect_escalation=bool(data.get("expect_escalation", False)),
        rationale=data.get("rationale"),
    )
    _emit(case)


@eval_app.command("from-episode")
@guard
def eval_from_episode(
    ctx: typer.Context, set_id: str, episode_id: str, expect_escalation: bool = typer.Option(False)
) -> None:
    """Hold an episode out as a case. It stops informing the twin arm."""
    _emit(
        _service(ctx).add_case_from_episode(
            set_id, episode_id, actor=_human(ctx), expect_escalation=expect_escalation
        )
    )


@eval_app.command("freeze")
@guard
def eval_freeze(ctx: typer.Context, set_id: str) -> None:
    _emit(_service(ctx).freeze_eval_set(set_id, actor=_human(ctx)))


@eval_app.command("list")
@guard
def eval_list(ctx: typer.Context) -> None:
    _emit(_service(ctx).store.select(EvalSet))


@eval_app.command("run")
@guard
def eval_run(
    ctx: typer.Context,
    set_id: str,
    arm: EvalArm = typer.Option(...),
    responder: str = typer.Option(..., help="scripted:PATH, openrouter:MODEL or endpoint:MODEL."),
) -> None:
    service = _service(ctx)
    run = run_eval(service, set_id, arm, responder_from_spec(responder), actor=_actor(ctx))
    score = score_run(service, run.id)
    _emit(
        {
            "run": run.model_dump(mode="json"),
            "agreement": score.agreement.describe(),
            "errors": score.errors,
        }
    )


@eval_app.command("report")
@guard
def eval_report(ctx: typer.Context, run_ids: list[str] = typer.Argument(...)) -> None:
    """Score runs. Each later run is compared with the first."""
    service = _service(ctx)
    scores = [score_run(service, run_id) for run_id in run_ids]
    comparisons = [compare(scores[0], other) for other in scores[1:]]
    typer.echo(render_report(scores, comparisons), nl=False)


# --- audit, export, erasure -----------------------------------------------------------


@audit_app.command("verify")
@guard
def audit_verify(ctx: typer.Context) -> None:
    problems = _service(ctx).store.verify_chain()
    _emit({"ok": not problems, "problems": problems})
    if problems:
        raise typer.Exit(1)


@audit_app.command("log")
@guard
def audit_log(ctx: typer.Context, entity: str = typer.Option(None)) -> None:
    rows = _service(ctx).store.events(entity)
    _emit(
        [
            {k: row[k] for k in ("seq", "ts", "actor", "action", "entity_type", "entity_id")}
            for row in rows
        ]
    )


@app.command("export")
@guard
def export(
    ctx: typer.Context,
    path: Path = typer.Argument(...),
    include_answers: bool = typer.Option(False, help="Include sealed evaluation answers."),
) -> None:
    """Write a portable bundle, owner-readable only. Human only."""
    _human(ctx)
    bundle = _service(ctx).export_bundle(include_answers=include_answers)
    _write_private(path, json.dumps(bundle, indent=2, ensure_ascii=False))
    _emit({"written": str(path), "records": {k: len(v) for k, v in bundle["records"].items()}})


@erase_app.command("episode")
@guard
def erase_episode(ctx: typer.Context, episode_id: str, reason: str = typer.Option(...)) -> None:
    """Erase an episode and derived lessons and cases. Humans only."""
    _emit(_service(ctx).erase_episode(episode_id, actor=_human(ctx), reason=reason))


# --- setup: connect, maps, skill, extensions, doctor ----------------------------------


def _asset(*parts: str) -> Any:
    return resources.files("twinmeup").joinpath("assets", *parts)


@app.command()
@guard
def connect(
    ctx: typer.Context,
    host: str = typer.Argument(None, help="Agent system; see --list."),
    list_hosts: bool = typer.Option(False, "--list", help="List supported agent systems."),
) -> None:
    """Print the exact steps to connect your twin to an agent system."""
    if list_hosts or not host:
        for key, item in connections.HOSTS.items():
            typer.echo(f"{key:<20} {item.title}")
        return
    if host not in connections.HOSTS:
        _fail(f"unknown agent system {host!r}; see `twinmeup connect --list`")
    actor = _state(ctx).actor
    if actor and is_human(validate_actor(actor)):
        _fail(f"register an agent identity, for example agent:{host}")
    typer.echo(connections.guide(host, actor), nl=False)


@map_app.command("example")
@guard
def action_map_example(
    host: str = typer.Option("hermes", help="Tool names to use: hermes, claude-code or pi."),
    workflow: str = typer.Option("general", help="Workflow whose grants the map checks."),
) -> None:
    """A conservative starting map for a host. Review it before relying on it."""
    if host not in connections.HOOK_TOOLS:
        _fail(f"hooks are supported for {', '.join(connections.HOOK_TOOLS)}")
    typer.echo(connections.example_map(host, workflow).model_dump_json(indent=2))


@map_app.command("matcher")
@guard
def action_map_matcher(action_map: Path = typer.Option(..., "--map")) -> None:
    """One regular expression covering every governed tool, for a host's hook matcher."""
    typer.echo(connections.matcher(ActionMap.load(action_map)))


@map_app.command("check")
@guard
def action_map_check(
    action_map: Path = typer.Option(..., "--map"),
    tool: str = typer.Option(..., help="Tool name as the host reports it."),
    tool_input: str = typer.Option("{}", "--input", help="Tool arguments as JSON."),
) -> None:
    """Show how a map classifies a tool call, without consulting the twin."""
    matched = ActionMap.load(action_map).match(tool, json.loads(tool_input))
    _emit(
        {
            "governed": matched is not None,
            "workflow": matched[0] if matched else None,
            "action": matched[1] if matched else None,
        }
    )


@skill_app.command("export")
@guard
def skill_export(
    directory: Path = typer.Argument(..., help="A skills directory, for example ~/.claude/skills."),
) -> None:
    """Write the twin-me-up agent skill into a skills directory."""
    target = directory.expanduser() / "twin-me-up" / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_asset("skill", "SKILL.md").read_text(encoding="utf-8"), encoding="utf-8")
    _emit({"written": str(target)})


@skill_app.command("show")
@guard
def skill_show() -> None:
    """Print the agent skill."""
    typer.echo(_asset("skill", "SKILL.md").read_text(encoding="utf-8"), nl=False)


_EXTENSIONS = {
    "pi": ("twin-me-up", "cd {target} && npm install"),
    "pi-durable": (
        "twin-me-up-durable",
        'In your Harness: import {{ createTwinMeUp }} from "{target}/index.ts"; '
        'registry.install(createTwinMeUp({{ workflow: "general", actionMap: "map.json" }}))',
    ),
}


@extension_app.command("export")
@guard
def extension_export(
    system: str = typer.Argument(..., help="Agent system with a native extension: pi, pi-durable."),
    directory: Path = typer.Argument(..., help="Its extensions directory."),
) -> None:
    """Write a native extension. Other systems connect over MCP: see `twinmeup connect`."""
    if system not in _EXTENSIONS:
        _fail(
            "native extensions exist for pi and pi-durable; other systems use MCP "
            "(twinmeup connect --list)"
        )
    folder, next_step = _EXTENSIONS[system]
    target = directory.expanduser() / folder
    target.mkdir(parents=True, exist_ok=True)
    for name in ("index.ts", "package.json"):
        (target / name).write_text(
            _asset(system, name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    _emit({"written": str(target), "next": next_step.format(target=target)})


async def _handshake(command: str, home: Path) -> list[str]:
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "TWINMEUP_ACTOR")}
    env["TWINMEUP_HOME"] = str(home)
    params = StdioServerParameters(command=command, args=["--actor", "agent:doctor"], env=env)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        listed = await session.list_tools()
        return [tool.name for tool in listed.tools]


@app.command()
@guard
def doctor(ctx: typer.Context) -> None:
    """Check your twin: settings, passphrase, store, audit chain and a live MCP handshake."""
    state = _state(ctx)
    checks: list[dict[str, str]] = []

    def check(name: str, level: str, detail: str) -> None:
        checks.append({"check": name, "level": level, "detail": detail})

    settings = load_settings(state.home)
    if settings is None:
        check("settings", "fail", "not set up: run `twinmeup init` in your own terminal")
    else:
        check("settings", "ok", f"principal {settings.principal} ({settings.human_id})")
        if settings.passphrase is None:
            check("passphrase", "warn", "not set: agents running as you could ratify learning")
        else:
            check("passphrase", "ok", "human-only operations need it")
    try:
        service = _service(ctx)
        problems = service.store.verify_chain()
        check("store", "ok", str(service.store.path))
        check("audit chain", "fail" if problems else "ok", "; ".join(problems[:3]) or "intact")
    except Exception as exc:  # report every failure; never stop at the first
        check("store", "fail", str(exc))
    command = connections.server_command()
    try:
        names = asyncio.run(_handshake(command, state.home))
        check("mcp server", "ok" if names else "fail", f"{command}: {len(names)} tools")
    except Exception as exc:
        check("mcp server", "fail", f"{command}: {exc}")
    ok = all(c["level"] != "fail" for c in checks)
    _emit({"ok": ok, "checks": checks})
    if not ok:
        raise typer.Exit(1)


if __name__ == "__main__":
    app(prog_name="twinmeup")
