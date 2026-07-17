#!/usr/bin/env python3
"""Validate the pinned compatibility baseline and, optionally, its Git blob."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 path
    import tomli as tomllib  # type: ignore[no-redef]


HEX40 = re.compile(r"^[0-9a-f]{40}$")


def git_blob_digest(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data, usedforsecurity=False).hexdigest()


def validate_baseline(path: Path, source_file: Path | None = None) -> list[str]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    errors: list[str] = []
    upstream = data.get("upstream", {})
    compatibility = data.get("compatibility", {})
    commit = upstream.get("commit", "")
    blob = upstream.get("source_blob", "")
    source_path = upstream.get("source_path", "")
    immutable_url = upstream.get("immutable_source_url", "")

    if data.get("schema") != "pyowl-projector.reference-baseline/1":
        errors.append("unexpected baseline schema")
    if data.get("profile") != "mowl-d993536-v1":
        errors.append("unexpected compatibility profile")
    if not HEX40.fullmatch(commit):
        errors.append("upstream commit must be a lowercase 40-hex digest")
    if not HEX40.fullmatch(blob):
        errors.append("source blob must be a lowercase 40-hex digest")
    if commit not in immutable_url or source_path not in immutable_url:
        errors.append("immutable source URL does not bind the commit and source path")
    if upstream.get("license") != "BSD-3-Clause":
        errors.append("unexpected mOWL license identifier")
    if compatibility.get("output_semantics") != "bag":
        errors.append("compatibility output semantics must remain bag")
    if compatibility.get("canonical_order") != "utf8(source,relation,destination)":
        errors.append("unexpected canonical edge order")
    if source_file is not None:
        actual = git_blob_digest(source_file.read_bytes())
        if actual != blob:
            errors.append(f"source Git blob mismatch: expected {blob}, got {actual}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--baseline",
        type=Path,
        default=Path("specs/baseline.toml"),
    )
    parser.add_argument("--source-file", type=Path)
    args = parser.parse_args(argv)
    errors = validate_baseline(args.baseline, args.source_file)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"baseline OK: {args.baseline}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
