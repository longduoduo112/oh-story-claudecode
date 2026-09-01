#!/usr/bin/env python3
"""Regression tests for TRAE shared-core ownership classification."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "skills/story-setup/scripts/trae-core-ownership.py"
CORE = ROOT / "skills/story-setup/references/trae/hooks/story_hook_core.js"
REGISTRY = ROOT / "skills/story-setup/references/trae/legacy-managed-sha256.json"


class TraeCoreOwnershipTests(unittest.TestCase):
    def run_helper(self, candidate: Path, registry: Path = REGISTRY) -> tuple[int, dict[str, object]]:
        completed = subprocess.run(
            [
                "python3",
                str(HELPER),
                "--candidate",
                str(candidate),
                "--registry",
                str(registry),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertTrue(completed.stdout.strip(), completed.stderr)
        return completed.returncode, json.loads(completed.stdout)

    def test_current_marker_is_managed(self) -> None:
        code, result = self.run_helper(CORE)
        self.assertEqual(code, 0)
        self.assertEqual(result["managed"], True)
        self.assertEqual(result["reason"], "marker")

    def test_pre_marker_release_is_managed_by_legacy_hash(self) -> None:
        payload = CORE.read_bytes().replace(
            b"// oh-story-managed: shared-hook-core\n\n", b"", 1
        )
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / "story_hook_core.js"
            candidate.write_bytes(payload)
            code, result = self.run_helper(candidate)
        self.assertEqual(code, 0)
        self.assertEqual(result["managed"], True)
        self.assertEqual(result["reason"], "legacy-sha256")

    def test_pre_marker_release_crlf_is_managed_by_legacy_hash(self) -> None:
        payload = CORE.read_bytes().replace(
            b"// oh-story-managed: shared-hook-core\n\n", b"", 1
        ).replace(b"\n", b"\r\n")
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / "story_hook_core.js"
            candidate.write_bytes(payload)
            code, result = self.run_helper(candidate)
        self.assertEqual(code, 0)
        self.assertEqual(result["managed"], True)
        self.assertEqual(result["reason"], "legacy-sha256")

    def test_unknown_same_name_file_is_user_owned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / "story_hook_core.js"
            candidate.write_text("// a user-owned hook core\n", encoding="utf-8")
            code, result = self.run_helper(candidate)
        self.assertEqual(code, 3)
        self.assertEqual(result["managed"], False)
        self.assertEqual(result["reason"], "unmanaged")

    def test_invalid_registry_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            registry = Path(temporary) / "registry.json"
            registry.write_text('{"story_hook_core.js":[]}', encoding="utf-8")
            code, result = self.run_helper(CORE, registry)
        # A current marked core does not need the legacy registry.  The helper
        # deliberately accepts the explicit marker without consulting it.
        self.assertEqual(code, 0)
        self.assertEqual(result["reason"], "marker")

        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / "story_hook_core.js"
            candidate.write_bytes(CORE.read_bytes().replace(
                b"// oh-story-managed: shared-hook-core\n\n", b"", 1
            ))
            registry = Path(temporary) / "registry.json"
            registry.write_text('{"story_hook_core.js":[]}', encoding="utf-8")
            code, result = self.run_helper(candidate, registry)
        self.assertEqual(code, 2)
        self.assertEqual(result["managed"], False)
        self.assertEqual(result["reason"], "error")

    def test_missing_asset_key_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / "renamed-core.js"
            candidate.write_text("// old unmarked content\n", encoding="utf-8")
            code, result = self.run_helper(candidate)
        self.assertEqual(code, 2)
        self.assertEqual(result["managed"], False)
        self.assertEqual(result["reason"], "error")


if __name__ == "__main__":
    unittest.main(verbosity=2)
