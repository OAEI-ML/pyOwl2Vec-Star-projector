#!/usr/bin/env python3
"""Compare shared-projector output with Exact-OM 2.0's committed WP-B captures."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, cast

import pyowl_core

from pyowl2vec_star_projector import Edge, ProjectionOptions, Projector
from pyowl2vec_star_projector.artifact import edge_json_record

_FIXTURE_NAMES = ("mini_src", "mini_tgt")
_MAX_BASELINE_BYTES = 32 * 1024 * 1024


def compare_exact_baselines(
    exact_root: Path,
    *,
    backend: str = "python",
) -> dict[str, object]:
    """Return machine-readable, path-free Exact migration evidence."""
    if backend not in ("python", "native"):
        raise ValueError("backend must be 'python' or 'native'")
    root = exact_root.resolve()
    fixture_root = root / "tests" / "fixtures" / "ontologies"
    baseline_root = root / "tests" / "baselines"
    comparisons: list[dict[str, object]] = []
    for fixture_name in _FIXTURE_NAMES:
        source = fixture_root / f"{fixture_name}.owl"
        baseline_path = baseline_root / f"{fixture_name}.backend.json.zst"
        if not source.is_file() or not baseline_path.is_file():
            raise FileNotFoundError(f"Exact fixture/baseline pair is missing for {fixture_name!r}")
        baseline = _read_projection_baseline(baseline_path)
        source_bytes = source.read_bytes()
        snapshot = pyowl_core.load_snapshot(
            source,
            options=pyowl_core.LoadOptions(
                backend=pyowl_core.BackendPreference.PYTHON,
                format=pyowl_core.DocumentFormat.RDF_XML,
            ),
        )
        before = _snapshot_state(snapshot)
        identity_preserved = True
        observed: dict[str, tuple[Edge, ...]] = {}
        for name, include_literals in (
            ("owl2vecstar", False),
            ("owl2vecstar_literals", True),
        ):
            projector = Projector()
            observed[name] = tuple(
                projector.project(
                    snapshot,
                    options=ProjectionOptions(
                        backend=cast(Any, backend),
                        profile="mowl-d993536-v1",
                        include_literals=include_literals,
                        duplicates="unique",
                        order="canonical",
                        compatibility_state="isolated",
                    ),
                )
            )
            identity_preserved &= projector.last_view is snapshot
        taxonomy_projector = Projector()
        observed["taxonomy"] = tuple(
            taxonomy_projector.project_taxonomy(
                snapshot,
                backend=cast(Any, backend),
                duplicates="unique",
                order="canonical",
            )
        )
        identity_preserved &= taxonomy_projector.last_view is snapshot
        after = _snapshot_state(snapshot)
        projection_results = [
            _compare_projection(name, observed[name], baseline[name])
            for name in ("owl2vecstar", "owl2vecstar_literals", "taxonomy")
        ]
        comparisons.append(
            {
                "fixture_id": fixture_name,
                "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
                "source_bytes": len(source_bytes),
                "load_calls": 1,
                "snapshot_identity_preserved": identity_preserved,
                "snapshot_unchanged": before == after,
                "snapshot": before,
                "projections": projection_results,
                "passed": identity_preserved
                and before == after
                and all(bool(item["ordered_equal"]) for item in projection_results),
            }
        )
    passed = all(bool(item["passed"]) for item in comparisons)
    return {
        "schema": "pyowl-projector.exact-baseline-comparison/1",
        "exact_baseline": "2.0.0/WP-B",
        "profile": "mowl-d993536-v1",
        "semantic_options": {
            "duplicates": "unique",
            "order": "canonical",
            "compatibility_state": "isolated",
            "taxonomy": "dedicated-asserted-taxonomy",
        },
        "backend": backend,
        "fixtures": comparisons,
        "difference_classification": (
            "none; every committed edge and ordering matches"
            if passed
            else "unclassified differences; baseline changes are forbidden"
        ),
        "passed": passed,
    }


def _read_projection_baseline(path: Path) -> dict[str, list[object]]:
    try:
        import zstandard
    except ImportError as error:
        raise RuntimeError(
            "Exact baseline comparison requires development-only zstandard>=0.23"
        ) from error
    payload = zstandard.ZstdDecompressor().decompress(
        path.read_bytes(),
        max_output_size=_MAX_BASELINE_BYTES,
    )
    document = json.loads(payload)
    if not isinstance(document, dict) or not isinstance(document.get("projection"), dict):
        raise ValueError(f"invalid Exact baseline document: {path.name}")
    projection = cast(dict[str, object], document["projection"])
    required = {"owl2vecstar", "owl2vecstar_literals", "taxonomy"}
    if set(projection) != required:
        raise ValueError(f"unexpected Exact projection keys in {path.name}")
    result: dict[str, list[object]] = {}
    for name in sorted(required):
        value = projection[name]
        if not isinstance(value, list):
            raise ValueError(f"Exact projection {name!r} is not a list")
        result[name] = cast(list[object], value)
    return result


def _compare_projection(
    name: str,
    observed: tuple[Edge, ...],
    expected_raw: list[object],
) -> dict[str, object]:
    expected: list[Edge] = []
    for value in expected_raw:
        if (
            not isinstance(value, list)
            or len(value) != 3
            or not all(isinstance(item, str) for item in value)
        ):
            raise ValueError(f"Exact projection {name!r} contains an invalid edge")
        expected.append(Edge(value[0], value[1], value[2]))
    expected_tuple = tuple(expected)
    observed_set = set(observed)
    expected_set = set(expected_tuple)
    missing = tuple(sorted(expected_set - observed_set, key=Edge.canonical_key))
    unexpected = tuple(sorted(observed_set - expected_set, key=Edge.canonical_key))
    return {
        "name": name,
        "observed_edges": len(observed),
        "expected_edges": len(expected_tuple),
        "observed_sha256": _edge_digest(observed),
        "expected_sha256": _edge_digest(expected_tuple),
        "ordered_equal": observed == expected_tuple,
        "missing_count": len(missing),
        "unexpected_count": len(unexpected),
        "missing_sample": [list(edge.as_tuple()) for edge in missing[:10]],
        "unexpected_sample": [list(edge.as_tuple()) for edge in unexpected[:10]],
    }


def _edge_digest(edges: tuple[Edge, ...]) -> str:
    digest = hashlib.sha256()
    for edge in edges:
        digest.update(edge_json_record(edge))
    return digest.hexdigest()


def _snapshot_state(snapshot: object) -> dict[str, object]:
    return {
        "identity_kind": type(snapshot).__name__,
        "structural_fingerprint": _fingerprint(snapshot, "structural_fingerprint"),
        "logical_fingerprint": _fingerprint(snapshot, "logical_fingerprint"),
        "signature_fingerprint": _fingerprint(snapshot, "signature_fingerprint"),
        "axiom_count": sum(1 for _item in snapshot.iter_axioms()),  # type: ignore[attr-defined]
        "signature_count": len(snapshot.signature()),  # type: ignore[attr-defined]
    }


def _fingerprint(value: object, name: str) -> str:
    selected = getattr(value, name)
    hex_value = getattr(selected, "hex", selected)
    if callable(hex_value):
        hex_value = hex_value()
    return str(hex_value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exact-root", required=True, type=Path)
    parser.add_argument("--backend", choices=("python", "native"), default="python")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = compare_exact_baselines(args.exact_root, backend=args.backend)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
