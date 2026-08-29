#!/usr/bin/env python3
"""Behavior tests for the external publishing bridge."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
BRIDGE = ROOT / "skills/story-publish/scripts/publish_bridge.py"


class PublishBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="story-publish-test-")
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.publisher = self.root / "publisher"
        self.project.mkdir()
        self.publisher.mkdir()
        self.venv_bin = self.publisher / ".venv" / "bin"
        self.venv_bin.mkdir(parents=True)
        self.python_link = self.venv_bin / "python"
        self.python_link.symlink_to(sys.executable)
        self.log = self.publisher / "calls.jsonl"
        self.adapter = self.publisher / "project_publish.py"
        self.adapter.write_text(
            """#!/usr/bin/env python3
import json, pathlib, sys
if '--help' in sys.argv:
    print('preview preflight draft publish schedule edit login books')
    raise SystemExit(0)
path = pathlib.Path(__file__).with_name('calls.jsonl')
with path.open('a', encoding='utf-8') as handle:
    handle.write(json.dumps(sys.argv[1:], ensure_ascii=False) + '\\n')
raise SystemExit(0)
""",
            encoding="utf-8",
        )
        configured = self.run_bridge(
            "configure",
            "fanqie",
            "--adapter",
            str(self.adapter),
            "--python",
            str(self.python_link),
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_bridge(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(BRIDGE),
                "--project-root",
                str(self.project),
                *arguments,
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def calls(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_config_contains_only_adapter_binding(self) -> None:
        config = json.loads((self.project / ".story-publish.json").read_text())
        self.assertEqual(config["schema_version"], 1)
        self.assertEqual(
            config["platforms"]["fanqie"]["python"], str(self.python_link)
        )
        serialized = json.dumps(config).lower()
        for forbidden in ("cookie", "token", "password", "book_id"):
            self.assertNotIn(forbidden, serialized)

    def test_preview_forwards_arguments_without_shell(self) -> None:
        result = self.run_bridge(
            "run", "fanqie", "preview", "--chapters", "108;touch nope"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.calls()[-1], ["preview", "--chapters", "108;touch nope"]
        )
        self.assertFalse((self.publisher / "nope").exists())

    def test_draft_requires_separate_confirmation(self) -> None:
        blocked = self.run_bridge(
            "run", "fanqie", "draft", "--chapters", "109"
        )
        self.assertEqual(blocked.returncode, 2)
        self.assertFalse(self.calls())
        allowed = self.run_bridge(
            "run",
            "fanqie",
            "draft",
            "--chapters",
            "109",
            "--confirm-remote-draft",
        )
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertEqual(self.calls()[-1], ["draft", "--chapters", "109"])

    def test_live_write_requires_existing_downstream_gates(self) -> None:
        blocked = self.run_bridge(
            "run", "fanqie", "publish", "--chapters", "109"
        )
        self.assertEqual(blocked.returncode, 2)
        allowed = self.run_bridge(
            "run",
            "fanqie",
            "publish",
            "--chapters",
            "109",
            "--confirm-live",
            "--ai-declaration",
            "yes",
        )
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertEqual(
            self.calls()[-1],
            [
                "publish",
                "--chapters",
                "109",
                "--confirm-live",
                "--ai-declaration",
                "yes",
            ],
        )

    def test_gui_and_unknown_actions_are_rejected(self) -> None:
        for action in ("gui", "delete"):
            result = self.run_bridge("run", "fanqie", action)
            self.assertEqual(result.returncode, 2)
        self.assertFalse(self.calls())


if __name__ == "__main__":
    unittest.main()
