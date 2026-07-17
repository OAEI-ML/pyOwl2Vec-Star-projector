#!/usr/bin/env python3
"""Enforce the projector/consumer dependency DAG from WP-P6."""

from __future__ import annotations

import argparse
import ast
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

if __package__:
    from .release_support import read_toml
else:
    from release_support import read_toml

_PROJECTOR_FORBIDDEN_IMPORTS = frozenset({"exact", "oaei_bioml_eval", "pyelk", "pyhermit"})
_PROJECTOR_FORBIDDEN_DISTRIBUTIONS = frozenset({"exact-om", "oaei-bioml-eval", "pyelk", "pyhermit"})
_OAEI_FORBIDDEN_IMPORTS = frozenset({"exact", "pyowl2vec_star_projector"})
_OAEI_FORBIDDEN_DISTRIBUTIONS = frozenset({"exact-om", "pyowl2vec-star-projector"})
_REQUIREMENT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")


def check_dependency_dag(projector_root: Path, oaei_root: Path | None = None) -> list[str]:
    """Return stable violations without importing either inspected package."""
    errors = _check_boundary(
        "projector",
        projector_root.resolve(),
        _PROJECTOR_FORBIDDEN_IMPORTS,
        _PROJECTOR_FORBIDDEN_DISTRIBUTIONS,
    )
    if oaei_root is not None:
        errors.extend(
            _check_boundary(
                "OAEI",
                oaei_root.resolve(),
                _OAEI_FORBIDDEN_IMPORTS,
                _OAEI_FORBIDDEN_DISTRIBUTIONS,
            )
        )
    return sorted(errors)


def _check_boundary(
    label: str,
    root: Path,
    forbidden_imports: frozenset[str],
    forbidden_distributions: frozenset[str],
) -> list[str]:
    metadata_path = root / "pyproject.toml"
    if not metadata_path.is_file():
        return [f"{label}: missing pyproject.toml at {root}"]
    errors: list[str] = []
    dependencies = _dependency_names(read_toml(metadata_path))
    forbidden_dependencies = dependencies & forbidden_distributions
    if forbidden_dependencies:
        errors.append(
            f"{label}: forbidden dependencies: {', '.join(sorted(forbidden_dependencies))}"
        )
    for path in _runtime_python_files(root):
        forbidden = _imported_roots(path) & forbidden_imports
        if forbidden:
            errors.append(
                f"{label}: forbidden imports in {path.relative_to(root)}: "
                + ", ".join(sorted(forbidden))
            )
    return errors


def _runtime_python_files(root: Path) -> tuple[Path, ...]:
    source = root / "src"
    if not source.is_dir():
        return ()
    return tuple(sorted(path for path in source.rglob("*.py") if path.is_file()))


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def _dependency_names(document: Mapping[str, object]) -> set[str]:
    result: set[str] = set()
    project = _mapping(document.get("project"))
    _collect_requirement_list(project.get("dependencies"), result)
    optional = _mapping(project.get("optional-dependencies"))
    for requirements in optional.values():
        _collect_requirement_list(requirements, result)

    tool = _mapping(document.get("tool"))
    poetry = _mapping(tool.get("poetry"))
    _collect_poetry_dependencies(poetry.get("dependencies"), result)
    groups = _mapping(poetry.get("group"))
    for group in groups.values():
        _collect_poetry_dependencies(_mapping(group).get("dependencies"), result)
    return result


def _collect_requirement_list(value: object, result: set[str]) -> None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return
    for item in value:
        if not isinstance(item, str):
            continue
        match = _REQUIREMENT_NAME.match(item.strip())
        if match:
            result.add(_normalize_distribution(match.group(0)))


def _collect_poetry_dependencies(value: object, result: set[str]) -> None:
    dependencies = _mapping(value)
    for name in dependencies:
        normalized = _normalize_distribution(name)
        if normalized != "python":
            result.add(normalized)


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def _normalize_distribution(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--projector-root", type=Path, default=Path.cwd())
    parser.add_argument("--oaei-root", type=Path)
    args = parser.parse_args(argv)
    errors = check_dependency_dag(args.projector_root, args.oaei_root)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    checked = "projector" if args.oaei_root is None else "projector and OAEI"
    print(f"dependency DAG OK: {checked}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
