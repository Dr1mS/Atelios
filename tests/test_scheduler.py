"""next_start tests (§4, §A4): non-overlapping rule + overrun flag."""

from __future__ import annotations

from atelios.scheduling import next_start


def test_normal_tick_schedules_at_interval():
    # Tick started at t=0, took less than the interval; now well within it.
    start, overrun = next_start(now=10.0, tick_start=0.0, interval=300.0)
    assert start == 300.0
    assert overrun is False


def test_overrun_starts_immediately():
    # Now is past tick_start + interval → next tick starts now, overrun True.
    start, overrun = next_start(now=350.0, tick_start=0.0, interval=300.0)
    assert start == 350.0
    assert overrun is True


def test_boundary_exactly_at_interval_is_overrun():
    # now == scheduled: not late, but not early either. Rule: start now, overrun.
    start, overrun = next_start(now=300.0, tick_start=0.0, interval=300.0)
    assert start == 300.0
    assert overrun is True


def test_never_returns_before_now():
    # Whatever happens, the next start is never in the past relative to now.
    start, _ = next_start(now=500.0, tick_start=100.0, interval=300.0)
    assert start >= 500.0
