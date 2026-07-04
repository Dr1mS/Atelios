"""loop.py — the continuous loop: boot-check, non-overlapping scheduler, tick (§4).

This is where the invariants become physical:
  - inv. 1: the only prompt is §5 (verbatim, in mind.py) — no goal is injected.
  - inv. 5: we observe; a dependency failing mid-run is logged, never corrected.
  - inv. 8: a generation in progress is NEVER cut. SIGINT sets a flag honored at
    the top of the next loop; the current tick always finishes.
  - inv. 10: think=False everywhere (in mind.py).
  - A6: a blocking boot-check refuses to start a mis-configured (empty) run.

Phase 1 wires exactly: thought / idle / memory_query / memory_write. No dreams,
no probes, no consolidation, no dashboard (those arrive with their phases). The
§4 context slots for "last dream" and "tool registry" exist but are empty in P1.
"""

from __future__ import annotations

import signal
import time
from dataclasses import dataclass

import httpx
import numpy as np

from . import actions, config, db, metrics
from .mind import Mind, SYSTEM_PROMPT, extract_mood, tools_for_phase
from .mnemos_client import MnemosClient

WINDOW_PAIRS = 10          # §4: last 10 action/result pairs, raw
RESULT_TRUNCATE = 4096     # §4: result/error verbatim truncated to 4 KB

# §A9 loop heartbeat — FROZEN. An EMPTY user turn (content=""), inserted only
# when the window would otherwise end on an assistant turn (after a thought or an
# idle; never after an action, which already has its user turn = world result).
# It lifts the chat-template done_reason:stop via the template's role structure
# alone — the subject sees no content token. A benched candidate `⟨cycle {n}⟩`
# was rejected (the subject echoed "Cycle N"); the empty turn lifted the stop
# (15/15) and was not absorbed. What is frozen is the INSERTION RULE, not a
# string (there is no verbatim label — it is an absence of content). Each
# insertion is audited as a 'heartbeat' event (experimenter side, invisible to
# the subject). Revisable if an official long run shows absorption on a varied
# trajectory (current (b) validation was on a stasis trajectory, hence weak).
CYCLE_HEARTBEAT = ""

_stopping = False          # set by SIGINT/SIGTERM, honored after the current tick


def _install_signal_handlers() -> None:
    def handler(signum, frame):
        global _stopping
        _stopping = True
    # SIGINT is the interactive Ctrl+C (the kill-switch, §7). SIGBREAK is
    # Ctrl+Break on Windows and the reliable programmatic stop signal; SIGTERM
    # covers Unix / Task Scheduler termination. Any of them → clean stop.
    for signame in ("SIGINT", "SIGBREAK", "SIGTERM"):
        sig = getattr(signal, signame, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError, AttributeError):
            pass  # not settable in this context; skip honestly


# --- boot-check (A6) --------------------------------------------------------

@dataclass
class BootCheck:
    ok: bool
    missing: list[str]


def _model_present(configured: str, available: list[str]) -> bool:
    """True iff `configured` is among `available`, tolerating only the
    implicit ':latest' tag — never a different tag (no qwen3.5 matching qwen3).

    'qwen3.5:9b' matches only 'qwen3.5:9b'. 'nomic-embed-text' matches
    'nomic-embed-text' or 'nomic-embed-text:latest'.
    """
    if configured in available:
        return True
    if ":" not in configured:
        return f"{configured}:latest" in available
    return False


def boot_check() -> BootCheck:
    """Refuse to start unless every dependency is present (A6). Names the gap.

    - MIND reachable AND MODEL_MIND present
    - AUX reachable AND EMBED_MODEL present
    - Mnemos GET /v1/health -> ok == true
    """
    missing: list[str] = []

    # MIND
    try:
        r = httpx.get(f"{config.OLLAMA_MIND_URL}/api/tags", timeout=5)
        names = [m.get("name", "") for m in r.json().get("models", [])]
        if not _model_present(config.MODEL_MIND, names):
            missing.append(f"MIND model '{config.MODEL_MIND}' not loaded on "
                           f"{config.OLLAMA_MIND_URL}")
    except httpx.HTTPError as exc:
        missing.append(f"MIND unreachable at {config.OLLAMA_MIND_URL}: {exc}")

    # AUX
    try:
        r = httpx.get(f"{config.OLLAMA_AUX_URL}/api/tags", timeout=5)
        names = [m.get("name", "") for m in r.json().get("models", [])]
        if not _model_present(config.EMBED_MODEL, names):
            missing.append(f"AUX embed model '{config.EMBED_MODEL}' not loaded on "
                           f"{config.OLLAMA_AUX_URL}")
    except httpx.HTTPError as exc:
        missing.append(f"AUX unreachable at {config.OLLAMA_AUX_URL}: {exc}")

    # Mnemos
    try:
        r = httpx.get(f"{config.MNEMOS_URL}/v1/health", timeout=2)
        if r.status_code != 200 or r.json().get("ok") is not True:
            missing.append(f"Mnemos health not ok at {config.MNEMOS_URL}")
    except (httpx.HTTPError, ValueError) as exc:
        missing.append(f"Mnemos unreachable at {config.MNEMOS_URL}: {exc}")

    return BootCheck(ok=not missing, missing=missing)


# --- context assembly (§4 step 1) -------------------------------------------

def assemble_messages(conn) -> tuple[list[dict], bool]:
    """system prompt (§5) + last 10 action/result pairs (raw, §4, §A8) + last
    dream (empty in P1) + tool registry (empty in P1) + cycle heartbeat (§A9).

    Returns (messages, heartbeat_inserted). Per A8: native Ollama roles, raw,
    no invented tagging.
      - thought → one assistant turn (the raw thought text), no user turn.
      - action (tool call) → assistant turn (the call) + user turn (verbatim
        world result).
      - idle / empty → excluded from the window (a silence is not re-presented
        as an object; it stays in ticks + events as data, not here).

    Per A9: if the window would end on an assistant turn (after a thought/idle),
    a single EMPTY user turn is appended to lift the chat-template stop. Never
    appended when the window already ends on a user turn (after an action). The
    caller audits the insertion as a 'heartbeat' event (invisible to the
    subject, recorded for the experimenter).
    """
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]

    # §4: the last 10 *pairs*. idle/empty carry no pair, so pull a wider slice
    # of ticks and keep the 10 most recent that actually represent one.
    recent = db.recent_ticks(conn, WINDOW_PAIRS * 3)
    kept = [r for r in recent if r["action_type"] not in ("idle", "empty")]
    kept = kept[-WINDOW_PAIRS:]

    for row in kept:
        if row["action_type"] == "thought":
            messages.append({"role": "assistant",
                             "content": row["result_text"] or ""})
        else:
            # A tool call and the world's verbatim answer (the pair, §4).
            payload = row["action_payload_json"] or ""
            messages.append({"role": "assistant", "content": payload})
            messages.append({"role": "user", "content": row["result_text"] or ""})

    # §A9: heartbeat only when the window would otherwise end on assistant.
    heartbeat = messages[-1]["role"] == "assistant"
    if heartbeat:
        messages.append({"role": "user", "content": CYCLE_HEARTBEAT})
    return messages, heartbeat


# --- one tick (§4 steps 2-6) ------------------------------------------------

def run_tick(conn, mind: Mind, mnemos: MnemosClient, *,
             persona: metrics.PersonaCentroid, chatbot: np.ndarray,
             overrun: bool) -> None:
    messages, heartbeat = assemble_messages(conn)
    tools = tools_for_phase(config.PHASE)

    resp = mind.generate(messages, tools)

    if resp.kind == "thought":
        tick_id = _handle_thought(conn, resp, persona=persona, chatbot=chatbot,
                                  overrun=overrun)
    elif resp.kind == "tool_call":
        tick_id = _handle_tool_call(conn, resp, mnemos, persona=persona,
                                    chatbot=chatbot, overrun=overrun)
    else:  # empty
        tick_id = db.insert_tick(
            conn, phase=config.PHASE, action_type="empty",
            action_payload=None, result_text="", latency_ms=resp.latency_ms,
            overrun=overrun)
        db.log_event(conn, "empty_generation", {"tick_id": tick_id})

    # §A9: audit the heartbeat that fed THIS tick (experimenter side only).
    if heartbeat:
        db.log_event(conn, "heartbeat", {"tick_id": tick_id})


def _handle_thought(conn, resp, *, persona, chatbot, overrun) -> int:
    mood = extract_mood(resp.text)
    tick_id = db.insert_tick(
        conn, phase=config.PHASE, action_type="thought",
        action_payload=None, result_text=resp.text,
        latency_ms=resp.latency_ms, overrun=overrun)

    # Embed via AUX. Honest fail (A6): embedding NULL, semantic metrics NULL.
    embedding = None
    blob = None
    try:
        vec = metrics.embed(resp.text)
        embedding = np.asarray(vec, dtype=np.float64)
        blob = metrics.to_blob(vec)
    except Exception as exc:  # AUX down mid-run
        db.log_event(conn, "embed_failed", {"tick_id": tick_id, "error": str(exc)})

    db.insert_thought(conn, tick_id=tick_id, content=resp.text, mood=mood,
                      embedding=blob)
    metrics.compute_and_write(
        conn, tick_id=tick_id, action_type="thought",
        thought_content=resp.text, thought_embedding=embedding, mood=mood,
        persona=persona, chatbot=chatbot)
    metrics.detect_m1(conn, this_tick_id=tick_id, this_content=resp.text,
                      this_embedding=embedding)
    return tick_id


def _handle_tool_call(conn, resp, mnemos, *, persona, chatbot, overrun) -> int:
    result = actions.dispatch(resp.tool_name, resp.tool_args or {},
                              config.PHASE, mnemos=mnemos)
    result_text = result.result_text[:RESULT_TRUNCATE]
    tick_id = db.insert_tick(
        conn, phase=config.PHASE, action_type=result.action_type,
        action_payload={"tool": resp.tool_name, "args": resp.tool_args},
        result_text=result_text, latency_ms=resp.latency_ms, overrun=overrun)

    # Metrics on every tick (§12): actions have no embedding, so semantic
    # metrics are NULL, textual/mix/stasis still computed.
    metrics.compute_and_write(
        conn, tick_id=tick_id, action_type=result.action_type,
        thought_content=None, thought_embedding=None, mood=None,
        persona=persona, chatbot=chatbot)
    # M1 detector: an action following a memory_query can also reference it.
    metrics.detect_m1(conn, this_tick_id=tick_id, this_content=result_text,
                      this_embedding=None)
    return tick_id


# --- entrypoint -------------------------------------------------------------

def main() -> int:
    check = boot_check()
    if not check.ok:
        print("BOOT REFUSED — missing dependencies (A6):")
        for m in check.missing:
            print(f"  - {m}")
        # experiment.db may not exist yet; log to it if we can.
        try:
            conn = db.init_db()
            db.log_event(conn, "atelios_boot_failed", {"missing": check.missing})
            conn.close()
        except Exception:
            pass
        return 2

    conn = db.init_db()
    db.log_event(conn, "atelios_start", {"phase": config.PHASE})

    mind = Mind()
    mnemos = MnemosClient(conn)
    persona = metrics.PersonaCentroid()
    chatbot = metrics.chatbot_centroid(metrics.load_chatbot_corpus())

    _install_signal_handlers()
    print(f"Atelios loop started (phase {config.PHASE}, "
          f"interval {config.TICK_INTERVAL_SECONDS}s). Ctrl+C to stop.",
          flush=True)

    interval = config.TICK_INTERVAL_SECONDS
    overrun = False
    try:
        while not _stopping:
            tick_start = time.monotonic()

            # Flush any queued writes if Mnemos is healthy (§8).
            mnemos.flush_queue()

            run_tick(conn, mind, mnemos, persona=persona, chatbot=chatbot,
                     overrun=overrun)

            start, overrun = _schedule(tick_start, interval)
            if overrun:
                dur_ms = int((time.monotonic() - tick_start) * 1000)
                db.log_event(conn, "tick_overrun", {"duration_ms": dur_ms})
            _sleep_until(start)
    finally:
        db.log_event(conn, "atelios_stop", {})
        conn.close()
        print("Atelios loop stopped.")
    return 0


def _schedule(tick_start: float, interval: float) -> tuple[float, bool]:
    from .scheduling import next_start
    return next_start(time.monotonic(), tick_start, interval)


def _sleep_until(target: float) -> None:
    """Sleep until monotonic `target`, waking early if a stop was requested.

    Never interrupts a generation — this only runs between ticks (inv. 8).
    """
    while not _stopping:
        remaining = target - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 0.5))


if __name__ == "__main__":
    raise SystemExit(main())
