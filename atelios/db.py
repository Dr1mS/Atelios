"""experiment.db — schema (§9) and low-level access. WAL mode.

This is the experimenter's instrument store (invariant 4). It is NEVER the
subject's memory (that is Mnemos, tenant atelios). No metric computation lives
here in Phase 0 — §9 metrics are run-time functions that arrive with metrics.py
in Phase 1. This module only creates the schema and offers thin insert/query
helpers, plus the events audit sink.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from . import config

# Full schema, §9 verbatim in structure. Kept as one string so init is atomic.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS ticks (
    id                  INTEGER PRIMARY KEY,
    ts                  REAL,
    phase               INTEGER,
    action_type         TEXT,
    action_payload_json TEXT,
    result_text         TEXT,
    latency_ms          INTEGER,
    overrun             INTEGER
);

CREATE TABLE IF NOT EXISTS thoughts (
    id        INTEGER PRIMARY KEY,
    tick_id   INTEGER,
    content   TEXT,
    mood      TEXT,
    embedding BLOB
);

CREATE TABLE IF NOT EXISTS dreams (
    id               INTEGER PRIMARY KEY,
    tick_id          INTEGER,
    content          TEXT,
    covers_from_tick INTEGER,
    covers_to_tick   INTEGER
);

CREATE TABLE IF NOT EXISTS probes (
    id       INTEGER PRIMARY KEY,
    tick_id  INTEGER,
    battery  TEXT,
    question TEXT,
    response TEXT
);

CREATE TABLE IF NOT EXISTS tools (
    id            INTEGER PRIMARY KEY,
    name          TEXT,
    version       INTEGER,
    description   TEXT,
    code_path     TEXT,
    created_tick  INTEGER,
    runs          INTEGER,
    failures      INTEGER,
    last_run_tick INTEGER
);

CREATE TABLE IF NOT EXISTS metrics (
    id      INTEGER PRIMARY KEY,
    tick_id INTEGER,
    name    TEXT,
    value   REAL
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY,
    ts          REAL,
    kind        TEXT,
    payload_json TEXT
);

CREATE TABLE IF NOT EXISTS m3_candidates (
    id                       INTEGER PRIMARY KEY,
    tick_id                  INTEGER,
    tool_name                TEXT,
    created_tick             INTEGER,
    window_gap               INTEGER,
    preceded_by_memory_query INTEGER
);
"""


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open a WAL connection to experiment.db. Row access by name."""
    path = Path(db_path) if db_path is not None else config.DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Create the schema if absent and return an open connection."""
    conn = connect(db_path)
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def log_event(conn: sqlite3.Connection, kind: str, payload: dict[str, Any]) -> int:
    """Append to the events audit sink. Returns the new row id.

    events is the experimenter's audit trail (fetches, refusals, kills,
    overruns, Mnemos outages). Never surfaced to the subject's window.
    """
    cur = conn.execute(
        "INSERT INTO events (ts, kind, payload_json) VALUES (?, ?, ?)",
        (time.time(), kind, json.dumps(payload, ensure_ascii=False)),
    )
    conn.commit()
    return int(cur.lastrowid)


def fetch_events(conn: sqlite3.Connection, kind: str | None = None) -> list[sqlite3.Row]:
    """Read events, optionally filtered by kind (audit/inspection helper)."""
    if kind is None:
        return conn.execute("SELECT * FROM events ORDER BY id").fetchall()
    return conn.execute(
        "SELECT * FROM events WHERE kind = ? ORDER BY id", (kind,)
    ).fetchall()
