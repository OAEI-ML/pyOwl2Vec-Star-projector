#!/usr/bin/env python3
"""Measure the load-excluded public encoded-compiler handoff.

Run one backend/configuration per cold process when collecting release RSS evidence. The harness
never requests an encoded view itself; it passes one retained public ontology view to Projector
and consumes only ProjectionReport diagnostics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import resource
import statistics
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, TypeVar, cast

import pyowl_core
from pyowl_core import BackendPreference, DocumentFormat, LoadOptions

from pyowl2vec_star_projector import ProjectionOptions, Projector
from pyowl2vec_star_projector.artifact import edge_json_record

T = TypeVar("T")

_REQUIRED_ZERO_COUNTERS = (
    "base_flattening_bytes",
    "materialized_scalar_rows",
    "parser_calls",
    "per_row_ffi_calls",
    "resolver_calls",
    "scalar_axiom_materializations",
    "scalar_term_materializations",
    "structural_copy_bytes",
    "wire_decoder_calls",
    "wire_encoder_calls",
)


@dataclass(slots=True)
class _CoreOperationProbe:
    """Count public acquisition/wire calls made after the retained view exists."""

    load_snapshot: int = 0
    parse_document: int = 0
    encode_snapshot: int = 0
    decode_snapshot: int = 0
    open_snapshot: int = 0

    def snapshot(self) -> dict[str, int]:
        return asdict(self)


def _counter_delta(after: Mapping[str, int], before: Mapping[str, int]) -> dict[str, int]:
    result = {name: after[name] - before[name] for name in before}
    if any(value < 0 for value in result.values()):  # pragma: no cover - probe invariant
        raise RuntimeError("core operation counters moved backwards")
    return result


@contextmanager
def _probe_core_operations() -> Iterator[_CoreOperationProbe]:
    probe = _CoreOperationProbe()
    originals: dict[str, Callable[..., object]] = {}
    for name in probe.snapshot():
        value = getattr(pyowl_core, name, None)
        if callable(value):
            originals[name] = value

    def wrapper(name: str, original: Callable[..., object]) -> Callable[..., object]:
        def counted(*args: object, **kwargs: object) -> object:
            setattr(probe, name, getattr(probe, name) + 1)
            return original(*args, **kwargs)

        return counted

    try:
        for name, original in originals.items():
            setattr(pyowl_core, name, wrapper(name, original))
        yield probe
    finally:
        for name, original in originals.items():
            setattr(pyowl_core, name, original)


def _max_rss_bytes() -> int:
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value if sys.platform == "darwin" else value * 1024


def _fingerprint(view: object, name: str) -> str:
    value = getattr(getattr(view, name), "hex", None)
    if not isinstance(value, str):
        raise RuntimeError(f"retained view omitted public {name}.hex")
    return value


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _measure(function: Callable[[], T]) -> tuple[T, float, float, int]:
    rss_before = _max_rss_bytes()
    wall_started = time.perf_counter()
    cpu_started = time.process_time()
    value = function()
    return (
        value,
        time.perf_counter() - wall_started,
        time.process_time() - cpu_started,
        max(0, _max_rss_bytes() - rss_before),
    )


def _sample(
    view: object,
    *,
    options: ProjectionOptions,
    buffer_edges: int,
    probe: _CoreOperationProbe,
) -> dict[str, object]:
    projector = Projector()
    operations_before = probe.snapshot()
    rss_before = _max_rss_bytes()
    wall_started = time.perf_counter()
    cpu_started = time.process_time()
    first_edge_seconds: float | None = None
    edge_count = 0
    digest = hashlib.sha256()
    for edge in projector.iter_edges(view, options=options, buffer_edges=buffer_edges):
        if first_edge_seconds is None:
            first_edge_seconds = time.perf_counter() - wall_started
        digest.update(edge_json_record(edge))
        edge_count += 1
    wall_seconds = time.perf_counter() - wall_started
    cpu_seconds = time.process_time() - cpu_started
    if projector.last_view is not view:
        raise RuntimeError("projector did not retain the supplied ontology identity")
    report = projector.last_report
    if report is None:
        raise RuntimeError("projector completed without publishing a report")
    ingestion = report.provenance.ingestion.to_dict()
    counters = cast(dict[str, int | bool], ingestion["counters"])
    return {
        "wall_seconds": wall_seconds,
        "cpu_seconds": cpu_seconds,
        "first_edge_seconds": first_edge_seconds,
        "incremental_peak_rss_bytes": max(0, _max_rss_bytes() - rss_before),
        "edge_count": edge_count,
        "edge_sha256": digest.hexdigest(),
        "selected_backend": report.provenance.selected_backend,
        "ingestion": ingestion,
        "counters": counters,
        "core_operation_delta": _counter_delta(probe.snapshot(), operations_before),
    }


def _acceptance_evidence(sample: Mapping[str, object]) -> dict[str, object]:
    ingestion = cast(Mapping[str, object], sample["ingestion"])
    counters = cast(Mapping[str, int | bool], sample["counters"])
    operations = cast(Mapping[str, int], sample["core_operation_delta"])
    missing = tuple(name for name in _REQUIRED_ZERO_COUNTERS if name not in counters)
    nonzero = {
        name: counters[name]
        for name in _REQUIRED_ZERO_COUNTERS
        if name in counters and counters[name] != 0
    }
    staging = counters.get("encoded_staging_copy_bytes")
    gil_released = counters.get("encoded_compiler_gil_released")
    core_calls = {name: value for name, value in operations.items() if value != 0}
    ready = (
        ingestion.get("path") == "encoded-native"
        and not missing
        and not nonzero
        and not core_calls
        and staging == 0
        and gil_released is True
    )
    return {
        "acceptance_ready": ready,
        "missing_public_zero_counters": list(missing),
        "nonzero_forbidden_counters": nonzero,
        "nonzero_core_operation_calls": core_calls,
        "direct_staging_copy_bytes": staging,
        "encoded_compiler_gil_released": gil_released,
    }


def run(
    ontology: Path,
    *,
    document_format: str,
    load_backend: str,
    projector_backend: str,
    order: str,
    duplicates: str,
    include_literals: bool,
    repetitions: int,
    warmups: int,
    buffer_edges: int,
    require_encoded_native: bool,
) -> dict[str, object]:
    if repetitions < 1 or warmups < 0 or buffer_edges < 1:
        raise ValueError("repetitions/buffer_edges must be positive and warmups nonnegative")
    path = ontology.resolve()
    view, load_wall_seconds, load_cpu_seconds, load_rss_bytes = _measure(
        lambda: pyowl_core.load_snapshot(
            path,
            options=LoadOptions(
                backend=BackendPreference(load_backend),
                format=DocumentFormat(document_format),
            ),
        )
    )
    options = ProjectionOptions(
        backend=cast(Any, projector_backend),
        order=cast(Any, order),
        duplicates=cast(Any, duplicates),
        include_literals=include_literals,
        compatibility_state="isolated",
    )
    with _probe_core_operations() as probe:
        for _index in range(warmups):
            _sample(view, options=options, buffer_edges=buffer_edges, probe=probe)
        samples = [
            _sample(view, options=options, buffer_edges=buffer_edges, probe=probe)
            for _index in range(repetitions)
        ]

    identities = {
        (
            sample["edge_count"],
            sample["edge_sha256"],
            cast(Mapping[str, object], sample["ingestion"])["path"],
        )
        for sample in samples
    }
    if len(identities) != 1:
        raise RuntimeError("benchmark repetitions changed edge identity or ingestion path")
    evidence = [_acceptance_evidence(sample) for sample in samples]
    if require_encoded_native and not all(item["acceptance_ready"] is True for item in evidence):
        raise RuntimeError("encoded-native acceptance evidence is incomplete or nonzero")

    report = getattr(view, "report", None)
    capabilities = getattr(view, "capabilities", None)
    return {
        "schema": "pyowl-projector.encoded-compiler-benchmark/1",
        "input": {
            "name": path.name,
            "bytes": path.stat().st_size,
            "sha256": _file_sha256(path),
            "format": document_format,
        },
        "core": {
            "package_version": pyowl_core.__version__,
            "load_backend": getattr(capabilities, "backend", None),
            "features": sorted(str(item) for item in getattr(capabilities, "features", ())),
            "effective_axiom_count": getattr(report, "effective_axiom_count", None),
            "structural_fingerprint": _fingerprint(view, "structural_fingerprint"),
            "logical_fingerprint": _fingerprint(view, "logical_fingerprint"),
            "signature_fingerprint": _fingerprint(view, "signature_fingerprint"),
        },
        "load_excluded": {
            "wall_seconds": load_wall_seconds,
            "cpu_seconds": load_cpu_seconds,
            "incremental_peak_rss_bytes": load_rss_bytes,
        },
        "configuration": {
            "projector_backend": projector_backend,
            "order": order,
            "duplicates": duplicates,
            "include_literals": include_literals,
            "buffer_edges": buffer_edges,
            "warmups": warmups,
            "repetitions": repetitions,
        },
        "identity": {"projector_retained_input": True},
        "samples": samples,
        "medians": {
            "wall_seconds": statistics.median(
                cast(float, item["wall_seconds"]) for item in samples
            ),
            "cpu_seconds": statistics.median(cast(float, item["cpu_seconds"]) for item in samples),
            "first_edge_seconds": statistics.median(
                cast(float, item["first_edge_seconds"])
                for item in samples
                if item["first_edge_seconds"] is not None
            )
            if any(item["first_edge_seconds"] is not None for item in samples)
            else None,
        },
        "acceptance_evidence": evidence,
        "process_peak_rss_bytes": _max_rss_bytes(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ontology", type=Path)
    parser.add_argument(
        "--format",
        default="rdfxml",
        choices=[item.value for item in DocumentFormat],
    )
    parser.add_argument(
        "--load-backend",
        default="auto",
        choices=[item.value for item in BackendPreference],
    )
    parser.add_argument("--projector-backend", default="native", choices=("python", "native"))
    parser.add_argument("--order", default="encounter", choices=("encounter", "canonical"))
    parser.add_argument("--duplicates", default="preserve", choices=("preserve", "unique"))
    parser.add_argument("--include-literals", action="store_true")
    parser.add_argument("--repetitions", type=int, default=7)
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--buffer-edges", type=int, default=250_000)
    parser.add_argument("--require-encoded-native", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run(
            args.ontology,
            document_format=args.format,
            load_backend=args.load_backend,
            projector_backend=args.projector_backend,
            order=args.order,
            duplicates=args.duplicates,
            include_literals=args.include_literals,
            repetitions=args.repetitions,
            warmups=args.warmups,
            buffer_edges=args.buffer_edges,
            require_encoded_native=args.require_encoded_native,
        )
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
