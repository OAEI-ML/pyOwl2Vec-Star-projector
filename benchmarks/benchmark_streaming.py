#!/usr/bin/env python3
"""One-process P4 canonical-streaming benchmark.

Run each configuration in a fresh process.  Ontology paths are caller supplied,
hash pinned in the result, and never copied into the repository.  The synthetic
view creates named ``SubClassOf`` axioms lazily, so neither its input nor its
projector output is materialized as a million-element Python list.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import resource
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pyowl_core
from pyowl_core import BackendPreference, DocumentFormat, LoadOptions, PythonParser
from pyowl_core.model import IRI, AxiomNode, Class, Entity, EntityKind, SubClassOf

import pyowl2vec_star_projector
from pyowl2vec_star_projector import (
    ProjectionOptions,
    Projector,
    StreamingLimits,
)
from pyowl2vec_star_projector.artifact import edge_json_record


@dataclass(frozen=True, slots=True)
class _Capabilities:
    adapter_protocol: int = 1
    model_schema: int = 2
    wire_format: tuple[int, int] = (1, 2)


class _SyntheticAxiomView:
    capabilities = _Capabilities()

    def __init__(self, count: int) -> None:
        self.count = count
        fingerprint = hashlib.sha256(f"synthetic-subclass-v1:{count}".encode()).hexdigest()
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
        if axiom_type not in (None, SubClassOf):
            return iter(())

        def generate() -> object:
            for index in range(self.count):
                yield SubClassOf(
                    Class(IRI(f"urn:p4:class:{index:09d}")),
                    Class(IRI(f"urn:p4:class:{index + 1:09d}")),
                )

        return generate()

    def signature(
        self,
        kind: EntityKind | None = None,
        *,
        scope: object = "closure",
        include_builtins: bool = True,
    ) -> tuple[Entity, ...]:
        del kind, scope, include_builtins
        return ()


class _DocumentView:
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
        if axiom_type is None:
            return iter(self._axioms)
        return (item for item in self._axioms if type(item) is axiom_type)

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


def _consume(
    view: object,
    options: ProjectionOptions,
    *,
    buffer_edges: int,
    limits: StreamingLimits,
    temporary: Path,
) -> tuple[int, str, float, dict[str, int]]:
    projector = Projector()
    digest = hashlib.sha256()
    count = 0
    started = time.perf_counter()
    for edge in projector.iter_edges(
        view,
        options=options,
        buffer_edges=buffer_edges,
        temp_directory=temporary,
        streaming_limits=limits,
    ):
        digest.update(edge_json_record(edge))
        count += 1
    duration = time.perf_counter() - started
    metrics = projector.last_spill_metrics
    if any(temporary.iterdir()):
        raise RuntimeError("streaming benchmark leaked temporary files")
    return (
        count,
        digest.hexdigest(),
        duration,
        {
            "runs_created": metrics.runs_created,
            "merge_passes": metrics.merge_passes,
            "peak_live_bytes": metrics.peak_live_bytes,
            "total_spill_bytes": metrics.total_spill_bytes,
        },
    )


def _time_to_first(
    view: object,
    options: ProjectionOptions,
    *,
    buffer_edges: int,
    limits: StreamingLimits,
    temporary: Path,
) -> float:
    iterator = Projector().iter_edges(
        view,
        options=options,
        buffer_edges=buffer_edges,
        temp_directory=temporary,
        streaming_limits=limits,
    )
    started = time.perf_counter()
    try:
        next(iterator, None)
        return time.perf_counter() - started
    finally:
        iterator.close()
        if any(temporary.iterdir()):
            raise RuntimeError("time-to-first benchmark leaked temporary files")


def _write_artifact(
    view: object,
    options: ProjectionOptions,
    *,
    buffer_edges: int,
    limits: StreamingLimits,
    temporary: Path,
) -> dict[str, object]:
    target = temporary / "edges.jsonl"
    started = time.perf_counter()
    result = Projector().write_artifact(
        view,
        target,
        options=options,
        buffer_edges=buffer_edges,
        temp_directory=temporary,
        streaming_limits=limits,
    )
    duration = time.perf_counter() - started
    target.unlink()
    if any(temporary.iterdir()):
        raise RuntimeError("artifact benchmark leaked temporary files")
    return {
        "seconds": duration,
        "bytes": result.bytes_written,
        "artifact_sha256": result.artifact_sha256,
        "canonical_edges_sha256": result.canonical_edges_sha256,
    }


def _cpu_allocation() -> list[int] | None:
    affinity = getattr(os, "sched_getaffinity", None)
    if not callable(affinity):
        return None
    return sorted(affinity(0))


def main() -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--synthetic-axioms", type=int)
    source.add_argument("--ontology", type=Path)
    parser.add_argument("--corpus-id", default="synthetic-subclass-v1")
    parser.add_argument(
        "--format",
        choices=[item.value for item in DocumentFormat],
        default=DocumentFormat.RDF_XML.value,
    )
    parser.add_argument("--allow-partial-rdf-mapping", action="store_true")
    parser.add_argument("--backend", choices=("python", "native"), default="python")
    parser.add_argument("--duplicates", choices=("preserve", "unique"), default="preserve")
    parser.add_argument("--buffer-edges", type=int, default=100_000)
    parser.add_argument("--merge-fan-in", type=int, default=32)
    parser.add_argument("--max-open-files", type=int, default=64)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--measure-artifact", action="store_true")
    args = parser.parse_args()
    if args.synthetic_axioms is not None and args.synthetic_axioms < 1:
        parser.error("synthetic-axioms must be positive")
    if args.buffer_edges < 1 or args.repetitions < 1 or args.warmups < 0:
        parser.error("buffer-edges/repetitions must be positive and warmups non-negative")

    load_started = time.perf_counter()
    source_record: dict[str, object]
    if args.synthetic_axioms is not None:
        view: object = _SyntheticAxiomView(args.synthetic_axioms)
        source_record = {
            "kind": "synthetic-axioms",
            "axioms": args.synthetic_axioms,
            "generator": "named-subclass-chain-v1",
        }
    else:
        path = args.ontology.resolve()
        source_identity = {
            "kind": "ontology",
            "file_name": path.name,
            "source_bytes": path.stat().st_size,
            "source_sha256": _file_sha256(path),
        }
        try:
            document = PythonParser().parse(
                path,
                format=DocumentFormat(args.format),
                options=LoadOptions(backend=BackendPreference.PYTHON),
                allow_partial_rdf_mapping=args.allow_partial_rdf_mapping,
            )
        except Exception as error:
            failure = {
                "schema": "pyowl-projector.streaming-benchmark/1",
                "corpus_id": args.corpus_id,
                "source": source_identity,
                "status": "load-failed",
                "failure": {
                    "type": type(error).__name__,
                    "code": str(getattr(error, "code", "")),
                    "message": str(error).replace(str(path), path.name),
                },
                "load_seconds": time.perf_counter() - load_started,
            }
            print(json.dumps(failure, ensure_ascii=False, sort_keys=True, indent=2))
            return 2
        view = _DocumentView(document)
        source_record = {
            **source_identity,
            "axioms": len(document.axioms),
            "document_fingerprint": document.document_fingerprint.digest.hex(),
        }
    load_seconds = time.perf_counter() - load_started
    snapshot_peak_rss = _max_rss_bytes()
    options = ProjectionOptions(
        backend=args.backend,
        duplicates=args.duplicates,
        order="canonical",
    )
    limits = StreamingLimits(
        merge_fan_in=args.merge_fan_in,
        max_open_files=args.max_open_files,
    )
    durations: list[float] = []
    output_digests: list[str] = []
    counts: list[int] = []
    metrics: list[dict[str, int]] = []
    artifact: dict[str, object] | None = None
    with tempfile.TemporaryDirectory(prefix="pyowl2vec-p4-benchmark-") as temporary_name:
        temporary = Path(temporary_name)
        first_edge_seconds = _time_to_first(
            view,
            options,
            buffer_edges=args.buffer_edges,
            limits=limits,
            temporary=temporary,
        )
        for _ in range(args.warmups):
            _consume(
                view,
                options,
                buffer_edges=args.buffer_edges,
                limits=limits,
                temporary=temporary,
            )
        for _ in range(args.repetitions):
            count, digest, duration, spill = _consume(
                view,
                options,
                buffer_edges=args.buffer_edges,
                limits=limits,
                temporary=temporary,
            )
            counts.append(count)
            output_digests.append(digest)
            durations.append(duration)
            metrics.append(spill)
        if args.measure_artifact:
            artifact = _write_artifact(
                view,
                options,
                buffer_edges=args.buffer_edges,
                limits=limits,
                temporary=temporary,
            )
    if len(set(counts)) != 1 or len(set(output_digests)) != 1:
        raise RuntimeError("streaming output changed across repetitions")
    process_peak_rss = _max_rss_bytes()
    result = {
        "schema": "pyowl-projector.streaming-benchmark/1",
        "corpus_id": args.corpus_id,
        "source": source_record,
        "configuration": {
            "backend": args.backend,
            "duplicates": args.duplicates,
            "order": "canonical",
            "buffer_edges": args.buffer_edges,
            "merge_fan_in": args.merge_fan_in,
            "max_open_files": args.max_open_files,
            "warmups": args.warmups,
            "repetitions": args.repetitions,
        },
        "environment": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "logical_cpus": os.cpu_count(),
            "cpu_affinity": _cpu_allocation(),
            "projector_version": pyowl2vec_star_projector.__version__,
            "core_version": pyowl_core.__version__,
        },
        "load_seconds_excluded": load_seconds,
        "time_to_first_edge_seconds": first_edge_seconds,
        "samples_seconds": durations,
        "median_seconds": statistics.median(durations),
        "edge_count": counts[0],
        "canonical_edges_sha256": output_digests[0],
        "spill_samples": metrics,
        "snapshot_peak_rss_bytes": snapshot_peak_rss,
        "process_peak_rss_bytes": process_peak_rss,
        "peak_rss_delta_upper_bound_bytes": max(0, process_peak_rss - snapshot_peak_rss),
    }
    if artifact is not None:
        result["artifact"] = artifact
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
