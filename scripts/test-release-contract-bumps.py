#!/usr/bin/env python3
"""Git-fixture regressions for check-release-contract-bumps.py."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().with_name("check-release-contract-bumps.py")


class ReleaseContractBumpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="ohstory-contract-bump-")
        self.root = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Contract Test")
        self.git("config", "user.email", "contract-test@example.invalid")

        (self.root / "scripts").mkdir()
        (self.root / "skills/story-setup/references/templates").mkdir(parents=True)
        (self.root / "skills/story-setup/scripts").mkdir()
        self.write_contract("1.2.7", 24)
        self.write("skills/story-setup/SKILL.md", "# story-setup\n")
        self.write("skills/story-setup/references/templates/agent.md", "agent v1\n")
        self.write("skills/story-setup/scripts/install.py", "print('v1')\n")
        self.commit("v0.7.5 baseline")
        self.git("tag", "v0.7.5")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["git", *args],
            cwd=self.root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode != 0:
            self.fail("git {} failed: {}".format(" ".join(args), result.stderr))
        return result

    def write(self, relative: str, content: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def write_contract(self, setup: str, agents: int) -> None:
        self.write(
            "scripts/current-contract.json",
            json.dumps(
                {"setup_skill_version": setup, "agents_version": agents},
                indent=2,
            )
            + "\n",
        )

    def commit(self, message: str) -> None:
        self.git("add", ".")
        self.git("commit", "-qm", message)

    def run_gate(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--repo-root", str(self.root), *extra],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_same_versions_fail_when_both_payloads_change_from_v075(self) -> None:
        self.write("skills/story-setup/SKILL.md", "# story-setup\nnew protocol\n")
        self.write(
            "skills/story-setup/references/templates/agent.md",
            "agent v2\n",
        )
        self.commit("change setup and deployed payload without bumps")

        result = self.run_gate("--base-tag", "v0.7.5")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("setup_skill_version must increase", result.stderr)
        self.assertIn("agents_version must increase", result.stderr)
        self.assertIn("base 1.2.7, current 1.2.7", result.stderr)
        self.assertIn("base 24, current 24", result.stderr)

    def test_correct_independent_bumps_pass(self) -> None:
        self.write("skills/story-setup/SKILL.md", "# story-setup\nnew protocol\n")
        self.write("skills/story-setup/scripts/install.py", "print('v2')\n")
        self.write_contract("1.2.8", 25)
        self.commit("change payloads with contract bumps")

        result = self.run_gate("--base-tag", "v0.7.5")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("setup_skill_version 1.2.7 -> 1.2.8", result.stdout)
        self.assertIn("agents_version 24 -> 25", result.stdout)

    def test_no_relevant_change_passes_without_bumps_and_auto_finds_tag(self) -> None:
        self.write("README.md", "unrelated release note\n")
        self.commit("unrelated change")

        result = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("against v0.7.5", result.stdout)

    def test_versions_cannot_roll_back_without_payload_changes(self) -> None:
        self.write_contract("1.2.6", 23)
        self.commit("attempt contract rollback")

        result = self.run_gate("--base-tag", "v0.7.5")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("setup_skill_version must not decrease", result.stderr)
        self.assertIn("agents_version must not decrease", result.stderr)


if __name__ == "__main__":
    unittest.main()
