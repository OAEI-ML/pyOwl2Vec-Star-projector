#!/usr/bin/env python3
"""Fail if runtime source or dependency metadata crosses forbidden boundaries."""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "deeponto",
        "exact",
        "jpype",
        "mowl",
        "oaei_bioml_eval",
        "owlapi",
        "pyelk",
        "pyhermit",
        "robot",
    }
)
FORBIDDEN_ARTIFACT_SUFFIXES = frozenset({".class", ".ear", ".jar", ".war"})


def imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def audit(root: Path) -> list[str]:
    errors: list[str] = []
    source = root / "src"
    for path in sorted(source.rglob("*")):
        if path.is_file() and path.suffix.lower() in FORBIDDEN_ARTIFACT_SUFFIXES:
            errors.append(f"forbidden runtime artifact: {path.relative_to(root)}")
        if path.is_file() and path.suffix == ".py":
            forbidden = imported_roots(path) & FORBIDDEN_IMPORT_ROOTS
            if forbidden:
                errors.append(
                    f"forbidden imports in {path.relative_to(root)}: "
                    + ", ".join(sorted(forbidden))
                )
    metadata = (root / "pyproject.toml").read_text(encoding="utf-8").lower()
    dependency_region = metadata.split("[project.optional-dependencies]", 1)[0]
    for name in sorted(FORBIDDEN_IMPORT_ROOTS):
        if f'"{name}' in dependency_region or f"'{name}" in dependency_region:
            errors.append(f"forbidden base dependency: {name}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    errors = audit(args.root.resolve())
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("runtime dependency boundary OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
