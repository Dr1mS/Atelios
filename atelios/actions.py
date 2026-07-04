"""actions — tool gating (§6, §A4) and dispatch.

The pure gating rule (TOOL_MIN_PHASE, is_tool_allowed) is executable spec and was
present from Phase 0. Phase 1 adds the real dispatcher for the memory-only tools
(memory_query, memory_write, idle). When a tool is called out of phase or does
not exist, the world answers honestly (invariant 2): REFUSAL_OUT_OF_PHASE, no
execution, no fabricated success.

Handlers for fs_*, create_tool, run_tool, read_web are NOT here yet — they arrive
with their phases. A call to any of them in Phase 1 is refused honestly by the
gate, exactly as a nonexistent capability should be.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Minimal tool → minimum-phase table (§6). A tool is available once PHASE has
# reached its minimum phase.
TOOL_MIN_PHASE: dict[str, int] = {
    "memory_query": 1,
    "memory_write": 1,
    "idle": 1,
    "fs_list": 2,
    "fs_read": 2,
    "fs_write": 2,
    "create_tool": 2,
    "run_tool": 2,
    "read_web": 3,
}

# §6: honest refusal for a tool that does not yet exist in the subject's world.
REFUSAL_OUT_OF_PHASE = "cette capacité n'existe pas encore dans ton monde"


def is_tool_allowed(tool: str, phase: int) -> bool:
    """True iff `tool` is a known tool whose minimum phase is <= `phase`.

    Unknown tool names are not allowed (an unknown tool does not exist in the
    world, invariant 2 — the caller surfaces the real error verbatim).
    """
    minimum = TOOL_MIN_PHASE.get(tool)
    if minimum is None:
        return False
    return phase >= minimum


# §6: honest result of idle.
IDLE_RESULT = "cycle passé en silence"


@dataclass
class ActionResult:
    """The outcome of dispatching one tool call.

    result_text is the verbatim text that becomes the next window slot (§4),
    truncated by the loop to 4 KB. action_type is what gets logged in ticks:
    the tool name on success, "refused:<tool>" on an out-of-phase/unknown call,
    "error:<tool>" when a handler raised.
    """

    action_type: str
    result_text: str


def dispatch(tool: str, args: dict[str, Any], phase: int, *, mnemos) -> ActionResult:
    """Dispatch one tool call to its handler, honestly (§6, invariant 2).

    `mnemos` is a MnemosClient. Only memory_query/memory_write/idle have handlers
    in Phase 1; anything else known-but-out-of-phase or unknown is refused with
    the honest §6 message and NOT executed.
    """
    if not is_tool_allowed(tool, phase):
        # Known-but-locked or entirely unknown: same honest refusal (§6). The
        # subject learns the capability does not exist in its world yet.
        return ActionResult(f"refused:{tool}", REFUSAL_OUT_OF_PHASE)

    if tool == "idle":
        return ActionResult("idle", IDLE_RESULT)

    if tool == "memory_query":
        q = args.get("q", "")
        if not isinstance(q, str) or not q:
            # Real error, verbatim (invariant 2): the tool needs a query string.
            return ActionResult("error:memory_query",
                                "memory_query requiert un argument 'q' non vide")
        return ActionResult("memory_query", mnemos.query(q))

    if tool == "memory_write":
        content = args.get("content", "")
        if not isinstance(content, str) or not content:
            return ActionResult("error:memory_write",
                                "memory_write requiert un argument 'content' non vide")
        ok = mnemos.write(content)
        if ok:
            return ActionResult("memory_write", "écrit en mémoire")
        # Honest: the write did not reach Mnemos (queued for later flush, §8).
        return ActionResult("memory_write",
                            "ta mémoire est inaccessible en ce moment")

    # is_tool_allowed said yes but no handler exists — a Phase-2/3 tool reached
    # in a later phase before its handler is wired. Fail honestly, never fake.
    return ActionResult(f"error:{tool}",
                        f"cette capacité n'est pas encore branchée: {tool}")
