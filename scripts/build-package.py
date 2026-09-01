#!/usr/bin/env python3
"""Build safe, deterministic oh-story development and release archives.

The source tree is never rewritten.  Development-only version changes are
applied to an in-memory copy of the files before the archives are created.
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import datetime as dt
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
import zipfile


PROJECT_NAME = "oh-story"
VERSION_FILE = PurePosixPath("skills/story/VERSION")
CANONICAL_SKILL_NAMES = frozenset(
    {
        "browser-cdp",
        "story",
        "story-cover",
        "story-data-analyze",
        "story-deslop",
        "story-explore",
        "story-import",
        "story-long-analyze",
        "story-long-scan",
        "story-long-write",
        "story-publish",
        "story-release-package",
        "story-research",
        "story-review",
        "story-setup",
        "story-short-analyze",
        "story-short-scan",
        "story-short-write",
    }
)
REQUIRED_PRODUCT_FILES = (
    VERSION_FILE.as_posix(),
    ".claude-plugin/marketplace.json",
    ".zcode-plugin/plugin.json",
    ".codebuddy-plugin/plugin.json",
    "reasonix-plugin.json",
    "marketplace.json",
)
STABLE_VERSION_RE = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$"
)
SKILL_NAME_RE = re.compile(r"^name:[ \t]*([a-z0-9][a-z0-9-]*)[ \t]*$")
SIMPLE_FRONTMATTER_KEY_RE = re.compile(
    r"^([A-Za-z][A-Za-z0-9_-]*):(?:[ \t]*(.*))?$"
)

# These exclusions are applied even to tracked files.  Release packages use
# the tracked Git inventory, but the second line of defence is intentional:
# accidentally committing a credential or generated tree must not put it in a
# distribution archive.
EXCLUDED_DIR_NAMES = frozenset(
    {
        ".git",
        ".claude",
        ".codebuddy",
        ".codex",
        ".codewhale",
        ".openclaw",
        ".opencode",
        ".reasonix",
        ".trae",
        ".zcode",
        ".omc",
        ".idea",
        ".vscode",
        ".vs",
        ".ssh",
        ".gnupg",
        ".aws",
        ".cache",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        ".venv",
        ".nyc_output",
        "__pycache__",
        "node_modules",
        "bower_components",
        "dist",
        "build",
        "test-results",
        "playwright-report",
        "coverage",
        "htmlcov",
        "tmp",
        "temp",
        "venv",
        "env",
        "_archive",
    }
)
EXCLUDED_FILE_NAMES = frozenset(
    {
        ".ds_store",
        "thumbs.db",
        "desktop.ini",
        ".npmrc",
        ".pypirc",
        ".netrc",
        ".coverage",
        "coverage.xml",
        "junit.xml",
        "agents.md",
        "claude.md",
        "skills-lock.json",
        "credentials.json",
        "secrets.json",
        "secrets.yml",
        "secrets.yaml",
        "id_rsa",
        "id_ed25519",
    }
)
EXCLUDED_SUFFIXES = (
    ".pyc",
    ".pyo",
    ".log",
    ".tmp",
    ".swp",
    ".swo",
    ".bak",
    ".orig",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".jks",
    ".keystore",
)

# `story-globalize` is a separately versioned overseas tool. The Chinese
# oh-story distribution must never absorb it, even if a maintainer temporarily
# places a checkout below this repository's skills/ directory while testing.
EXCLUDED_EXTERNAL_SKILL_PREFIXES = (
    ("skills", "story-globalize"),
)
EXCLUDED_EXTERNAL_PATH_SEGMENTS = frozenset(
    prefix[-1] for prefix in EXCLUDED_EXTERNAL_SKILL_PREFIXES
)

CLAUDE_MARKETPLACE_NAME = "oh-story-skills"
ZCODE_MARKETPLACE_NAME = "oh-story-zcode"
ZCODE_ENTRYPOINTS = {
    "skills": "skills",
    "commands": "skills/story-setup/references/zcode/commands",
    "hooks": "skills/story-setup/references/zcode/hooks/hooks.json",
}
CODEBUDDY_ENTRYPOINTS = {
    "skills": "./skills/",
    "agents": [
        "./skills/story-setup/references/workbuddy/agents/",
        "./skills/story-data-analyze/agents/workbuddy/",
    ],
    "hooks": "./skills/story-setup/references/workbuddy/hooks/hooks.json",
}
REASONIX_ENTRYPOINTS = {"skills": "skills"}


class BuildError(RuntimeError):
    """A packaging precondition failed."""


@dataclasses.dataclass
class SourceEntry:
    path: str
    data: bytes
    mode: int
    kind: str = "file"
    source_mtime: int = 0


@dataclasses.dataclass(frozen=True)
class BuildResult:
    channel: str
    version: str
    source_sha: str
    archive_root: str
    zip_path: Path
    tar_path: Path
    manifest_path: Path
    checksums_path: Path


def _run_git(root: Path, args: Sequence[str], *, check: bool = True) -> bytes:
    command = ["git", "-C", os.fspath(root)] + list(args)
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError as exc:
        raise BuildError("git is required for this operation but was not found") from exc
    if check and completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        raise BuildError("git command failed: {}".format(detail or "unknown error"))
    return completed.stdout


def _git_root(root: Path) -> Optional[Path]:
    try:
        raw = _run_git(root, ["rev-parse", "--show-toplevel"], check=False)
    except BuildError:
        return None
    if not raw:
        return None
    candidate = Path(os.fsdecode(raw.rstrip(b"\n"))).resolve()
    return candidate if candidate == root.resolve() else None


def _git_head(root: Path) -> Tuple[str, str]:
    full = _run_git(root, ["rev-parse", "HEAD^{commit}"]).decode("ascii").strip()
    short = _run_git(root, ["rev-parse", "--short=12", "HEAD^{commit}"]).decode(
        "ascii"
    ).strip()
    if not re.fullmatch(r"[0-9a-fA-F]{7,64}", full) or not re.fullmatch(
        r"[0-9a-fA-F]{7,64}", short
    ):
        raise BuildError("git returned an invalid source commit id")
    return full.lower(), short.lower()


def _relative_output(root: Path, output_dir: Path) -> Optional[PurePosixPath]:
    try:
        relative = output_dir.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    if not relative.parts:
        raise BuildError("the output directory cannot be the repository root")
    return PurePosixPath(*relative.parts)


def _git_is_dirty(root: Path, output_rel: Optional[PurePosixPath]) -> bool:
    args = ["status", "--porcelain=v1", "--untracked-files=all", "--", "."]
    if output_rel is not None:
        value = output_rel.as_posix()
        args.extend([":(exclude){}".format(value), ":(exclude){}/**".format(value)])
    return bool(_run_git(root, args).strip())


def _validate_release_git(
    root: Path,
    version: str,
    output_rel: Optional[PurePosixPath],
    *,
    verify_tag: bool,
    allow_dirty: bool,
) -> Tuple[Optional[str], Optional[str], bool]:
    git_root = _git_root(root)
    if git_root is None:
        if verify_tag:
            raise BuildError(
                "release tag verification requires the source root to be a Git repository; "
                "use --skip-tag-check only for an intentional source export"
            )
        if not allow_dirty:
            raise BuildError(
                "release cleanliness cannot be verified outside Git; pass --allow-dirty "
                "explicitly for an exported source tree"
            )
        return None, None, True

    full_sha, short_sha = _git_head(root)
    dirty = _git_is_dirty(root, output_rel)
    if dirty and not allow_dirty:
        raise BuildError(
            "release builds require a clean working tree (use --allow-dirty only "
            "for an intentional CI/source-export build)"
        )

    if verify_tag:
        tag = "v{}".format(version)
        tag_type = _run_git(
            root, ["cat-file", "-t", "refs/tags/{}".format(tag)], check=False
        ).decode("ascii", "replace").strip()
        if not tag_type:
            raise BuildError("required release tag {!r} does not exist".format(tag))
        if tag_type != "tag":
            raise BuildError(
                "release tag {} must be annotated (lightweight tags are not accepted)".format(
                    tag
                )
            )
        tag_sha_raw = _run_git(
            root, ["rev-parse", "--verify", "--quiet", "refs/tags/{}^{{commit}}".format(tag)], check=False
        )
        tag_sha = tag_sha_raw.decode("ascii", "replace").strip()
        if tag_sha.lower() != full_sha:
            raise BuildError(
                "release tag {} points to {}, not HEAD {}".format(
                    tag, tag_sha[:12], short_sha
                )
            )
    return full_sha, short_sha, dirty


def _validate_relative_path(value: str) -> PurePosixPath:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise BuildError("source path is not valid UTF-8: {!r}".format(value)) from exc
    path = PurePosixPath(value.replace(os.sep, "/"))
    if path.is_absolute() or not path.parts or any(part in ("", ".", "..") for part in path.parts):
        raise BuildError("unsafe source path: {!r}".format(value))
    return path


def _is_excluded(path: PurePosixPath, output_rel: Optional[PurePosixPath]) -> bool:
    lower_parts = tuple(part.lower() for part in path.parts)
    if any(part in EXCLUDED_EXTERNAL_PATH_SEGMENTS for part in lower_parts):
        raise BuildError(
            "source inventory contains story-globalize, a separate overseas tool: {}".format(
                path
            )
        )
    if output_rel is not None:
        output_parts = tuple(part.lower() for part in output_rel.parts)
        if lower_parts[: len(output_parts)] == output_parts:
            return True
    if any(part in EXCLUDED_DIR_NAMES for part in lower_parts[:-1]):
        return True
    # A path reported as a directory symlink still needs its own basename check.
    if lower_parts[-1] in EXCLUDED_DIR_NAMES:
        return True

    name = lower_parts[-1]
    if name in EXCLUDED_FILE_NAMES:
        return True
    if name == ".env" or name.startswith(".env."):
        return True
    if name.startswith("service-account") and name.endswith(".json"):
        return True
    if name.endswith(".local") or any(name.endswith(suffix) for suffix in EXCLUDED_SUFFIXES):
        return True
    if name.endswith("~"):
        return True
    # docs/plans is a repository-local planning area in this project.
    if len(lower_parts) >= 2 and lower_parts[:2] == ("docs", "plans"):
        return True
    return False


def _git_inventory(root: Path, channel: str) -> List[str]:
    args = ["ls-files", "-z", "--cached"]
    if channel == "dev":
        args.extend(["--others", "--exclude-standard"])
    raw = _run_git(root, args)
    values = []
    for item in raw.split(b"\0"):
        if not item:
            continue
        values.append(os.fsdecode(item))
    return values


def _filesystem_inventory(root: Path) -> List[str]:
    values: List[str] = []
    for current, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        relative_current = current_path.relative_to(root)

        kept_dirs = []
        for dirname in sorted(dirnames, key=lambda value: value.encode("utf-8")):
            absolute = current_path / dirname
            relative = PurePosixPath(*(relative_current / dirname).parts)
            if absolute.is_symlink():
                values.append(relative.as_posix())
            else:
                kept_dirs.append(dirname)
        dirnames[:] = kept_dirs

        for filename in sorted(filenames, key=lambda value: value.encode("utf-8")):
            relative = PurePosixPath(*(relative_current / filename).parts)
            values.append(relative.as_posix())
    return values


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _read_entry(root: Path, relative: PurePosixPath) -> Optional[SourceEntry]:
    absolute = root.joinpath(*relative.parts)
    try:
        metadata = absolute.lstat()
    except FileNotFoundError:
        # A deleted tracked file is relevant to dev builds but has no content.
        return None

    if stat.S_ISLNK(metadata.st_mode):
        target = os.readlink(os.fspath(absolute))
        if os.path.isabs(target):
            raise BuildError("absolute symlink is not package-safe: {} -> {}".format(relative, target))
        resolved = (absolute.parent / target).resolve(strict=False)
        if not _path_is_within(resolved, root.resolve()):
            raise BuildError("symlink escapes the source tree: {} -> {}".format(relative, target))
        return SourceEntry(
            path=relative.as_posix(),
            data=os.fsencode(target),
            mode=0o777,
            kind="symlink",
            source_mtime=int(metadata.st_mtime),
        )
    if not stat.S_ISREG(metadata.st_mode):
        raise BuildError("unsupported source file type: {}".format(relative))

    mode = 0o755 if metadata.st_mode & 0o111 else 0o644
    try:
        data = absolute.read_bytes()
    except OSError as exc:
        raise BuildError("unable to read source file {}: {}".format(relative, exc)) from exc
    return SourceEntry(
        path=relative.as_posix(),
        data=data,
        mode=mode,
        kind="file",
        source_mtime=int(metadata.st_mtime),
    )


def _collect_entries(
    root: Path,
    channel: str,
    output_rel: Optional[PurePosixPath],
    git_available: bool,
) -> List[SourceEntry]:
    candidates = _git_inventory(root, channel) if git_available else _filesystem_inventory(root)
    entries: List[SourceEntry] = []
    seen = set()
    for value in candidates:
        relative = _validate_relative_path(value)
        if _is_excluded(relative, output_rel):
            continue
        key = relative.as_posix()
        if key in seen:
            continue
        seen.add(key)
        entry = _read_entry(root, relative)
        if entry is not None:
            entries.append(entry)
    entries.sort(key=lambda entry: entry.path.encode("utf-8"))
    if not any(entry.path == VERSION_FILE.as_posix() for entry in entries):
        raise BuildError("{} must be included in the source inventory".format(VERSION_FILE))
    return entries


def _read_source_version(root: Path, channel: str) -> str:
    path = root.joinpath(*VERSION_FILE.parts)
    try:
        version = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise BuildError("unable to read {}: {}".format(VERSION_FILE, exc)) from exc
    if not STABLE_VERSION_RE.fullmatch(version):
        if channel == "release":
            raise BuildError("release VERSION must be a plain X.Y.Z value, got {!r}".format(version))
        raise BuildError("source VERSION must be a plain X.Y.Z value, got {!r}".format(version))
    return version


def _inventory_difference(actual: set[str]) -> str:
    missing = sorted(CANONICAL_SKILL_NAMES.difference(actual))
    unexpected = sorted(actual.difference(CANONICAL_SKILL_NAMES))
    details = []
    if missing:
        details.append("missing: {}".format(", ".join(missing)))
    if unexpected:
        details.append("unexpected: {}".format(", ".join(unexpected)))
    return "; ".join(details)


def _declared_skill_name(data: bytes, relative: str) -> str:
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise BuildError("canonical skill file is not UTF-8: {}".format(relative)) from exc
    if not lines or lines[0].strip() != "---":
        raise BuildError("canonical skill file has no YAML frontmatter: {}".format(relative))
    try:
        closing = next(index for index in range(1, len(lines)) if lines[index].strip() == "---")
    except StopIteration as exc:
        raise BuildError("canonical skill file has unterminated YAML frontmatter: {}".format(relative)) from exc
    names = []
    seen_keys = set()
    for line in lines[1:closing]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0] in " \t":
            continue
        key_match = SIMPLE_FRONTMATTER_KEY_RE.fullmatch(line)
        if key_match is None:
            raise BuildError(
                "canonical skill frontmatter only permits simple unquoted top-level keys: {}".format(
                    relative
                )
            )
        key = key_match.group(1)
        if key in seen_keys:
            raise BuildError(
                "canonical skill frontmatter contains duplicate key {!r}: {}".format(
                    key, relative
                )
            )
        seen_keys.add(key)
        if key == "name":
            name_match = SKILL_NAME_RE.fullmatch(line)
            if name_match is None:
                raise BuildError(
                    "canonical skill name must use one simple unquoted value: {}".format(
                        relative
                    )
                )
            names.append(name_match.group(1))
    if len(names) != 1:
        raise BuildError("canonical skill file must declare exactly one simple name: {}".format(relative))
    return names[0]


def _validate_source_root(root: Path) -> None:
    """Fail closed unless *root* is the canonical Chinese product source tree."""

    skills_root = root / "skills"
    try:
        skills_metadata = skills_root.lstat()
    except OSError as exc:
        raise BuildError("source root has no readable skills directory: {}".format(exc)) from exc
    if not stat.S_ISDIR(skills_metadata.st_mode):
        raise BuildError("source root skills path must be a real directory")

    try:
        children = list(skills_root.iterdir())
    except OSError as exc:
        raise BuildError("cannot inspect source skill inventory: {}".format(exc)) from exc
    actual = {
        child.name
        for child in children
        if (child.is_dir() or child.is_symlink())
        and child.name.casefold() not in EXCLUDED_DIR_NAMES
    }
    if "story-globalize" in actual:
        raise BuildError(
            "source root contains story-globalize, a separate overseas tool that must "
            "not be absorbed into the Chinese oh-story package"
        )
    if actual != CANONICAL_SKILL_NAMES:
        raise BuildError(
            "source root does not have the exact canonical 18-skill Chinese inventory ({})".format(
                _inventory_difference(actual)
            )
        )

    for skill_name in sorted(CANONICAL_SKILL_NAMES):
        skill_dir = skills_root / skill_name
        skill_file = skill_dir / "SKILL.md"
        try:
            directory_metadata = skill_dir.lstat()
            file_metadata = skill_file.lstat()
        except OSError as exc:
            raise BuildError(
                "canonical skill {} is incomplete: {}".format(skill_name, exc)
            ) from exc
        if not stat.S_ISDIR(directory_metadata.st_mode):
            raise BuildError("canonical skill directory must not be a symlink: {}".format(skill_name))
        if not stat.S_ISREG(file_metadata.st_mode):
            raise BuildError("canonical skill SKILL.md must be a regular file: {}".format(skill_name))
        try:
            declared_name = _declared_skill_name(
                skill_file.read_bytes(),
                "skills/{}/SKILL.md".format(skill_name),
            )
        except OSError as exc:
            raise BuildError("cannot read canonical skill {}: {}".format(skill_name, exc)) from exc
        if declared_name != skill_name:
            raise BuildError(
                "canonical skill name mismatch at skills/{}/SKILL.md: declared {!r}".format(
                    skill_name, declared_name
                )
            )

    for relative in REQUIRED_PRODUCT_FILES:
        path = root.joinpath(*PurePosixPath(relative).parts)
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise BuildError("source root is missing required product file {}: {}".format(relative, exc)) from exc
        if not stat.S_ISREG(metadata.st_mode):
            raise BuildError("required product file must be a regular file: {}".format(relative))


def _validate_entry_inventory(entries: Sequence[SourceEntry]) -> None:
    """Repeat identity checks against the actual Git/filesystem package inventory."""

    by_path = {entry.path: entry for entry in entries}
    actual = {
        parts[1]
        for entry in entries
        for parts in (PurePosixPath(entry.path).parts,)
        if len(parts) >= 2 and parts[0] == "skills"
    }
    if actual != CANONICAL_SKILL_NAMES:
        raise BuildError(
            "package inventory does not have the exact canonical 18-skill Chinese inventory ({})".format(
                _inventory_difference(actual)
            )
        )
    for skill_name in sorted(CANONICAL_SKILL_NAMES):
        relative = "skills/{}/SKILL.md".format(skill_name)
        entry = by_path.get(relative)
        if entry is None or entry.kind != "file":
            raise BuildError("package inventory is missing regular canonical skill file {}".format(relative))
        declared_name = _declared_skill_name(entry.data, relative)
        if declared_name != skill_name:
            raise BuildError(
                "package canonical skill name mismatch at {}: declared {!r}".format(
                    relative, declared_name
                )
            )
    for relative in REQUIRED_PRODUCT_FILES:
        entry = by_path.get(relative)
        if entry is None or entry.kind != "file":
            raise BuildError("package inventory is missing required product file {}".format(relative))


def _load_json_entry(entries: Dict[str, SourceEntry], path: str) -> Optional[object]:
    entry = entries.get(path)
    if entry is None:
        return None
    if entry.kind != "file":
        raise BuildError("product manifest is not a regular file: {}".format(path))
    try:
        return json.loads(entry.data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BuildError("invalid JSON product manifest {}: {}".format(path, exc)) from exc


def _require_product_version(current: object, source_version: str, label: str) -> None:
    if current != source_version:
        raise BuildError(
            "{} is {!r}, expected canonical product version {!r}".format(
                label, current, source_version
            )
        )


def _require_manifest_value(
    document: object,
    field: str,
    expected: object,
    label: str,
) -> None:
    if not isinstance(document, dict) or document.get(field) != expected:
        actual = document.get(field) if isinstance(document, dict) else None
        raise BuildError(
            "{} field {!r} is {!r}, expected {!r}".format(
                label, field, actual, expected
            )
        )


def _validate_claude_plugin_inventory(document: object) -> None:
    label = ".claude-plugin/marketplace.json"
    _require_manifest_value(document, "name", CLAUDE_MARKETPLACE_NAME, label)
    if not isinstance(document, dict) or not isinstance(document.get("plugins"), list):
        raise BuildError("{} has no plugins array".format(label))
    plugins = document["plugins"]
    by_name = {}
    for plugin in plugins:
        if not isinstance(plugin, dict) or not isinstance(plugin.get("name"), str):
            raise BuildError("{} contains a plugin without a valid name".format(label))
        name = plugin["name"]
        if name in by_name:
            raise BuildError("{} contains duplicate plugin {!r}".format(label, name))
        by_name[name] = plugin
    if set(by_name) != CANONICAL_SKILL_NAMES:
        raise BuildError(
            "{} plugin names do not match the canonical 18-skill inventory ({})".format(
                label, _inventory_difference(set(by_name))
            )
        )
    for name, plugin in by_name.items():
        _require_manifest_value(plugin, "source", "./", "{} plugin {!r}".format(label, name))
        _require_manifest_value(
            plugin,
            "skills",
            ["./skills/{}".format(name)],
            "{} plugin {!r}".format(label, name),
        )


def _rewrite_product_versions(
    entries: List[SourceEntry], source_version: str, package_version: str
) -> List[SourceEntry]:
    packaged = [copy.copy(entry) for entry in entries]
    by_path = {entry.path: entry for entry in packaged}

    version_entry = by_path[VERSION_FILE.as_posix()]
    if version_entry.kind != "file":
        raise BuildError("{} must be a regular file".format(VERSION_FILE))
    try:
        inventory_version = version_entry.data.decode("utf-8").strip()
    except UnicodeError as exc:
        raise BuildError("{} is not UTF-8".format(VERSION_FILE)) from exc
    _require_product_version(inventory_version, source_version, str(VERSION_FILE))
    version_entry.data = (package_version + "\n").encode("utf-8")

    def update_json(path: str, updater) -> None:
        document = _load_json_entry(by_path, path)
        if document is None:
            return
        changed = updater(document)
        if changed:
            by_path[path].data = (
                json.dumps(document, ensure_ascii=False, indent=2) + "\n"
            ).encode("utf-8")

    def update_top_version(document: object, label: str) -> bool:
        if not isinstance(document, dict) or "version" not in document:
            raise BuildError("{} has no top-level version field".format(label))
        _require_product_version(document["version"], source_version, label + " version")
        document["version"] = package_version
        return package_version != source_version

    def update_zcode(document: object) -> bool:
        label = ".zcode-plugin/plugin.json"
        changed = update_top_version(document, label)
        _require_manifest_value(document, "name", PROJECT_NAME, label)
        for field, expected in ZCODE_ENTRYPOINTS.items():
            _require_manifest_value(document, field, expected, label)
        return changed

    update_json(".zcode-plugin/plugin.json", update_zcode)

    def update_codebuddy(document: object) -> bool:
        label = ".codebuddy-plugin/plugin.json"
        changed = update_top_version(document, label)
        _require_manifest_value(document, "name", PROJECT_NAME, label)
        for field, expected in CODEBUDDY_ENTRYPOINTS.items():
            _require_manifest_value(document, field, expected, label)
        return changed

    update_json(".codebuddy-plugin/plugin.json", update_codebuddy)

    def update_reasonix(document: object) -> bool:
        label = "reasonix-plugin.json"
        changed = update_top_version(document, label)
        _require_manifest_value(document, "name", PROJECT_NAME, label)
        for field, expected in REASONIX_ENTRYPOINTS.items():
            _require_manifest_value(document, field, expected, label)
        return changed

    update_json("reasonix-plugin.json", update_reasonix)

    def update_claude_marketplace(document: object) -> bool:
        if not isinstance(document, dict) or not isinstance(document.get("metadata"), dict):
            raise BuildError(".claude-plugin/marketplace.json has no metadata object")
        metadata = document["metadata"]
        if "version" not in metadata:
            raise BuildError(".claude-plugin/marketplace.json has no metadata.version")
        _require_product_version(
            metadata["version"], source_version, ".claude-plugin/marketplace.json metadata.version"
        )
        _validate_claude_plugin_inventory(document)
        metadata["version"] = package_version
        return package_version != source_version

    update_json(".claude-plugin/marketplace.json", update_claude_marketplace)

    def update_marketplace(document: object) -> bool:
        _require_manifest_value(document, "name", ZCODE_MARKETPLACE_NAME, "marketplace.json")
        _require_manifest_value(document, "version", 1, "marketplace.json")
        if not isinstance(document, dict) or not isinstance(document.get("plugins"), list):
            raise BuildError("marketplace.json has no plugins array")
        matches = [
            plugin
            for plugin in document["plugins"]
            if isinstance(plugin, dict) and plugin.get("name") == PROJECT_NAME
        ]
        if len(matches) != 1:
            raise BuildError("marketplace.json must contain exactly one oh-story plugin")
        if len(document["plugins"]) != 1:
            raise BuildError("marketplace.json must not contain non-oh-story plugins")
        plugin = matches[0]
        _require_manifest_value(plugin, "source", "./", "marketplace.json oh-story plugin")
        _require_product_version(
            plugin.get("version"), source_version, "marketplace.json oh-story version"
        )
        plugin["version"] = package_version
        return package_version != source_version

    update_json("marketplace.json", update_marketplace)

    def update_package_json(document: object) -> bool:
        if not isinstance(document, dict):
            raise BuildError("package.json must contain a JSON object")
        name = document.get("name")
        is_product_package = name == PROJECT_NAME or (
            isinstance(name, str) and name.endswith("/" + PROJECT_NAME)
        )
        if not is_product_package or "version" not in document:
            return False
        _require_product_version(document["version"], source_version, "package.json version")
        document["version"] = package_version
        return package_version != source_version

    update_json("package.json", update_package_json)
    return packaged


def _content_fingerprint(entries: Iterable[SourceEntry]) -> str:
    digest = hashlib.sha256()
    for entry in entries:
        digest.update(entry.path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(entry.kind.encode("ascii"))
        digest.update(b"\0")
        digest.update("{:o}".format(entry.mode).encode("ascii"))
        digest.update(b"\0")
        digest.update(entry.data)
        digest.update(b"\0")
    return digest.hexdigest()


def _contract_versions(entries: Sequence[SourceEntry]) -> dict:
    by_path = {entry.path: entry for entry in entries}
    contract = _load_json_entry(by_path, "scripts/current-contract.json")
    if contract is None:
        return {}
    if not isinstance(contract, dict):
        raise BuildError("scripts/current-contract.json must contain a JSON object")
    setup_version = contract.get("setup_skill_version")
    agents_version = contract.get("agents_version")
    if not isinstance(setup_version, str) or not setup_version:
        raise BuildError("current-contract setup_skill_version must be a non-empty string")
    if not isinstance(agents_version, int) or isinstance(agents_version, bool):
        raise BuildError("current-contract agents_version must be an integer")
    return {
        "setup_skill_version": setup_version,
        "agents_version": agents_version,
    }


def _source_date_epoch(
    explicit: Optional[int], git_root: Optional[Path], entries: Sequence[SourceEntry]
) -> int:
    if explicit is not None:
        value = explicit
    elif "SOURCE_DATE_EPOCH" in os.environ:
        raw = os.environ["SOURCE_DATE_EPOCH"]
        try:
            value = int(raw, 10)
        except ValueError as exc:
            raise BuildError("SOURCE_DATE_EPOCH must be a non-negative integer") from exc
    elif git_root is not None:
        raw = _run_git(git_root, ["show", "-s", "--format=%ct", "HEAD"]).decode(
            "ascii", "replace"
        ).strip()
        try:
            value = int(raw, 10)
        except ValueError as exc:
            raise BuildError("git returned an invalid commit timestamp") from exc
    else:
        value = max((entry.source_mtime for entry in entries), default=int(time.time()))
    if value < 0 or value > 253402300799:
        raise BuildError("source date epoch is outside the supported UTC range")
    return value


def _utc_label(epoch: int) -> str:
    return dt.datetime.fromtimestamp(epoch, tz=dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _zip_datetime(epoch: int) -> Tuple[int, int, int, int, int, int]:
    minimum = int(dt.datetime(1980, 1, 1, tzinfo=dt.timezone.utc).timestamp())
    maximum = int(dt.datetime(2107, 12, 31, 23, 59, 58, tzinfo=dt.timezone.utc).timestamp())
    value = min(max(epoch, minimum), maximum)
    current = dt.datetime.fromtimestamp(value, tz=dt.timezone.utc)
    return (current.year, current.month, current.day, current.hour, current.minute, current.second)


def _archive_name(root_name: str, relative: str) -> str:
    path = _validate_relative_path(relative)
    return PurePosixPath(root_name, path).as_posix()


def _write_zip(path: Path, root_name: str, entries: Sequence[SourceEntry], epoch: int) -> None:
    timestamp = _zip_datetime(epoch)
    with zipfile.ZipFile(
        path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9, strict_timestamps=True
    ) as archive:
        for entry in entries:
            info = zipfile.ZipInfo(_archive_name(root_name, entry.path), date_time=timestamp)
            info.create_system = 3
            info.flag_bits |= 0x800
            if entry.kind == "symlink":
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
            else:
                info.external_attr = (stat.S_IFREG | entry.mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, entry.data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def _write_tar_gz(path: Path, root_name: str, entries: Sequence[SourceEntry], epoch: int) -> None:
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=epoch) as zipped:
            with tarfile.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for entry in entries:
                    info = tarfile.TarInfo(_archive_name(root_name, entry.path))
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mtime = epoch
                    info.mode = entry.mode
                    info.pax_headers = {}
                    if entry.kind == "symlink":
                        info.type = tarfile.SYMTYPE
                        info.linkname = os.fsdecode(entry.data)
                        info.size = 0
                        archive.addfile(info)
                    else:
                        info.type = tarfile.REGTYPE
                        info.size = len(entry.data)
                        archive.addfile(info, io.BytesIO(entry.data))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_package(
    *,
    root: Path,
    output_dir: Path,
    channel: str,
    source_date_epoch: Optional[int] = None,
    verify_tag: bool = True,
    allow_dirty: bool = False,
) -> BuildResult:
    """Build both archive formats and return their output paths."""

    if channel not in ("dev", "release"):
        raise BuildError("channel must be 'dev' or 'release'")
    root = root.resolve()
    output_dir = output_dir.resolve()
    if not root.is_dir():
        raise BuildError("source root is not a directory: {}".format(root))
    output_rel = _relative_output(root, output_dir)

    _validate_source_root(root)
    source_version = _read_source_version(root, channel)
    git_root = _git_root(root)
    full_sha: Optional[str] = None
    short_sha: Optional[str] = None
    dirty = False
    if channel == "release":
        full_sha, short_sha, dirty = _validate_release_git(
            root,
            source_version,
            output_rel,
            verify_tag=verify_tag,
            allow_dirty=allow_dirty,
        )
    elif git_root is not None:
        full_sha, short_sha = _git_head(root)
        dirty = _git_is_dirty(root, output_rel)

    entries = _collect_entries(root, channel, output_rel, git_root is not None)
    _validate_entry_inventory(entries)
    if full_sha is None or short_sha is None:
        full_sha = _content_fingerprint(entries)
        short_sha = full_sha[:12]

    epoch = _source_date_epoch(source_date_epoch, git_root, entries)
    if channel == "dev":
        package_version = "{}-dev.{}+g{}".format(
            source_version, _utc_label(epoch), short_sha
        )
    else:
        package_version = source_version

    source_content_sha256 = _content_fingerprint(entries)
    packaged_entries = _rewrite_product_versions(entries, source_version, package_version)
    payload_content_sha256 = _content_fingerprint(packaged_entries)
    contract_versions = _contract_versions(entries)
    archive_root = "{}-{}".format(PROJECT_NAME, package_version)
    zip_name = archive_root + ".zip"
    tar_name = archive_root + ".tar.gz"
    manifest_name = archive_root + ".manifest.json"

    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".build-package-", dir=os.fspath(output_dir)) as temporary:
        staging = Path(temporary)
        zip_path = staging / zip_name
        tar_path = staging / tar_name
        manifest_path = staging / manifest_name
        sums_path = staging / "SHA256SUMS"

        _write_zip(zip_path, archive_root, packaged_entries, epoch)
        _write_tar_gz(tar_path, archive_root, packaged_entries, epoch)

        checksums = {
            zip_name: _sha256(zip_path),
            tar_name: _sha256(tar_path),
        }
        files = [
            {"name": zip_name, "format": "zip", "size": zip_path.stat().st_size},
            {"name": tar_name, "format": "tar.gz", "size": tar_path.stat().st_size},
        ]
        manifest = {
            "schema_version": 1,
            "channel": channel,
            "version": package_version,
            "source_sha": full_sha,
            "source_short_sha": short_sha,
            "source_dirty": dirty,
            "source_content_sha256": source_content_sha256,
            "payload_content_sha256": payload_content_sha256,
            "source_date_epoch": epoch,
            "created_at": dt.datetime.fromtimestamp(epoch, tz=dt.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "archive_root": archive_root,
            "contract_versions": contract_versions,
            "files": files,
            "checksums": checksums,
        }
        with manifest_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        with sums_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(
                "".join(
                    "{}  {}\n".format(checksums[name], name) for name in sorted(checksums)
                )
            )

        final_paths = {
            zip_path: output_dir / zip_name,
            tar_path: output_dir / tar_name,
            manifest_path: output_dir / manifest_name,
            sums_path: output_dir / "SHA256SUMS",
        }
        for source, destination in final_paths.items():
            os.replace(os.fspath(source), os.fspath(destination))

    return BuildResult(
        channel=channel,
        version=package_version,
        source_sha=full_sha,
        archive_root=archive_root,
        zip_path=output_dir / zip_name,
        tar_path=output_dir / tar_name,
        manifest_path=output_dir / manifest_name,
        checksums_path=output_dir / "SHA256SUMS",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build reproducible oh-story .zip and .tar.gz distributions."
    )
    parser.add_argument("channel", choices=("dev", "release"))
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="source repository root (default: parent of scripts/)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="artifact directory (default: ROOT/dist)",
    )
    parser.add_argument(
        "--source-date-epoch",
        type=int,
        help="fixed archive/build timestamp; also supports SOURCE_DATE_EPOCH",
    )
    parser.add_argument(
        "--skip-tag-check",
        action="store_true",
        help="release only: do not require vX.Y.Z to point at HEAD",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="release only: explicitly permit a dirty or exported source tree",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    root = args.root.resolve()
    output_dir = (args.output_dir or (root / "dist")).resolve()
    try:
        result = build_package(
            root=root,
            output_dir=output_dir,
            channel=args.channel,
            source_date_epoch=args.source_date_epoch,
            verify_tag=not args.skip_tag_check,
            allow_dirty=args.allow_dirty,
        )
    except BuildError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 1

    print("built {} package {}".format(result.channel, result.version))
    for path in (
        result.zip_path,
        result.tar_path,
        result.manifest_path,
        result.checksums_path,
    ):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
