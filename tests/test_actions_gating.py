"""is_tool_allowed tests (§6, §A4): tool→phase gating."""

from __future__ import annotations

from atelios.actions import TOOL_MIN_PHASE, is_tool_allowed


def test_phase1_tools():
    for tool in ("memory_query", "memory_write", "idle"):
        assert is_tool_allowed(tool, 1)
        assert is_tool_allowed(tool, 2)
        assert is_tool_allowed(tool, 3)


def test_phase2_tools_blocked_in_phase1():
    for tool in ("fs_list", "fs_read", "fs_write", "create_tool", "run_tool"):
        assert not is_tool_allowed(tool, 1)
        assert is_tool_allowed(tool, 2)


def test_read_web_only_phase3():
    assert not is_tool_allowed("read_web", 1)
    assert not is_tool_allowed("read_web", 2)
    assert is_tool_allowed("read_web", 3)


def test_unknown_tool_never_allowed():
    assert not is_tool_allowed("format_disk", 1)
    assert not is_tool_allowed("format_disk", 99)
    assert not is_tool_allowed("", 3)


def test_table_covers_exactly_the_documented_tools():
    # §6 exposes exactly these nine tools — guard against silent additions.
    assert set(TOOL_MIN_PHASE) == {
        "memory_query", "memory_write", "idle",
        "fs_list", "fs_read", "fs_write",
        "create_tool", "run_tool", "read_web",
    }
