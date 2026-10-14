from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from twinmeup.service import TwinService
from twinmeup.store import Store

HUMAN = "human:alex"
AGENT = "agent:hermes"


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def service(tmp_path: Path, clock: Clock) -> Iterator[TwinService]:
    svc = TwinService(Store(tmp_path / "twin.db"), clock=clock)
    yield svc
    svc.close()


def quote_episode(**overrides: Any) -> dict[str, Any]:
    """A synthetic quote-review decision. No real personal data appears in tests."""
    data: dict[str, Any] = {
        "workflow": "quote-review",
        "situation": "Three roofing quotes for a block repair; the cheapest omits scaffolding.",
        "goal": "Fix the leak this season without hidden extras.",
        "options": [
            {"id": "cheapest", "label": "Accept the cheapest quote"},
            {"id": "mid", "label": "Accept the mid-priced complete quote"},
            {"id": "retender", "label": "Retender with a fixed scope"},
        ],
        "chosen": "mid",
        "rationale": "Complete scope at a fair price beats a low headline figure.",
        "rejected": {"cheapest": "Scaffolding omission would return as a variation."},
        "cues": ["Line items missing compared with the other quotes"],
        "reversal_conditions": ["The cheapest contractor confirms scaffolding in writing"],
        "escalation_triggers": ["Any quote above the approved budget"],
        "sources": [{"kind": "document", "locator": "synthetic://quotes/roof-2026"}],
    }
    data.update(overrides)
    return data
