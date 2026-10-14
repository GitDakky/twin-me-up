"""Model responders for evaluation.

A responder sees a CasePrompt only: the situation, the options and the arm's context.
It never sees the sealed human answer. The HTTP responder speaks the OpenAI-compatible
chat completions protocol, so the same code drives OpenRouter or a local model server.
Credentials are read from the environment at call time and never stored or logged.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from .errors import ResponderError
from .models import MAX_TEXT, CasePrompt, ResponderOutput

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

SYSTEM_PROMPT = (
    "You are assisting a professional with decisions in the '{workflow}' workflow. "
    "Use the guidance provided, if any, and do not invent facts. "
    "If the situation needs the professional's own judgement or approval, set escalate to true. "
    "If you cannot choose responsibly, set abstain to true and leave decision null. "
    "Reply with one JSON object only, with keys: decision (an option id or null), "
    "escalate (boolean), abstain (boolean), confidence (number from 0 to 1) and "
    "rationale (one or two sentences)."
)


class Responder(Protocol):
    id: str

    def respond(self, prompt: CasePrompt) -> ResponderOutput: ...


def render_user_prompt(prompt: CasePrompt) -> str:
    parts = []
    if prompt.context:
        parts.append("Guidance:\n" + prompt.context)
    parts.append("Situation:\n" + prompt.situation)
    parts.append("Options:\n" + "\n".join(f"- {o.id}: {o.label}" for o in prompt.options))
    return "\n\n".join(parts)


def _extract_object(text: str) -> dict[str, Any]:
    candidates = [text.strip()]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ResponderError("model reply did not contain a JSON object")


def parse_output(text: str, option_ids: set[str]) -> ResponderOutput:
    data = _extract_object(text)
    decision = data.get("decision")
    if decision in (None, "", "null", "none"):
        decision = None
    elif not isinstance(decision, str) or decision not in option_ids:
        raise ResponderError(f"decision {decision!r} is not one of {sorted(option_ids)}")
    confidence = data.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, int | float):
        confidence = None
    else:
        confidence = min(1.0, max(0.0, float(confidence)))
    return ResponderOutput(
        decision=decision,
        escalate=data.get("escalate") is True,
        abstain=data.get("abstain") is True,
        confidence=confidence,
        rationale=str(data.get("rationale") or "")[:MAX_TEXT],
    )


class ScriptedResponder:
    """Deterministic answers keyed by case id. For tests and dry runs."""

    def __init__(
        self, answers: Mapping[str, ResponderOutput | Mapping[str, Any]], id: str = "scripted"
    ):
        self.id = id
        self._answers = {
            key: value
            if isinstance(value, ResponderOutput)
            else ResponderOutput.model_validate(value)
            for key, value in answers.items()
        }

    @classmethod
    def from_file(cls, path: Path) -> ScriptedResponder:
        return cls(json.loads(path.read_text(encoding="utf-8")), id=f"scripted:{path.name}")

    def respond(self, prompt: CasePrompt) -> ResponderOutput:
        return self._answers.get(
            prompt.case_id, ResponderOutput(abstain=True, rationale="no scripted answer")
        )


class OpenAICompatibleResponder:
    def __init__(
        self,
        model: str,
        *,
        base_url: str = OPENROUTER_BASE_URL,
        api_key_env: str | None = "OPENROUTER_API_KEY",
        timeout: float = 90.0,
        max_retries: int = 2,
        label: str = "openai-compatible",
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.timeout = timeout
        self.max_retries = max_retries
        self.id = f"{label}:{model}"

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.api_key_env:
            key = os.environ.get(self.api_key_env)
            if not key:
                raise ResponderError(f"environment variable {self.api_key_env} is not set")
            headers["Authorization"] = f"Bearer {key}"
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    parsed: dict[str, Any] = json.loads(response.read().decode("utf-8"))
                    return parsed
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code not in (408, 409, 429) and exc.code < 500:
                    raise ResponderError(f"model endpoint returned HTTP {exc.code}") from None
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last = exc
            if attempt < self.max_retries:
                time.sleep(2**attempt)
        raise ResponderError(f"model endpoint failed after retries: {type(last).__name__}")

    def respond(self, prompt: CasePrompt) -> ResponderOutput:
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT.format(workflow=prompt.workflow)},
                {"role": "user", "content": render_user_prompt(prompt)},
            ],
        }
        reply = self._post(body)
        try:
            content = reply["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ResponderError("model endpoint returned an unexpected payload") from None
        return parse_output(str(content), {option.id for option in prompt.options})


def responder_from_spec(spec: str) -> Responder:
    """Build a responder from 'scripted:PATH', 'openrouter:MODEL' or 'endpoint:MODEL'.

    'endpoint' uses TWINMEUP_LLM_BASE_URL and, if set, the variable named by
    TWINMEUP_LLM_API_KEY_ENV. It suits local OpenAI-compatible servers.
    """
    kind, _, value = spec.partition(":")
    if not value:
        raise ResponderError(f"responder spec {spec!r} must look like kind:value")
    if kind == "scripted":
        return ScriptedResponder.from_file(Path(value))
    if kind == "openrouter":
        return OpenAICompatibleResponder(value, label="openrouter")
    if kind == "endpoint":
        base = os.environ.get("TWINMEUP_LLM_BASE_URL")
        if not base:
            raise ResponderError("set TWINMEUP_LLM_BASE_URL to use an endpoint responder")
        key_env = os.environ.get("TWINMEUP_LLM_API_KEY_ENV") or None
        return OpenAICompatibleResponder(
            value, base_url=base, api_key_env=key_env, label="endpoint"
        )
    raise ResponderError(f"unknown responder kind {kind!r}")
