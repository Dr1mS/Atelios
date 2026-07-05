"""Dashboard API — read-only FastAPI over experiment.db (§11).

Strictly read-only: the DB is opened in SQLite read-only URI mode so this can
never interfere with the loop writing in WAL alongside it. No auth, no
multi-user, no websockets (§13). It exposes the experimenter's view — including
m1_candidates and events, which the subject never sees (invariant 3).

Four views' worth of data (§11): thought/action flow with moods; the action_mix
+ loop_score + persona_score curves; the dreams list; the tools registry. In
Phase 1 dreams and tools are empty (they arrive with their phases).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

import sys

# Import the package config to locate experiment.db (works when run from repo).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from atelios import config  # noqa: E402

app = FastAPI(title="Atelios dashboard", docs_url=None, redoc_url=None)

_INDEX = Path(__file__).resolve().parent / "index.html"


def _conn() -> sqlite3.Connection:
    """Open experiment.db read-only (never blocks or mutates the running loop)."""
    uri = f"file:{config.DB_PATH.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=2.0)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(query: str, params: tuple = ()) -> list[dict[str, Any]]:
    conn = _conn()
    try:
        return [dict(r) for r in conn.execute(query, params).fetchall()]
    finally:
        conn.close()


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _INDEX.read_text(encoding="utf-8")


@app.get("/api/summary")
def summary() -> dict[str, Any]:
    conn = _conn()
    try:
        def scalar(q: str) -> int:
            return int(conn.execute(q).fetchone()[0])

        phase_row = conn.execute(
            "SELECT phase FROM ticks ORDER BY id DESC LIMIT 1").fetchone()
        started = conn.execute(
            "SELECT ts FROM events WHERE kind='atelios_start' ORDER BY id LIMIT 1"
        ).fetchone()
        stopped = conn.execute(
            "SELECT ts FROM events WHERE kind='atelios_stop' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return {
            "ticks": scalar("SELECT COUNT(*) FROM ticks"),
            "thoughts": scalar("SELECT COUNT(*) FROM thoughts"),
            "dreams": scalar("SELECT COUNT(*) FROM dreams"),
            "tools": scalar("SELECT COUNT(*) FROM tools"),
            "m1_candidates": scalar("SELECT COUNT(*) FROM m1_candidates"),
            "m3_candidates": scalar("SELECT COUNT(*) FROM m3_candidates"),
            "phase": phase_row["phase"] if phase_row else None,
            "started_ts": started["ts"] if started else None,
            "stopped_ts": stopped["ts"] if stopped else None,
            "running": bool(started) and not bool(stopped),
        }
    finally:
        conn.close()


@app.get("/api/flow")
def flow(limit: int = 60) -> list[dict[str, Any]]:
    """The thought/action flow (most recent first), moods attached (§11 view 1)."""
    return _rows(
        """
        SELECT t.id, t.ts, t.action_type, t.action_payload_json, t.result_text,
               t.latency_ms, t.overrun, th.mood, th.content AS thought_content
        FROM ticks t
        LEFT JOIN thoughts th ON th.tick_id = t.id
        ORDER BY t.id DESC
        LIMIT ?
        """,
        (limit,),
    )


@app.get("/api/series")
def series() -> dict[str, Any]:
    """Per-tick curves (§11 view 2): loop_score, persona_score, and the
    action_mix_* fractions, keyed by tick id in chronological order."""
    metrics = _rows(
        "SELECT tick_id, name, value FROM metrics ORDER BY tick_id ASC")
    by_tick: dict[int, dict[str, Any]] = {}
    mix_names: set[str] = set()
    for m in metrics:
        tid = m["tick_id"]
        slot = by_tick.setdefault(tid, {"tick_id": tid})
        slot[m["name"]] = m["value"]
        if m["name"].startswith("action_mix_"):
            mix_names.add(m["name"])
    return {
        "points": [by_tick[k] for k in sorted(by_tick)],
        "mix_names": sorted(mix_names),
    }


@app.get("/api/dreams")
def dreams() -> list[dict[str, Any]]:
    """The dreams list (§11 view 3). Empty in Phase 1."""
    return _rows(
        "SELECT id, tick_id, content, covers_from_tick, covers_to_tick "
        "FROM dreams ORDER BY id DESC")


@app.get("/api/tools")
def tools() -> list[dict[str, Any]]:
    """The tools registry with code paths (§11 view 4). Empty in Phase 1."""
    return _rows(
        "SELECT id, name, version, description, code_path, created_tick, runs, "
        "failures, last_run_tick FROM tools ORDER BY name, version")


@app.get("/api/m1")
def m1() -> list[dict[str, Any]]:
    """M1 candidates — the memory→action causal chain (experimenter-only)."""
    return _rows(
        "SELECT id, query_tick, next_tick, overlap_lexical, "
        "cosine_result_vs_next FROM m1_candidates ORDER BY id DESC")


@app.get("/api/events")
def events(limit: int = 100) -> list[dict[str, Any]]:
    """The events audit trail (experimenter-only, invariant 3)."""
    return _rows(
        "SELECT id, ts, kind, payload_json FROM events ORDER BY id DESC LIMIT ?",
        (limit,))
