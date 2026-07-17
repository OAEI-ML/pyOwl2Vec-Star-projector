#!/usr/bin/env python3
"""Reproducible load-excluded projector and edge-policy benchmark.

Ontology files are caller-supplied and never copied into this repository. Run
one process per configuration under the platform RSS measurement tool when
collecting release memory evidence (for example ``/usr/bin/time -l`` on macOS).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import resource
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from pyowl_core import BackendPreference, DocumentFormat, LoadOptions, PythonParser
from pyowl_core.model import AxiomNode, Entity, EntityKind

from pyowl2vec_star_projector import Edge, ProjectionOptions, Projector
from pyowl2vec_star_projector.native import iter_native_policy


@dataclass(frozen=True, slots=True)
class _Capabilities:
    adapter_protocol: int = 1
    model_schema: int = 1
    wire_format: tuple[int, int] = (1, 0)


class _IndexedDocumentView:
    """Benchmark activation view with load-time indexes excluded from samples."""

    capabilities = _Capabilities()

    def __init__(self, document: Any) -> None:
        self.root = document
        self._axioms = tuple(document.iter_axioms())
        self._signature = cast(tuple[Entity, ...], document.signature())
        fingerprint = document.document_fingerprint.digest.hex()
        self.structural_fingerprint = fingerprint
        self.logical_fingerprint = fingerprint
        self.signature_fingerprint = fingerprint

    def iter_axioms(
        self,
        axiom_type: type[AxiomNode] | None = None,
        *,
        scope: object = "closure",
    ) -> object:
        del scope
        selected = (
            self._axioms
            if axiom_type is None
            else tuple(item for item in self._axioms if type(item) is axiom_type)
        )
        return iter(selected)

    def signature(
        self,
        kind: EntityKind | None = None,
        *,
        scope: object = "closure",
        include_builtins: bool = True,
    ) -> tuple[Entity, ...]:
        del scope, include_builtins
        if kind is None:
            return self._signature
        return tuple(item for item in self._signature if item.kind is kind)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _max_rss_bytes() -> int:
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value if sys.platform == "darwin" else value * 1024


def _ontology_benchmark(args: argparse.Namespace) -> dict[str, object]:
    path = args.ontology.resolve()
    started = time.perf_counter()
    document = PythonParser().parse(
        path,
        format=DocumentFormat(args.format),
        options=LoadOptions(backend=BackendPreference.PYTHON),
        allow_partial_rdf_mapping=args.allow_partial_rdf_mapping,
    )
    view = _IndexedDocumentView(document)
    load_seconds = time.perf_counter() - started
    samples: dict[str, list[float]] = {}
    edge_counts: dict[str, int] = {}
    for order in ("encounter", "canonical"):
        for backend in ("python", "native"):
            key = f"{order}.{backend}"
            durations: list[float] = []
            count = 0
            for _ in range(args.repetitions):
                before = time.perf_counter()
                count = len(
                    Projector().project(
                        view,
                        options=ProjectionOptions(backend=backend, order=order),
                    )
                )
                durations.append(time.perf_counter() - before)
            samples[key] = durations
            edge_counts[key] = count
    result: dict[str, object] = {
        "kind": "ontology",
        "path_name": path.name,
        "source_bytes": path.stat().st_size,
        "source_sha256": _file_sha256(path),
        "document_fingerprint": document.document_fingerprint.digest.hex(),
        "axioms": len(document.axioms),
        "load_seconds_excluded": load_seconds,
        "repetitions": args.repetitions,
        "samples_seconds": samples,
        "medians_seconds": {key: statistics.median(value) for key, value in samples.items()},
        "edge_counts": edge_counts,
    }
    result["process_max_rss_bytes"] = _max_rss_bytes()
    return result


def _synthetic_edges(count: int) -> list[Edge]:
    distinct = max(1, count * 4 // 5)
    return [
        Edge(
            f"urn:synthetic:class:{index % distinct:09d}",
            f"urn:synthetic:relation:{index % 17:02d}",
            f"urn:synthetic:class:{(index * 37) % distinct:09d}",
        )
        for index in range(count)
    ]


def _python_policy(edges: list[Edge], order: str, duplicates: str) -> list[Edge]:
    output = edges if duplicates == "preserve" else list(dict.fromkeys(edges))
    if order == "canonical":
        return sorted(output, key=Edge.canonical_key)
    # Copy to make the output ownership comparable with the native boundary.
    return list(output)


def _synthetic_benchmark(args: argparse.Namespace) -> dict[str, object]:
    edges = _synthetic_edges(args.synthetic_edges)
    samples: dict[str, list[float]] = {}
    digests: dict[str, str] = {}
    for backend in ("python", "native"):
        durations: list[float] = []
        output: list[Edge] = []
        for _ in range(args.repetitions):
            before = time.perf_counter()
            output = (
                _python_policy(edges, args.order, args.duplicates)
                if backend == "python"
                else list(
                    iter_native_policy(
                        edges,
                        duplicates=args.duplicates,
                        order=args.order,
                        batch_edges=args.batch_edges,
                    )
                )
            )
            durations.append(time.perf_counter() - before)
        samples[backend] = durations
        digest = hashlib.sha256()
        for edge in output:
            for value in edge.as_tuple():
                encoded = value.encode("utf-8")
                digest.update(len(encoded).to_bytes(8, "big"))
                digest.update(encoded)
        digests[backend] = digest.hexdigest()
    if digests["python"] != digests["native"]:
        raise RuntimeError("synthetic benchmark detected a backend mismatch")
    result: dict[str, object] = {
        "kind": "synthetic-edge-policy",
        "input_edges": len(edges),
        "order": args.order,
        "duplicates": args.duplicates,
        "batch_edges": args.batch_edges,
        "repetitions": args.repetitions,
        "samples_seconds": samples,
        "medians_seconds": {key: statistics.median(value) for key, value in samples.items()},
        "output_sha256": digests["python"],
    }
    result["process_max_rss_bytes"] = _max_rss_bytes()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--ontology", type=Path)
    source.add_argument("--synthetic-edges", type=int)
    parser.add_argument(
        "--format",
        default="rdfxml",
        choices=[item.value for item in DocumentFormat],
    )
    parser.add_argument("--allow-partial-rdf-mapping", action="store_true")
    parser.add_argument("--repetitions", type=int, default=7)
    parser.add_argument("--batch-edges", type=int, default=250_000)
    parser.add_argument("--order", choices=("canonical", "encounter"), default="canonical")
    parser.add_argument("--duplicates", choices=("preserve", "unique"), default="preserve")
    args = parser.parse_args()
    if args.repetitions < 1 or args.batch_edges < 1:
        parser.error("repetitions and batch-edges must be positive")
    if args.synthetic_edges is not None and args.synthetic_edges < 1:
        parser.error("synthetic-edges must be positive")
    result = _ontology_benchmark(args) if args.ontology is not None else _synthetic_benchmark(args)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
