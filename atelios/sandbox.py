"""Sandbox — FS jail and subprocess tool runner (§7).

The jail is the trust boundary, not the model (invariant 9). Nothing here leaks
host paths, secrets, or the parent environment into the subprocess. Errors are
real and returned verbatim to the caller (invariant 2) — this module never
fabricates success.

Phase 0 builds the primitives (path resolution, file read/write under the jail,
the subprocess runner with timeout + RAM watchdog). It does NOT wire create_tool
or run_tool into the mind — that is Phase 2.
"""

from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import psutil

from . import config

MAX_FILE_BYTES = 1_000_000        # 1 MB per file (§7)
OUTPUT_TRUNCATE_BYTES = 4096      # stdout+stderr truncated to 4 KB (§7)


class JailError(Exception):
    """Raised when a path escapes the jail or violates its rules."""


def _jail_root() -> Path:
    return config.SANDBOX_ROOT.resolve()


def resolve_in_jail(path: str | Path) -> Path:
    """Resolve `path` and prove it stays within SANDBOX_ROOT.

    §7: resolve() + is_relative_to(root). Symlinks are refused: we reject if any
    existing component along the path is a symlink, and if the resolved target
    is not relative to the (real) root.
    """
    root = _jail_root()
    raw = Path(path)

    # Interpret relative paths against the jail root, absolute paths as-is
    # (they will be checked against the root and rejected if outside).
    candidate = raw if raw.is_absolute() else (root / raw)

    # Refuse symlinks on any existing component (defence against symlink escape).
    probe = candidate
    seen: set[Path] = set()
    while True:
        if probe in seen:
            break
        seen.add(probe)
        if probe.exists() and probe.is_symlink():
            raise JailError(f"symlink refused: {probe}")
        if probe.parent == probe:
            break
        probe = probe.parent

    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise JailError(f"path escapes jail: {resolved}")
    return resolved


def jail_read(path: str | Path) -> str:
    p = resolve_in_jail(path)
    if not p.exists() or not p.is_file():
        raise JailError(f"no such file in jail: {path}")
    data = p.read_bytes()
    if len(data) > MAX_FILE_BYTES:
        raise JailError(f"file exceeds {MAX_FILE_BYTES} bytes: {path}")
    return data.decode("utf-8", errors="replace")


def jail_write(path: str | Path, content: str) -> Path:
    p = resolve_in_jail(path)
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_FILE_BYTES:
        raise JailError(f"content exceeds {MAX_FILE_BYTES} bytes")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(encoded)
    return p


def jail_list(path: str | Path = ".") -> list[str]:
    p = resolve_in_jail(path)
    if not p.exists() or not p.is_dir():
        raise JailError(f"no such directory in jail: {path}")
    root = _jail_root()
    return sorted(str(child.relative_to(root)) for child in p.iterdir())


@dataclass
class RunResult:
    stdout: str
    stderr: str
    returncode: int | None
    killed: bool
    kill_reason: str | None   # "timeout" | "ram" | None
    duration_s: float


def _truncate(data: bytes) -> str:
    text = data.decode("utf-8", errors="replace")
    if len(text) > OUTPUT_TRUNCATE_BYTES:
        return text[:OUTPUT_TRUNCATE_BYTES]
    return text


def run_tool_file(
    tool_path: str | Path,
    args: str,
    *,
    timeout_s: int | None = None,
    max_ram_mb: int | None = None,
) -> RunResult:
    """Run a tool file in the sandbox venv subprocess (§7).

    Contract: the tool receives `args` as sys.argv[1] and writes to stdout.
    Enforced: sandbox venv python.exe, cwd=workspace, minimal env (no parent
    PATH / host vars), timeout kill, RAM watchdog (psutil poll 500 ms, kill
    above the limit). stdout+stderr captured and truncated to 4 KB.
    """
    timeout_s = timeout_s if timeout_s is not None else config.TOOL_TIMEOUT_S
    max_ram_mb = max_ram_mb if max_ram_mb is not None else config.TOOL_MAX_RAM_MB
    tool_path = Path(tool_path)

    # Minimal environment: nothing inherited from the host (§7).
    env = {"SYSTEMROOT": _system_root()} if _system_root() else {}

    proc = subprocess.Popen(
        [str(config.SANDBOX_PYTHON), str(tool_path), args],
        cwd=str(config.SANDBOX_WORKSPACE),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    kill_reason: dict[str, str | None] = {"reason": None}
    stop = threading.Event()

    def watchdog() -> None:
        """Poll RSS every 500 ms; kill if over the RAM limit."""
        limit_bytes = max_ram_mb * 1024 * 1024
        try:
            ps = psutil.Process(proc.pid)
        except psutil.NoSuchProcess:
            return
        while not stop.is_set():
            try:
                rss = ps.memory_info().rss
                for child in ps.children(recursive=True):
                    try:
                        rss += child.memory_info().rss
                    except psutil.Error:
                        pass
            except psutil.NoSuchProcess:
                return
            if rss > limit_bytes:
                kill_reason["reason"] = "ram"
                _kill_tree(proc.pid)
                return
            if stop.wait(0.5):
                return

    wd = threading.Thread(target=watchdog, daemon=True)
    start = time.monotonic()
    wd.start()

    killed = False
    try:
        out, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        kill_reason["reason"] = "timeout"
        _kill_tree(proc.pid)
        killed = True
        out, err = proc.communicate()
    finally:
        stop.set()
        wd.join(timeout=1.0)

    duration = time.monotonic() - start
    if kill_reason["reason"] == "ram":
        killed = True

    return RunResult(
        stdout=_truncate(out or b""),
        stderr=_truncate(err or b""),
        returncode=proc.returncode,
        killed=killed,
        kill_reason=kill_reason["reason"],
        duration_s=duration,
    )


def _kill_tree(pid: int) -> None:
    """Kill a process and its children (tool may spawn subprocesses)."""
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    procs = parent.children(recursive=True) + [parent]
    for p in procs:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(procs, timeout=3)


def _system_root() -> str | None:
    """Windows needs SYSTEMROOT for the interpreter to start; nothing else."""
    import os

    return os.environ.get("SYSTEMROOT")
