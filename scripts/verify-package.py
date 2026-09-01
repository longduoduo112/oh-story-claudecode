#!/usr/bin/env python3
"""Verify an oh-story ZIP package without installing it globally."""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple
import unicodedata
import zipfile


PROJECT_NAME = "oh-story"
SCHEMA_VERSION = 1
SKILLS_CLI_VERSION = "1.5.22"
DEFAULT_EXPECTED_SKILLS = 18
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
    "skills/story/VERSION",
    ".claude-plugin/marketplace.json",
    ".zcode-plugin/plugin.json",
    ".codebuddy-plugin/plugin.json",
    "reasonix-plugin.json",
    "marketplace.json",
)
MAX_ARCHIVE_MEMBERS = 10_000
MAX_MEMBER_SIZE = 256 * 1024 * 1024
MAX_UNCOMPRESSED_SIZE = 1024 * 1024 * 1024

STABLE_VERSION_RE = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$"
)
DEV_VERSION_RE = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"-dev\.[0-9]{8}T[0-9]{6}Z\+g[0-9a-f]{7,64}$"
)
SOURCE_SHA_RE = re.compile(r"^[0-9a-f]{7,64}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
ANSI_ESCAPE_RE = re.compile(
    r"(?:\x1B\][^\x07]*(?:\x07|\x1B\\)|\x1B\[[0-?]*[ -/]*[@-~])"
)
FOUND_SKILLS_RE = re.compile(r"\bFound\s+([0-9]+)\s+skills?\b", re.IGNORECASE)
SKILL_NAME_RE = re.compile(r"^name:[ \t]*([a-z0-9][a-z0-9-]*)[ \t]*$")
SIMPLE_FRONTMATTER_KEY_RE = re.compile(
    r"^([A-Za-z][A-Za-z0-9_-]*):(?:[ \t]*(.*))?$"
)

EXCLUDED_EXTERNAL_PATH_SEGMENTS = frozenset({"story-globalize"})
FORBIDDEN_DEPLOYMENT_DIR_NAMES = frozenset(
    {
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
    }
)
CANONICAL_PLUGIN_DIR_NAMES = frozenset(
    {".claude-plugin", ".codebuddy-plugin", ".zcode-plugin"}
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


class VerificationError(RuntimeError):
    """A package failed a verification requirement."""


@dataclasses.dataclass(frozen=True)
class VerificationResult:
    version: str
    channel: str
    source_sha: str
    archive_root: str
    member_count: int
    discovered_skills: Optional[int] = None


@dataclasses.dataclass(frozen=True)
class ArchiveInspection:
    root: str
    members: Tuple[zipfile.ZipInfo, ...]
    by_name: Mapping[str, zipfile.ZipInfo]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise VerificationError("cannot read package {}: {}".format(path, exc)) from exc
    return digest.hexdigest()


def _default_manifest_path(zip_path: Path) -> Path:
    if zip_path.suffix.lower() != ".zip":
        raise VerificationError("package path must have a .zip suffix: {}".format(zip_path))
    return zip_path.with_suffix(".manifest.json")


def _load_manifest(path: Path) -> dict:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VerificationError("cannot read valid manifest JSON from {}: {}".format(path, exc)) from exc
    if not isinstance(document, dict):
        raise VerificationError("manifest must contain a JSON object")
    return document


def _safe_artifact_name(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise VerificationError("{} must be a non-empty string".format(label))
    if (
        value in (".", "..")
        or "/" in value
        or "\\" in value
        or WINDOWS_DRIVE_RE.match(value)
        or any(ord(character) < 32 for character in value)
    ):
        raise VerificationError("{} is not a safe artifact basename: {!r}".format(label, value))
    return value


def _validate_manifest(document: dict, zip_path: Path) -> dict:
    required = {
        "schema_version",
        "channel",
        "version",
        "source_sha",
        "archive_root",
        "files",
        "checksums",
    }
    missing = sorted(required.difference(document))
    if missing:
        raise VerificationError("manifest is missing required fields: {}".format(", ".join(missing)))

    schema_version = document["schema_version"]
    if isinstance(schema_version, bool) or schema_version != SCHEMA_VERSION:
        raise VerificationError(
            "unsupported manifest schema_version {!r}; expected {}".format(
                schema_version, SCHEMA_VERSION
            )
        )

    channel = document["channel"]
    if channel not in ("dev", "release"):
        raise VerificationError("manifest channel must be 'dev' or 'release'")

    version = document["version"]
    if not isinstance(version, str):
        raise VerificationError("manifest version must be a string")
    if channel == "release" and not STABLE_VERSION_RE.fullmatch(version):
        raise VerificationError("release manifest version must be a plain X.Y.Z value")
    if channel == "dev" and not DEV_VERSION_RE.fullmatch(version):
        raise VerificationError("dev manifest version does not match the build-package format")

    source_sha = document["source_sha"]
    if not isinstance(source_sha, str) or not SOURCE_SHA_RE.fullmatch(source_sha):
        raise VerificationError("manifest source_sha must be 7-64 lowercase hexadecimal characters")

    archive_root = document["archive_root"]
    expected_root = "{}-{}".format(PROJECT_NAME, version)
    if archive_root != expected_root:
        raise VerificationError(
            "manifest archive_root is {!r}, expected {!r}".format(archive_root, expected_root)
        )
    _safe_artifact_name(archive_root, "manifest archive_root")

    files = document["files"]
    if not isinstance(files, list) or not files:
        raise VerificationError("manifest files must be a non-empty array")
    file_records: Dict[str, dict] = {}
    folded_names: Dict[str, str] = {}
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            raise VerificationError("manifest files[{}] must be an object".format(index))
        name = _safe_artifact_name(item.get("name"), "manifest files[{}].name".format(index))
        folded = unicodedata.normalize("NFC", name).casefold()
        if name in file_records or folded in folded_names:
            raise VerificationError("manifest contains duplicate artifact name {!r}".format(name))
        artifact_format = item.get("format")
        if not isinstance(artifact_format, str) or not artifact_format:
            raise VerificationError("manifest files[{}].format must be a non-empty string".format(index))
        size = item.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise VerificationError("manifest files[{}].size must be a non-negative integer".format(index))
        file_records[name] = item
        folded_names[folded] = name

    checksums = document["checksums"]
    if not isinstance(checksums, dict) or not checksums:
        raise VerificationError("manifest checksums must be a non-empty object")
    checksum_records: Dict[str, str] = {}
    for raw_name, digest in checksums.items():
        name = _safe_artifact_name(raw_name, "manifest checksum name")
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise VerificationError("manifest checksum for {!r} is not lowercase SHA-256".format(name))
        checksum_records[name] = digest
    if set(file_records) != set(checksum_records):
        missing_checksums = sorted(set(file_records).difference(checksum_records))
        extra_checksums = sorted(set(checksum_records).difference(file_records))
        details = []
        if missing_checksums:
            details.append("missing checksums for {}".format(", ".join(missing_checksums)))
        if extra_checksums:
            details.append("checksums without file records for {}".format(", ".join(extra_checksums)))
        raise VerificationError("manifest files/checksums disagree: {}".format("; ".join(details)))

    zip_name = zip_path.name
    expected_zip_name = archive_root + ".zip"
    if zip_name != expected_zip_name:
        raise VerificationError(
            "ZIP basename is {!r}, expected {!r} from archive_root".format(
                zip_name, expected_zip_name
            )
        )
    zip_record = file_records.get(zip_name)
    if zip_record is None:
        raise VerificationError("manifest files does not list the selected ZIP {!r}".format(zip_name))
    if zip_record["format"] != "zip":
        raise VerificationError("manifest format for {!r} must be 'zip'".format(zip_name))
    try:
        actual_size = zip_path.stat().st_size
    except OSError as exc:
        raise VerificationError("cannot stat package {}: {}".format(zip_path, exc)) from exc
    if zip_record["size"] != actual_size:
        raise VerificationError(
            "ZIP size mismatch: manifest has {}, file has {}".format(
                zip_record["size"], actual_size
            )
        )
    actual_digest = _sha256(zip_path)
    if checksum_records[zip_name] != actual_digest:
        raise VerificationError(
            "ZIP checksum mismatch: manifest has {}, file has {}".format(
                checksum_records[zip_name], actual_digest
            )
        )

    return {
        "channel": channel,
        "version": version,
        "source_sha": source_sha,
        "archive_root": archive_root,
    }


def _normalise_member_name(raw_name: str) -> Tuple[str, Tuple[str, ...], bool]:
    if not raw_name:
        raise VerificationError("ZIP contains an empty member name")
    if "\x00" in raw_name or "\\" in raw_name or any(ord(character) < 32 for character in raw_name):
        raise VerificationError("ZIP contains an unsafe member path {!r}".format(raw_name))
    is_directory = raw_name.endswith("/")
    trimmed = raw_name[:-1] if is_directory else raw_name
    if not trimmed or raw_name.startswith("/"):
        raise VerificationError("ZIP contains an unsafe member path {!r}".format(raw_name))
    parts = tuple(trimmed.split("/"))
    if any(part in ("", ".", "..") for part in parts) or WINDOWS_DRIVE_RE.match(parts[0]):
        raise VerificationError("ZIP contains an unsafe member path {!r}".format(raw_name))
    normalised = PurePosixPath(*parts).as_posix()
    return normalised, parts, is_directory


def _entry_kind(info: zipfile.ZipInfo, is_directory: bool) -> str:
    if is_directory or info.is_dir():
        return "directory"
    if info.create_system != 3:
        return "file"
    mode = (info.external_attr >> 16) & 0xFFFF
    file_type = stat.S_IFMT(mode)
    if file_type == stat.S_IFLNK:
        return "symlink"
    if file_type in (0, stat.S_IFREG):
        return "file"
    raise VerificationError("ZIP contains unsupported file type at {!r}".format(info.filename))


def _inspect_archive(archive: zipfile.ZipFile, expected_root: str) -> ArchiveInspection:
    members = archive.infolist()
    if not members:
        raise VerificationError("ZIP archive is empty")
    if len(members) > MAX_ARCHIVE_MEMBERS:
        raise VerificationError(
            "ZIP has {} members, exceeding the safety limit {}".format(
                len(members), MAX_ARCHIVE_MEMBERS
            )
        )

    by_name: Dict[str, zipfile.ZipInfo] = {}
    member_kinds: Dict[str, str] = {}
    folded_paths: Dict[str, str] = {}
    roots = set()
    total_size = 0
    for info in members:
        normalised, parts, is_directory = _normalise_member_name(info.filename)
        policy_parts = tuple(
            unicodedata.normalize("NFC", part).casefold() for part in parts[1:]
        )
        if any(part in EXCLUDED_EXTERNAL_PATH_SEGMENTS for part in policy_parts):
            raise VerificationError(
                "ZIP contains story-globalize, a separate overseas tool: {!r}".format(
                    info.filename
                )
            )
        forbidden_deployment_parts = [
            part
            for part in policy_parts
            if part in FORBIDDEN_DEPLOYMENT_DIR_NAMES
            and part not in CANONICAL_PLUGIN_DIR_NAMES
        ]
        if forbidden_deployment_parts:
            raise VerificationError(
                "ZIP contains forbidden local deployment directory {!r}: {!r}".format(
                    forbidden_deployment_parts[0], info.filename
                )
            )
        kind = _entry_kind(info, is_directory)
        if info.flag_bits & 0x1:
            raise VerificationError("ZIP contains encrypted member {!r}".format(info.filename))
        if info.file_size < 0 or info.compress_size < 0 or info.file_size > MAX_MEMBER_SIZE:
            raise VerificationError("ZIP member {!r} exceeds the safety size limit".format(info.filename))
        total_size += info.file_size
        if total_size > MAX_UNCOMPRESSED_SIZE:
            raise VerificationError("ZIP uncompressed size exceeds the safety limit")

        folded = unicodedata.normalize("NFC", normalised).casefold()
        previous = folded_paths.get(folded)
        if normalised in by_name or previous is not None:
            detail = previous if previous is not None else normalised
            raise VerificationError(
                "ZIP contains duplicate or colliding member paths {!r} and {!r}".format(
                    detail, normalised
                )
            )
        by_name[normalised] = info
        member_kinds[normalised] = kind
        folded_paths[folded] = normalised
        roots.add(parts[0])
        if kind != "directory" and len(parts) < 2:
            raise VerificationError("ZIP root must be a directory, not member {!r}".format(info.filename))

    if len(roots) != 1:
        raise VerificationError(
            "ZIP must contain exactly one root directory; found {}".format(
                ", ".join(sorted(roots))
            )
        )
    actual_root = next(iter(roots))
    if actual_root != expected_root:
        raise VerificationError(
            "ZIP root is {!r}, but manifest archive_root is {!r}".format(
                actual_root, expected_root
            )
        )

    for name, kind in member_kinds.items():
        parts = name.split("/")
        for index in range(1, len(parts)):
            ancestor = "/".join(parts[:index])
            ancestor_kind = member_kinds.get(ancestor)
            if ancestor_kind is not None and ancestor_kind != "directory":
                raise VerificationError(
                    "ZIP member {!r} is nested below non-directory {!r}".format(
                        name, ancestor
                    )
                )

    try:
        corrupt_member = archive.testzip()
    except (NotImplementedError, OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise VerificationError("cannot fully read ZIP members: {}".format(exc)) from exc
    if corrupt_member is not None:
        raise VerificationError("ZIP member failed CRC validation: {!r}".format(corrupt_member))

    return ArchiveInspection(
        root=actual_root,
        members=tuple(members),
        by_name=by_name,
    )


def _read_required_file(
    archive: zipfile.ZipFile,
    inspection: ArchiveInspection,
    relative: str,
) -> bytes:
    name = "{}/{}".format(inspection.root, relative)
    info = inspection.by_name.get(name)
    if info is None:
        raise VerificationError("ZIP is missing required product version surface {}".format(relative))
    _, _, is_directory = _normalise_member_name(info.filename)
    if _entry_kind(info, is_directory) != "file":
        raise VerificationError("product version surface {} must be a regular file".format(relative))
    try:
        return archive.read(info)
    except (NotImplementedError, OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise VerificationError("cannot read ZIP member {}: {}".format(relative, exc)) from exc


def _load_json_surface(
    archive: zipfile.ZipFile,
    inspection: ArchiveInspection,
    relative: str,
) -> dict:
    raw = _read_required_file(archive, inspection, relative)
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise VerificationError("{} is not valid UTF-8 JSON: {}".format(relative, exc)) from exc
    if not isinstance(document, dict):
        raise VerificationError("{} must contain a JSON object".format(relative))
    return document


def _require_manifest_value(
    document: object,
    field: str,
    expected: object,
    label: str,
) -> None:
    if not isinstance(document, dict) or document.get(field) != expected:
        actual = document.get(field) if isinstance(document, dict) else None
        raise VerificationError(
            "{} field {!r} is {!r}, expected {!r}".format(
                label, field, actual, expected
            )
        )


def _verify_claude_plugin_inventory(document: object) -> None:
    label = ".claude-plugin/marketplace.json"
    _require_manifest_value(document, "name", CLAUDE_MARKETPLACE_NAME, label)
    if not isinstance(document, dict) or not isinstance(document.get("plugins"), list):
        raise VerificationError("{} has no plugins array".format(label))
    by_name = {}
    for plugin in document["plugins"]:
        if not isinstance(plugin, dict) or not isinstance(plugin.get("name"), str):
            raise VerificationError("{} contains a plugin without a valid name".format(label))
        name = plugin["name"]
        if name in by_name:
            raise VerificationError("{} contains duplicate plugin {!r}".format(label, name))
        by_name[name] = plugin
    if set(by_name) != CANONICAL_SKILL_NAMES:
        missing = sorted(CANONICAL_SKILL_NAMES.difference(by_name))
        unexpected = sorted(set(by_name).difference(CANONICAL_SKILL_NAMES))
        details = []
        if missing:
            details.append("missing: {}".format(", ".join(missing)))
        if unexpected:
            details.append("unexpected: {}".format(", ".join(unexpected)))
        raise VerificationError(
            "{} plugin names do not match the canonical 18-skill inventory ({})".format(
                label, "; ".join(details)
            )
        )
    for name, plugin in by_name.items():
        plugin_label = "{} plugin {!r}".format(label, name)
        _require_manifest_value(plugin, "source", "./", plugin_label)
        _require_manifest_value(
            plugin,
            "skills",
            ["./skills/{}".format(name)],
            plugin_label,
        )


def _verify_product_manifest_contracts(
    claude: dict,
    zcode: dict,
    codebuddy: dict,
    reasonix: dict,
    marketplace: dict,
) -> None:
    _verify_claude_plugin_inventory(claude)

    _require_manifest_value(zcode, "name", PROJECT_NAME, ".zcode-plugin/plugin.json")
    for field, expected in ZCODE_ENTRYPOINTS.items():
        _require_manifest_value(zcode, field, expected, ".zcode-plugin/plugin.json")

    _require_manifest_value(
        codebuddy, "name", PROJECT_NAME, ".codebuddy-plugin/plugin.json"
    )
    for field, expected in CODEBUDDY_ENTRYPOINTS.items():
        _require_manifest_value(
            codebuddy, field, expected, ".codebuddy-plugin/plugin.json"
        )

    _require_manifest_value(reasonix, "name", PROJECT_NAME, "reasonix-plugin.json")
    for field, expected in REASONIX_ENTRYPOINTS.items():
        _require_manifest_value(reasonix, field, expected, "reasonix-plugin.json")

    _require_manifest_value(
        marketplace, "name", ZCODE_MARKETPLACE_NAME, "marketplace.json"
    )
    _require_manifest_value(marketplace, "version", 1, "marketplace.json")
    plugins = marketplace.get("plugins")
    if not isinstance(plugins, list) or len(plugins) != 1:
        raise VerificationError(
            "marketplace.json must contain exactly one oh-story plugin"
        )
    plugin = plugins[0]
    _require_manifest_value(plugin, "name", PROJECT_NAME, "marketplace.json plugin")
    _require_manifest_value(plugin, "source", "./", "marketplace.json plugin")


def _verify_product_versions(
    archive: zipfile.ZipFile,
    inspection: ArchiveInspection,
    expected_version: str,
) -> None:
    try:
        version = _read_required_file(
            archive, inspection, "skills/story/VERSION"
        ).decode("utf-8").strip()
    except UnicodeError as exc:
        raise VerificationError("skills/story/VERSION is not UTF-8") from exc

    claude = _load_json_surface(archive, inspection, ".claude-plugin/marketplace.json")
    metadata = claude.get("metadata")
    claude_version = metadata.get("version") if isinstance(metadata, dict) else None

    zcode = _load_json_surface(archive, inspection, ".zcode-plugin/plugin.json")
    codebuddy = _load_json_surface(archive, inspection, ".codebuddy-plugin/plugin.json")
    reasonix = _load_json_surface(archive, inspection, "reasonix-plugin.json")
    marketplace = _load_json_surface(archive, inspection, "marketplace.json")
    plugins = marketplace.get("plugins")
    matches = []
    if isinstance(plugins, list):
        matches = [
            plugin
            for plugin in plugins
            if isinstance(plugin, dict) and plugin.get("name") == PROJECT_NAME
        ]
    if len(matches) != 1:
        raise VerificationError("marketplace.json must contain exactly one oh-story plugin")

    surfaces = {
        "skills/story/VERSION": version,
        ".claude-plugin/marketplace.json:metadata.version": claude_version,
        ".zcode-plugin/plugin.json:version": zcode.get("version"),
        ".codebuddy-plugin/plugin.json:version": codebuddy.get("version"),
        "marketplace.json:oh-story.version": matches[0].get("version"),
        "reasonix-plugin.json:version": reasonix.get("version"),
    }
    mismatches = {
        label: value for label, value in surfaces.items() if value != expected_version
    }
    if mismatches:
        details = ", ".join(
            "{}={!r}".format(label, value) for label, value in mismatches.items()
        )
        raise VerificationError(
            "product version surfaces disagree with manifest version {!r}: {}".format(
                expected_version, details
            )
        )
    _verify_product_manifest_contracts(
        claude,
        zcode,
        codebuddy,
        reasonix,
        marketplace,
    )


def _declared_skill_name(raw: bytes, relative: str) -> str:
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise VerificationError("canonical skill file is not UTF-8: {}".format(relative)) from exc
    if not lines or lines[0].strip() != "---":
        raise VerificationError("canonical skill file has no YAML frontmatter: {}".format(relative))
    try:
        closing = next(
            index
            for index in range(1, len(lines))
            if lines[index].strip() == "---"
        )
    except StopIteration as exc:
        raise VerificationError(
            "canonical skill file has unterminated YAML frontmatter: {}".format(relative)
        ) from exc

    names = []
    seen_keys = set()
    for line in lines[1:closing]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0] in " \t":
            continue
        key_match = SIMPLE_FRONTMATTER_KEY_RE.fullmatch(line)
        if key_match is None:
            raise VerificationError(
                "canonical skill frontmatter only permits simple unquoted top-level keys: {}".format(
                    relative
                )
            )
        key = key_match.group(1)
        if key in seen_keys:
            raise VerificationError(
                "canonical skill frontmatter contains duplicate key {!r}: {}".format(
                    key, relative
                )
            )
        seen_keys.add(key)
        if key == "name":
            name_match = SKILL_NAME_RE.fullmatch(line)
            if name_match is None:
                raise VerificationError(
                    "canonical skill name must use one simple unquoted value: {}".format(
                        relative
                    )
                )
            names.append(name_match.group(1))
    if len(names) != 1:
        raise VerificationError(
            "canonical skill file must declare exactly one simple name: {}".format(relative)
        )
    return names[0]


def _verify_chinese_product_inventory(
    archive: zipfile.ZipFile,
    inspection: ArchiveInspection,
) -> None:
    """Reject missing, renamed, or foreign skills before any install smoke."""

    skill_names = set()
    for member_name in inspection.by_name:
        relative = PurePosixPath(member_name).relative_to(inspection.root)
        if len(relative.parts) >= 2 and relative.parts[0] == "skills":
            skill_names.add(relative.parts[1])

    if "story-globalize" in skill_names:
        raise VerificationError(
            "ZIP contains story-globalize, a separate overseas tool that is not part "
            "of the Chinese oh-story package"
        )
    if skill_names != CANONICAL_SKILL_NAMES:
        missing = sorted(CANONICAL_SKILL_NAMES.difference(skill_names))
        unexpected = sorted(skill_names.difference(CANONICAL_SKILL_NAMES))
        details = []
        if missing:
            details.append("missing: {}".format(", ".join(missing)))
        if unexpected:
            details.append("unexpected: {}".format(", ".join(unexpected)))
        raise VerificationError(
            "ZIP does not contain the exact canonical 18-skill Chinese inventory ({})".format(
                "; ".join(details)
            )
        )

    declared_names = set()
    for skill_name in sorted(CANONICAL_SKILL_NAMES):
        relative = "skills/{}/SKILL.md".format(skill_name)
        raw = _read_required_file(archive, inspection, relative)
        declared_name = _declared_skill_name(raw, relative)
        declared_names.add(declared_name)
        if declared_name != skill_name:
            raise VerificationError(
                "canonical skill name mismatch at {}: declared {!r}".format(
                    relative, declared_name
                )
            )
    if declared_names != CANONICAL_SKILL_NAMES:
        raise VerificationError("canonical skill frontmatter names are not the exact Chinese inventory")
    for relative in REQUIRED_PRODUCT_FILES:
        _read_required_file(archive, inspection, relative)


def _safe_symlink_target(member_name: str, target: str, archive_root: str) -> None:
    if (
        not target
        or "\x00" in target
        or "\\" in target
        or target.startswith("/")
        or WINDOWS_DRIVE_RE.match(target)
        or any(ord(character) < 32 for character in target)
    ):
        raise VerificationError("ZIP symlink {!r} has unsafe target {!r}".format(member_name, target))
    member_relative = PurePosixPath(member_name).relative_to(archive_root)
    combined = posixpath.normpath((member_relative.parent / PurePosixPath(target)).as_posix())
    if combined in ("", ".", "..") or combined.startswith("../") or combined.startswith("/"):
        raise VerificationError("ZIP symlink {!r} escapes archive_root".format(member_name))


def _extract_safely(
    archive: zipfile.ZipFile,
    inspection: ArchiveInspection,
    destination: Path,
) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    directories: List[Tuple[zipfile.ZipInfo, Path]] = []
    files: List[Tuple[zipfile.ZipInfo, Path]] = []
    symlinks: List[Tuple[zipfile.ZipInfo, Path, str]] = []

    for info in inspection.members:
        normalised, parts, is_directory = _normalise_member_name(info.filename)
        output = destination.joinpath(*parts)
        kind = _entry_kind(info, is_directory)
        if kind == "directory":
            directories.append((info, output))
        elif kind == "file":
            files.append((info, output))
        else:
            try:
                target = archive.read(info).decode("utf-8")
            except (UnicodeError, NotImplementedError, OSError, RuntimeError, zipfile.BadZipFile) as exc:
                raise VerificationError("cannot read ZIP symlink {!r}: {}".format(normalised, exc)) from exc
            _safe_symlink_target(normalised, target, inspection.root)
            symlinks.append((info, output, target))

    for _, output in sorted(directories, key=lambda item: len(item[1].parts)):
        output.mkdir(parents=True, exist_ok=True)

    for info, output in files:
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            with archive.open(info, "r") as source, output.open("xb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
        except (FileExistsError, NotImplementedError, OSError, RuntimeError, zipfile.BadZipFile) as exc:
            raise VerificationError("cannot safely extract {!r}: {}".format(info.filename, exc)) from exc
        if info.create_system == 3:
            mode = (info.external_attr >> 16) & 0o777
            try:
                output.chmod(mode or 0o644)
            except OSError as exc:
                raise VerificationError("cannot set permissions on {!r}: {}".format(info.filename, exc)) from exc

    for info, output, target in symlinks:
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            output.symlink_to(target)
        except OSError as exc:
            raise VerificationError("cannot safely extract symlink {!r}: {}".format(info.filename, exc)) from exc

    extracted_root = destination / inspection.root
    if not extracted_root.is_dir():
        raise VerificationError("extracted archive_root is not a directory")
    return extracted_root


def _run_install_smoke(
    extracted_root: Path,
    expected_skills: int,
    *,
    runner: Optional[Callable[..., subprocess.CompletedProcess]] = None,
) -> int:
    if isinstance(expected_skills, bool) or not isinstance(expected_skills, int) or expected_skills <= 0:
        raise VerificationError("expected skill count must be a positive integer")
    command = [
        "npx",
        "--yes",
        "skills@{}".format(SKILLS_CLI_VERSION),
        "add",
        os.fspath(extracted_root),
        "--list",
    ]
    environment = os.environ.copy()
    environment.update({"CI": "1", "NO_COLOR": "1"})
    invoke = runner or subprocess.run
    try:
        completed = invoke(
            command,
            cwd=os.fspath(extracted_root.parent),
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=180,
        )
    except FileNotFoundError as exc:
        raise VerificationError("install smoke requires npx, but it was not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise VerificationError("install smoke timed out after 180 seconds") from exc
    except OSError as exc:
        raise VerificationError("cannot run install smoke: {}".format(exc)) from exc

    stdout = completed.stdout if isinstance(completed.stdout, str) else ""
    stderr = completed.stderr if isinstance(completed.stderr, str) else ""
    output = ANSI_ESCAPE_RE.sub("", "{}\n{}".format(stdout, stderr))
    if completed.returncode != 0:
        detail = output.strip()
        if len(detail) > 2000:
            detail = detail[-2000:]
        raise VerificationError(
            "install smoke failed with exit code {}{}".format(
                completed.returncode, ": " + detail if detail else ""
            )
        )

    matches = FOUND_SKILLS_RE.findall(output)
    if not matches:
        raise VerificationError("install smoke did not report a discovered skill count")
    discovered = int(matches[-1], 10)
    if discovered != expected_skills:
        raise VerificationError(
            "install smoke discovered {} skills, expected {}".format(
                discovered, expected_skills
            )
        )
    return discovered


def verify_package(
    zip_path: Path,
    manifest_path: Optional[Path] = None,
    *,
    install_smoke: bool = False,
    expected_skills: int = DEFAULT_EXPECTED_SKILLS,
    runner: Optional[Callable[..., subprocess.CompletedProcess]] = None,
) -> VerificationResult:
    """Verify manifest integrity, ZIP safety, versions, and optional discovery."""

    zip_path = Path(zip_path).resolve()
    if not zip_path.is_file():
        raise VerificationError("ZIP package does not exist or is not a file: {}".format(zip_path))
    manifest_path = (
        Path(manifest_path).resolve()
        if manifest_path is not None
        else _default_manifest_path(zip_path)
    )
    manifest = _load_manifest(manifest_path)
    metadata = _validate_manifest(manifest, zip_path)

    discovered_skills: Optional[int] = None
    try:
        archive_context = zipfile.ZipFile(zip_path, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise VerificationError("cannot open valid ZIP package {}: {}".format(zip_path, exc)) from exc

    with archive_context as archive:
        inspection = _inspect_archive(archive, metadata["archive_root"])
        _verify_chinese_product_inventory(archive, inspection)
        _verify_product_versions(archive, inspection, metadata["version"])
        if install_smoke:
            with tempfile.TemporaryDirectory(prefix="oh-story-install-smoke-") as temporary:
                extracted_root = _extract_safely(archive, inspection, Path(temporary))
                discovered_skills = _run_install_smoke(
                    extracted_root,
                    expected_skills,
                    runner=runner,
                )

    return VerificationResult(
        version=metadata["version"],
        channel=metadata["channel"],
        source_sha=metadata["source_sha"],
        archive_root=metadata["archive_root"],
        member_count=len(inspection.members),
        discovered_skills=discovered_skills,
    )


def _positive_integer(value: str) -> int:
    try:
        parsed = int(value, 10)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify an oh-story ZIP package and its build manifest."
    )
    parser.add_argument("zip_path", type=Path, help="versioned oh-story ZIP package")
    parser.add_argument(
        "manifest_path",
        nargs="?",
        type=Path,
        help="optional manifest path (default: ZIP basename with .manifest.json)",
    )
    parser.add_argument(
        "--manifest",
        dest="manifest_option",
        type=Path,
        help="explicit manifest path (alternative to the optional positional path)",
    )
    parser.add_argument(
        "--install-smoke",
        action="store_true",
        help="extract safely and use skills@1.5.22 --list to verify discovery",
    )
    parser.add_argument(
        "--expected-skills",
        type=_positive_integer,
        default=DEFAULT_EXPECTED_SKILLS,
        help="expected discovery count for --install-smoke (default: 18)",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.manifest_path is not None and args.manifest_option is not None:
        parser.error("manifest may be supplied either positionally or with --manifest, not both")
    manifest_path = args.manifest_option or args.manifest_path
    try:
        result = verify_package(
            args.zip_path,
            manifest_path,
            install_smoke=args.install_smoke,
            expected_skills=args.expected_skills,
        )
    except VerificationError as exc:
        print("FAIL: {}".format(exc), file=sys.stderr)
        return 1

    message = "OK {} package {}: {} safe ZIP members".format(
        result.channel, result.version, result.member_count
    )
    if result.discovered_skills is not None:
        message += ", {} skills discovered".format(result.discovered_skills)
    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
