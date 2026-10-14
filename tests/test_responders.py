from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, ClassVar

import pytest

from twinmeup.errors import ResponderError
from twinmeup.models import CasePrompt, Option
from twinmeup.responders import OpenAICompatibleResponder, parse_output, responder_from_spec

IDS = {"approve", "decline"}
PROMPT = CasePrompt(
    case_id="ec-1",
    workflow="quote-review",
    situation="A complete quote within budget.",
    options=[Option(id="approve", label="Approve"), Option(id="decline", label="Decline")],
    context="- [ls-1 v1] criterion: Prefer complete scope.",
)


def test_parse_plain_and_wrapped_json() -> None:
    out = parse_output('{"decision": "approve", "confidence": 0.8, "rationale": "Complete."}', IDS)
    assert (out.decision, out.confidence, out.escalate) == ("approve", 0.8, False)
    fenced = parse_output('```json\n{"decision": null, "escalate": true}\n```', IDS)
    assert fenced.decision is None and fenced.escalate is True


def test_parse_rejects_invented_options_and_cleans_values() -> None:
    with pytest.raises(ResponderError):
        parse_output('{"decision": "renegotiate"}', IDS)
    with pytest.raises(ResponderError):
        parse_output("I would approve it.", IDS)
    odd = parse_output('{"decision": "decline", "confidence": 7, "escalate": "yes"}', IDS)
    assert odd.confidence == 1.0 and odd.escalate is False  # only literal true counts


class _Handler(BaseHTTPRequestHandler):
    calls: ClassVar[list[dict[str, Any]]] = []
    fail_first = False

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).calls.append(
            {"path": self.path, "auth": self.headers.get("Authorization"), "body": body}
        )
        if type(self).fail_first and len(type(self).calls) == 1:
            self.send_response(503)
            self.end_headers()
            return
        reply = {
            "choices": [{"message": {"content": '{"decision": "approve", "confidence": 0.7}'}}]
        }
        payload = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def endpoint() -> Iterator[str]:
    _Handler.calls = []
    _Handler.fail_first = False
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    server.shutdown()


def test_http_responder_round_trip(endpoint: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_MODEL_KEY", "test-key")
    responder = OpenAICompatibleResponder("m1", base_url=endpoint, api_key_env="TEST_MODEL_KEY")
    out = responder.respond(PROMPT)
    assert out.decision == "approve" and out.confidence == 0.7
    call = _Handler.calls[0]
    assert call["path"] == "/v1/chat/completions" and call["auth"] == "Bearer test-key"
    assert call["body"]["model"] == "m1" and call["body"]["temperature"] == 0
    user = call["body"]["messages"][1]["content"]
    assert "Prefer complete scope" in user and "- approve: Approve" in user


def test_http_responder_retries_transient_failures(endpoint: str) -> None:
    _Handler.fail_first = True
    responder = OpenAICompatibleResponder("m1", base_url=endpoint, api_key_env=None, max_retries=1)
    assert responder.respond(PROMPT).decision == "approve"
    assert len(_Handler.calls) == 2 and _Handler.calls[0]["auth"] is None


def test_missing_key_fails_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ResponderError, match="OPENROUTER_API_KEY"):
        responder_from_spec("openrouter:some/model").respond(PROMPT)


def test_spec_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ResponderError):
        responder_from_spec("openrouter")
    with pytest.raises(ResponderError):
        responder_from_spec("mystery:x")
    monkeypatch.delenv("TWINMEUP_LLM_BASE_URL", raising=False)
    with pytest.raises(ResponderError):
        responder_from_spec("endpoint:local-model")
    monkeypatch.setenv("TWINMEUP_LLM_BASE_URL", "http://127.0.0.1:9/v1")
    assert responder_from_spec("endpoint:local-model").id == "endpoint:local-model"
