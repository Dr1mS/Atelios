"""smoke_phase0.py — the Phase 0 acceptance gate (§12).

Runs the five checks the build document requires, with REAL effects (no mocks,
no stub — addendum A2):

  1. jail refuses ../
  2. allowlist refuses an off-list domain AND fetches wttr.in
  3. runner executes a fake tool AND kills a `while True`
  4. 5 Mnemos writes (tenant atelios, role=user) + reread
  5. JSONL queue tested by really cutting Mnemos (client pointed at a dead port),
     then flushed against the live server

Exit code is non-zero if any check fails. Honest by construction: a failure is a
failure, printed verbatim (invariant 2). Run from the repo root with the main
venv python.

Requirements to pass live: Mnemos up on MNEMOS_URL, sandbox venv present, network
for the wttr.in fetch.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

# Windows consoles default to cp1252; force UTF-8 so honest verbatim output
# (which may contain non-ASCII, e.g. web content or French refusals) never
# crashes the reporter.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

# Make the package importable when run as a script.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from atelios import config, db, sandbox  # noqa: E402
from atelios.mnemos_client import MnemosClient  # noqa: E402
from atelios.webread import WebReader  # noqa: E402

PASS = "PASS"
FAIL = "FAIL"
_results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append((name, ok, detail))
    tag = PASS if ok else FAIL
    line = f"[{tag}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)


def check_1_jail() -> None:
    """jail refuses ../"""
    try:
        sandbox.resolve_in_jail("../escape.txt")
        check("1. jail refuses ../", False, "traversal was NOT refused")
    except sandbox.JailError as exc:
        check("1. jail refuses ../", True, f"refused: {exc}")


def check_2_allowlist(conn) -> None:
    """allowlist refuses an off-list domain AND fetches wttr.in"""
    reader = WebReader(conn)
    off = reader.read("https://example.com/")
    off_ok = off.refused and not off.ok

    hit = reader.read("https://wttr.in/Paris?format=3")
    hit_ok = hit.ok and len(hit.text) > 0

    check("2a. allowlist refuses off-list domain", off_ok,
          f"refused={off.refused}")
    check("2b. fetch wttr.in", hit_ok,
          f"status={hit.status}, chars={len(hit.text)}, sample={hit.text[:60]!r}")


def check_3_runner() -> None:
    """runner executes a fake tool AND kills a `while True`"""
    tools_dir = config.SANDBOX_TOOLS
    tools_dir.mkdir(parents=True, exist_ok=True)

    # Fake tool: echo argv[1] to stdout (the whole contract, §6).
    echo = tools_dir / "smoke_echo.py"
    echo.write_text(
        "import sys\n"
        "print('echo:' + (sys.argv[1] if len(sys.argv) > 1 else ''))\n",
        encoding="utf-8",
    )
    res = sandbox.run_tool_file(echo, "hello", timeout_s=10)
    echo_ok = (not res.killed) and res.returncode == 0 and "echo:hello" in res.stdout
    check("3a. runner executes fake tool", echo_ok,
          f"stdout={res.stdout.strip()!r}, rc={res.returncode}")

    # Infinite loop: must be killed by the timeout.
    spin = tools_dir / "smoke_spin.py"
    spin.write_text("while True:\n    pass\n", encoding="utf-8")
    t0 = time.monotonic()
    res2 = sandbox.run_tool_file(spin, "", timeout_s=3)
    elapsed = time.monotonic() - t0
    spin_ok = res2.killed and res2.kill_reason == "timeout" and elapsed < 10
    check("3b. runner kills while True", spin_ok,
          f"killed={res2.killed}, reason={res2.kill_reason}, {elapsed:.1f}s")

    for f in (echo, spin):
        try:
            f.unlink()
        except OSError:
            pass


def check_4_mnemos(conn) -> None:
    """5 Mnemos writes (tenant atelios, role=user) + reread"""
    # Isolated queue file: the smoke must never touch the real data/ queue, and
    # queue_depth must measure THIS run's failures, not leftovers from a prior
    # run (a residual could otherwise mask a real failure or fake one).
    tmp = Path(tempfile.mkdtemp()) / "mnemos_queue.jsonl"
    client = MnemosClient(conn, queue_path=tmp)
    if not client.health_ok():
        check("4. Mnemos 5 writes + reread", False,
              "Mnemos health not green (ok!=true) — start Mnemos and retry")
        return

    marker = f"atelios smoke phase0 marker {int(time.time())}"
    writes_ok = True
    for i in range(5):
        if not client.write(f"{marker} — entree {i}"):
            writes_ok = False
    # Honest gate: every write must have succeeded (nothing fell into the queue).
    writes_ok = writes_ok and client.queue_depth() == 0
    check("4a. 5 writes tenant atelios role=user", writes_ok,
          f"queue_depth after writes={client.queue_depth()} (0 = all wrote live)")

    # Reread: the write path embeds synchronously so a query should surface it.
    result = client.query(marker)
    reread_ok = marker.split(" — ")[0] in result or "smoke phase0" in result
    check("4b. reread finds written content", reread_ok,
          f"query returned {len(result)} chars")


def check_5_queue(conn) -> None:
    """JSONL queue tested by really cutting Mnemos, then flushed live."""
    # A queue file isolated from the real one, so the smoke never touches prod.
    tmp = Path(tempfile.mkdtemp()) / "mnemos_queue.jsonl"

    # Point the client at a dead port = a real connection failure (no stub).
    dead = MnemosClient(conn, base_url="http://127.0.0.1:59999",
                        queue_path=tmp)
    down = not dead.health_ok()
    check("5a. health fail-closed when Mnemos cut", down,
          f"health_ok={not down}")

    ok = dead.write("atelios smoke — write during outage")
    queued = (not ok) and dead.queue_depth() == 1
    check("5b. failed write lands in JSONL queue", queued,
          f"write_ok={ok}, queue_depth={dead.queue_depth()}")

    # Now flush against the live server (same queue file, real base URL).
    live = MnemosClient(conn, queue_path=tmp)
    if not live.health_ok():
        check("5c. queue flushes when Mnemos returns", False,
              "live Mnemos not green — cannot demonstrate flush")
        return
    flushed = live.flush_queue()
    flush_ok = flushed >= 1 and live.queue_depth() == 0
    check("5c. queue flushes when Mnemos returns", flush_ok,
          f"flushed={flushed}, remaining={live.queue_depth()}")


def main() -> int:
    print("=== Atelios Phase 0 smoke test ===")
    print(f"MNEMOS_URL={config.MNEMOS_URL}  SANDBOX_ROOT={config.SANDBOX_ROOT}")
    print(f"SANDBOX_PYTHON={config.SANDBOX_PYTHON}")
    print()

    # Use a throwaway DB so the smoke never pollutes a real experiment.db.
    smoke_db = Path(tempfile.mkdtemp()) / "smoke_experiment.db"
    conn = db.init_db(smoke_db)

    check_1_jail()
    check_2_allowlist(conn)
    check_3_runner()
    check_4_mnemos(conn)
    check_5_queue(conn)

    conn.close()

    print()
    passed = sum(1 for _, ok, _ in _results if ok)
    total = len(_results)
    failed = [name for name, ok, _ in _results if not ok]
    print(f"=== {passed}/{total} checks passed ===")
    if failed:
        print("FAILED:")
        for name in failed:
            print(f"  - {name}")
        return 1
    print("Phase 0 smoke GREEN.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
