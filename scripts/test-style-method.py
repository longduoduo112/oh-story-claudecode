#!/usr/bin/env python3
"""Behavior tests for Chinese A/B writing-method qualification and binding."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "skills/story-long-write/scripts/style_method.py"
FEATURES = [
    "sentence-rhythm",
    "paragraph-breathing",
    "dialogue-ratio",
    "dialogue-tags",
    "narrative-distance",
    "sensory-selection",
    "emotion-landing",
    "figurative-density",
    "information-release",
    "transition",
    "chapter-ending",
]


def write_json(path: Path, value: object) -> None:
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


def digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def base_rules() -> list[dict[str, object]]:
    return [
        {
            "rule_key": "pressure-dialogue-compression",
            "dimension": "dialogue",
            "instruction": "高压对峙时缩短问答回合，让动作承担未说出口的抵抗。",
            "applies_to": ["高压", "对峙"],
            "avoid": "不要补成解释性旁白。",
            "priority": 90,
        },
        {
            "rule_key": "object-carried-transition",
            "dimension": "transition",
            "instruction": "转场优先让上一场留下的物件或动作进入下一场。",
            "applies_to": ["转场"],
            "avoid": "不要使用时间总结句替代场景。",
            "priority": 70,
        },
        {
            "rule_key": "aftermath-body-action",
            "dimension": "emotion-landing",
            "instruction": "重击后的余波先落在身体动作和选择变化上。",
            "applies_to": ["余波", "悲伤"],
            "avoid": "不要追加意义总结。",
            "priority": 80,
        },
        {
            "rule_key": "ending-concrete-change",
            "dimension": "chapter-ending",
            "instruction": "章尾用具体物件、动作或关系状态的变化留下余势。",
            "applies_to": ["章尾", "*"],
            "avoid": "不要写预告式总结。",
            "priority": 60,
        },
    ]


def work(work_id: str, author_id: str, split: str, *, series_id: str | None = None) -> dict[str, object]:
    return {
        "work_id": work_id,
        "title": f"测试作品{work_id}",
        "author_id": author_id,
        "series_id": series_id,
        "char_count": 20000,
        "chapter_count": 10,
        "split": split,
        "source_sha256": digest(work_id),
    }


def manifest(corpus_id: str, corpus_type: str, works: list[dict[str, object]], *, mode: str | None = None) -> dict[str, object]:
    controls: list[dict[str, object]] = []
    if corpus_type == "author-corpus":
        controls = [
            {"author_id": "control-01", "work_count": 1, "char_count": 20000},
            {"author_id": "control-02", "work_count": 1, "char_count": 20000},
        ]
    return {
        "schema_version": 1,
        "corpus_id": corpus_id,
        "corpus_type": corpus_type,
        "author_style_mode": mode,
        "authorization": {
            "lawful_access_confirmed": True,
            "rights_basis": "lawfully-held-analysis",
            "authorized_fidelity_confirmed": False,
        },
        "works": works,
        "control_authors": controls,
        "scene_function_coverage": ["opening", "escalation", "turning-point", "aftermath"],
        "anti_copy": {
            "store_source_text": False,
            "compile_distinctive_expressions": False,
            "phrase_overlap_gate": True,
            "plot_independence_gate": True,
        },
        "control_separation_confirmed": corpus_type == "author-corpus",
        "forward_test_plan": {
            "compare_against": "A-standard",
            "minimum_samples": 6,
            "blind_reviewers": 2,
        },
    }


def write_packs(corpus: Path, works: list[dict[str, object]], *, contaminate: bool = False) -> None:
    for item in works:
        rules: list[dict[str, object]] = []
        for index, source in enumerate(base_rules(), start=1):
            rule = dict(source)
            rule["evidence_locators"] = [{"chapter": index, "scene": f"场景{index}", "paragraph": "中段"}]
            rules.append(rule)
        pack: dict[str, object] = {
            "schema_version": 1,
            "work_id": item["work_id"],
            "source_sha256": item["source_sha256"],
            "split": item["split"],
            "sampled_chapter_count": 4,
            "sampled_scene_count": 8,
            "scene_functions": ["opening", "escalation", "turning-point", "aftermath"],
            "rules": rules,
        }
        if contaminate and item is works[0]:
            pack["quote"] = "不应进入机制包的原句"
        write_json(corpus / "work-mechanics-packs" / f"{item['work_id']}.json", pack)


def write_author_pack(corpus: Path, works: list[dict[str, object]], *, mode: str = "author-mechanics") -> None:
    rules = []
    for index, source in enumerate(base_rules()[:2], start=1):
        rules.append(
            {
                "rule_key": f"author-overlay-{index}",
                "dimension": source["dimension"],
                "instruction": source["instruction"],
                "applies_to": source["applies_to"],
                "avoid": source["avoid"],
                "priority": 75,
                "support_work_ids": [str(item["work_id"]) for item in works],
                "control_author_ids": ["control-01", "control-02"],
            }
        )
    evaluations = {
        "holdout-style-separation": "passed",
        "content-preservation": "passed",
        "chinese-naturalness": "passed",
        "phrase-overlap": "passed",
        "plot-independence": "passed",
        "source-obscurity" if mode == "author-mechanics" else "blind-attribution": "passed",
    }
    write_json(
        corpus / "author-style-pack.json",
        {
            "schema_version": 1,
            "target_author_id": "target-author",
            "mode": mode,
            "feature_families": FEATURES,
            "rules": rules,
            "evaluations": evaluations,
            "source_names_visible_to_writer": mode == "authorized-fidelity",
            "distinctive_expression_allowed": False,
        },
    )


def write_forward(compiled: Path) -> None:
    method_sha = hashlib.sha256((compiled / "compiled-method.json").read_bytes()).hexdigest()
    write_json(
        compiled / "forward-test.json",
        {
            "schema_version": 1,
            "method_sha256": method_sha,
            "test_id": "forward-001",
            "sample_count": 6,
            "blind_reviewer_count": 2,
            "baseline": "A-standard",
            "candidate": "B-distilled",
            "metrics": {
                "content_preservation_pass": True,
                "chinese_naturalness_pass": True,
                "phrase_overlap_pass": True,
                "plot_independence_pass": True,
                "b_preference_rate": 0.67,
            },
            "status": "passed",
            "review_completed": True,
        },
    )


with tempfile.TemporaryDirectory(prefix="style-method-") as temporary:
    root = Path(temporary)
    project = root / "book"
    (project / "设定").mkdir(parents=True)

    default_status = json.loads(run("check", "--project", str(project)).stdout)
    assert default_status["method_branch"] == "A-standard" and default_status["implicit_default"] is True
    default_runtime = json.loads(run("resolve", "--project", str(project)).stdout)
    assert default_runtime["read_benchmark_style"] is True and default_runtime["selected_rules"] == []

    run("standard", "--project", str(project), expected=2)
    run("standard", "--project", str(project), "--confirm", "STANDARD")
    explicit_status = json.loads(run("check", "--project", str(project)).stdout)
    assert explicit_status["method_branch"] == "A-standard" and explicit_status["implicit_default"] is False

    pilot = root / "pilot"
    pilot_works = [work("pilot-01", "pilot-author", "train")]
    write_json(pilot / "corpus-manifest.json", manifest("pilot", "single-work-pilot", pilot_works))
    write_packs(pilot, pilot_works)
    pilot_report = json.loads(run("qualify", "--corpus", str(pilot), "--confirm", "QUALIFY", expected=2).stdout)
    assert pilot_report["status"] == "unqualified"
    assert any("single-work-pilot" in item for item in pilot_report["errors"])

    shelf = root / "shelf"
    shelf_works = [
        work("shelf-01", "author-01", "train"),
        work("shelf-02", "author-02", "calibration"),
        work("shelf-03", "author-03", "holdout"),
    ]
    write_json(shelf / "corpus-manifest.json", manifest("shelf-method", "shelf-corpus", shelf_works))
    write_packs(shelf, shelf_works, contaminate=True)
    contaminated = json.loads(run("qualify", "--corpus", str(shelf), "--confirm", "QUALIFY", expected=2).stdout)
    assert any("原文/引文" in item for item in contaminated["errors"]), contaminated
    write_packs(shelf, shelf_works)
    qualified = json.loads(run("qualify", "--corpus", str(shelf), "--confirm", "QUALIFY").stdout)
    assert qualified["status"] == "qualified" and qualified["gate_summary"]["mechanics_packs"] == 3

    first_pack = shelf / "work-mechanics-packs" / "shelf-01.json"
    changed = json.loads(first_pack.read_text(encoding="utf-8"))
    changed["rules"][0]["priority"] = 89
    write_json(first_pack, changed)
    stale = run("compile", "--corpus", str(shelf), "--confirm", "COMPILE", expected=2)
    assert "已过期" in stale.stderr
    changed["rules"][0]["priority"] = 90
    write_json(first_pack, changed)
    run("qualify", "--corpus", str(shelf), "--confirm", "QUALIFY")
    compiled_result = json.loads(run("compile", "--corpus", str(shelf), "--confirm", "COMPILE").stdout)
    assert compiled_result["rule_count"] == 4
    compiled = shelf / "compiled"

    bind_without_test = run(
        "bind",
        "--project",
        str(project),
        "--compiled",
        str(compiled),
        "--confirm",
        "BIND",
        expected=2,
    )
    assert "前向盲测结果" in bind_without_test.stderr
    write_forward(compiled)
    bound = json.loads(
        run(
            "bind",
            "--project",
            str(project),
            "--compiled",
            str(compiled),
            "--confirm",
            "BIND",
            "--note",
            "用户确认启用",
        ).stdout
    )
    assert bound["method_branch"] == "B-distilled" and bound["read_raw_anchor_excerpts"] is False
    runtime = json.loads(
        run("resolve", "--project", str(project), "--scene-tag", "高压", "--scene-tag", "对峙").stdout
    )
    assert runtime["selected_rules"][0]["rule_key"] == "pressure-dialogue-compression"
    assert "support_work_count" not in runtime["selected_rules"][0]

    project_method = project / "设定" / "写作方法" / "compiled-method.json"
    original_method = project_method.read_text(encoding="utf-8")
    project_method.write_text(original_method + "\n", encoding="utf-8")
    tampered = run("check", "--project", str(project), expected=2)
    assert "已变化" in tampered.stderr
    project_method.write_text(original_method, encoding="utf-8")

    author = root / "author"
    author_works = [
        work("author-01", "target-author", "train", series_id="series-a"),
        work("author-02", "target-author", "calibration", series_id="series-b"),
        work("author-03", "target-author", "holdout", series_id=None),
    ]
    write_json(author / "corpus-manifest.json", manifest("author-method", "author-corpus", author_works, mode="author-mechanics"))
    write_packs(author, author_works)
    write_author_pack(author, author_works)
    author_report = json.loads(run("qualify", "--corpus", str(author), "--confirm", "QUALIFY").stdout)
    assert author_report["status"] == "qualified" and author_report["gate_summary"]["author_pack"] is True
    author_compiled = json.loads(run("compile", "--corpus", str(author), "--confirm", "COMPILE").stdout)
    assert author_compiled["rule_count"] == 6

    same_series_manifest = manifest("author-same-series", "author-corpus", author_works, mode="author-mechanics")
    for item in same_series_manifest["works"]:
        item["series_id"] = "one-series"
    write_json(author / "corpus-manifest.json", same_series_manifest)
    same_series = json.loads(run("qualify", "--corpus", str(author), "--confirm", "QUALIFY", expected=2).stdout)
    assert any("同一系列" in item for item in same_series["errors"])

    fidelity = root / "fidelity"
    write_json(
        fidelity / "corpus-manifest.json",
        manifest("fidelity", "author-corpus", author_works, mode="authorized-fidelity"),
    )
    write_packs(fidelity, author_works)
    write_author_pack(fidelity, author_works, mode="authorized-fidelity")
    fidelity_report = json.loads(run("qualify", "--corpus", str(fidelity), "--confirm", "QUALIFY", expected=2).stdout)
    assert any("高保真授权" in item or "本人作品" in item for item in fidelity_report["errors"])

print("OK: A remains compatible; B requires qualified multi-work evidence, blind tests, hashes, and explicit binding")
