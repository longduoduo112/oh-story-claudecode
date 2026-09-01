#!/usr/bin/env python3
"""Regression tests for scripts/verify-package.py."""

from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile


SCRIPT = Path(__file__).with_name("verify-package.py")
SPEC = importlib.util.spec_from_file_location("oh_story_verify_package", SCRIPT)
if SPEC is None or SPEC.loader is None:  # pragma: no cover - import machinery guard
    raise RuntimeError("cannot load {}".format(SCRIPT))
verify_package = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = verify_package
SPEC.loader.exec_module(verify_package)


class PackageFixture:
    def __init__(
        self,
        directory: Path,
        version: str = "1.2.3",
        channel: str = "release",
    ) -> None:
        self.directory = directory
        self.version = version
        self.channel = channel
        self.archive_root = "oh-story-{}".format(version)
        self.zip_path = directory / (self.archive_root + ".zip")
        self.manifest_path = directory / (self.archive_root + ".manifest.json")
        self.entries = self._base_entries()
        self.manifest = {}
        self.write()

    def _json(self, value: object) -> bytes:
        return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    def _base_entries(self):
        entries = [
            ("skills/story/VERSION", (self.version + "\n").encode("utf-8"), "file"),
            ("skills/story/SKILL.md", b"---\nname: story\ndescription: story\n---\n", "file"),
            (
                ".claude-plugin/marketplace.json",
                self._json(
                    {
                        "name": "oh-story-skills",
                        "metadata": {"version": self.version},
                        "plugins": [
                            {
                                "name": skill_name,
                                "source": "./",
                                "skills": ["./skills/{}".format(skill_name)],
                                "version": "1.0.0",
                            }
                            for skill_name in sorted(
                                verify_package.CANONICAL_SKILL_NAMES
                            )
                        ],
                    }
                ),
                "file",
            ),
            (
                ".zcode-plugin/plugin.json",
                self._json(
                    {
                        "name": "oh-story",
                        "version": self.version,
                        **verify_package.ZCODE_ENTRYPOINTS,
                    }
                ),
                "file",
            ),
            (
                ".codebuddy-plugin/plugin.json",
                self._json(
                    {
                        "name": "oh-story",
                        "version": self.version,
                        **verify_package.CODEBUDDY_ENTRYPOINTS,
                    }
                ),
                "file",
            ),
            (
                "reasonix-plugin.json",
                self._json(
                    {
                        "name": "oh-story",
                        "version": self.version,
                        **verify_package.REASONIX_ENTRYPOINTS,
                    }
                ),
                "file",
            ),
            (
                "marketplace.json",
                self._json(
                    {
                        "name": "oh-story-zcode",
                        "version": 1,
                        "plugins": [
                            {
                                "name": "oh-story",
                                "source": "./",
                                "version": self.version,
                            }
                        ]
                    }
                ),
                "file",
            ),
            ("README.md", b"fixture\n", "file"),
        ]
        for name in sorted(verify_package.CANONICAL_SKILL_NAMES.difference({"story"})):
            entries.append(
                (
                    "skills/{}/SKILL.md".format(name),
                    (
                        "---\nname: {}\ndescription: fixture skill\n---\n".format(name)
                    ).encode("utf-8"),
                    "file",
                )
            )
        return entries

    def write(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(
                self.zip_path,
                "w",
                compression=zipfile.ZIP_DEFLATED,
            ) as archive:
                for relative, content, kind in self.entries:
                    if relative.startswith("!raw!"):
                        full_name = relative[len("!raw!") :]
                    elif relative.startswith("/") or relative.startswith("C:"):
                        full_name = relative
                    else:
                        full_name = "{}/{}".format(self.archive_root, relative)
                    info = zipfile.ZipInfo(full_name)
                    info.create_system = 3
                    if kind == "symlink":
                        info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    elif kind == "directory":
                        if not info.filename.endswith("/"):
                            info.filename += "/"
                        info.external_attr = (stat.S_IFDIR | 0o755) << 16
                    else:
                        info.external_attr = (stat.S_IFREG | 0o644) << 16
                    archive.writestr(info, content)

        digest = hashlib.sha256(self.zip_path.read_bytes()).hexdigest()
        zip_name = self.zip_path.name
        tar_name = self.archive_root + ".tar.gz"
        self.manifest = {
            "schema_version": 1,
            "channel": self.channel,
            "version": self.version,
            "source_sha": "a" * 40,
            "archive_root": self.archive_root,
            "files": [
                {"name": zip_name, "format": "zip", "size": self.zip_path.stat().st_size},
                {"name": tar_name, "format": "tar.gz", "size": 123},
            ],
            "checksums": {zip_name: digest, tar_name: "b" * 64},
        }
        self.write_manifest()

    def write_manifest(self) -> None:
        self.manifest_path.write_text(
            json.dumps(self.manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def replace(self, relative: str, content: bytes) -> None:
        for index, (name, _, kind) in enumerate(self.entries):
            if name == relative:
                self.entries[index] = (name, content, kind)
                self.write()
                return
        raise AssertionError("fixture entry does not exist: {}".format(relative))

    def remove(self, relative: str) -> None:
        self.entries = [entry for entry in self.entries if entry[0] != relative]
        self.write()


class VerifyPackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="verify-package-tests-")
        self.base = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def fixture(self, **kwargs) -> PackageFixture:
        return PackageFixture(self.base / "artifact", **kwargs)

    def test_valid_package_uses_default_manifest_and_build_manifest_shape(self) -> None:
        fixture = self.fixture()

        result = verify_package.verify_package(fixture.zip_path)

        self.assertEqual(result.version, "1.2.3")
        self.assertEqual(result.channel, "release")
        self.assertEqual(result.archive_root, "oh-story-1.2.3")
        self.assertEqual(result.source_sha, "a" * 40)
        self.assertEqual(result.member_count, len(fixture.entries))
        self.assertIsNone(result.discovered_skills)

    def test_valid_dev_package_version_matches_build_package_format(self) -> None:
        version = "1.2.3-dev.20240101T000000Z+gabcdef123456"
        fixture = self.fixture(version=version, channel="dev")

        result = verify_package.verify_package(
            fixture.zip_path, fixture.manifest_path
        )

        self.assertEqual(result.version, version)
        self.assertEqual(result.channel, "dev")

    def test_manifest_requires_supported_core_fields_and_consistent_inventory(self) -> None:
        fixture = self.fixture()
        original = copy.deepcopy(fixture.manifest)
        mutations = (
            ("missing schema", lambda value: value.pop("schema_version"), "missing required"),
            ("future schema", lambda value: value.update(schema_version=2), "schema_version"),
            ("bad channel", lambda value: value.update(channel="nightly"), "channel"),
            ("bad sha", lambda value: value.update(source_sha="not-a-sha"), "source_sha"),
            (
                "missing checksum",
                lambda value: value["checksums"].pop(fixture.archive_root + ".tar.gz"),
                "files/checksums disagree",
            ),
            (
                "unsafe artifact name",
                lambda value: value["files"].append(
                    {"name": "../evil", "format": "zip", "size": 0}
                ),
                "safe artifact basename",
            ),
        )
        for label, mutate, error in mutations:
            with self.subTest(label=label):
                fixture.manifest = copy.deepcopy(original)
                mutate(fixture.manifest)
                fixture.write_manifest()
                with self.assertRaisesRegex(verify_package.VerificationError, error):
                    verify_package.verify_package(fixture.zip_path, fixture.manifest_path)

    def test_manifest_verifies_zip_size_and_checksum(self) -> None:
        fixture = self.fixture()

        fixture.manifest["files"][0]["size"] += 1
        fixture.write_manifest()
        with self.assertRaisesRegex(verify_package.VerificationError, "size mismatch"):
            verify_package.verify_package(fixture.zip_path, fixture.manifest_path)

        fixture.write()
        fixture.manifest["checksums"][fixture.zip_path.name] = "0" * 64
        fixture.write_manifest()
        with self.assertRaisesRegex(verify_package.VerificationError, "checksum mismatch"):
            verify_package.verify_package(fixture.zip_path, fixture.manifest_path)

    def test_archive_rejects_unsafe_paths(self) -> None:
        unsafe_paths = (
            "/absolute.txt",
            "../escape.txt",
            "safe/../escape.txt",
            "safe\\windows.txt",
            "C:/drive.txt",
            "safe//empty.txt",
        )
        for index, path in enumerate(unsafe_paths):
            with self.subTest(path=path):
                fixture = PackageFixture(self.base / "unsafe-{}".format(index))
                fixture.entries.append((path, b"bad\n", "file"))
                fixture.write()
                with self.assertRaisesRegex(verify_package.VerificationError, "unsafe member path"):
                    verify_package.verify_package(fixture.zip_path, fixture.manifest_path)

    def test_archive_rejects_duplicate_and_case_colliding_paths(self) -> None:
        fixture = self.fixture()
        fixture.entries.append(("README.md", b"duplicate\n", "file"))
        fixture.write()
        with self.assertRaisesRegex(verify_package.VerificationError, "duplicate or colliding"):
            verify_package.verify_package(fixture.zip_path, fixture.manifest_path)

        fixture = PackageFixture(self.base / "case-collision")
        fixture.entries.append(("readme.md", b"collision\n", "file"))
        fixture.write()
        with self.assertRaisesRegex(verify_package.VerificationError, "duplicate or colliding"):
            verify_package.verify_package(fixture.zip_path, fixture.manifest_path)

    def test_archive_requires_one_manifest_matching_root(self) -> None:
        fixture = self.fixture()
        fixture.entries.append(("!raw!other-root/file.txt", b"other\n", "file"))
        fixture.write()
        with self.assertRaisesRegex(verify_package.VerificationError, "exactly one root"):
            verify_package.verify_package(fixture.zip_path, fixture.manifest_path)

        fixture = PackageFixture(self.base / "wrong-root")
        fixture.entries = [
            ("!raw!different/" + relative, content, kind)
            for relative, content, kind in fixture.entries
        ]
        fixture.write()
        with self.assertRaisesRegex(verify_package.VerificationError, "ZIP root"):
            verify_package.verify_package(fixture.zip_path, fixture.manifest_path)

    def test_archive_rejects_member_below_a_regular_file(self) -> None:
        fixture = self.fixture()
        fixture.entries.extend(
            [
                ("collision", b"file\n", "file"),
                ("collision/child", b"nested\n", "file"),
            ]
        )
        fixture.write()
        with self.assertRaisesRegex(verify_package.VerificationError, "nested below non-directory"):
            verify_package.verify_package(fixture.zip_path, fixture.manifest_path)

    def test_all_six_public_product_version_surfaces_must_match(self) -> None:
        mutations = (
            ("skills/story/VERSION", None),
            (
                ".claude-plugin/marketplace.json",
                lambda document: document["metadata"].update(version="9.9.9"),
            ),
            (
                ".zcode-plugin/plugin.json",
                lambda document: document.update(version="9.9.9"),
            ),
            (
                ".codebuddy-plugin/plugin.json",
                lambda document: document.update(version="9.9.9"),
            ),
            (
                "reasonix-plugin.json",
                lambda document: document.update(version="9.9.9"),
            ),
            (
                "marketplace.json",
                lambda document: document["plugins"][0].update(version="9.9.9"),
            ),
        )
        for index, (relative, mutate) in enumerate(mutations):
            with self.subTest(surface=relative):
                fixture = PackageFixture(self.base / "version-{}".format(index))
                if mutate is None:
                    content = b"9.9.9\n"
                else:
                    current = next(
                        content
                        for name, content, _ in fixture.entries
                        if name == relative
                    )
                    document = json.loads(current.decode("utf-8"))
                    mutate(document)
                    content = json.dumps(document).encode("utf-8")
                fixture.replace(relative, content)
                with self.assertRaisesRegex(verify_package.VerificationError, "version surfaces disagree"):
                    verify_package.verify_package(fixture.zip_path, fixture.manifest_path)

    def test_missing_or_malformed_product_surface_fails(self) -> None:
        fixture = self.fixture()
        fixture.remove("reasonix-plugin.json")
        with self.assertRaisesRegex(verify_package.VerificationError, "missing required product"):
            verify_package.verify_package(fixture.zip_path, fixture.manifest_path)

        fixture = PackageFixture(self.base / "malformed")
        fixture.replace(".zcode-plugin/plugin.json", b"not json\n")
        with self.assertRaisesRegex(verify_package.VerificationError, "valid UTF-8 JSON"):
            verify_package.verify_package(fixture.zip_path, fixture.manifest_path)

    def test_archive_requires_exact_chinese_skill_inventory(self) -> None:
        fixture = self.fixture()
        fixture.remove("skills/story-review/SKILL.md")
        with self.assertRaisesRegex(
            verify_package.VerificationError,
            "exact canonical 18-skill Chinese inventory.*missing: story-review",
        ):
            verify_package.verify_package(fixture.zip_path, fixture.manifest_path)

        fixture = PackageFixture(self.base / "renamed-skill")
        fixture.remove("skills/story-review/SKILL.md")
        fixture.entries.append(
            (
                "skills/story-globalize/SKILL.md",
                b"---\nname: story-globalize\ndescription: overseas\n---\n",
                "file",
            )
        )
        fixture.write()
        with self.assertRaisesRegex(verify_package.VerificationError, "separate overseas tool"):
            verify_package.verify_package(fixture.zip_path, fixture.manifest_path)

        fixture = PackageFixture(self.base / "foreign-skill")
        fixture.entries.append(
            (
                "skills/not-oh-story/SKILL.md",
                b"---\nname: not-oh-story\ndescription: foreign\n---\n",
                "file",
            )
        )
        fixture.write()
        with self.assertRaisesRegex(
            verify_package.VerificationError,
            "exact canonical 18-skill Chinese inventory.*unexpected: not-oh-story",
        ):
            verify_package.verify_package(fixture.zip_path, fixture.manifest_path)

        fixture = PackageFixture(self.base / "frontmatter-impersonation")
        fixture.replace(
            "skills/story-review/SKILL.md",
            b"---\nname: story-globalize\ndescription: overseas\n---\n",
        )
        with self.assertRaisesRegex(verify_package.VerificationError, "skill name mismatch"):
            verify_package.verify_package(fixture.zip_path, fixture.manifest_path)

    def test_archive_rejects_relocated_story_globalize_path_segment(self) -> None:
        fixture = self.fixture()
        fixture.entries.append(
            ("vendor/Story-Globalize/SKILL.md", b"foreign\n", "file")
        )
        fixture.write()
        with self.assertRaisesRegex(verify_package.VerificationError, "separate overseas tool"):
            verify_package.verify_package(fixture.zip_path, fixture.manifest_path)

    def test_archive_rejects_local_deployment_dirs_but_keeps_plugin_manifests(self) -> None:
        valid = self.fixture()
        verify_package.verify_package(valid.zip_path, valid.manifest_path)

        for index, dirname in enumerate(
            sorted(verify_package.FORBIDDEN_DEPLOYMENT_DIR_NAMES)
        ):
            with self.subTest(dirname=dirname):
                fixture = PackageFixture(self.base / "local-dir-{}".format(index))
                fixture.entries.append(
                    ("nested/{}/local.txt".format(dirname), b"local\n", "file")
                )
                fixture.write()
                with self.assertRaisesRegex(
                    verify_package.VerificationError,
                    "forbidden local deployment directory",
                ):
                    verify_package.verify_package(
                        fixture.zip_path, fixture.manifest_path
                    )

    def test_skill_frontmatter_rejects_duplicate_quoted_and_complex_name_keys(self) -> None:
        cases = {
            "duplicate": b"name: story-review\nname: story-globalize",
            "quoted-key": b'name: story-review\n"name": story-globalize',
            "quoted-value": b'name: "story-review"',
            "tagged-key": b"name: story-review\n!!str name: story-globalize",
        }
        for index, (label, name_lines) in enumerate(cases.items()):
            with self.subTest(label=label):
                fixture = PackageFixture(self.base / "complex-name-{}".format(index))
                fixture.replace(
                    "skills/story-review/SKILL.md",
                    b"---\n" + name_lines + b"\ndescription: fixture\n---\n",
                )
                with self.assertRaisesRegex(
                    verify_package.VerificationError,
                    "frontmatter|simple unquoted",
                ):
                    verify_package.verify_package(
                        fixture.zip_path, fixture.manifest_path
                    )

    def test_product_manifest_identity_and_entrypoints_are_locked(self) -> None:
        manifest_paths = (
            ".claude-plugin/marketplace.json",
            ".zcode-plugin/plugin.json",
            ".codebuddy-plugin/plugin.json",
            "reasonix-plugin.json",
            "marketplace.json",
        )
        for index, relative in enumerate(manifest_paths):
            with self.subTest(relative=relative, field="name"):
                fixture = PackageFixture(self.base / "manifest-name-{}".format(index))
                current = next(
                    content
                    for name, content, _ in fixture.entries
                    if name == relative
                )
                document = json.loads(current.decode("utf-8"))
                document["name"] = "other-product"
                fixture.replace(relative, json.dumps(document).encode("utf-8"))
                with self.assertRaisesRegex(verify_package.VerificationError, "field 'name'"):
                    verify_package.verify_package(
                        fixture.zip_path, fixture.manifest_path
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
                fixture = PackageFixture(self.base / "manifest-route-{}".format(index))
                current = next(
                    content
                    for name, content, _ in fixture.entries
                    if name == relative
                )
                document = json.loads(current.decode("utf-8"))
                mutate(document)
                fixture.replace(relative, json.dumps(document).encode("utf-8"))
                with self.assertRaises(verify_package.VerificationError):
                    verify_package.verify_package(
                        fixture.zip_path, fixture.manifest_path
                    )

    def test_archive_requires_every_product_manifest(self) -> None:
        for index, relative in enumerate(verify_package.REQUIRED_PRODUCT_FILES):
            with self.subTest(relative=relative):
                fixture = PackageFixture(self.base / "missing-product-{}".format(index))
                fixture.remove(relative)
                with self.assertRaises(verify_package.VerificationError):
                    verify_package.verify_package(fixture.zip_path, fixture.manifest_path)

    def test_install_smoke_extracts_safely_runs_list_only_and_counts_skills(self) -> None:
        fixture = self.fixture()
        fixture.entries.append((".agents/skills", b"../skills", "symlink"))
        fixture.write()
        calls = []

        def fake_runner(command, **kwargs):
            calls.append((command, kwargs))
            extracted_root = Path(command[4])
            self.assertTrue(extracted_root.is_dir())
            self.assertTrue((extracted_root / ".agents/skills").is_symlink())
            self.assertEqual((extracted_root / ".agents/skills").readlink(), Path("../skills"))
            self.assertTrue((extracted_root / "skills/story/SKILL.md").is_file())
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="\x1b[?25l Found \x1b[32m18\x1b[0m skills\n",
                stderr="",
            )

        result = verify_package.verify_package(
            fixture.zip_path,
            install_smoke=True,
            runner=fake_runner,
        )

        self.assertEqual(result.discovered_skills, 18)
        self.assertEqual(len(calls), 1)
        command, kwargs = calls[0]
        self.assertEqual(command[:4], ["npx", "--yes", "skills@1.5.22", "add"])
        self.assertEqual(command[-1], "--list")
        self.assertNotIn("--global", command)
        self.assertNotIn("-g", command)
        self.assertEqual(kwargs["env"]["CI"], "1")
        self.assertEqual(kwargs["env"]["NO_COLOR"], "1")

    def test_install_smoke_rejects_wrong_count_and_command_failure(self) -> None:
        fixture = self.fixture()

        def wrong_count(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout="Found 16 skills\n", stderr="")

        with self.assertRaisesRegex(verify_package.VerificationError, "discovered 16 skills"):
            verify_package.verify_package(
                fixture.zip_path,
                install_smoke=True,
                runner=wrong_count,
            )

        def command_failure(command, **kwargs):
            return subprocess.CompletedProcess(command, 7, stdout="", stderr="network failed")

        with self.assertRaisesRegex(verify_package.VerificationError, "exit code 7"):
            verify_package.verify_package(
                fixture.zip_path,
                install_smoke=True,
                runner=command_failure,
            )

    def test_install_smoke_rejects_escaping_symlink_before_running_npx(self) -> None:
        fixture = self.fixture()
        fixture.entries.append(("links/escape", b"../../../outside", "symlink"))
        fixture.write()
        calls = []

        def should_not_run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, stdout="Found 18 skills\n", stderr="")

        with self.assertRaisesRegex(verify_package.VerificationError, "escapes archive_root"):
            verify_package.verify_package(
                fixture.zip_path,
                install_smoke=True,
                runner=should_not_run,
            )
        self.assertEqual(calls, [])

    def test_cli_supports_default_flag_and_positional_manifest_paths(self) -> None:
        fixture = self.fixture()
        for argv in (
            [str(fixture.zip_path)],
            [str(fixture.zip_path), str(fixture.manifest_path)],
            [str(fixture.zip_path), "--manifest", str(fixture.manifest_path)],
        ):
            with self.subTest(argv=argv):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    status = verify_package.main(argv)
                self.assertEqual(status, 0)
                self.assertIn("OK release package 1.2.3", stdout.getvalue())
                self.assertEqual(stderr.getvalue(), "")

    def test_cli_returns_one_for_verification_failure(self) -> None:
        fixture = self.fixture()
        fixture.manifest["checksums"][fixture.zip_path.name] = "0" * 64
        fixture.write_manifest()
        stdout = io.StringIO()
        stderr = io.StringIO()

        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = verify_package.main([str(fixture.zip_path)])

        self.assertEqual(status, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("FAIL: ZIP checksum mismatch", stderr.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
