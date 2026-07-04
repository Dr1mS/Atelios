"""Jail tests (§7): ../ traversal, absolute paths, symlinks, size limit.

Pure-logic tests only (§13). Uses a tmp jail root via monkeypatching config.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


@pytest.fixture
def jail(tmp_path, monkeypatch):
    """A fresh jail rooted at tmp_path/sandbox, with sandbox reimported."""
    from atelios import config

    root = tmp_path / "sandbox"
    (root / "workspace").mkdir(parents=True)
    monkeypatch.setattr(config, "SANDBOX_ROOT", root)
    monkeypatch.setattr(config, "SANDBOX_WORKSPACE", root / "workspace")
    from atelios import sandbox

    return sandbox, root


def test_rejects_parent_traversal(jail):
    sandbox, _ = jail
    with pytest.raises(sandbox.JailError):
        sandbox.resolve_in_jail("../escape.txt")
    with pytest.raises(sandbox.JailError):
        sandbox.resolve_in_jail("workspace/../../escape.txt")


def test_rejects_absolute_path_outside(jail):
    sandbox, _ = jail
    outside = Path.home() / "outside.txt"
    with pytest.raises(sandbox.JailError):
        sandbox.resolve_in_jail(str(outside))


def test_accepts_path_inside(jail):
    sandbox, root = jail
    resolved = sandbox.resolve_in_jail("workspace/note.txt")
    assert resolved.is_relative_to(root.resolve())


def test_rejects_symlink_escape(jail):
    sandbox, root = jail
    # Create a symlink inside the jail pointing outside it.
    target = root.parent / "secret"
    target.mkdir()
    link = root / "workspace" / "leak"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this host")
    with pytest.raises(sandbox.JailError):
        sandbox.resolve_in_jail("workspace/leak/x.txt")


def test_write_read_roundtrip_inside(jail):
    sandbox, _ = jail
    sandbox.jail_write("workspace/hello.txt", "bonjour")
    assert sandbox.jail_read("workspace/hello.txt") == "bonjour"


def test_write_rejects_oversize(jail):
    sandbox, _ = jail
    big = "x" * (sandbox.MAX_FILE_BYTES + 1)
    with pytest.raises(sandbox.JailError):
        sandbox.jail_write("workspace/big.txt", big)


def test_read_rejects_oversize(jail):
    sandbox, root = jail
    # Write an oversize file directly (bypassing jail_write) then read via jail.
    p = root / "workspace" / "big.bin"
    p.write_bytes(b"x" * (sandbox.MAX_FILE_BYTES + 1))
    with pytest.raises(sandbox.JailError):
        sandbox.jail_read("workspace/big.bin")
