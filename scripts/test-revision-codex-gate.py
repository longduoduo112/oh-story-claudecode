#!/usr/bin/env python3
"""Regression tests for the Codex revision write gate adapter."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "skills/story-setup/references/codex/hooks/story_codex_hook.py"
SPEC = importlib.util.spec_from_file_location("story_codex_hook", HOOK)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RevisionCodexGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.book = self.root / "测试书"
        self.old_chapter = self.book / "正文/第2章_旧章.md"
        self.next_chapter = self.book / "正文/第3章_新章.md"
        self.setting = self.book / "设定/角色/甲.md"
        self.impact = self.book / "追踪/修改影响"
        for parent in (self.old_chapter.parent, self.setting.parent, self.impact):
            parent.mkdir(parents=True, exist_ok=True)
        self.old_chapter.write_text("旧章\n", encoding="utf-8")
        self.setting.write_text("旧设定\n", encoding="utf-8")
        (self.book / "追踪/_tracking-state.json").write_text(
            json.dumps({"schema_version": 5, "state_revision": 9, "last_committed_chapter": 2}),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_plan_and_approval_lifecycle(self) -> None:
        self.assertIn("修改旧内容被拦截", MODULE.revision_block_reason(self.root, self.old_chapter))
        self.assertIsNone(MODULE.revision_block_reason(self.root, self.next_chapter))

        manifest_path = self.impact / "active.json"
        manifest = {
            "schema_version": 1,
            "change_id": "rev-002",
            "changed_files": ["正文/第2章_旧章.md"],
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.assertIsNone(MODULE.revision_block_reason(self.root, self.old_chapter))
        self.assertIn("计划外修改被拦截", MODULE.revision_block_reason(self.root, self.setting))
        self.assertIn("计划外修改被拦截", MODULE.revision_block_reason(self.root, self.next_chapter))

        manifest_bytes = manifest_path.read_bytes()
        (self.impact / "active.approved.json").write_text(
            json.dumps({
                "schema_version": 1,
                "status": "PASS",
                "change_id": "rev-002",
                "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            }),
            encoding="utf-8",
        )
        self.assertIn("已验收关闭", MODULE.revision_block_reason(self.root, self.old_chapter))
        self.assertIsNone(MODULE.revision_block_reason(self.root, self.next_chapter))

        manifest_path.write_bytes(manifest_bytes + b"\n")
        self.assertIsNone(MODULE.revision_block_reason(self.root, self.old_chapter))
        self.assertIn("计划外修改被拦截", MODULE.revision_block_reason(self.root, self.next_chapter))


if __name__ == "__main__":
    unittest.main(verbosity=2)
