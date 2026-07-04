"""Mnemos client — the subject's memory (tenant atelios), over the real API.

Invariant 4: this is the subject's store, sealed from the experimenter's
experiment.db and from Adrien's personal tenant. Routes verified at build time
(addendum §A0): write = POST /v1/episodes (role="user", §A1), query = POST
/v1/query, health = GET /v1/health (strict ok==true, §A3), consolidate =
POST /v1/admin/consolidate (not used in Phase 0).

Resilience (§8, P3): a failed write is appended to data/mnemos_queue.jsonl and
flushed when a strict health check goes green. A query during an outage returns
the honest message defined in §8 — the subject never sees infra detail; the
failing dependency goes to the events audit table only (§A3).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from . import config

HEALTH_TIMEOUT_S = 2.0
RW_TIMEOUT_S = 10.0
QUERY_TOP_K = 5  # §6: top-5

MSG_MEMORY_UNAVAILABLE = "ta mémoire est inaccessible en ce moment"


class MnemosClient:
    def __init__(self, conn, base_url: str | None = None,
                 tenant: str | None = None,
                 queue_path: Path | None = None):
        from . import db  # local import to avoid cycle

        self._db = db
        self._conn = conn
        self._base = (base_url or config.MNEMOS_URL).rstrip("/")
        self._tenant = tenant or config.MNEMOS_TENANT
        self._queue_path = queue_path or config.MNEMOS_QUEUE_PATH

    # --- health -------------------------------------------------------------
    def health_ok(self) -> bool:
        """Strict, fail-closed (§A3): 200 AND ok==true. Malformed = down.

        The failing dependency detail is written to events (audit); it is never
        surfaced to the subject.
        """
        try:
            resp = httpx.get(f"{self._base}/v1/health", timeout=HEALTH_TIMEOUT_S)
        except httpx.HTTPError as exc:
            self._db.log_event(self._conn, "mnemos_down",
                               {"stage": "health", "error": str(exc)})
            return False
        if resp.status_code != 200:
            self._db.log_event(self._conn, "mnemos_down",
                               {"stage": "health", "status": resp.status_code})
            return False
        try:
            body = resp.json()
        except (json.JSONDecodeError, ValueError):
            self._db.log_event(self._conn, "mnemos_down",
                               {"stage": "health", "error": "malformed_json"})
            return False
        if body.get("ok") is not True:
            # Log which dependencies failed, for the experimenter only.
            self._db.log_event(self._conn, "mnemos_down",
                               {"stage": "health",
                                "ollama": body.get("ollama"),
                                "dbs": body.get("dbs"),
                                "failures": body.get("failures")})
            return False
        return True

    # --- write --------------------------------------------------------------
    def write(self, content: str) -> bool:
        """Write an episode (role=user, tenant=atelios). Returns True on 2xx.

        On failure, the payload is queued to JSONL for a later flush. The error
        is logged to events; the caller decides what honest message the subject
        sees (§8).
        """
        payload = {"content": content, "role": "user", "tenant": self._tenant}
        try:
            resp = httpx.post(f"{self._base}/v1/episodes", json=payload,
                              timeout=RW_TIMEOUT_S)
        except httpx.HTTPError as exc:
            self._enqueue(payload)
            self._db.log_event(self._conn, "mnemos_write_failed",
                               {"error": str(exc), "queued": True})
            return False
        if resp.status_code // 100 != 2:
            self._enqueue(payload)
            self._db.log_event(self._conn, "mnemos_write_failed",
                               {"status": resp.status_code, "queued": True})
            return False
        return True

    # --- query --------------------------------------------------------------
    def query(self, q: str) -> str:
        """Query memory, return raw text (§6: top-5, texte brut).

        During an outage, returns the honest message MSG_MEMORY_UNAVAILABLE and
        logs an event. Never surfaces infra detail to the subject.
        """
        payload = {"q": q, "k": QUERY_TOP_K, "tenant": self._tenant}
        try:
            resp = httpx.post(f"{self._base}/v1/query", json=payload,
                              timeout=RW_TIMEOUT_S)
            resp.raise_for_status()
            body = resp.json()
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            self._db.log_event(self._conn, "mnemos_query_failed",
                               {"error": str(exc)})
            return MSG_MEMORY_UNAVAILABLE
        return self._format_query(body)

    @staticmethod
    def _format_query(body: dict[str, Any]) -> str:
        """Flatten the /v1/query response into raw text (§6)."""
        lines: list[str] = []
        for item in body.get("episodes", []):
            ep = item.get("episode", {}) if isinstance(item, dict) else {}
            content = ep.get("content")
            if content:
                lines.append(content)
        for item in body.get("facts", []):
            fact = item.get("fact", {}) if isinstance(item, dict) else {}
            text = fact.get("text") or fact.get("content")
            if text:
                lines.append(text)
        for proc in body.get("procedural", []):
            if proc:
                lines.append(str(proc))
        return "\n".join(lines)

    # --- JSONL queue (P3) ---------------------------------------------------
    def _enqueue(self, payload: dict[str, Any]) -> None:
        self._queue_path.parent.mkdir(parents=True, exist_ok=True)
        with self._queue_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def queue_depth(self) -> int:
        if not self._queue_path.exists():
            return 0
        with self._queue_path.open("r", encoding="utf-8") as fh:
            return sum(1 for line in fh if line.strip())

    def flush_queue(self) -> int:
        """Flush queued writes if health is green. Returns count flushed.

        Only entries that write successfully are dropped; anything that fails
        again is re-queued, preserving order. No-op (returns 0) if down.
        """
        if not self._queue_path.exists():
            return 0
        if not self.health_ok():
            return 0

        with self._queue_path.open("r", encoding="utf-8") as fh:
            entries = [json.loads(line) for line in fh if line.strip()]

        flushed = 0
        remaining: list[dict[str, Any]] = []
        for payload in entries:
            if self._post_raw(payload):
                flushed += 1
            else:
                remaining.append(payload)

        # Rewrite the queue with whatever still failed (order preserved).
        with self._queue_path.open("w", encoding="utf-8") as fh:
            for payload in remaining:
                fh.write(json.dumps(payload, ensure_ascii=False) + "\n")

        if flushed:
            self._db.log_event(self._conn, "mnemos_queue_flushed",
                               {"flushed": flushed, "remaining": len(remaining)})
        return flushed

    def _post_raw(self, payload: dict[str, Any]) -> bool:
        try:
            resp = httpx.post(f"{self._base}/v1/episodes", json=payload,
                              timeout=RW_TIMEOUT_S)
            return resp.status_code // 100 == 2
        except httpx.HTTPError:
            return False
