#!/usr/bin/env python3
"""Create or verify a stable SHA-256 manifest for release artifacts."""

from __future__ import annotations

import argparse
import hmac
import sys
from pathlib import Path

if __package__:
    from .release_support import release_artifacts, sha256_file
else:
    from release_support import release_artifacts, sha256_file


def create_manifest(directory: Path) -> str:
    artifacts = release_artifacts(directory)
    if not artifacts:
        raise ValueError(f"no wheel or sdist artifacts in {directory}")
    return "".join(f"{sha256_file(path)}  {path.name}\n" for path in artifacts)


def verify_manifest(directory: Path, manifest: Path) -> list[str]:
    errors: list[str] = []
    for number, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        try:
            expected, name = line.split("  ", 1)
        except ValueError:
            errors.append(f"line {number}: malformed hash record")
            continue
        path = directory / name
        if not path.is_file():
            errors.append(f"line {number}: missing {name}")
            continue
        actual = sha256_file(path)
        if not hmac.compare_digest(expected, actual):
            errors.append(f"line {number}: hash mismatch for {name}")
    return errors


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
