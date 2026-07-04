"""actions — tool gating by phase (§6, §A4).

Phase 0 holds ONLY the pure gating rule: the minimal tool→phase table and the
predicate is_tool_allowed. No dispatch, no handlers — those arrive with their
capabilities in Phases 1-3. When a tool is called out of phase, the world
answers honestly (invariant 2) with REFUSAL_OUT_OF_PHASE.
"""

from __future__ import annotations

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
