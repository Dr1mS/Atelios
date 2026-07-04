"""Scheduling — the non-overlapping tick rule as a pure function (§4, §A4).

This is executable spec, not run behaviour: next_start encodes the rule
`next_start = max(now, tick_start + interval)` and reports whether the tick
overran its interval (which the loop turns into a `tick_overrun` event). The
loop itself (loop.py) is Phase 1+; nothing here schedules or sleeps.
"""

from __future__ import annotations


def next_start(now: float, tick_start: float, interval: float) -> tuple[float, bool]:
    """Return (start_of_next_tick, overrun).

    §4: the scheduler never cancels a generation (invariant 8). If a tick
    (generation + tool execution) overran the interval — i.e. now is already at
    or past tick_start + interval — the next tick starts immediately (at now)
    and overrun is True. Otherwise it starts at tick_start + interval.
    """
    scheduled = tick_start + interval
    if now >= scheduled:
        return now, True
    return scheduled, False
