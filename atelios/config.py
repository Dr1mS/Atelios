"""Configuration: read .env, expose constants. Stdlib only (no dotenv dep).

The .env keys are those of ATELIOS_BUILD.md §15 (verbatim). One value the runner
needs but which §15 does not list is the path to the sandbox venv's python.exe
(§7: separate stdlib-only venv). It is exposed here with a conventional default
(sandbox_venv/Scripts/python.exe at repo root) and can be overridden via the
optional SANDBOX_PYTHON env key; documented in the README.
"""

from __future__ import annotations

import os
from pathlib import Path

# Repo root = parent of the atelios/ package directory.
REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> dict[str, str]:
    """Minimal .env parser: KEY=VALUE per line, # comments, no interpolation."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip()
    return values


_DOTENV = _load_dotenv(REPO_ROOT / ".env")


def _get(key: str, default: str | None = None) -> str:
    """Precedence: process environment > .env file > default."""
    if key in os.environ:
        return os.environ[key]
    if key in _DOTENV:
        return _DOTENV[key]
    if default is None:
        raise KeyError(f"missing config key: {key}")
    return default


def _get_int(key: str, default: str) -> int:
    return int(_get(key, default))


def _resolve_root(value: str) -> Path:
    """Resolve a possibly-relative path against the repo root."""
    p = Path(value)
    return p if p.is_absolute() else (REPO_ROOT / p).resolve()


# --- §15 keys ---------------------------------------------------------------
PHASE = _get_int("PHASE", "1")
TICK_INTERVAL_SECONDS = _get_int("TICK_INTERVAL_SECONDS", "300")
DREAM_EVERY_N_TICKS = _get_int("DREAM_EVERY_N_TICKS", "36")

MODEL_MIND = _get("MODEL_MIND", "qwen3.5:9b")
OLLAMA_MIND_URL = _get("OLLAMA_MIND_URL", "http://localhost:11434")
OLLAMA_AUX_URL = _get("OLLAMA_AUX_URL", "http://localhost:11435")
EMBED_MODEL = _get("EMBED_MODEL", "nomic-embed-text")

MNEMOS_URL = _get("MNEMOS_URL", "http://127.0.0.1:8765")
MNEMOS_TENANT = _get("MNEMOS_TENANT", "atelios")

SANDBOX_ROOT = _resolve_root(_get("SANDBOX_ROOT", "./sandbox"))
TOOL_TIMEOUT_S = _get_int("TOOL_TIMEOUT_S", "30")
TOOL_MAX_RAM_MB = _get_int("TOOL_MAX_RAM_MB", "512")

WEB_MAX_CHARS = _get_int("WEB_MAX_CHARS", "20000")
WEB_RATE_PER_HOUR = _get_int("WEB_RATE_PER_HOUR", "10")

ATELIOS_BACKUP_DIR = _get("ATELIOS_BACKUP_DIR", "D:/backups/atelios")
DB_PATH = _resolve_root(_get("DB_PATH", "./experiment.db"))

# --- derived / runner-only (not in §15; see module docstring) ---------------
# Default sandbox venv python.exe on Windows; overridable via SANDBOX_PYTHON.
_DEFAULT_SANDBOX_PYTHON = REPO_ROOT / "sandbox_venv" / "Scripts" / "python.exe"
SANDBOX_PYTHON = Path(_get("SANDBOX_PYTHON", str(_DEFAULT_SANDBOX_PYTHON)))

# Convenience derived paths (jail subdirs, mnemos queue).
SANDBOX_TOOLS = SANDBOX_ROOT / "tools"
SANDBOX_WORKSPACE = SANDBOX_ROOT / "workspace"
MNEMOS_QUEUE_PATH = REPO_ROOT / "data" / "mnemos_queue.jsonl"
ALLOWLIST_PATH = REPO_ROOT / "allowlist.txt"
