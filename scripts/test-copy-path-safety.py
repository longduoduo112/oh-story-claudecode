#!/usr/bin/env python3
"""Behavior tests for story-setup recursive-copy path safety."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "skills/story-setup/scripts/copy-path-safety.py"


def run(source: Path, target: Path, expected: int) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, str(TOOL), str(source), str(target)],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != expected:
        raise AssertionError(
            f"expected {expected}, got {completed.returncode}\n"
            f"stdout={completed.stdout}\nstderr={completed.stderr}"
        )
    return json.loads(completed.stdout)


with tempfile.TemporaryDirectory(prefix="copy-path-safety-") as temporary:
    root = Path(temporary)
    source = root / "source"
    target = root / "target"
    source.mkdir()
    target.mkdir()

    safe = run(source, target, 0)
    assert safe["status"] == "safe" and safe["copy_allowed"] is True, safe

    same = run(source, source, 0)
    assert same["status"] == "same" and same["copy_allowed"] is False, same

    child = source / "nested-target"
    child.mkdir()
    nested = run(source, child, 1)
    assert nested["status"] == "unsafe" and nested["reason"] == "target_inside_source", nested

    parent = root / "parent"
    parent.mkdir()
    inner_source = parent / "inner-source"
    inner_source.mkdir()
    ancestor = run(inner_source, parent, 1)
    assert ancestor["status"] == "unsafe" and ancestor["reason"] == "source_inside_target", ancestor

    missing = run(root / "missing", target, 2)
    assert missing["status"] == "error" and missing["reason"] == "source_missing", missing

    alias = root / "source-alias"
    try:
        os.symlink(source, alias, target_is_directory=True)
    except (OSError, NotImplementedError):
        pass
    else:
        same_alias = run(source, alias, 0)
        assert same_alias["status"] == "same", same_alias

print("OK: copy path safety rejects recursive containment and treats aliases as no-op")
