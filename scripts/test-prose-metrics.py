#!/usr/bin/env python3
"""Behavior tests for deterministic prose metrics."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "skills/story-deslop/scripts/prose_metrics.py"
MIRROR = ROOT / "skills/story-long-write/scripts/prose_metrics.py"


def run(path: Path, expected: int = 0) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, str(TOOL), str(path)],
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


assert TOOL.read_bytes() == MIRROR.read_bytes(), "story-deslop/story-long-write prose metrics must stay identical"

with tempfile.TemporaryDirectory(prefix="prose-metrics-") as temporary:
    root = Path(temporary)
    prose = root / "prose.md"
    prose.write_text(
        "## 第一章 雨夜\n\n"
        "雨停了。\n"
        "林川推开院门，鞋底带进一层泥水，母亲背对着他坐在窗下，正把桌上的旧药包拆开又重新扎紧。\n"
        "他没问。只把灯芯拨亮，又去灶边添了半瓢水，等锅里慢慢冒出热气。\n",
        encoding="utf-8",
    )
    result = run(prose)
    assert result["status"] == "measured", result
    assert result["sentence_count"] == 4, result
    distribution = result["sentence_distribution"]
    assert distribution["short"]["count"] == 2, result
    assert distribution["medium"]["count"] == 1, result
    assert distribution["long"]["count"] == 1, result
    total_rate = sum(distribution[key]["rate_pct"] for key in ("short", "medium", "long"))
    assert abs(total_rate - 100.0) < 0.001, result

    empty = root / "empty.md"
    empty.write_text("## 只有标题\n", encoding="utf-8")
    error = run(empty, expected=2)
    assert error["status"] == "error", error

print("OK: prose metrics report deterministic sentence buckets and reject empty prose")
