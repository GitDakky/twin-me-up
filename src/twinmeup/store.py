"""SQLite persistence with a hash-chained, redactable audit log.

Every write is an entity upsert plus an audit event in the same transaction. Each event
hash covers the previous hash and a hash of its payload, so payloads can be redacted for
erasure without breaking verification of the chain itself.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from .errors import IntegrityError, NotFoundError, StateError
from .models import (
    SCHEMA_VERSION,
    Episode,
    EvalAnswer,
    EvalCase,
    EvalResult,
    EvalRun,
    EvalSet,
    Grant,
    HandoffPacket,
    Lesson,
    utcnow,
    validate_actor,
)

GENESIS = "0" * 64
REDACTED_ACTION = "redacted"

TYPES: dict[str, type[BaseModel]] = {
    "episode": Episode,
    "lesson": Lesson,
    "grant": Grant,
    "handoff": HandoffPacket,
    "eval_set": EvalSet,
    "eval_case": EvalCase,
    "eval_answer": EvalAnswer,
    "eval_run": EvalRun,
    "eval_result": EvalResult,
}
TYPE_NAMES: dict[type[BaseModel], str] = {cls: name for name, cls in TYPES.items()}

T = TypeVar("T", bound=BaseModel)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS entities (
    type TEXT NOT NULL,
    id TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (type, id)
);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    payload TEXT,
    payload_hash TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_entity ON events (entity_type, entity_id);
"""


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _event_hash(prev: str, ts: str, actor: str, action: str, etype: str, eid: str, ph: str) -> str:
    return sha256(canonical([prev, ts, actor, action, etype, eid, ph]))


class Store:
    """Thread-safe store. One connection guarded by a re-entrant lock."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        created = not path.exists()
        if created:
            # Create owner-only before SQLite opens it: WAL and SHM sidecars inherit this mode.
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        self._lock = threading.RLock()
        self._depth = 0
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)
        if created:
            os.chmod(path, 0o600)
        self._check_schema_version()

    def _check_schema_version(self) -> None:
        row = self._conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),)
            )
        elif int(row["value"]) > SCHEMA_VERSION:
            raise StateError(
                f"store schema {row['value']} is newer than this package ({SCHEMA_VERSION})"
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Re-entrant: nested calls join the outermost transaction."""
        with self._lock:
            outermost = self._depth == 0
            if outermost:
                self._conn.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield
            except BaseException:
                self._depth -= 1
                if outermost:
                    self._conn.execute("ROLLBACK")
                raise
            self._depth -= 1
            if outermost:
                self._conn.execute("COMMIT")

    # --- entities ---------------------------------------------------------------------

    def put(self, record: BaseModel, *, actor: str, action: str) -> None:
        validate_actor(actor)
        type_name = TYPE_NAMES[type(record)]
        record_id = str(getattr(record, "id"))  # noqa: B009 - every stored model has an id
        data = record.model_dump(mode="json")
        now = utcnow().isoformat()
        with self.transaction():
            self._conn.execute(
                "INSERT INTO entities(type, id, data, created_at, updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(type, id) DO UPDATE SET data=excluded.data, "
                "updated_at=excluded.updated_at",
                (type_name, record_id, canonical(data), now, now),
            )
            self._append(actor, action, type_name, record_id, data)

    def find(self, cls: type[T], record_id: str) -> T | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM entities WHERE type=? AND id=?", (TYPE_NAMES[cls], record_id)
            ).fetchone()
        return None if row is None else cls.model_validate_json(row["data"])

    def get(self, cls: type[T], record_id: str) -> T:
        record = self.find(cls, record_id)
        if record is None:
            raise NotFoundError(f"no {TYPE_NAMES[cls]} with id {record_id!r}")
        return record

    def select(self, cls: type[T], where: Callable[[T], bool] | None = None) -> list[T]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT data FROM entities WHERE type=? ORDER BY created_at, rowid",
                (TYPE_NAMES[cls],),
            ).fetchall()
        records = [cls.model_validate_json(row["data"]) for row in rows]
        return records if where is None else [r for r in records if where(r)]

    # --- audit ------------------------------------------------------------------------

    def record_event(
        self, *, actor: str, action: str, entity_type: str, entity_id: str, payload: Any
    ) -> None:
        validate_actor(actor)
        with self.transaction():
            self._append(actor, action, entity_type, entity_id, payload)

    def _append(self, actor: str, action: str, etype: str, eid: str, payload: Any) -> None:
        row = self._conn.execute("SELECT hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        prev = row["hash"] if row else GENESIS
        body = canonical(payload)
        payload_hash = sha256(body)
        ts = utcnow().isoformat()
        digest = _event_hash(prev, ts, actor, action, etype, eid, payload_hash)
        self._conn.execute(
            "INSERT INTO events(ts, actor, action, entity_type, entity_id, payload, payload_hash,"
            " prev_hash, hash) VALUES(?,?,?,?,?,?,?,?,?)",
            (ts, actor, action, etype, eid, body, payload_hash, prev, digest),
        )

    def events(self, entity_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM events" + (" WHERE entity_id=?" if entity_id else "") + " ORDER BY seq"
        with self._lock:
            rows = self._conn.execute(sql, (entity_id,) if entity_id else ()).fetchall()
        return [dict(row) for row in rows]

    def head(self) -> str:
        with self._lock:
            row = self._conn.execute("SELECT hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        return row["hash"] if row else GENESIS

    def verify_chain(self) -> list[str]:
        """Return a list of problems. Empty means the chain and every payload verify."""
        problems: list[str] = []
        prev = GENESIS
        redacted: set[int] = set()
        rows = self.events()
        for row in rows:
            if row["action"] == REDACTED_ACTION and row["payload"]:
                redacted.update(json.loads(row["payload"]).get("seqs", []))
        for row in rows:
            seq = row["seq"]
            if row["prev_hash"] != prev:
                problems.append(f"event {seq}: broken link to previous event")
            expected = _event_hash(
                row["prev_hash"],
                row["ts"],
                row["actor"],
                row["action"],
                row["entity_type"],
                row["entity_id"],
                row["payload_hash"],
            )
            if row["hash"] != expected:
                problems.append(f"event {seq}: hash mismatch")
            if row["payload"] is None:
                if seq not in redacted:
                    problems.append(f"event {seq}: payload missing without a redaction record")
            elif sha256(row["payload"]) != row["payload_hash"]:
                problems.append(f"event {seq}: payload does not match its recorded hash")
            prev = row["hash"]
        return problems

    def require_valid_chain(self) -> None:
        problems = self.verify_chain()
        if problems:
            raise IntegrityError("; ".join(problems[:5]))

    # --- erasure ----------------------------------------------------------------------

    def erase(self, cls: type[BaseModel], record_id: str, *, actor: str, reason: str) -> int:
        """Delete a record and redact its audit payloads. The chain stays verifiable."""
        validate_actor(actor)
        type_name = TYPE_NAMES[cls]
        with self.transaction():
            seqs = [
                row["seq"]
                for row in self._conn.execute(
                    "SELECT seq FROM events WHERE entity_type=? AND entity_id=? "
                    "AND payload IS NOT NULL",
                    (type_name, record_id),
                )
            ]
            deleted = self._conn.execute(
                "DELETE FROM entities WHERE type=? AND id=?", (type_name, record_id)
            ).rowcount
            if seqs:
                marks = ",".join("?" * len(seqs))
                self._conn.execute(f"UPDATE events SET payload=NULL WHERE seq IN ({marks})", seqs)
            self._append(
                actor,
                REDACTED_ACTION,
                type_name,
                record_id,
                {"seqs": seqs, "reason": reason, "deleted": bool(deleted)},
            )
        return len(seqs)

    def dump(self) -> dict[str, list[dict[str, Any]]]:
        with self._lock:
            rows = self._conn.execute("SELECT type, data FROM entities ORDER BY created_at, rowid")
            out: dict[str, list[dict[str, Any]]] = {name: [] for name in TYPES}
            for row in rows:
                out[row["type"]].append(json.loads(row["data"]))
        return out
