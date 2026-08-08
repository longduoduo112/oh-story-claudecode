#!/usr/bin/env python3
"""Run the quality gate and build an approved dev or release package.

Local release builds are deliberately two-step: an already-built clean dev
artifact for the exact HEAD must exist before the release channel is allowed.
GitHub's release workflow applies the equivalent check against a successful
package-dev workflow run for the same commit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys


STABLE_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


class GateError(RuntimeError):
    pass


def run(command: list[str], root: Path) -> None:
    completed = subprocess.run(command, cwd=root, check=False)
    if completed.returncode != 0:
        raise GateError(f"command failed ({completed.returncode}): {' '.join(command)}")


def output(command: list[str], root: Path) -> str:
    completed = subprocess.run(
        command,
        cwd=root,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise GateError(detail or f"command failed: {' '.join(command)}")
    return completed.stdout.strip()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def approved_dev_manifest(root: Path, version: str, head: str) -> Path:
    candidates: list[tuple[float, Path]] = []
    for path in (root / "dist/dev").glob("*.manifest.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("channel") != "dev":
            continue
        if data.get("source_sha") != head or data.get("source_dirty") is not False:
            continue
        package_version = data.get("version")
        if not isinstance(package_version, str) or not package_version.startswith(version + "-dev."):
            continue

        checksums = data.get("checksums")
        if not isinstance(checksums, dict) or not checksums:
            continue
        valid = True
        for name, expected in checksums.items():
            artifact = path.parent / name
            if (
                not isinstance(name, str)
                or not isinstance(expected, str)
                or not artifact.is_file()
                or sha256(artifact) != expected
            ):
                valid = False
                break
        if valid:
            candidates.append((path.stat().st_mtime, path))

    if not candidates:
        raise GateError(
            "no verified clean dev package exists for this exact HEAD; run "
            "`python3 scripts/package-channel.py dev`, install/smoke-test that archive, "
            "then create the annotated release tag"
        )
    candidates.sort(reverse=True)
    return candidates[0][1]


def built_manifest(output_dir: Path, channel: str, head: str) -> Path:
    candidates: list[tuple[float, Path]] = []
    for path in output_dir.glob("*.manifest.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("channel") == channel and data.get("source_sha") == head:
            candidates.append((path.stat().st_mtime, path))
    if not candidates:
        raise GateError(f"builder did not produce a {channel} manifest for HEAD {head}")
    candidates.sort(reverse=True)
    return candidates[0][1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("channel", choices=("dev", "release"))
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="repository root",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.resolve()
    try:
        version = (root / "skills/story/VERSION").read_text(encoding="utf-8").strip()
        if not STABLE_SEMVER.fullmatch(version):
            raise GateError(f"skills/story/VERSION must be stable X.Y.Z, got {version!r}")

        head = output(["git", "rev-parse", "HEAD^{commit}"], root)
        if args.channel == "release":
            approved = approved_dev_manifest(root, version, head)
            print(f"[release] approved dev evidence: {approved}")
            run(
                [
                    sys.executable,
                    "scripts/manage-version.py",
                    "check",
                    "--tag",
                    f"v{version}",
                    "--require-changelog",
                ],
                root,
            )

        run(["bash", "scripts/run-quality-gate.sh"], root)
        output_dir = root / "dist" / args.channel
        run(
            [
                sys.executable,
                "scripts/build-package.py",
                args.channel,
                "--output-dir",
                os.fspath(output_dir),
            ],
            root,
        )
        manifest = built_manifest(output_dir, args.channel, head)
        metadata = json.loads(manifest.read_text(encoding="utf-8"))
        archive_root = metadata.get("archive_root")
        if not isinstance(archive_root, str) or not archive_root:
            raise GateError(f"invalid archive_root in {manifest}")
        run(
            [
                sys.executable,
                "scripts/verify-package.py",
                os.fspath(output_dir / f"{archive_root}.zip"),
                "--manifest",
                os.fspath(manifest),
                "--install-smoke",
            ],
            root,
        )
        print(f"[{args.channel}] package ready in {output_dir}")
    except (GateError, OSError, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
