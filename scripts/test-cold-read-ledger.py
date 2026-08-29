#!/usr/bin/env python3
"""Behavior tests for sequential cold-read records and blocking issue closure."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "skills/story-long-write/scripts/cold_read_ledger.py"


def write_json(path: Path, value: dict[str, object]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(*args: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run([sys.executable, str(TOOL), *args], text=True, capture_output=True, check=False)
    if completed.returncode != expected:
        raise AssertionError(f"expected {expected}, got {completed.returncode}\n{completed.stdout}\n{completed.stderr}")
    return completed


def record(chapter: int, issues: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "chapter": chapter,
        "reader_note": f"首次顺读第{chapter}章。",
        "clock": [],
        "promises": [],
        "knowledge": [],
        "props": [],
        "issues": issues or [],
    }


with tempfile.TemporaryDirectory(prefix="cold-read-") as temporary:
    project = Path(temporary) / "book"
    project.mkdir()
    created = run("init", "--project", str(project), "--from-chapter", "1", "--to-chapter", "2", "--id", "R001")
    workspace = Path(created.stdout.strip())

    wrong = Path(temporary) / "wrong.json"
    write_json(wrong, record(2))
    blocked = run("record", "--run", str(workspace), "--input", str(wrong), expected=2)
    assert "只允许第 1 章" in blocked.stderr

    first = Path(temporary) / "first.json"
    write_json(
        first,
        record(
            1,
            [
                {
                    "id": "CR001",
                    "severity": "S2",
                    "type": "knowledge-leak",
                    "location": "第1章末段",
                    "description": "角色使用了尚未获得的信息。",
                }
            ],
        ),
    )
    run("record", "--run", str(workspace), "--input", str(first))
    second = Path(temporary) / "second.json"
    write_json(second, record(2))
    run("record", "--run", str(workspace), "--input", str(second))

    checked = run("check", "--run", str(workspace), expected=2)
    assert "CR001" in checked.stdout
    run("close", "--run", str(workspace), "--confirm", "CLOSE", expected=2)
    run(
        "resolve",
        "--run",
        str(workspace),
        "--issue-id",
        "CR001",
        "--resolution",
        "经修订事务删除越界台词并复核相邻章。",
    )
    run("check", "--run", str(workspace))
    run("close", "--run", str(workspace), "--confirm", "CLOSE")
    ledger = json.loads((workspace / "ledger.json").read_text(encoding="utf-8"))
    assert ledger["status"] == "closed"
    assert (workspace / "report.md").is_file()
    events = (workspace / "issues.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(events) == 2 and '"opened"' in events[0] and '"resolved"' in events[1]

print("OK: cold read is sequential, append-only, and blocks closure on unresolved S1/S2")
