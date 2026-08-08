#!/usr/bin/env python3
"""Regression tests for scripts/build-package.py."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile


SCRIPT = Path(__file__).with_name("build-package.py")
SPEC = importlib.util.spec_from_file_location("oh_story_build_package", SCRIPT)
if SPEC is None or SPEC.loader is None:  # pragma: no cover - import machinery guard
    raise RuntimeError("cannot load {}".format(SCRIPT))
build_package = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = build_package
SPEC.loader.exec_module(build_package)


FIXED_EPOCH = 1704067200  # 2024-01-01T00:00:00Z


class PackageFixture:
    def __init__(self, root: Path, version: str = "1.2.3") -> None:
        self.root = root
        self.version = version
        self.write("skills/story/VERSION", version + "\n")
        self.write("skills/story/SKILL.md", "# story\n")
        self.write("skills/example/manifest.json", '{"version":"1.0.0"}\n')
        self.write_json(
            ".claude-plugin/marketplace.json",
            {
                "name": "oh-story-skills",
                "metadata": {"version": version},
                "plugins": [{"name": "story", "version": "1.0.0"}],
            },
        )
        self.write_json(
            ".zcode-plugin/plugin.json", {"name": "oh-story", "version": version}
        )
        self.write_json(
            "reasonix-plugin.json", {"name": "oh-story", "version": version}
        )
        self.write_json(
            "marketplace.json",
            {
                "name": "oh-story-zcode",
                "version": 1,
                "plugins": [{"name": "oh-story", "version": version}],
            },
        )
        self.write_json(
            "package.json", {"name": "oh-story-dashboard-tests", "private": True}
        )
        self.write_json(
            "scripts/current-contract.json",
            {"setup_skill_version": "1.2.7", "agents_version": 24},
        )
        self.write("README.md", "fixture\n")

    def write(self, relative: str, content: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def write_json(self, relative: str, value: object) -> None:
        self.write(relative, json.dumps(value, ensure_ascii=False, indent=2) + "\n")

    def git(self, *args: str, env=None) -> str:
        completed = subprocess.run(
            ["git", "-C", os.fspath(self.root)] + list(args),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
        )
        return completed.stdout.strip()

    def commit(self, *, tag: bool = False) -> None:
        self.git("init", "-q")
        self.git("config", "user.name", "Package Test")
        self.git("config", "user.email", "package-test@example.invalid")
        self.git("add", "-f", ".")
        environment = os.environ.copy()
        environment.update(
            {
                "GIT_AUTHOR_DATE": "2024-01-01T00:00:00Z",
                "GIT_COMMITTER_DATE": "2024-01-01T00:00:00Z",
            }
        )
        self.git("commit", "-qm", "fixture", env=environment)
        if tag:
            self.git("tag", "-a", "v{}".format(self.version), "-m", "release")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def zip_members(path: Path):
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


class BuildPackageTests(unittest.TestCase):
    def test_dev_naming_and_in_archive_version_sync_do_not_touch_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            root.mkdir()
            fixture = PackageFixture(root)
            fixture.commit()
            original = {
                relative: (root / relative).read_bytes()
                for relative in (
                    "skills/story/VERSION",
                    ".claude-plugin/marketplace.json",
                    ".zcode-plugin/plugin.json",
                    "reasonix-plugin.json",
                    "marketplace.json",
                )
            }
            short_sha = fixture.git("rev-parse", "--short=12", "HEAD")

            result = build_package.build_package(
                root=root,
                output_dir=root / "dist",
                channel="dev",
                source_date_epoch=FIXED_EPOCH,
            )

            expected_version = "1.2.3-dev.20240101T000000Z+g{}".format(short_sha)
            self.assertEqual(result.version, expected_version)
            self.assertEqual(result.zip_path.name, "oh-story-{}.zip".format(expected_version))
            self.assertEqual(result.tar_path.name, "oh-story-{}.tar.gz".format(expected_version))

            members = zip_members(result.zip_path)
            prefix = "oh-story-{}/".format(expected_version)
            self.assertTrue(members)
            self.assertEqual({name.split("/", 1)[0] for name in members}, {result.archive_root})
            self.assertEqual(
                members[prefix + "skills/story/VERSION"].decode().strip(), expected_version
            )
            claude = json.loads(members[prefix + ".claude-plugin/marketplace.json"])
            self.assertEqual(claude["metadata"]["version"], expected_version)
            self.assertEqual(claude["plugins"][0]["version"], "1.0.0")
            self.assertEqual(
                json.loads(members[prefix + ".zcode-plugin/plugin.json"])["version"],
                expected_version,
            )
            self.assertEqual(
                json.loads(members[prefix + "reasonix-plugin.json"])["version"],
                expected_version,
            )
            marketplace = json.loads(members[prefix + "marketplace.json"])
            self.assertEqual(marketplace["version"], 1)
            self.assertEqual(marketplace["plugins"][0]["version"], expected_version)
            self.assertEqual(
                json.loads(members[prefix + "skills/example/manifest.json"])["version"],
                "1.0.0",
            )
            for relative, content in original.items():
                self.assertEqual((root / relative).read_bytes(), content)

    def test_release_has_one_root_and_excludes_generated_local_and_sensitive_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            root.mkdir()
            fixture = PackageFixture(root)
            included = "tests/test_example.py"
            fixture.write(included, "assert True\n")
            excluded = (
                "dist/old.zip",
                "node_modules/pkg/index.js",
                "scripts/__pycache__/tool.pyc",
                "test-results/dashboard/out.json",
                "playwright-report/index.html",
                ".env",
                ".env.production",
                ".npmrc",
                "private.pem",
                ".DS_Store",
                ".claude/settings.local.json",
                "AGENTS.md",
                "notes.local",
            )
            for relative in excluded:
                fixture.write(relative, "must not ship\n")
            fixture.commit(tag=True)

            result = build_package.build_package(
                root=root,
                output_dir=root / "dist",
                channel="release",
                source_date_epoch=FIXED_EPOCH,
            )
            zip_names = set(zip_members(result.zip_path))
            prefix = result.archive_root + "/"
            self.assertIn(prefix + included, zip_names)
            for relative in excluded:
                self.assertNotIn(prefix + relative, zip_names)
            self.assertEqual({name.split("/", 1)[0] for name in zip_names}, {result.archive_root})

            with tarfile.open(result.tar_path, "r:gz") as archive:
                tar_names = set(archive.getnames())
            self.assertEqual({name.split("/", 1)[0] for name in tar_names}, {result.archive_root})
            self.assertIn(prefix + included, tar_names)
            for relative in excluded:
                self.assertNotIn(prefix + relative, tar_names)

            self.assertEqual(
                zip_members(result.zip_path)[prefix + "skills/story/VERSION"], b"1.2.3\n"
            )

    def test_release_validates_version_tag_cleanliness_and_manifest_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)

            invalid_root = base / "invalid"
            invalid_root.mkdir()
            invalid = PackageFixture(invalid_root, "1.2")
            with self.assertRaisesRegex(build_package.BuildError, "plain X.Y.Z"):
                build_package.build_package(
                    root=invalid_root,
                    output_dir=base / "invalid-out",
                    channel="release",
                    verify_tag=False,
                    allow_dirty=True,
                )

            no_tag_root = base / "no-tag"
            no_tag_root.mkdir()
            no_tag = PackageFixture(no_tag_root)
            no_tag.commit()
            with self.assertRaisesRegex(build_package.BuildError, "tag 'v1.2.3'"):
                build_package.build_package(
                    root=no_tag_root,
                    output_dir=base / "no-tag-out",
                    channel="release",
                )

            lightweight_root = base / "lightweight"
            lightweight_root.mkdir()
            lightweight = PackageFixture(lightweight_root)
            lightweight.commit()
            lightweight.git("tag", "v1.2.3")
            with self.assertRaisesRegex(build_package.BuildError, "must be annotated"):
                build_package.build_package(
                    root=lightweight_root,
                    output_dir=base / "lightweight-out",
                    channel="release",
                )

            dirty_root = base / "dirty"
            dirty_root.mkdir()
            dirty = PackageFixture(dirty_root)
            dirty.commit(tag=True)
            dirty.write("README.md", "changed\n")
            with self.assertRaisesRegex(build_package.BuildError, "clean working tree"):
                build_package.build_package(
                    root=dirty_root,
                    output_dir=base / "dirty-out",
                    channel="release",
                )
            allowed = build_package.build_package(
                root=dirty_root,
                output_dir=base / "dirty-allowed",
                channel="release",
                allow_dirty=True,
                source_date_epoch=FIXED_EPOCH,
            )
            manifest = json.loads(allowed.manifest_path.read_text(encoding="utf-8"))
            self.assertTrue(manifest["source_dirty"])

            drift_root = base / "drift"
            drift_root.mkdir()
            drift = PackageFixture(drift_root)
            drift.write_json(
                "reasonix-plugin.json", {"name": "oh-story", "version": "9.9.9"}
            )
            drift.commit(tag=True)
            with self.assertRaisesRegex(build_package.BuildError, "canonical product version"):
                build_package.build_package(
                    root=drift_root,
                    output_dir=base / "drift-out",
                    channel="release",
                )

    def test_fixed_epoch_builds_are_reproducible_and_checksums_verify(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "repo"
            root.mkdir()
            fixture = PackageFixture(root)
            fixture.write("bin/run.sh", "#!/bin/sh\nexit 0\n")
            (root / "bin/run.sh").chmod(0o755)
            fixture.commit(tag=True)

            first = build_package.build_package(
                root=root,
                output_dir=base / "out-one",
                channel="release",
                source_date_epoch=FIXED_EPOCH,
            )
            second = build_package.build_package(
                root=root,
                output_dir=base / "out-two",
                channel="release",
                source_date_epoch=FIXED_EPOCH,
            )

            self.assertEqual(first.zip_path.read_bytes(), second.zip_path.read_bytes())
            self.assertEqual(first.tar_path.read_bytes(), second.tar_path.read_bytes())
            self.assertEqual(first.manifest_path.read_bytes(), second.manifest_path.read_bytes())
            self.assertEqual(first.checksums_path.read_bytes(), second.checksums_path.read_bytes())

            manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["channel"], "release")
            self.assertEqual(manifest["version"], "1.2.3")
            self.assertEqual(manifest["source_sha"], fixture.git("rev-parse", "HEAD"))
            self.assertEqual(
                manifest["contract_versions"],
                {"setup_skill_version": "1.2.7", "agents_version": 24},
            )
            self.assertRegex(manifest["source_content_sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(manifest["payload_content_sha256"], r"^[0-9a-f]{64}$")
            expected_names = {first.zip_path.name, first.tar_path.name}
            self.assertEqual({item["name"] for item in manifest["files"]}, expected_names)

            sums = {}
            for line in first.checksums_path.read_text(encoding="utf-8").splitlines():
                digest, name = line.split("  ", 1)
                sums[name] = digest
            self.assertEqual(set(sums), expected_names)
            for name in expected_names:
                artifact = first.zip_path.parent / name
                self.assertEqual(manifest["checksums"][name], sha256(artifact))
                self.assertEqual(sums[name], sha256(artifact))


if __name__ == "__main__":
    unittest.main(verbosity=2)
