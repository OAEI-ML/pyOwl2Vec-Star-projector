#!/usr/bin/env python3
"""Create or verify a stable SHA-256 manifest for release artifacts."""

from __future__ import annotations

import argparse
import hmac
import re
import sys
from pathlib import Path

if __package__:
    from .release_support import read_stable_regular_file, release_artifacts, sha256_file
else:
    from release_support import read_stable_regular_file, release_artifacts, sha256_file

_SHA256 = re.compile(r"[0-9a-f]{64}")


def create_manifest(directory: Path) -> str:
    artifacts = release_artifacts(directory)
    if not artifacts:
        raise ValueError(f"no wheel or sdist artifacts in {directory}")
    return "".join(f"{sha256_file(path)}  {path.name}\n" for path in artifacts)


def verify_manifest_content(directory: Path, content: bytes) -> list[str]:
    errors: list[str] = []
    artifacts = {path.name: path for path in release_artifacts(directory)}
    seen: set[str] = set()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return ["hash manifest is not UTF-8"]
    for number, line in enumerate(text.splitlines(), 1):
        try:
            expected, name = line.split("  ", 1)
        except ValueError:
            errors.append(f"line {number}: malformed hash record")
            continue
        if _SHA256.fullmatch(expected) is None:
            errors.append(f"line {number}: invalid SHA-256 digest")
        if not name or "/" in name or "\\" in name or name in {".", ".."}:
            errors.append(f"line {number}: unsafe artifact name {name!r}")
            continue
        if name in seen:
            errors.append(f"line {number}: duplicate artifact record for {name}")
            continue
        seen.add(name)
        path = artifacts.get(name)
        if path is None:
            errors.append(f"line {number}: unexpected release artifact {name}")
            continue
        actual = sha256_file(path)
        if _SHA256.fullmatch(expected) is not None and not hmac.compare_digest(expected, actual):
            errors.append(f"line {number}: hash mismatch for {name}")
    for name in sorted(artifacts.keys() - seen):
        errors.append(f"manifest missing release artifact {name}")
    return errors


def verify_manifest(directory: Path, manifest: Path) -> list[str]:
    try:
        content = read_stable_regular_file(manifest, label="SHA256SUMS")
    except ValueError as error:
        return [str(error)]
    return verify_manifest_content(directory, content)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args(argv)
    directory = args.directory.resolve()
    if args.verify:
        errors = verify_manifest(directory, args.verify.resolve())
        if errors:
            print("\n".join(errors), file=sys.stderr)
            return 1
        print(f"verified {args.verify}")
        return 0
    rendered = create_manifest(directory)
    output = args.output or directory / "SHA256SUMS"
    output.write_text(rendered, encoding="utf-8", newline="\n")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
