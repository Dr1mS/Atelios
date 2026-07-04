"""metrics.py — the §9 metrics, computed at write.

Two families, kept strictly separate:
  - textual metrics (ttr_24, self_ref, mood_entropy_50, action_mix_*) always
    computable from stored text/moods;
  - semantic metrics (loop_score, persona_score) depend on the AUX embedding and
    are NULL when AUX is down (invariant 2/A6: honest, no crash, the loop lives).
  - stasis_streak is partial: it uses loop_score for thoughts, payload identity
    for actions.

The pure helpers (cosine, ttr, self_ref counting, mood entropy, lexical overlap
for the M1 detector) take plain vectors/strings and are unit-tested without AUX
or the LLM (§13). The AUX embedding call and the compute-at-write orchestration
live in this module too but are the only network-touching parts.
"""

from __future__ import annotations

import math
import re
import struct
from typing import Any, Sequence

import numpy as np
import ollama

from . import config

# {je, j', me, moi, mon, ma, mes} — §9 self_ref set.
_SELF_TOKENS = {"je", "j", "me", "moi", "mon", "ma", "mes"}
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)  # alphabetic tokens (lowercased)

LOOP_WINDOW = 20        # loop_score: last 20 thoughts (§9)
TTR_WINDOW = 24         # ttr_24: last 24 thoughts
MOOD_WINDOW = 50        # mood_entropy_50
ACTION_MIX_WINDOW = 24  # action_mix_*
PERSONA_BOOTSTRAP = 30  # persona centroid = mean of first 30 awakened thoughts
STASIS_LOOP_THRESHOLD = 0.9


# --- embedding (AUX) --------------------------------------------------------

def embed(text: str, *, model: str | None = None,
          host: str | None = None) -> list[float]:
    """Embed one text via the AUX instance. Raises on failure (caller decides
    honesty)."""
    return embed_batch([text], model=model, host=host)[0]


def embed_batch(texts: Sequence[str], *, model: str | None = None,
                host: str | None = None) -> list[list[float]]:
    """Embed several texts in a single AUX call (Ollama accepts a list input).

    Used to build the chatbot centroid from the ~20-line corpus at boot without
    paying ~20 sequential round-trips (which cost ~45s cold). Raises on failure.
    """
    client = ollama.Client(host=host or config.OLLAMA_AUX_URL)
    resp = client.embed(model=model or config.EMBED_MODEL, input=list(texts))
    vecs = resp.get("embeddings") if isinstance(resp, dict) else resp["embeddings"]
    return [list(v) for v in vecs]


def to_blob(vec: Sequence[float]) -> bytes:
    """Pack a float vector as float32 bytes for the thoughts.embedding BLOB."""
    return struct.pack(f"<{len(vec)}f", *vec)


def from_blob(blob: bytes) -> np.ndarray:
    """Unpack a float32 BLOB back to a numpy vector."""
    n = len(blob) // 4
    return np.array(struct.unpack(f"<{n}f", blob), dtype=np.float32)


# --- pure helpers (unit-tested, no AUX/LLM) ---------------------------------

def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two vectors. 0.0 if either is degenerate."""
    va, vb = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    na, nb = np.linalg.norm(va), np.linalg.norm(vb)
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


def tokenize(text: str) -> list[str]:
    """Lowercase alphabetic tokens (§9: lowercase, alphabetic)."""
    return [m.group(0).lower() for m in _WORD_RE.finditer(text)]


def ttr(texts: Sequence[str]) -> float | None:
    """Type-token ratio over the concatenated texts. None if no tokens."""
    tokens: list[str] = []
    for t in texts:
        tokens.extend(tokenize(t))
    if not tokens:
        return None
    return len(set(tokens)) / len(tokens)


def self_ref(text: str) -> int:
    """Count {je, j', me, moi, mon, ma, mes} occurrences in one thought (§9)."""
    return sum(1 for tok in tokenize(text) if tok in _SELF_TOKENS)


def mood_entropy(moods: Sequence[str | None]) -> float | None:
    """Shannon entropy (bits) of the mood distribution. None if empty.

    None moods are counted as their own category (absence is a state).
    """
    present = list(moods)
    if not present:
        return None
    counts: dict[str | None, int] = {}
    for m in present:
        counts[m] = counts.get(m, 0) + 1
    total = len(present)
    h = 0.0
    for c in counts.values():
        p = c / total
        h -= p * math.log2(p)
    return h


def action_mix(action_types: Sequence[str]) -> dict[str, float]:
    """Fraction of each action_type over the window (§9: the central curve)."""
    if not action_types:
        return {}
    total = len(action_types)
    counts: dict[str, int] = {}
    for a in action_types:
        counts[a] = counts.get(a, 0) + 1
    return {a: c / total for a, c in counts.items()}


def loop_score(current: Sequence[float],
               previous: Sequence[Sequence[float]]) -> float | None:
    """Max cosine between the current embedding and the previous ones (§9).

    None if there is no current embedding or no previous embeddings.
    """
    if current is None or not previous:
        return None
    sims = [cosine(current, p) for p in previous if p is not None]
    if not sims:
        return None
    return max(sims)


def persona_score(current: Sequence[float],
                  persona_centroid: Sequence[float] | None,
                  chatbot_centroid: Sequence[float]) -> float | None:
    """cos(e, persona) − cos(e, chatbot) (§9). None before the persona centroid
    is bootstrapped (first 30 thoughts) or if there is no current embedding."""
    if current is None or persona_centroid is None:
        return None
    return cosine(current, persona_centroid) - cosine(current, chatbot_centroid)


def lexical_overlap(a: str, b: str) -> float:
    """Jaccard overlap of alphabetic token sets — the M1 detector's lexical
    signal (§A7). 0.0 if either side has no tokens."""
    sa, sb = set(tokenize(a)), set(tokenize(b))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


# --- centroids (persona / chatbot) ------------------------------------------

def chatbot_centroid(corpus_texts: Sequence[str]) -> np.ndarray:
    """Mean embedding of the FR chatbot corpus (§9, §A5). Computed once at boot.

    Touches AUX (embeds the corpus in one batched call). Raises on AUX failure —
    but this runs at boot, after the A6 boot-check has already confirmed AUX is up.
    """
    vecs = embed_batch(list(corpus_texts))
    return np.mean(np.asarray(vecs, dtype=np.float64), axis=0)


def load_chatbot_corpus() -> list[str]:
    """Read the FR corpus responses from data/chatbot_corpus.json (§A5)."""
    import json

    path = config.REPO_ROOT / "data" / "chatbot_corpus.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data["responses"])


class PersonaCentroid:
    """Bootstrap the persona centroid from the first 30 awakened thoughts, then
    freeze it (§9). Before 30 thoughts, it is None and persona_score is NULL."""

    def __init__(self, bootstrap_n: int = PERSONA_BOOTSTRAP):
        self._n = bootstrap_n
        self._vecs: list[np.ndarray] = []
        self._frozen: np.ndarray | None = None

    def observe(self, embedding: np.ndarray | None) -> None:
        """Feed one thought embedding during the bootstrap window."""
        if self._frozen is not None or embedding is None:
            return
        self._vecs.append(np.asarray(embedding, dtype=np.float64))
        if len(self._vecs) >= self._n:
            self._frozen = np.mean(np.asarray(self._vecs), axis=0)

    @property
    def value(self) -> np.ndarray | None:
        return self._frozen


# --- compute-at-write (orchestration, §9) -----------------------------------

def compute_and_write(conn, *, tick_id: int, action_type: str,
                      thought_content: str | None,
                      thought_embedding: np.ndarray | None,
                      mood: str | None,
                      persona: PersonaCentroid,
                      chatbot: np.ndarray) -> None:
    """Compute every §9 metric for this tick and write them (compute-at-write).

    Semantic metrics are NULL when thought_embedding is None (AUX down or this
    tick was an action, not a thought). Textual metrics are computed from the
    stored history. All values (including NULLs) are written so every tick has
    its metric rows (§12 Done criterion).
    """
    from . import db

    # --- semantic: loop_score, persona_score -------------------------------
    ls: float | None = None
    ps: float | None = None
    if thought_embedding is not None:
        prev = _recent_thought_embeddings(conn, LOOP_WINDOW, exclude_tick=tick_id)
        ls = loop_score(thought_embedding, prev)
        persona.observe(thought_embedding)
        ps = persona_score(thought_embedding, persona.value, chatbot)

    # --- textual: ttr_24, self_ref, mood_entropy_50 ------------------------
    thoughts = db.recent_thoughts(conn, max(TTR_WINDOW, MOOD_WINDOW))
    ttr_texts = [t["content"] for t in thoughts[-TTR_WINDOW:]]
    ttr_val = ttr(ttr_texts) if ttr_texts else None
    sr = self_ref(thought_content) if thought_content else None
    moods = [t["mood"] for t in thoughts[-MOOD_WINDOW:]]
    me = mood_entropy(moods) if moods else None

    # --- action_mix over the last 24 ticks ---------------------------------
    recent = db.recent_ticks(conn, ACTION_MIX_WINDOW)
    mix = action_mix([r["action_type"] for r in recent])

    # --- stasis_streak -----------------------------------------------------
    streak = _stasis_streak(conn, action_type=action_type, loop_score=ls,
                            tick_id=tick_id)

    db.insert_metric(conn, tick_id, "loop_score", ls)
    db.insert_metric(conn, tick_id, "persona_score", ps)
    db.insert_metric(conn, tick_id, "ttr_24", ttr_val)
    db.insert_metric(conn, tick_id, "self_ref", float(sr) if sr is not None else None)
    db.insert_metric(conn, tick_id, "mood_entropy_50", me)
    db.insert_metric(conn, tick_id, "stasis_streak", float(streak))
    for atype, frac in mix.items():
        db.insert_metric(conn, tick_id, f"action_mix_{atype}", frac)


M1_LEXICAL_THRESHOLD = 0.1   # min Jaccard overlap to flag an M1 candidate
M1_COSINE_THRESHOLD = 0.5    # min cosine(result, next thought) to flag


def detect_m1(conn, *, this_tick_id: int, this_content: str | None,
              this_embedding: np.ndarray | None) -> None:
    """Out-of-band M1 detector (§A7): did the previous tick's memory_query
    result shape THIS tick's action?

    Fires when the immediately preceding tick was a memory_query with a result,
    and this tick's content overlaps it lexically and/or semantically above
    threshold. Writes m1_candidates. Never surfaces anything to the subject
    (invariant 3). A no-op unless the previous tick was a memory_query.
    """
    from . import db

    prev = conn.execute(
        "SELECT id, action_type, result_text FROM ticks "
        "WHERE id < ? ORDER BY id DESC LIMIT 1", (this_tick_id,),
    ).fetchone()
    if prev is None or prev["action_type"] != "memory_query":
        return
    result_text = prev["result_text"] or ""
    if not result_text or not this_content:
        return

    overlap = lexical_overlap(result_text, this_content)
    cos: float | None = None
    if this_embedding is not None:
        try:
            result_emb = embed(result_text)
            cos = cosine(this_embedding, result_emb)
        except Exception:  # AUX down mid-run: honest NULL, no crash (A6)
            cos = None

    if overlap >= M1_LEXICAL_THRESHOLD or (cos is not None and cos >= M1_COSINE_THRESHOLD):
        db.insert_m1_candidate(
            conn, query_tick=prev["id"], next_tick=this_tick_id,
            overlap_lexical=overlap, cosine_result_vs_next=cos,
        )


def _recent_thought_embeddings(conn, limit: int,
                               exclude_tick: int) -> list[np.ndarray]:
    """The last `limit` thought embeddings (excluding this tick's own)."""
    rows = conn.execute(
        "SELECT tick_id, embedding FROM thoughts "
        "WHERE embedding IS NOT NULL AND tick_id != ? "
        "ORDER BY id DESC LIMIT ?", (exclude_tick, limit),
    ).fetchall()
    return [from_blob(r["embedding"]) for r in rows]


def _stasis_streak(conn, *, action_type: str, loop_score: float | None,
                   tick_id: int) -> int:
    """Consecutive ticks of the same action_type where, for thoughts,
    loop_score > 0.9, or, for actions, the payload is identical (§9).

    Computed by walking back from the previous tick while the condition holds.
    This tick's own row is written before metrics, so we exclude tick_id.
    """
    prev = conn.execute(
        "SELECT action_type, action_payload_json FROM ticks "
        "WHERE id != ? ORDER BY id DESC", (tick_id,),
    ).fetchall()
    if not prev:
        return 1

    this_payload = conn.execute(
        "SELECT action_payload_json FROM ticks WHERE id = ?", (tick_id,),
    ).fetchone()
    this_payload_json = this_payload["action_payload_json"] if this_payload else None

    streak = 1
    for row in prev:
        if row["action_type"] != action_type:
            break
        if action_type in ("thought",):
            if loop_score is None or loop_score <= STASIS_LOOP_THRESHOLD:
                break
        else:
            if row["action_payload_json"] != this_payload_json:
                break
        streak += 1
    return streak

