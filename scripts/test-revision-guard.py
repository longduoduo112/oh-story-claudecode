#!/usr/bin/env python3
"""Regression tests for the cross-artifact revision impact gate."""

from __future__ import annotations

import json
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "skills/story-long-write/scripts/revision_guard.py"


class RevisionGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "某书"
        for directory in ("正文", "正文/原稿备份", "大纲", "大纲/旧稿备份", "设定/角色", "追踪/事实档案", "追踪/角色状态"):
            (self.project / directory).mkdir(parents=True, exist_ok=True)
        files = {
            "正文/第001章_开篇.md": "林岚查家谱。\n",
            "正文/第002章_续.md": "林岚继续查。\n",
            "大纲/细纲_第001章.md": "林岚发现血缘线索。\n",
            "大纲/细纲_第002章.md": "林岚核对。\n",
            "大纲/细纲_第999章.md": "其他人的独立细纲。\n",
            "大纲/旧稿备份/细纲_第003章.md": "林岚的废弃方案。\n",
            "大纲/卷纲_第1卷.md": "身世线。\n",
            "大纲/大纲.md": "林岚的身世线。\n",
            "设定/角色/林岚.md": "林岚身世档案。\n",
            "正文/原稿备份/第001章_旧稿.md": "林岚的废弃身世。\n",
            "追踪/长期事实.md": "# 长期事实\n",
            "追踪/关系清单.md": "# 关系清单\n",
            "追踪/上下文.md": "# 上下文\n",
            "追踪/事实档案/林岚.md": "# 林岚\n",
            "追踪/角色状态/林岚.md": "# 林岚\n",
        }
        for relative, text in files.items():
            (self.project / relative).write_text(text, encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_tool(self, command: str, document: dict[str, object], *, expect: int = 0) -> tuple[subprocess.CompletedProcess[str], Path]:
        input_path = self.root / f"{command}-input.json"
        output_path = self.root / "manifest.json"
        input_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        args = [sys.executable, str(TOOL), command, "--project", str(self.project), "--input", str(input_path)]
        if command == "plan":
            args.extend(["--output", str(output_path)])
        completed = subprocess.run(args, text=True, capture_output=True, encoding="utf-8", check=False)
        self.assertEqual(completed.returncode, expect, completed.stderr)
        return completed, output_path

    def request(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "change_id": "rev-001",
            "summary": "修正林岚血缘来源",
            "semantic_change": True,
            "changed_files": ["正文/第001章_开篇.md"],
            "affected_entities": ["林岚"],
            "affected_fact_ids": ["R001"],
            "affected_chapters": [1],
        }

    def test_plan_expands_body_change_to_outline_neighbors_and_canon_views(self) -> None:
        _, output = self.run_tool("plan", self.request())
        manifest = json.loads(output.read_text(encoding="utf-8"))
        required = manifest["required_checks"]
        self.assertIn("大纲/细纲_第001章.md", required)
        self.assertIn("正文/第002章_续.md", required)
        self.assertIn("大纲/卷纲_第1卷.md", required)
        self.assertIn("追踪/长期事实.md", required)
        self.assertIn("追踪/事实档案/林岚.md", required)
        self.assertNotIn("正文/原稿备份/第001章_旧稿.md", required)
        self.assertNotIn("大纲/旧稿备份/细纲_第003章.md", required)

    def test_character_setting_change_uses_entity_hits_without_forcing_every_fine_outline(self) -> None:
        request = self.request()
        request["changed_files"] = ["设定/角色/林岚.md"]
        request["affected_chapters"] = []
        _, output = self.run_tool("plan", request)
        required = json.loads(output.read_text(encoding="utf-8"))["required_checks"]
        self.assertIn("大纲/细纲_第001章.md", required)
        self.assertIn("大纲/卷纲_第1卷.md", required)
        self.assertNotIn("大纲/细纲_第999章.md", required)

    def test_backup_cannot_be_declared_as_a_revision_source(self) -> None:
        request = self.request()
        request["changed_files"] = ["正文/原稿备份/第001章_旧稿.md"]
        completed, _ = self.run_tool("plan", request, expect=2)
        self.assertIn("not active canon", completed.stderr)

    def test_check_rejects_unchecked_files_unresolved_conflicts_and_missing_tracking(self) -> None:
        _, output = self.run_tool("plan", self.request())
        manifest = json.loads(output.read_text(encoding="utf-8"))
        completed, _ = self.run_tool("check", manifest, expect=2)
        self.assertIn("unchecked files", completed.stderr)

        manifest["checked_files"] = manifest["required_checks"]
        manifest["conflicts"] = [{
            "severity": "S1", "source": "正文", "target": "设定", "issue": "血缘冲突",
            "resolution": "待处理", "status": "unresolved",
        }]
        completed, _ = self.run_tool("check", manifest, expect=2)
        self.assertIn("unresolved", completed.stderr)

        manifest["conflicts"][0]["resolution"] = "统一为胞弟长房血线"
        manifest["conflicts"][0]["status"] = "resolved"
        completed, _ = self.run_tool("check", manifest, expect=2)
        self.assertIn("must close", completed.stderr)

    def test_check_passes_only_after_full_review_and_tracking_revision(self) -> None:
        _, output = self.run_tool("plan", self.request())
        manifest = json.loads(output.read_text(encoding="utf-8"))
        manifest["checked_files"] = manifest["required_checks"]
        manifest["conflicts"] = [{
            "severity": "S1", "source": "正文/第001章_开篇.md", "target": "设定/角色/林岚.md",
            "issue": "血缘来源口径不一", "resolution": "两处统一为胞弟长房血线", "status": "resolved",
        }]
        manifest["tracking_action"] = {
            "kind": "commit", "state_revision_before": 34, "state_revision_after": 35, "reason": "更新 R001",
        }
        completed, _ = self.run_tool("check", manifest)
        self.assertIn('"status": "PASS"', completed.stdout)

    def test_check_stamp_is_bound_to_exact_manifest_bytes(self) -> None:
        _, output = self.run_tool("plan", self.request())
        manifest = json.loads(output.read_text(encoding="utf-8"))
        manifest["checked_files"] = manifest["required_checks"]
        manifest["tracking_action"] = {
            "kind": "commit", "state_revision_before": 34, "state_revision_after": 35,
            "reason": "更新 R001",
        }
        output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        stamp = self.root / "active.approved.json"
        completed = subprocess.run(
            [sys.executable, str(TOOL), "check", "--project", str(self.project),
             "--input", str(output), "--stamp", str(stamp)],
            text=True, capture_output=True, encoding="utf-8", check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        approval = json.loads(stamp.read_text(encoding="utf-8"))
        self.assertEqual(approval["status"], "PASS")
        self.assertEqual(
            approval["manifest_sha256"],
            hashlib.sha256(output.read_bytes()).hexdigest(),
        )

        output.write_text(output.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        self.assertNotEqual(
            approval["manifest_sha256"],
            hashlib.sha256(output.read_bytes()).hexdigest(),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
