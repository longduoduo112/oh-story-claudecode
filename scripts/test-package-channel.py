#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("package-channel.py")
SPEC = importlib.util.spec_from_file_location("oh_story_package_channel", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {SCRIPT}")
package_channel = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = package_channel
SPEC.loader.exec_module(package_channel)


class PackageChannelTests(unittest.TestCase):
    def make_manifest(
        self,
        root: Path,
        *,
        head: str = "a" * 40,
        dirty: bool = False,
        content: bytes = b"package",
    ) -> Path:
        output = root / "dist/dev"
        output.mkdir(parents=True)
        archive = output / "oh-story-1.2.3-dev.test.zip"
        archive.write_bytes(content)
        manifest = output / "oh-story-1.2.3-dev.test.manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "channel": "dev",
                    "version": "1.2.3-dev.test",
                    "source_sha": head,
                    "source_dirty": dirty,
                    "checksums": {archive.name: package_channel.sha256(archive)},
                }
            ),
            encoding="utf-8",
        )
        return manifest

    def test_accepts_clean_exact_head_with_valid_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected = self.make_manifest(root)
            actual = package_channel.approved_dev_manifest(root, "1.2.3", "a" * 40)
            self.assertEqual(actual, expected)

    def test_rejects_dirty_or_wrong_head(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_manifest(root, dirty=True)
            with self.assertRaises(package_channel.GateError):
                package_channel.approved_dev_manifest(root, "1.2.3", "a" * 40)

    def test_rejects_tampered_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self.make_manifest(root)
            (manifest.parent / "oh-story-1.2.3-dev.test.zip").write_bytes(b"tampered")
            with self.assertRaises(package_channel.GateError):
                package_channel.approved_dev_manifest(root, "1.2.3", "a" * 40)


if __name__ == "__main__":
    unittest.main()
