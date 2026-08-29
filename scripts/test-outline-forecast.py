#!/usr/bin/env python3
"""Behavior tests for isolated branch forecasts, staleness, and selection receipts."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "skills/story-long-write/scripts/outline_forecast.py"


def run(*args: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run([sys.executable, str(TOOL), *args], text=True, capture_output=True, check=False)
    if completed.returncode != expected:
        raise AssertionError(f"expected {expected}, got {completed.returncode}\n{completed.stdout}\n{completed.stderr}")
    return completed


with tempfile.TemporaryDirectory(prefix="outline-forecast-") as temporary:
    project = Path(temporary) / "book"
    (project / "大纲").mkdir(parents=True)
    (project / "追踪").mkdir()
    base = project / "大纲" / "大纲.md"
    base.write_text("# 总纲\n\n主角暂不公开能力。\n", encoding="utf-8")
    state_path = project / "追踪" / "_tracking-state.json"
    state_path.write_text(json.dumps({"state_revision": 7}), encoding="utf-8")
    created = run(
        "init",
        str(project),
        "--level",
        "unit",
        "--divergence",
        "主角是否公开能力",
        "--base",
        "大纲/大纲.md",
        "--id",
        "F001",
    )
    workspace = Path(created.stdout.strip())
    forecast_path = workspace / "forecast.json"
    forecast = json.loads(forecast_path.read_text(encoding="utf-8"))
    assert forecast["base_state_revision"] == 7
    for index, branch in enumerate(forecast["branches"], start=1):
        branch.update(
            {
                "title": f"路线{index}",
                "premise": "主角作出可见选择。",
                "beats": ["选择发生", "后果落地"],
                "reader_contract_effect": "兑现当前期待，同时保留后续压力。",
                "protagonist_agency": "关键决定由主角作出。",
                "end_state": "形成互斥的单元终态。",
            }
        )
    forecast_path.write_text(json.dumps(forecast, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    run("check", str(workspace))

    state_path.write_text(json.dumps({"state_revision": 8}), encoding="utf-8")
    stale = run("check", str(workspace), expected=2)
    assert "state_revision_changed" in stale.stdout
    state_path.write_text(json.dumps({"state_revision": 7}), encoding="utf-8")

    missing_confirmation = run("select", str(workspace), "--branch", "B1", "--approval-note", "用户选择 B1", expected=2)
    assert "--confirm" in missing_confirmation.stderr
    run(
        "select",
        str(workspace),
        "--branch",
        "B1",
        "--confirm",
        "SELECT",
        "--approval-note",
        "用户明确选择 B1",
    )
    receipt = json.loads((workspace / "selection-receipt.json").read_text(encoding="utf-8"))
    assert receipt["branch_id"] == "B1"
    assert receipt["canonical_writeback_allowed"] is False
    assert (workspace / "selected-plan.md").is_file()

print("OK: forecasts bind canon revision, stale safely, and require a selection receipt")
