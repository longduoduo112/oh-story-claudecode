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
        for skill_name in sorted(build_package.CANONICAL_SKILL_NAMES):
            self.write(
                "skills/{}/SKILL.md".format(skill_name),
                "---\nname: {}\ndescription: fixture\n---\n".format(skill_name),
            )
        self.write("skills/story-cover/manifest.json", '{"version":"1.0.0"}\n')
        self.write_json(
            ".claude-plugin/marketplace.json",
            {
                "name": "oh-story-skills",
                "metadata": {"version": version},
                "plugins": [
                    {
                        "name": skill_name,
                        "source": "./",
                        "skills": ["./skills/{}".format(skill_name)],
                        "version": "1.0.0",
                    }
                    for skill_name in sorted(build_package.CANONICAL_SKILL_NAMES)
                ],
            },
        )
        self.write_json(
            ".zcode-plugin/plugin.json",
            {
                "name": "oh-story",
                "version": version,
                **build_package.ZCODE_ENTRYPOINTS,
            },
        )
        self.write_json(
            ".codebuddy-plugin/plugin.json",
            {
                "name": "oh-story",
                "version": version,
                **build_package.CODEBUDDY_ENTRYPOINTS,
            },
        )
        self.write_json(
            "reasonix-plugin.json",
            {
                "name": "oh-story",
                "version": version,
                **build_package.REASONIX_ENTRYPOINTS,
            },
        )
        self.write_json(
            "marketplace.json",
            {
                "name": "oh-story-zcode",
                "version": 1,
                "plugins": [
                    {"name": "oh-story", "source": "./", "version": version}
                ],
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
                    ".codebuddy-plugin/plugin.json",
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
                json.loads(members[prefix + ".codebuddy-plugin/plugin.json"])["version"],
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
                json.loads(members[prefix + "skills/story-cover/manifest.json"])["version"],
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
                ".codex/agents/local.toml",
                ".openclaw/hooks/local.js",
                ".opencode/agents/local.md",
                ".reasonix/local.json",
                ".trae/skills/local/SKILL.md",
                ".zcode/config.json",
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

    def test_wrong_workspace_root_with_story_globalize_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "workspace"
            root.mkdir()
            fixture = PackageFixture(root)
            fixture.write(
                "skills/story-globalize/SKILL.md",
                "---\nname: story-globalize\ndescription: separate overseas tool\n---\n",
            )
            fixture.write("中文小说项目/正文/第1章.md", "fixture\n")
            fixture.commit()

            with self.assertRaisesRegex(build_package.BuildError, "separate overseas tool"):
                build_package.build_package(
                    root=root,
                    output_dir=root / "dist",
                    channel="dev",
                    source_date_epoch=FIXED_EPOCH,
                )
            self.assertFalse((root / "dist").exists())

    def test_relocated_story_globalize_path_segment_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            root.mkdir()
            fixture = PackageFixture(root)
            fixture.write("vendor/story-globalize/SKILL.md", "foreign\n")
            fixture.commit()

            with self.assertRaisesRegex(build_package.BuildError, "separate overseas tool"):
                build_package.build_package(
                    root=root,
                    output_dir=root / "dist",
                    channel="dev",
                    source_date_epoch=FIXED_EPOCH,
                )
            self.assertFalse((root / "dist").exists())

    def test_foreign_or_missing_skill_inventory_fails_for_git_and_exports(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)

            foreign_root = base / "foreign"
            foreign_root.mkdir()
            foreign = PackageFixture(foreign_root)
            foreign.write(
                "skills/not-oh-story/SKILL.md",
                "---\nname: not-oh-story\ndescription: foreign\n---\n",
            )
            foreign.commit()
            with self.assertRaisesRegex(build_package.BuildError, "unexpected: not-oh-story"):
                build_package.build_package(
                    root=foreign_root,
                    output_dir=base / "foreign-out",
                    channel="dev",
                    source_date_epoch=FIXED_EPOCH,
                )

            export_root = base / "export"
            export_root.mkdir()
            PackageFixture(export_root)
            for path in (export_root / "skills/story-review").iterdir():
                path.unlink()
            (export_root / "skills/story-review").rmdir()
            with self.assertRaisesRegex(build_package.BuildError, "missing: story-review"):
                build_package.build_package(
                    root=export_root,
                    output_dir=base / "export-out",
                    channel="release",
                    source_date_epoch=FIXED_EPOCH,
                    verify_tag=False,
                    allow_dirty=True,
                )

    def test_required_product_manifests_fail_closed_before_packaging(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            root.mkdir()
            PackageFixture(root)
            (root / ".codebuddy-plugin/plugin.json").unlink()

            with self.assertRaisesRegex(build_package.BuildError, "required product file"):
                build_package.build_package(
                    root=root,
                    output_dir=root / "dist",
                    channel="dev",
                    source_date_epoch=FIXED_EPOCH,
                )

    def test_skill_frontmatter_name_cannot_impersonate_a_canonical_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            root.mkdir()
            fixture = PackageFixture(root)
            fixture.write(
                "skills/story-review/SKILL.md",
                "---\nname: story-globalize\ndescription: overseas\n---\n",
            )

            with self.assertRaisesRegex(build_package.BuildError, "skill name mismatch"):
                build_package.build_package(
                    root=root,
                    output_dir=root / "dist",
                    channel="dev",
                    source_date_epoch=FIXED_EPOCH,
                )

    def test_skill_frontmatter_rejects_duplicate_quoted_and_complex_name_keys(self) -> None:
        cases = {
            "duplicate": "name: story-review\nname: story-globalize",
            "quoted-key": 'name: story-review\n"name": story-globalize',
            "quoted-value": 'name: "story-review"',
            "tagged-key": "name: story-review\n!!str name: story-globalize",
        }
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            for label, name_lines in cases.items():
                with self.subTest(label=label):
                    root = base / label
                    root.mkdir()
                    fixture = PackageFixture(root)
                    fixture.write(
                        "skills/story-review/SKILL.md",
                        "---\n{}\ndescription: fixture\n---\n".format(name_lines),
                    )
                    with self.assertRaisesRegex(
                        build_package.BuildError, "frontmatter|simple unquoted"
                    ):
                        build_package.build_package(
                            root=root,
                            output_dir=base / (label + "-out"),
                            channel="dev",
                            source_date_epoch=FIXED_EPOCH,
                        )

    def test_product_manifest_identity_and_entrypoints_are_locked(self) -> None:
        manifest_paths = (
            ".claude-plugin/marketplace.json",
            ".zcode-plugin/plugin.json",
            ".codebuddy-plugin/plugin.json",
            "reasonix-plugin.json",
            "marketplace.json",
        )
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            for index, relative in enumerate(manifest_paths):
                with self.subTest(relative=relative, field="name"):
                    root = base / "name-{}".format(index)
                    root.mkdir()
                    fixture = PackageFixture(root)
                    document = json.loads((root / relative).read_text(encoding="utf-8"))
                    document["name"] = "other-product"
                    fixture.write_json(relative, document)
                    with self.assertRaisesRegex(build_package.BuildError, "field 'name'"):
                        build_package.build_package(
                            root=root,
                            output_dir=base / "name-out-{}".format(index),
                            channel="dev",
                            source_date_epoch=FIXED_EPOCH,
                        )

            entrypoint_cases = (
                (
                    ".claude-plugin/marketplace.json",
                    lambda document: document["plugins"][0].update(
                        skills=["./skills/not-the-declared-skill"]
                    ),
                ),
                (
                    ".zcode-plugin/plugin.json",
                    lambda document: document.update(hooks="elsewhere/hooks.json"),
                ),
                (
                    ".codebuddy-plugin/plugin.json",
                    lambda document: document.update(agents=[]),
                ),
                (
                    "reasonix-plugin.json",
                    lambda document: document.update(skills="elsewhere"),
                ),
                (
                    "marketplace.json",
                    lambda document: document["plugins"][0].update(source="elsewhere"),
                ),
            )
            for index, (relative, mutate) in enumerate(entrypoint_cases):
                with self.subTest(relative=relative, field="entrypoint"):
                    root = base / "route-{}".format(index)
                    root.mkdir()
                    fixture = PackageFixture(root)
                    document = json.loads((root / relative).read_text(encoding="utf-8"))
                    mutate(document)
                    fixture.write_json(relative, document)
                    with self.assertRaises(build_package.BuildError):
                        build_package.build_package(
                            root=root,
                            output_dir=base / "route-out-{}".format(index),
                            channel="dev",
                            source_date_epoch=FIXED_EPOCH,
                        )

    def test_non_git_export_build_ignores_cache_and_local_deployment_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "export"
            root.mkdir()
            fixture = PackageFixture(root)
            fixture.write("skills/__pycache__/generated.pyc", "cache\n")
            fixture.write("skills/.cache/index", "cache\n")
            fixture.write(".trae/hooks/local.js", "local\n")

            result = build_package.build_package(
                root=root,
                output_dir=base / "out",
                channel="dev",
                source_date_epoch=FIXED_EPOCH,
            )

            names = set(zip_members(result.zip_path))
            self.assertFalse(any("/__pycache__/" in name for name in names))
            self.assertFalse(any("/.cache/" in name for name in names))
            self.assertFalse(any("/.trae/" in name for name in names))

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
                "reasonix-plugin.json",
                {
                    "name": "oh-story",
                    "version": "9.9.9",
                    **build_package.REASONIX_ENTRYPOINTS,
                },
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
