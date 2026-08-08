#!/usr/bin/env python3
"""Require release-contract version bumps when story-setup payloads change."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


CONTRACT_PATH = "scripts/current-contract.json"
SETUP_SKILL_PATH = "skills/story-setup/SKILL.md"
AGENTS_PAYLOAD_PATHS = (
    "skills/story-setup/references",
    "skills/story-setup/scripts",
)
DOTTED_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


class GateError(RuntimeError):
    """An actionable release-gate failure."""


@dataclass(frozen=True)
class ContractVersions:
    setup_text: str
    setup_parts: tuple[int, ...]
    agents: int


def git(
    root: Path,
    *arguments: str,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise GateError(detail or "git {} failed".format(" ".join(arguments)))
    return completed


def text_output(root: Path, *arguments: str) -> str:
    return git(root, *arguments).stdout.decode("utf-8", errors="strict").strip()


def validate_repo_root(root: Path) -> None:
    try:
        actual = Path(text_output(root, "rev-parse", "--show-toplevel")).resolve()
    except (OSError, UnicodeError, GateError) as exc:
        raise GateError("not a readable Git repository: {}".format(root)) from exc
    if actual != root:
        raise GateError(
            "--repo-root must name the Git repository root (expected {}, got {})".format(
                actual, root
            )
        )


def resolve_base_tag(root: Path, requested: str | None) -> tuple[str, str]:
    if requested is None:
        described = git(root, "describe", "--tags", "--abbrev=0", "HEAD", check=False)
        if described.returncode != 0:
            raise GateError(
                "no tag is reachable from HEAD; pass the release baseline with --base-tag"
            )
        requested = described.stdout.decode("utf-8", errors="strict").strip()
        if not requested:
            raise GateError(
                "Git returned an empty nearest tag; pass the release baseline with --base-tag"
            )

    tag_ref = "refs/tags/{}^{{commit}}".format(requested)
    resolved = git(root, "rev-parse", "--verify", "--quiet", tag_ref, check=False)
    if resolved.returncode != 0:
        raise GateError(
            "base tag {!r} does not exist or does not point to a commit".format(
                requested
            )
        )
    commit = resolved.stdout.decode("ascii", errors="strict").strip()
    return requested, commit


def load_contract(raw: bytes, source: str) -> ContractVersions:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GateError("{} is not valid UTF-8 JSON: {}".format(source, exc)) from exc
    if not isinstance(value, dict):
        raise GateError("{} must contain a JSON object".format(source))

    setup = value.get("setup_skill_version")
    if not isinstance(setup, str) or DOTTED_VERSION.fullmatch(setup) is None:
        raise GateError(
            "{}: setup_skill_version must be a dotted numeric x.y.z string, got {!r}".format(
                source, setup
            )
        )
    agents = value.get("agents_version")
    if not isinstance(agents, int) or isinstance(agents, bool) or agents < 1:
        raise GateError(
            "{}: agents_version must be a positive integer, got {!r}".format(source, agents)
        )
    return ContractVersions(setup, tuple(int(part) for part in setup.split(".")), agents)


def contract_at_commit(root: Path, commit: str, source: str) -> ContractVersions:
    result = git(root, "show", "{}:{}".format(commit, CONTRACT_PATH), check=False)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise GateError(
            "cannot read {} from {}{}".format(
                CONTRACT_PATH,
                source,
                ": {}".format(detail) if detail else "",
            )
        )
    return load_contract(result.stdout, "{}:{}".format(source, CONTRACT_PATH))


def changed_paths(root: Path, base_commit: str) -> set[str]:
    """Return tracked paths changed between the baseline and current HEAD."""

    pathspecs = (SETUP_SKILL_PATH, *AGENTS_PAYLOAD_PATHS)
    result = git(
        root,
        "diff",
        "--name-only",
        "--no-renames",
        "-z",
        base_commit,
        "HEAD",
        "--",
        *pathspecs,
    )
    return {
        item.decode("utf-8", errors="strict")
        for item in result.stdout.split(b"\0")
        if item
    }


def check_bumps(
    base: ContractVersions,
    current: ContractVersions,
    paths: set[str],
    base_label: str,
) -> list[str]:
    failures: list[str] = []
    setup_changed = SETUP_SKILL_PATH in paths
    agents_changed = any(
        path == prefix or path.startswith(prefix + "/")
        for prefix in AGENTS_PAYLOAD_PATHS
        for path in paths
    )

    if current.setup_parts < base.setup_parts:
        failures.append(
            "setup_skill_version must not decrease relative to {} "
            "(base {}, current {})".format(base_label, base.setup_text, current.setup_text)
        )
    elif setup_changed and current.setup_parts == base.setup_parts:
        failures.append(
            "setup_skill_version must increase because {} changed relative to {} "
            "(base {}, current {})".format(
                SETUP_SKILL_PATH, base_label, base.setup_text, current.setup_text
            )
        )

    if current.agents < base.agents:
        failures.append(
            "agents_version must not decrease relative to {} "
            "(base {}, current {})".format(base_label, base.agents, current.agents)
        )
    elif agents_changed and current.agents == base.agents:
        payload_changes = sorted(
            path
            for path in paths
            if any(
                path == prefix or path.startswith(prefix + "/")
                for prefix in AGENTS_PAYLOAD_PATHS
            )
        )
        failures.append(
            "agents_version must increase because the story-setup deployment payload "
            "changed relative to {} (base {}, current {}): {}".format(
                base_label,
                base.agents,
                current.agents,
                ", ".join(payload_changes),
            )
        )
    return failures


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        "--root",
        dest="repo_root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Git repository root (default: parent of scripts/)",
    )
    parser.add_argument(
        "--base-tag",
        help="release baseline tag (default: nearest tag reachable from HEAD)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.repo_root.resolve()
    try:
        validate_repo_root(root)
        base_label, base_commit = resolve_base_tag(root, args.base_tag)
        head_commit = text_output(root, "rev-parse", "--verify", "HEAD^{commit}")
        base = contract_at_commit(root, base_commit, base_label)
        current = contract_at_commit(root, head_commit, "HEAD")
        paths = changed_paths(root, base_commit)
        failures = check_bumps(base, current, paths, base_label)
    except (OSError, UnicodeError, GateError) as exc:
        print("FAIL: {}".format(exc), file=sys.stderr)
        return 1

    if failures:
        print(
            "Release contract bump check failed against {}:".format(base_label),
            file=sys.stderr,
        )
        for failure in failures:
            print("  - {}".format(failure), file=sys.stderr)
        return 1

    print(
        "OK: release contract versions are valid against {} "
        "(setup_skill_version {} -> {}, agents_version {} -> {})".format(
            base_label,
            base.setup_text,
            current.setup_text,
            base.agents,
            current.agents,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
