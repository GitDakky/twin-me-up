"""Small, deterministic BM25 ranking. A twin corpus is hundreds of items, not millions."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Sequence
from typing import TypeVar

T = TypeVar("T")

_WORD = re.compile(r"[a-z0-9]+")
_STOPWORD_TEXT = (
    "a an and are as at be by do does for from has have if in into is it its no not of on "
    "or our that the this to was we were will with you your"
)
STOPWORDS = frozenset(_STOPWORD_TEXT.split())


def _fold(word: str) -> str:
    """Conservative plural folding, so 'deposits' matches 'deposit' and 'quotes' 'quote'."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def tokens(text: str) -> list[str]:
    return [_fold(t) for t in _WORD.findall(text.lower()) if len(t) > 1 and t not in STOPWORDS]


def bm25(query: str, documents: Sequence[str], k1: float = 1.5, b: float = 0.75) -> list[float]:
    docs = [tokens(d) for d in documents]
    if not docs:
        return []
    terms = set(tokens(query))
    n = len(docs)
    avg = (sum(len(d) for d in docs) / n) or 1.0
    df = Counter(t for d in docs for t in set(d))
    scores: list[float] = []
    for doc in docs:
        tf = Counter(doc)
        length = len(doc) or 1
        score = 0.0
        for term in terms:
            if term not in tf:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            score += idf * tf[term] * (k1 + 1) / (tf[term] + k1 * (1 - b + b * length / avg))
        scores.append(score)
    return scores


def rank(
    query: str,
    items: Sequence[T],
    text_of: Callable[[T], str],
    *,
    limit: int,
    keep_zero: bool,
) -> list[T]:
    """Order items by relevance, stable on ties. Zero-score items are kept only if asked."""
    scores = bm25(query, [text_of(item) for item in items])
    order = sorted(range(len(items)), key=lambda i: (-scores[i], i))
    return [items[i] for i in order if keep_zero or scores[i] > 0][:limit]
