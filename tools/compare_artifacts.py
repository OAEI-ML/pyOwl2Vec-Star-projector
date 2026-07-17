#!/usr/bin/env python3
"""Compare two independently built release directories byte-for-byte."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__:
    from .release_support import normalized_members, release_artifacts, sha256_file
else:
    from release_support import normalized_members, release_artifacts, sha256_file


def compare(first: Path, second: Path) -> list[str]:
    errors: list[str] = []
    left = {path.name: path for path in release_artifacts(first)}
    right = {path.name: path for path in release_artifacts(second)}
    if left.keys() != right.keys():
        errors.append(f"artifact sets differ: {sorted(left)} != {sorted(right)}")
        return errors
    for name in sorted(left):
        left_hash = sha256_file(left[name])
        right_hash = sha256_file(right[name])
        if left_hash == right_hash:
            continue
        left_members = normalized_members(left[name])
        right_members = normalized_members(right[name])
        differing = sorted(
            key
            for key in left_members.keys() | right_members.keys()
            if left_members.get(key) != right_members.get(key)
        )
        detail = ", ".join(differing[:8])
        errors.append(f"{name}: archive bytes differ; changed members: {detail or 'metadata only'}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("first", type=Path)
    parser.add_argument("second", type=Path)
    args = parser.parse_args(argv)
    errors = compare(args.first.resolve(), args.second.resolve())
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("release artifacts are byte-for-byte reproducible")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
