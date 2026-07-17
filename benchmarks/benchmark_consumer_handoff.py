#!/usr/bin/env python3
"""Load-excluded benchmark for direct and SnapshotProvider projector handoff."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import resource
import statistics
import sys
import time
from typing import Any

import pyowl_core

import pyowl2vec_star_projector
from pyowl2vec_star_projector import (
    Edge,
    Projector,
    SnapshotProviderProbe,
    consumer_conformance_case,
    consumer_conformance_fixture,
    consumer_conformance_fixture_metadata,
    project_source,
    project_taxonomy,
    verify_consumer_conformance,
)
from pyowl2vec_star_projector.artifact import edge_json_record


def _max_rss_bytes() -> int:
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value if sys.platform == "darwin" else value * 1024


def _fingerprint(value: Any) -> str:
    selected = value.structural_fingerprint
    hex_value = getattr(selected, "hex", selected)
    if callable(hex_value):
        hex_value = hex_value()
    return str(hex_value)


def _digest(edges: list[Edge]) -> str:
    digest = hashlib.sha256()
    for edge in edges:
        digest.update(edge_json_record(edge))
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--case-id",
        choices=("exact-owl2vec", "exact-owl2vec-literals", "exact-taxonomy"),
        default="exact-owl2vec",
    )
    parser.add_argument("--backend", choices=("python", "native"), default="python")
    parser.add_argument("--repetitions", type=int, default=25)
    parser.add_argument("--warmups", type=int, default=3)
    args = parser.parse_args()
    if args.repetitions < 1 or args.warmups < 0:
        parser.error("repetitions must be positive and warmups must be nonnegative")

    fixture = consumer_conformance_fixture()
    metadata = consumer_conformance_fixture_metadata()
    load_started = time.perf_counter()
    snapshot = pyowl_core.load_snapshot(
        fixture,
        document_iri=metadata.document_iri,
        options=pyowl_core.LoadOptions(
            backend=pyowl_core.BackendPreference.PYTHON,
            format=pyowl_core.DocumentFormat.FUNCTIONAL,
        ),
    )
    load_seconds = time.perf_counter() - load_started
    case = consumer_conformance_case(args.case_id)
    conformance = verify_consumer_conformance(
        snapshot,
        case_id=case.case_id,
        backend=args.backend,
    )

    def direct() -> list[Edge]:
        projector = Projector()
        if case.operation == "owl2vec-star":
            result = projector.project(snapshot, options=case.projection_options(args.backend))
        else:
            result = projector.project_taxonomy(
                snapshot,
                bidirectional=case.bidirectional,
                duplicates="unique",
                order="canonical",
                backend=args.backend,
            )
        if projector.last_view is not snapshot:
            raise RuntimeError("direct benchmark lost snapshot identity")
        return result

    provider_calls: list[int] = []
    source_accesses: list[int] = []

    def provider_handoff() -> list[Edge]:
        probe = SnapshotProviderProbe(snapshot)
        if case.operation == "owl2vec-star":
            result = project_source(probe, options=case.projection_options(args.backend))
        else:
            result = project_taxonomy(
                probe,
                bidirectional=case.bidirectional,
                duplicates="unique",
                order="canonical",
                backend=args.backend,
            )
        provider_calls.append(probe.provider_calls)
        source_accesses.append(probe.source_accesses)
        return result

    for _index in range(args.warmups):
        if direct() != list(case.edges) or provider_handoff() != list(case.edges):
            raise RuntimeError("consumer benchmark warm-up differs from the golden")
    direct_samples: list[float] = []
    provider_samples: list[float] = []
    observed: list[Edge] = []
    for _index in range(args.repetitions):
        started = time.perf_counter()
        observed = direct()
        direct_samples.append(time.perf_counter() - started)
        started = time.perf_counter()
        provided = provider_handoff()
        provider_samples.append(time.perf_counter() - started)
        if observed != provided or observed != list(case.edges):
            raise RuntimeError("consumer benchmark output differs from the golden")
    if set(provider_calls) != {1} or set(source_accesses) != {0}:
        raise RuntimeError("provider benchmark attempted duplicate or source access")
    direct_median = statistics.median(direct_samples)
    provider_median = statistics.median(provider_samples)
    report = {
        "schema": "pyowl-projector.consumer-handoff-benchmark/1",
        "case_id": case.case_id,
        "backend": args.backend,
        "fixture_sha256": hashlib.sha256(fixture).hexdigest(),
        "snapshot_structural_fingerprint": _fingerprint(snapshot),
        "load_count": 1,
        "load_seconds_excluded": load_seconds,
        "provider_calls_per_projection": 1,
        "source_accesses": 0,
        "snapshot_identity_preserved": True,
        "conformance": conformance.to_dict(),
        "edge_count": len(observed),
        "canonical_edges_sha256": _digest(observed),
        "warmups": args.warmups,
        "repetitions": args.repetitions,
        "samples_seconds": {
            "direct": direct_samples,
            "provider": provider_samples,
        },
        "medians_seconds": {
            "direct": direct_median,
            "provider": provider_median,
        },
        "provider_overhead_ratio": provider_median / direct_median,
        "process_max_rss_bytes": _max_rss_bytes(),
        "environment": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "projector": pyowl2vec_star_projector.__version__,
            "core": pyowl_core.__version__,
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
