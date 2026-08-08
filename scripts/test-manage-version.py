#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts/manage-version.py"


class ManageVersionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="ohstory-version-")
        self.root = Path(self.tmp.name)
        (self.root / "skills/story").mkdir(parents=True)
        (self.root / ".claude-plugin").mkdir()
        (self.root / ".zcode-plugin").mkdir()
        self.write_version("1.2.3")
        (self.root / "CHANGELOG.md").write_text(
            "# Changelog\n\n## v1.2.3（test）\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_json(self, relative: str, value: dict) -> None:
        (self.root / relative).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def write_version(self, version: str) -> None:
        (self.root / "skills/story/VERSION").write_text(version + "\n", encoding="utf-8")
        self.write_json(
            ".claude-plugin/marketplace.json",
            {"metadata": {"version": version}, "plugins": []},
        )
        self.write_json(".zcode-plugin/plugin.json", {"version": version})
        self.write_json("marketplace.json", {"plugins": [{"version": version}]})
        self.write_json("reasonix-plugin.json", {"version": version})

    def run_script(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.root), *args],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_check_accepts_consistent_surfaces_and_tag(self) -> None:
        result = self.run_script("check", "--tag", "v1.2.3", "--require-changelog")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_check_rejects_manifest_drift(self) -> None:
        self.write_json("reasonix-plugin.json", {"version": "1.2.2"})
        result = self.run_script("check")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reasonix-plugin.json", result.stderr)

    def test_check_rejects_tag_mismatch(self) -> None:
        result = self.run_script("check", "--tag", "v1.2.4")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("release tag must be v1.2.3", result.stderr)

    def test_set_updates_only_public_version_fields(self) -> None:
        result = self.run_script("set", "2.0.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads((self.root / ".claude-plugin/marketplace.json").read_text())["plugins"],
            [],
        )
        result = self.run_script("check", "--tag", "v2.0.0")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_set_rejects_prerelease_for_public_release_surface(self) -> None:
        result = self.run_script("set", "2.0.0-dev.1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("stable X.Y.Z", result.stderr)

    def test_set_validates_every_manifest_before_writing(self) -> None:
        original_version = (self.root / "skills/story/VERSION").read_text(encoding="utf-8")
        self.write_json("marketplace.json", {"plugins": []})
        result = self.run_script("set", "2.0.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            (self.root / "skills/story/VERSION").read_text(encoding="utf-8"),
            original_version,
        )
        self.assertEqual(
            json.loads(
                (self.root / ".claude-plugin/marketplace.json").read_text(encoding="utf-8")
            )["metadata"]["version"],
            "1.2.3",
        )


if __name__ == "__main__":
    unittest.main()
