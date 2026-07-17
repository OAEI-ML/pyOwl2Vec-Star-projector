#!/usr/bin/env python3
"""Fail if runtime source or dependency metadata crosses forbidden boundaries."""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import Any

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
FORBIDDEN_DEPENDENCIES = frozenset(
    {
        "deeponto",
        "exact",
        "exact-om",
        "jpype",
        "jpype1",
        "mowl",
        "oaei-bioml-eval",
        "owlapi",
        "pyelk",
        "pyhermit",
        "robot",
    }
)
_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


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
    for scope, requirement in _dependencies(root / "pyproject.toml"):
        match = _REQUIREMENT_NAME.match(requirement)
        if match is None:
            errors.append(f"invalid {scope} dependency requirement: {requirement!r}")
            continue
        name = re.sub(r"[-_.]+", "-", match.group(1)).lower()
        if name in FORBIDDEN_DEPENDENCIES:
            errors.append(f"forbidden {scope} dependency: {name}")
    return errors


def _dependencies(path: Path) -> list[tuple[str, str]]:
    try:
        import tomllib
    except ImportError:  # pragma: no cover - Python 3.10
        import tomli as tomllib  # type: ignore[no-redef]

    with path.open("rb") as stream:
        document: dict[str, Any] = tomllib.load(stream)
    project = document.get("project", {})
    if not isinstance(project, dict):
        return []
    result: list[tuple[str, str]] = []
    dependencies = project.get("dependencies", [])
    if isinstance(dependencies, list):
        result.extend(("base", item) for item in dependencies if isinstance(item, str))
    optional = project.get("optional-dependencies", {})
    if isinstance(optional, dict):
        for extra, values in optional.items():
            if isinstance(extra, str) and isinstance(values, list):
                result.extend(
                    (f"optional extra {extra!r}", item) for item in values if isinstance(item, str)
                )
    return result


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
