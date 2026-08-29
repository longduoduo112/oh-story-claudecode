#!/usr/bin/env python3
"""Behavior tests for deterministic cross-chapter shape evidence."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "skills/story-deslop/scripts/chapter_shape_gate.py"
MIRROR = ROOT / "skills/story-long-write/scripts/chapter_shape_gate.py"


def run(*args: str) -> dict[str, object]:
    completed = subprocess.run([sys.executable, str(TOOL), *args], text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        raise AssertionError(f"shape tool failed\nstdout={completed.stdout}\nstderr={completed.stderr}")
    return json.loads(completed.stdout)


def chapter_text(number: int) -> str:
    return (
        f"# 第{number}章 柜前问账\n\n"
        "掌柜把账簿推到柜前，让围着的人先看最后一栏。\n\n"
        "“这笔钱由谁先退？”伙计问。\n\n"
        "“谁收的钱，谁先退。查到上一手，再拿凭据往回算。”沈掌柜说。\n\n"
        "众人逐项核过签名，又把空着的地方圈出来。有人想拿铺印代替名字，她没有答应。\n\n"
        "“若最后查明没有错呢？”\n\n"
        "“先把眼前这一包记清，不能拿还没查完当借口。”\n\n"
        "她合上簿册，把笔放到下一家面前。\n"
    )


assert TOOL.read_bytes() == MIRROR.read_bytes(), "chapter shape tools must stay identical"

with tempfile.TemporaryDirectory(prefix="chapter-shape-") as temporary:
    project = Path(temporary) / "book"
    prose = project / "正文"
    prose.mkdir(parents=True)
    for chapter in range(1, 7):
        (prose / f"第{chapter:03d}章_柜前问账.md").write_text(chapter_text(chapter), encoding="utf-8")
    candidate = project / "候选.md"
    candidate.write_text(chapter_text(7), encoding="utf-8")
    payload = run(
        "--project",
        str(project),
        "--candidate",
        str(candidate),
        "--chapter",
        "7",
        "--window",
        "6",
    )
    assert payload["status"] == "semantic_review_required", payload
    assert payload["severity"] == "advisory" and payload["history"], payload
    assert any(item["code"] == "surface-shape-cluster" for item in payload["signals"]), payload
    assert len(payload["review_questions"]) == 5, payload

with tempfile.TemporaryDirectory(prefix="chapter-shape-short-") as temporary:
    project = Path(temporary) / "book"
    (project / "正文").mkdir(parents=True)
    candidate = project / "候选.md"
    candidate.write_text(chapter_text(1), encoding="utf-8")
    payload = run("--project", str(project), "--candidate", str(candidate), "--chapter", "1")
    assert payload["status"] == "insufficient_history", payload

print("OK: cross-chapter shape gate prepares advisory evidence without auto-rewriting prose")
