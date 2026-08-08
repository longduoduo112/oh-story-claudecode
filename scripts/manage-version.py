#!/usr/bin/env python3
"""Check or update the public oh-story package version surfaces.

The product version is intentionally separate from story-setup's schema and
agents bundle versions.  This script owns only the five public package version
fields used by installers and marketplaces.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


STABLE_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read valid JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object in {path}")
    return value


def version_surfaces(root: Path) -> dict[str, str]:
    version_file = root / "skills/story/VERSION"
    claude_file = root / ".claude-plugin/marketplace.json"
    zcode_file = root / ".zcode-plugin/plugin.json"
    marketplace_file = root / "marketplace.json"
    reasonix_file = root / "reasonix-plugin.json"

    values = {
        str(version_file.relative_to(root)): version_file.read_text(encoding="utf-8").strip(),
        ".claude-plugin/marketplace.json:metadata.version": load_json(claude_file)
        .get("metadata", {})
        .get("version"),
        ".zcode-plugin/plugin.json:version": load_json(zcode_file).get("version"),
        "marketplace.json:plugins[0].version": (
            load_json(marketplace_file).get("plugins") or [{}]
        )[0].get("version"),
        "reasonix-plugin.json:version": load_json(reasonix_file).get("version"),
    }
    return values


def checked_version(
    root: Path,
    *,
    tag: str | None = None,
    require_changelog: bool = False,
) -> str:
    values = version_surfaces(root)
    canonical = values["skills/story/VERSION"]
    if not isinstance(canonical, str) or not STABLE_SEMVER.fullmatch(canonical):
        raise ValueError(
            f"skills/story/VERSION must be a stable X.Y.Z version, got {canonical!r}"
        )

    mismatches = {name: value for name, value in values.items() if value != canonical}
    if mismatches:
        details = ", ".join(f"{name}={value!r}" for name, value in mismatches.items())
        raise ValueError(f"package version surfaces disagree with {canonical}: {details}")

    if tag is not None:
        expected_tag = f"v{canonical}"
        if tag != expected_tag:
            raise ValueError(f"release tag must be {expected_tag}, got {tag!r}")

    if require_changelog:
        changelog_path = root / "CHANGELOG.md"
        changelog = changelog_path.read_text(encoding="utf-8")
        if not re.search(rf"^##\s+v{re.escape(canonical)}(?:\s|（|\()", changelog, re.M):
            raise ValueError(f"CHANGELOG.md is missing a top-level v{canonical} entry")

    return canonical


def write_json(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def set_version(root: Path, version: str) -> None:
    if not STABLE_SEMVER.fullmatch(version):
        raise ValueError(f"new package version must be stable X.Y.Z, got {version!r}")

    # Load and validate every destination before writing any of them. This
    # prevents a malformed manifest from leaving the public version surfaces
    # half-updated.
    claude_path = root / ".claude-plugin/marketplace.json"
    claude = load_json(claude_path)
    if not isinstance(claude.get("metadata"), dict):
        raise ValueError(f"{claude_path} is missing metadata")
    claude["metadata"]["version"] = version

    zcode_path = root / ".zcode-plugin/plugin.json"
    zcode = load_json(zcode_path)
    zcode["version"] = version

    marketplace_path = root / "marketplace.json"
    marketplace = load_json(marketplace_path)
    plugins = marketplace.get("plugins")
    if not isinstance(plugins, list) or len(plugins) != 1 or not isinstance(plugins[0], dict):
        raise ValueError(f"{marketplace_path} must contain exactly one plugin entry")
    plugins[0]["version"] = version

    reasonix_path = root / "reasonix-plugin.json"
    reasonix = load_json(reasonix_path)
    reasonix["version"] = version

    (root / "skills/story/VERSION").write_text(version + "\n", encoding="utf-8")
    write_json(claude_path, claude)
    write_json(zcode_path, zcode)
    write_json(marketplace_path, marketplace)
    write_json(reasonix_path, reasonix)

    checked_version(root)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=repo_root(),
        help="repository root (defaults to the parent of scripts/)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check", help="verify all public version surfaces")
    check.add_argument("--tag", help="also require an exact vX.Y.Z release tag")
    check.add_argument(
        "--require-changelog",
        action="store_true",
        help="require a matching top-level CHANGELOG entry",
    )

    set_parser = subparsers.add_parser("set", help="update all public version surfaces together")
    set_parser.add_argument("version")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.resolve()
    try:
        if args.command == "check":
            version = checked_version(
                root,
                tag=args.tag,
                require_changelog=args.require_changelog,
            )
            print(f"OK package version {version} is consistent")
        else:
            set_version(root, args.version)
            print(f"Updated public package version surfaces to {args.version}")
    except (IndexError, KeyError, OSError, TypeError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
