#!/usr/bin/env python3
"""Behavior tests for exact chapter permission, acceptance receipts, and story doctor."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / "skills/story-long-write/scripts/chapter_candidate.py"
TRACKING = ROOT / "skills/story-long-write/scripts/tracking_commit.py"
DOCTOR = ROOT / "skills/story-long-write/scripts/story_doctor.py"


def write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(tool: Path, *args: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run([sys.executable, str(tool), *args], text=True, capture_output=True, check=False)
    if completed.returncode != expected:
        raise AssertionError(
            f"expected {expected}, got {completed.returncode}: {tool.name} {' '.join(args)}\n"
            f"stdout={completed.stdout}\nstderr={completed.stderr}"
        )
    return completed


def initial_document() -> dict[str, object]:
    return {
        "schema_version": 1,
        "book_title": "候选门禁测试",
        "last_chapter": 0,
        "context": {
            "position": {"volume": "第一卷", "volume_start_chapter": 1, "story_time": "春日清晨", "scene": "林家旧院"},
            "long_term_constraints": [],
            "active_character_names": [],
            "continuity_risks": [],
            "recent_chapters": [],
            "next_chapter_commitments": [],
        },
        "character_snapshots": {},
        "foreshadow": [],
        "timeline_events": [],
        "facts": [],
    }


def chapter_transaction() -> dict[str, object]:
    return {
        "schema_version": 1,
        "mode": "append",
        "chapter": 1,
        "chapter_title": "春雨入院",
        "expected_state_revision": 0,
        "delta": {
            "result": "林川送回药包，确认母亲的旧疾暂时稳定。",
            "character_changes": [],
            "foreshadow_changes": [],
            "timeline_events": [],
            "fact_changes": [],
            "constraints": [],
            "next_chapter_commitments": ["查清药包里缺失的一味药。"],
            "retired_context_items": [],
            "retired_characters": [],
            "continuity_changes": [],
        },
        "context": {
            "position": {"volume": "第一卷", "volume_start_chapter": 1, "story_time": "春日清晨", "scene": "林家旧院"},
            "long_term_constraints": [],
            "active_character_names": [],
            "continuity_risks": [],
        },
        "character_snapshots": {},
    }


with tempfile.TemporaryDirectory(prefix="chapter-candidate-") as temporary:
    project = Path(temporary) / "book"
    (project / "大纲").mkdir(parents=True)
    (project / "正文").mkdir()
    outline = project / "大纲" / "细纲_第001章.md"
    outline.write_text("# 第一章细纲\n\n林川冒雨回家，把药包交给母亲。\n", encoding="utf-8")
    init_input = Path(temporary) / "init.json"
    write_json(init_input, initial_document())
    run(TRACKING, "init", "--project", str(project), "--input", str(init_input))

    created = run(
        CANDIDATE,
        "init",
        "--project",
        str(project),
        "--chapter",
        "1",
        "--outline",
        "大纲/细纲_第001章.md",
        "--target",
        "正文/第001章_春雨入院.md",
        "--id",
        "C001",
    )
    workspace = Path(created.stdout.strip())
    candidate = workspace / "candidate.md"
    prose = "# 第一章 春雨入院\n\n林川推开木门，雨水顺着青石缝流进院里。他把药包放到桌边，问母亲今天是否好些。\n"
    candidate.write_text(prose, encoding="utf-8")
    checked = json.loads(run(CANDIDATE, "check", "--run", str(workspace)).stdout)
    gate_names = {item["name"] for item in checked["gates"]}
    assert {"writing_method", "prose_metrics", "outline_copy", "accepted_voice_profile", "cross_chapter_shape"} <= gate_names, checked
    shape_gate = next(item for item in checked["gates"] if item["name"] == "cross_chapter_shape")
    assert shape_gate["payload"]["status"] == "insufficient_history", shape_gate

    blocked_parallel = run(
        CANDIDATE,
        "init",
        "--project",
        str(project),
        "--chapter",
        "1",
        "--outline",
        "大纲/细纲_第001章.md",
        "--target",
        "正文/第001章_另一版.md",
        expected=2,
    )
    assert "未闭环候选章" in blocked_parallel.stderr

    original_outline = outline.read_text(encoding="utf-8")
    outline.write_text(original_outline + "\n临时改动。\n", encoding="utf-8")
    stale = run(CANDIDATE, "check", "--run", str(workspace), "--freshness-only", expected=2)
    assert "已过期" in stale.stderr
    outline.write_text(original_outline, encoding="utf-8")

    run(CANDIDATE, "approve", "--run", str(workspace), "--confirm", "ACCEPT", expected=2)
    run(
        CANDIDATE,
        "approve",
        "--run",
        str(workspace),
        "--confirm",
        "ACCEPT",
        "--approval-note",
        "用户明确采用这一版",
    )
    candidate.write_text(prose + "又添一句。\n", encoding="utf-8")
    changed_after_approval = run(CANDIDATE, "promote", "--run", str(workspace), "--confirm", "PROMOTE", expected=2)
    assert "接纳后被修改" in changed_after_approval.stderr
    candidate.write_text(prose, encoding="utf-8")
    run(CANDIDATE, "promote", "--run", str(workspace), "--confirm", "PROMOTE")
    run(CANDIDATE, "close", "--run", str(workspace), expected=2)

    transaction = Path(temporary) / "chapter.json"
    write_json(transaction, chapter_transaction())
    run(TRACKING, "commit", "--project", str(project), "--input", str(transaction))
    run(CANDIDATE, "close", "--run", str(workspace))
    doctor = run(DOCTOR, "--project", str(project))
    report = json.loads(doctor.stdout)
    assert report["status"] == "pass", report
    assert any(item["name"] == "writing_method" and item["status"] == "pass" for item in report["checks"]), report
    assert any(item["code"] == "voice-profile-not-configured" for item in report["warnings"]), report

    final_prose = project / "正文" / "第001章_春雨入院.md"
    final_prose.write_text(final_prose.read_text(encoding="utf-8") + "静默手改。\n", encoding="utf-8")
    broken = run(DOCTOR, "--project", str(project), expected=2)
    broken_report = json.loads(broken.stdout)
    assert any(item["code"] == "accepted-prose-digest-mismatch" for item in broken_report["errors"]), broken_report

print("OK: chapter candidates require exact permission, explicit acceptance, digest binding, and doctor closure")
