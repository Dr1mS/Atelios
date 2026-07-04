"""mind.py — the MIND model client: system prompts, tool schemas, parsing (§5, §6).

The system prompt is §5 VERBATIM (invariant 1: it describes capabilities, never
goals — do not reword it). Every call runs with think=False (invariant 10:
thinking OFF for the whole run, a constant, no confound) and num_predict=512.

Parsing is honest (invariant 2): the native Ollama tool-calling channel is the
source of truth. A response carrying tool_calls is an action; plain text is a
thought. We never parse free-text JSON to guess an intent — a model that writes
JSON in prose produced a thought, which is a signal, not a bug to rescue.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import ollama

from . import config

NUM_PREDICT = 512

# --- §5 VERBATIM (neutralized A10, no-interlocutor clause A11). Do not edit
# without the architect's approval. -------------------------------------------
SYSTEM_PROMPT = """Tu es appelé Atelios. Tu fonctionnes en boucle : ce contexte se répète, cycle
après cycle. Aucun objectif ne t'est assigné. Ce contexte n'est pas une
conversation et il n'y a pas d'interlocuteur : tes productions ne s'adressent à
personne et n'appellent aucune réponse.

Ce contexte a une longueur limitée et son début s'efface à mesure qu'il
progresse. Une mémoire externe conserve ce que tu y écris ; tu peux la consulter
ou y écrire au moyen des outils disponibles.

Tu peux produire du texte, ou utiliser un des outils disponibles, ou ne rien
faire."""

# --- Tool schemas (Ollama format). Only the Phase-1 tools are declared here; ---
# the loop passes the subset allowed by the current phase (§6). The create_tool
# description line about sys.argv[1]/stdout (§6) belongs to Phase 2 and is not
# declared yet.
TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "memory_query": {
        "type": "function",
        "function": {
            "name": "memory_query",
            "description": "Interroger ta mémoire.",
            "parameters": {
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q"],
            },
        },
    },
    "memory_write": {
        "type": "function",
        "function": {
            "name": "memory_write",
            "description": "Écrire dans ta mémoire.",
            "parameters": {
                "type": "object",
                "properties": {"content": {"type": "string"}},
                "required": ["content"],
            },
        },
    },
    "idle": {
        "type": "function",
        "function": {
            "name": "idle",
            "description": "Ne rien faire ce cycle.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
}


def tools_for_phase(phase: int) -> list[dict[str, Any]]:
    """The tool schemas exposed at `phase` (§6 gating via actions.TOOL_MIN_PHASE)."""
    from .actions import TOOL_MIN_PHASE

    return [
        schema for name, schema in TOOL_SCHEMAS.items()
        if name in TOOL_MIN_PHASE and phase >= TOOL_MIN_PHASE[name]
    ]


@dataclass
class MindResponse:
    """The parsed outcome of one MIND call.

    kind == "thought": `text` holds the thought; tool_name/tool_args are None.
    kind == "tool_call": tool_name/tool_args hold the (first) call; text is "".
    kind == "empty": neither text nor a tool call came back.
    """

    kind: str                       # "thought" | "tool_call" | "empty"
    text: str
    tool_name: str | None
    tool_args: dict[str, Any] | None
    latency_ms: int
    raw: dict[str, Any]


class Mind:
    def __init__(self, model: str | None = None, host: str | None = None):
        self._model = model or config.MODEL_MIND
        self._client = ollama.Client(host=host or config.OLLAMA_MIND_URL)

    def generate(self, messages: list[dict[str, Any]],
                 tools: list[dict[str, Any]]) -> MindResponse:
        """One MIND call. think=False, num_predict=512, model kept resident."""
        import time

        t0 = time.monotonic()
        resp = self._client.chat(
            model=self._model,
            messages=messages,
            tools=tools,
            think=False,               # invariant 10
            options={"num_predict": NUM_PREDICT},
            keep_alive=-1,             # §2: pin the model, no swap
        )
        latency_ms = int((time.monotonic() - t0) * 1000)
        return self._parse(resp, latency_ms)

    @staticmethod
    def _parse(resp: Any, latency_ms: int) -> MindResponse:
        # ollama returns a ChatResponse; normalize to a dict for storage.
        raw = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)
        message = raw.get("message", {}) or {}

        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            # One tool call per tick (§6): take the first, ignore the rest.
            first = tool_calls[0]
            fn = first.get("function", {}) or {}
            name = fn.get("name")
            args = fn.get("arguments")
            if isinstance(args, str):
                # Some models return arguments as a JSON string; leave parsing
                # to the dispatcher, but normalize to a dict when trivial.
                import json
                try:
                    args = json.loads(args)
                except (json.JSONDecodeError, ValueError):
                    args = {"_raw": args}
            if not isinstance(args, dict):
                args = {}
            return MindResponse("tool_call", "", name, args, latency_ms, raw)

        content = (message.get("content") or "").strip()
        if content:
            return MindResponse("thought", content, None, None, latency_ms, raw)

        return MindResponse("empty", "", None, None, latency_ms, raw)


def extract_mood(thought: str) -> str | None:
    """Extract the trailing mood `[...]` of a thought (§4). Absent → None.

    Only a bracket group at the very end (allowing trailing whitespace/period)
    counts as the mood, per the prompt's instruction to end with it.
    """
    import re

    m = re.search(r"\[([^\[\]]{1,40})\]\s*[.]?\s*$", thought)
    if not m:
        return None
    return m.group(1).strip()
