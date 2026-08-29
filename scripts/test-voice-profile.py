#!/usr/bin/env python3
"""Behavior tests for accepted-prose voice profiles and blind review packs."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "skills/story-deslop/scripts/voice_profile.py"
MIRROR = ROOT / "skills/story-long-write/scripts/voice_profile.py"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(*args: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run([sys.executable, str(TOOL), *args], text=True, capture_output=True, check=False)
    if completed.returncode != expected:
        raise AssertionError(
            f"expected {expected}, got {completed.returncode}: {' '.join(args)}\n"
            f"stdout={completed.stdout}\nstderr={completed.stderr}"
        )
    return completed


def chapter_text(number: int) -> str:
    return (
        f"# 第{number}章 雨夜旧院\n\n"
        f"雨从瓦缝落下来，林川把第{number}只药包压在掌心，沿着青石路走到廊下。"
        "屋里的灯没有熄，母亲正靠着窗边缝那件旧衣。\n\n"
        "“今天还疼吗？”他问。\n\n"
        "母亲摇头，把针收进木盒。院门外有人踩过积水，却没有敲门。"
        "林川等了片刻，才将药包推到桌角。他没有解释来处，只说天亮前别开门。\n"
    )


def add_receipt(project: Path, chapter: int, prose: Path) -> None:
    write_json(
        project / "追踪" / "章节提交" / f"第{chapter:03d}章.json",
        {
            "schema_version": 1,
            "status": "committed",
            "chapter": chapter,
            "target": prose.relative_to(project).as_posix(),
            "accepted_prose_sha256": sha256_file(prose),
        },
    )


assert TOOL.read_bytes() == MIRROR.read_bytes(), "story-deslop/story-long-write voice tools must stay identical"

with tempfile.TemporaryDirectory(prefix="voice-profile-") as temporary:
    project = Path(temporary) / "book"
    (project / "正文").mkdir(parents=True)
    for chapter in range(1, 6):
        prose = project / "正文" / f"第{chapter:03d}章_雨夜旧院.md"
        prose.write_text(chapter_text(chapter), encoding="utf-8")
        add_receipt(project, chapter, prose)

    unsafe = run("build", "--project", str(project), "--min-chapters", "1", expected=2)
    assert "伪精确画像" in unsafe.stderr, unsafe.stderr
    built = json.loads(run("build", "--project", str(project)).stdout)
    assert built["status"] == "built" and built["source_count"] == 5, built
    profile_path = project / "追踪" / "文风" / "accepted-voice-profile.json"
    summary_path = project / "追踪" / "文风" / "accepted-voice-profile.md"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    assert profile["policy"]["severity"] == "advisory", profile
    assert profile["policy"]["not_a_quality_score"] is True, profile
    assert summary_path.is_file() and "不得为了回到均值" in summary_path.read_text(encoding="utf-8")

    verified = json.loads(run("verify", "--project", str(project)).stdout)
    assert verified["status"] == "fresh", verified

    denied_golden = run(
        "golden-build",
        "--project",
        str(project),
        "--chapters",
        "1,2,3,4,5",
        "--confirm",
        "NO",
        expected=2,
    )
    assert "GOLDEN_APPROVED" in denied_golden.stderr, denied_golden.stderr
    golden = json.loads(
        run(
            "golden-build",
            "--project",
            str(project),
            "--chapters",
            "1,2,3,4,5",
            "--confirm",
            "GOLDEN_APPROVED",
        ).stdout
    )
    assert golden["status"] == "built" and golden["chapters"] == [1, 2, 3, 4, 5], golden
    assert json.loads(run("golden-verify", "--project", str(project)).stdout)["status"] == "fresh"

    candidate = project / "候选.md"
    candidate.write_text(
        "# 第六章 门后\n\n"
        "“谁？”\n\n“我。”\n\n“药呢？”\n\n“没了！”\n\n"
        "林川没有动。林川没有动。林川没有动。随后，他推开门。\n\n"
        + "“别进来！”院里的人喊。林川跨过门槛，又停住脚。他抬头看了一眼檐下的灯，随后把手里的空药袋摊开。\n\n" * 8,
        encoding="utf-8",
    )
    checked = json.loads(run("check", "--project", str(project), "--candidate", str(candidate)).stdout)
    assert checked["status"] == "advisory", checked
    assert checked["severity"] == "advisory" and checked["drift_advisories"], checked
    assert {item["direction"] for item in checked["drift_advisories"]} <= {"above", "below"}
    assert checked["golden_voice"]["status"] == "advisory", checked
    assert checked["golden_voice"]["drift_advisories"], checked

    blind = json.loads(
        run(
            "blind",
            "--project",
            str(project),
            "--candidate",
            str(candidate),
            "--id",
            "V001",
            "--confirm",
            "PREPARE",
        ).stdout
    )
    assert Path(blind["pack"]).is_file() and Path(blind["answer_key"]).is_file(), blind
    pack = Path(blind["pack"]).read_text(encoding="utf-8")
    assert "kind" not in pack and "candidate" not in pack.lower(), pack
    answer_key = json.loads(Path(blind["answer_key"]).read_text(encoding="utf-8"))
    assert answer_key["baseline_kind"] == "author_curated_golden", answer_key

    prose6 = project / "正文" / "第006章_门后.md"
    prose6.write_text(chapter_text(6), encoding="utf-8")
    add_receipt(project, 6, prose6)
    stale = json.loads(run("verify", "--project", str(project), expected=2).stdout)
    assert stale["status"] == "stale", stale
    assert any(item["reason"] == "newly_accepted" for item in stale["changes"]), stale
    updated = json.loads(run("update", "--project", str(project)).stdout)
    assert updated["source_count"] == 6, updated
    assert json.loads(run("verify", "--project", str(project)).stdout)["status"] == "fresh"
    assert json.loads(run("golden-verify", "--project", str(project)).stdout)["status"] == "fresh"

    original = prose6.read_text(encoding="utf-8")
    prose6.write_text(original + "\n静默改动。\n", encoding="utf-8")
    mismatch = run("verify", "--project", str(project), expected=2)
    assert "接纳回执摘要不一致" in mismatch.stderr, mismatch.stderr
    prose6.write_text(original, encoding="utf-8")

    prose1 = project / "正文" / "第001章_雨夜旧院.md"
    prose1.write_text(prose1.read_text(encoding="utf-8") + "\n檐水改了落点。\n", encoding="utf-8")
    add_receipt(project, 1, prose1)
    run("update", "--project", str(project))
    golden_stale = json.loads(run("golden-verify", "--project", str(project), expected=2).stdout)
    assert golden_stale["status"] == "stale", golden_stale
    assert any(item["chapter"] == 1 for item in golden_stale["changes"]), golden_stale

with tempfile.TemporaryDirectory(prefix="voice-profile-legacy-") as temporary:
    project = Path(temporary) / "legacy-book"
    (project / "正文").mkdir(parents=True)
    for chapter in range(1, 6):
        (project / "正文" / f"第{chapter:03d}章_旧章.md").write_text(chapter_text(chapter), encoding="utf-8")
    denied = run(
        "build",
        "--project",
        str(project),
        "--legacy-approved-through",
        "5",
        expected=2,
    )
    assert "LEGACY_APPROVED" in denied.stderr, denied.stderr
    legacy = json.loads(
        run(
            "build",
            "--project",
            str(project),
            "--legacy-approved-through",
            "5",
            "--confirm",
            "LEGACY_APPROVED",
        ).stdout
    )
    assert legacy["source_count"] == 5, legacy

print("OK: accepted and author-curated golden voice profiles bind digests, report bidirectional drift, and prepare blind packs")
