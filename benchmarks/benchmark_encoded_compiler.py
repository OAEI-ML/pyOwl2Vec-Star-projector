#!/usr/bin/env python3
"""Measure the load-excluded encoded-compiler handoff.

Run one backend/configuration per cold process when collecting release RSS evidence. The harness
never requests an encoded view itself; it passes one retained public ontology view to Projector
and consumes only ProjectionReport diagnostics. The default exercises the public projector. An
explicitly labelled private-candidate mode exists for development evidence but cannot satisfy the
public encoded-native acceptance gate.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import platform
import re
import resource
import statistics
import sys
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, TypeVar, cast

import pyowl_core
from pyowl_core import BackendPreference, DocumentFormat, LoadOptions

from pyowl2vec_star_projector import Edge, ProjectionOptions, Projector
from pyowl2vec_star_projector.artifact import edge_json_record

T = TypeVar("T")

_REVISION = re.compile(r"[0-9a-f]{40}")
_PRIVATE_NATIVE_COUNTERS = (
    "encoded_buffer_count",
    "encoded_detached_buffer_count",
    "encoded_segment_count",
    "encoded_zero_copy_buffers",
    "native_batch_edges",
    "native_boundary_calls",
    "native_compiled_edges",
    "native_edge_batches",
    "native_output_vector_edges",
    "native_peak_buffered_edges",
)
_PRIVATE_CANDIDATE_BLOCKERS = (
    "private-candidate-evidence-cannot-substitute-for-public-acceptance",
)
_PRIVATE_NATIVE_SURFACES = frozenset({"iterator", "sink", "digest", "artifact"})

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


def _json_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validated_revision(value: str | None, name: str) -> str | None:
    if value is not None and _REVISION.fullmatch(value) is None:
        raise ValueError(f"{name} must be a full lowercase 40-character Git SHA")
    return value


def _module_artifact(module_name: str) -> dict[str, object]:
    try:
        spec = importlib.util.find_spec(module_name)
    except (ImportError, AttributeError, ValueError) as error:
        return {
            "module": module_name,
            "available": False,
            "origin": None,
            "bytes": None,
            "sha256": None,
            "reason": f"module probe failed: {error}",
        }
    if spec is None or not isinstance(spec.origin, str):
        return {
            "module": module_name,
            "available": False,
            "origin": None,
            "bytes": None,
            "sha256": None,
            "reason": "module has no filesystem origin",
        }
    path = Path(spec.origin).resolve()
    try:
        size = path.stat().st_size
        digest = _file_sha256(path)
    except OSError as error:
        return {
            "module": module_name,
            "available": False,
            "origin": str(path),
            "bytes": None,
            "sha256": None,
            "reason": f"module artifact is unreadable: {error}",
        }
    loaded = sys.modules.get(module_name)
    raw_features = () if loaded is None else getattr(loaded, "FEATURES", ())
    features = (
        sorted(str(item) for item in raw_features)
        if isinstance(raw_features, (list, tuple, set, frozenset))
        else []
    )
    return {
        "module": module_name,
        "available": True,
        "origin": str(path),
        "bytes": size,
        "sha256": digest,
        "implementation_version": (
            None if loaded is None else getattr(loaded, "__version__", None)
        ),
        "features": features,
        "encoded_direct_kernel_version": (
            None if loaded is None else getattr(loaded, "ENCODED_DIRECT_KERNEL_VERSION", None)
        ),
        "reason": None,
    }


def _is_beneath(path: object, root: Path | None) -> bool:
    if not isinstance(path, str) or root is None:
        return False
    try:
        Path(path).resolve().relative_to(root)
    except (OSError, ValueError):
        return False
    return True


def _distribution_binding(
    distribution_name: str,
    package_module: str,
    native_module: str,
    *,
    require_native_artifact: bool,
) -> dict[str, object]:
    package = _module_artifact(package_module)
    native = _module_artifact(native_module)
    distribution_root: Path | None = None
    distribution_version: str | None = None
    record_sha256: str | None = None
    metadata_reason: str | None = None
    try:
        distribution = importlib.metadata.distribution(distribution_name)
        distribution_root = Path(str(distribution.locate_file(""))).resolve()
        distribution_version = distribution.version
        record = distribution.read_text("RECORD")
        if record is not None:
            record_sha256 = hashlib.sha256(record.encode("utf-8")).hexdigest()
        else:
            metadata_reason = "installed distribution has no RECORD"
    except importlib.metadata.PackageNotFoundError:
        metadata_reason = "distribution metadata is unavailable"
    except (OSError, UnicodeError, ValueError) as error:
        metadata_reason = f"distribution metadata is unreadable: {error}"

    package_loaded = _is_beneath(package["origin"], distribution_root)
    native_loaded = _is_beneath(native["origin"], distribution_root)
    package_version = package.get("implementation_version")
    package_version_matches = (
        isinstance(package_version, str)
        and distribution_version is not None
        and package_version == distribution_version
    )
    installed_payload = (
        package["available"] is True
        and package_loaded
        and package_version_matches
        and record_sha256 is not None
        and (not require_native_artifact or (native["available"] is True and native_loaded))
    )
    return {
        "distribution": distribution_name,
        "version": distribution_version,
        "distribution_root": None if distribution_root is None else str(distribution_root),
        "record_sha256": record_sha256,
        "package": package,
        "native_extension": native,
        "package_loaded_from_distribution": package_loaded,
        "package_version_matches_distribution": package_version_matches,
        "native_extension_loaded_from_distribution": native_loaded,
        "installed_payload": installed_payload,
        "reason": (
            None if installed_payload else metadata_reason or "module is outside distribution"
        ),
    }


def _runtime_binding(*, core_native_required: bool) -> dict[str, object]:
    return {
        "python": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
            "executable": str(Path(sys.executable).resolve()),
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "harness": {
            "path": str(Path(__file__).resolve()),
            "sha256": _file_sha256(Path(__file__).resolve()),
        },
        "projector": _distribution_binding(
            "pyowl2vec-star-projector",
            "pyowl2vec_star_projector",
            "pyowl2vec_star_projector._native",
            require_native_artifact=True,
        ),
        "core": _distribution_binding(
            "pyowl-core",
            "pyowl_core",
            "pyowl_core._native",
            require_native_artifact=core_native_required,
        ),
    }


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
    private_native_candidate: bool,
    private_native_surface: str,
) -> dict[str, object]:
    projector = Projector()
    operations_before = probe.snapshot()
    rss_before = _max_rss_bytes()
    wall_started = time.perf_counter()
    cpu_started = time.process_time()
    first_edge_seconds: float | None = None
    edge_count = 0
    digest = hashlib.sha256()
    execution_surface = "private-native-candidate" if private_native_candidate else "public"
    consumer_surface = private_native_surface if private_native_candidate else "iterator"
    consumer_metrics: dict[str, object] = {
        "first_edge_observable": consumer_surface in {"iterator", "sink"},
        "surface": consumer_surface,
    }
    if consumer_surface == "sink":
        sink_batches = 0
        peak_sink_batch_edges = 0

        def consume_batch(batch: tuple[Edge, ...]) -> None:
            nonlocal edge_count, first_edge_seconds, peak_sink_batch_edges, sink_batches
            if batch and first_edge_seconds is None:
                first_edge_seconds = time.perf_counter() - wall_started
            sink_batches += 1
            peak_sink_batch_edges = max(peak_sink_batch_edges, len(batch))
            for edge in batch:
                digest.update(edge_json_record(edge))
                edge_count += 1

        projector._project_native_encoded_to_sink(
            view,
            consume_batch,
            options=options,
            batch_size=buffer_edges,
            buffer_edges=buffer_edges,
        )
        consumer_metrics.update(
            {
                "batch_count": sink_batches,
                "peak_batch_edges": peak_sink_batch_edges,
            }
        )
        edge_sha256 = digest.hexdigest()
    elif consumer_surface == "digest":
        digest_result = projector._canonical_native_encoded_digest(
            view,
            options=options,
            buffer_edges=buffer_edges,
        )
        edge_count = digest_result.edge_count
        edge_sha256 = digest_result.sha256
        consumer_metrics.update(
            {
                "canonical_edges_sha256": digest_result.sha256,
                "duplicate_count": digest_result.duplicate_count,
            }
        )
    elif consumer_surface == "artifact":
        with tempfile.TemporaryFile(mode="w+b") as destination:
            artifact_result = projector._write_native_encoded_artifact(
                view,
                destination,
                options=options,
                buffer_edges=buffer_edges,
            )
        edge_count = artifact_result.edge_count
        edge_sha256 = artifact_result.canonical_edges_sha256
        consumer_metrics.update(
            {
                "artifact_sha256": artifact_result.artifact_sha256,
                "bytes_written": artifact_result.bytes_written,
                "canonical_edges_sha256": artifact_result.canonical_edges_sha256,
                "duplicate_count": artifact_result.duplicate_count,
            }
        )
    else:
        iterator = (
            projector._iter_native_encoded_edges(
                view,
                options=options,
                buffer_edges=buffer_edges,
            )
            if private_native_candidate
            else projector.iter_edges(view, options=options, buffer_edges=buffer_edges)
        )
        try:
            for edge in iterator:
                if first_edge_seconds is None:
                    first_edge_seconds = time.perf_counter() - wall_started
                digest.update(edge_json_record(edge))
                edge_count += 1
        finally:
            close = getattr(iterator, "close", None)
            if callable(close):
                close()
        edge_sha256 = digest.hexdigest()
    wall_seconds = time.perf_counter() - wall_started
    cpu_seconds = time.process_time() - cpu_started
    if projector.last_view is not view:
        raise RuntimeError("projector did not retain the supplied ontology identity")
    report = projector.last_report
    if report is None:
        raise RuntimeError("projector completed without publishing a report")
    ingestion = report.provenance.ingestion.to_dict()
    counters = cast(dict[str, int | bool], ingestion["counters"])
    core_operations = _counter_delta(probe.snapshot(), operations_before)
    return {
        "execution_surface": execution_surface,
        "consumer_surface": consumer_surface,
        "consumer_metrics": consumer_metrics,
        "consumer_metrics_sha256": _json_sha256(consumer_metrics),
        "wall_seconds": wall_seconds,
        "cpu_seconds": cpu_seconds,
        "first_edge_seconds": first_edge_seconds,
        "incremental_peak_rss_bytes": max(0, _max_rss_bytes() - rss_before),
        "edge_count": edge_count,
        "edge_sha256": edge_sha256,
        "selected_backend": report.provenance.selected_backend,
        "ingestion": ingestion,
        "counters": counters,
        "counter_ledger_sha256": _json_sha256(counters),
        "core_operation_delta": core_operations,
        "core_operation_ledger_sha256": _json_sha256(core_operations),
    }


def _private_counter_evidence(
    sample: Mapping[str, object],
    counters: Mapping[str, int | bool],
    *,
    buffer_edges: int,
) -> tuple[tuple[str, ...], dict[str, str]]:
    missing = tuple(name for name in _PRIVATE_NATIVE_COUNTERS if name not in counters)
    violations: dict[str, str] = {}
    if missing:
        return missing, violations

    def integer(name: str) -> int:
        value = counters[name]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            violations[name] = "must be a nonnegative integer"
            return 0
        return value

    encoded_buffers = integer("encoded_buffer_count")
    detached_buffers = integer("encoded_detached_buffer_count")
    zero_copy_buffers = integer("encoded_zero_copy_buffers")
    segments = integer("encoded_segment_count")
    native_bound = integer("native_batch_edges")
    boundary_calls = integer("native_boundary_calls")
    compiled_edges = integer("native_compiled_edges")
    native_batches = integer("native_edge_batches")
    output_vector_edges = integer("native_output_vector_edges")
    peak_buffered_edges = integer("native_peak_buffered_edges")
    emitted_edges = sample["edge_count"]
    if isinstance(emitted_edges, bool) or not isinstance(emitted_edges, int):
        violations["edge_count"] = "must be an integer"
        emitted_edges = 0

    if encoded_buffers < 1:
        violations["encoded_buffer_count"] = "must be positive"
    if detached_buffers != encoded_buffers:
        violations["encoded_detached_buffer_count"] = "must equal encoded_buffer_count"
    if zero_copy_buffers != encoded_buffers:
        violations["encoded_zero_copy_buffers"] = "must equal encoded_buffer_count"
    if segments != 1:
        violations["encoded_segment_count"] = "private candidate supports exactly one segment"
    if native_bound != buffer_edges:
        violations["native_batch_edges"] = "must equal the configured caller batch bound"
    expected_batches = (compiled_edges + buffer_edges - 1) // buffer_edges
    if native_batches != expected_batches:
        violations["native_edge_batches"] = "must equal the bounded native drain count"
    if boundary_calls != native_batches + 1:
        violations["native_boundary_calls"] = "must equal compile plus drain calls"
    if compiled_edges < emitted_edges:
        violations["native_compiled_edges"] = "must cover every emitted policy edge"
    if output_vector_edges != 0:
        violations["native_output_vector_edges"] = "must remain zero for cursor-backed drains"
    if peak_buffered_edges != min(buffer_edges, compiled_edges):
        violations["native_peak_buffered_edges"] = "must equal the largest bounded native batch"
    return missing, violations


def _acceptance_evidence(
    sample: Mapping[str, object],
    *,
    buffer_edges: int,
    runtime_binding: Mapping[str, object],
    projector_revision: str | None,
    core_revision: str | None,
) -> dict[str, object]:
    ingestion = cast(Mapping[str, object], sample["ingestion"])
    counters = cast(Mapping[str, int | bool], sample["counters"])
    operations = cast(Mapping[str, int], sample["core_operation_delta"])
    execution_surface = sample["execution_surface"]
    missing = tuple(name for name in _REQUIRED_ZERO_COUNTERS if name not in counters)
    nonzero = {
        name: counters[name]
        for name in _REQUIRED_ZERO_COUNTERS
        if name in counters and counters[name] != 0
    }
    staging = counters.get("encoded_staging_copy_bytes")
    gil_released = counters.get("encoded_compiler_gil_released")
    core_calls = {name: value for name, value in operations.items() if value != 0}
    boundary_ready = (
        ingestion.get("path") == "encoded-native"
        and not missing
        and not nonzero
        and not core_calls
        and staging == 0
        and gil_released is True
    )
    private_missing, private_violations = _private_counter_evidence(
        sample,
        counters,
        buffer_edges=buffer_edges,
    )
    private_boundary_ready = (
        execution_surface == "private-native-candidate"
        and boundary_ready
        and not private_missing
        and not private_violations
    )
    projector_binding = cast(Mapping[str, object], runtime_binding["projector"])
    core_binding = cast(Mapping[str, object], runtime_binding["core"])
    installed_artifacts = (
        projector_binding.get("installed_payload") is True
        and core_binding.get("installed_payload") is True
    )
    revisions_bound = projector_revision is not None and core_revision is not None
    evidence_binding_sha256 = _json_sha256(
        {
            "consumer_surface": sample["consumer_surface"],
            "consumer_metrics_sha256": sample["consumer_metrics_sha256"],
            "core_operation_ledger_sha256": sample["core_operation_ledger_sha256"],
            "core_revision": core_revision,
            "counter_ledger_sha256": sample["counter_ledger_sha256"],
            "edge_sha256": sample["edge_sha256"],
            "execution_surface": sample["execution_surface"],
            "projector_revision": projector_revision,
            "runtime_binding": runtime_binding,
        }
    )
    return {
        "execution_surface": execution_surface,
        "acceptance_ready": execution_surface == "public" and boundary_ready,
        "private_candidate_boundary_ready": private_boundary_ready,
        "private_candidate_evidence_ready": (
            private_boundary_ready and installed_artifacts and revisions_bound
        ),
        "installed_artifacts_bound": installed_artifacts,
        "source_revisions_bound": revisions_bound,
        "counter_ledger_sha256": sample["counter_ledger_sha256"],
        "core_operation_ledger_sha256": sample["core_operation_ledger_sha256"],
        "evidence_binding_sha256": evidence_binding_sha256,
        "missing_public_zero_counters": list(missing),
        "nonzero_forbidden_counters": nonzero,
        "nonzero_core_operation_calls": core_calls,
        "missing_private_candidate_counters": list(private_missing),
        "private_candidate_counter_violations": private_violations,
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
    private_native_candidate: bool = False,
    private_native_surface: str = "iterator",
    require_private_native_candidate: bool = False,
    projector_revision: str | None = None,
    core_revision: str | None = None,
) -> dict[str, object]:
    if repetitions < 1 or warmups < 0 or buffer_edges < 1:
        raise ValueError("repetitions/buffer_edges must be positive and warmups nonnegative")
    projector_revision = _validated_revision(projector_revision, "projector_revision")
    core_revision = _validated_revision(core_revision, "core_revision")
    if private_native_candidate and projector_backend != "native":
        raise ValueError("private native candidate requires projector_backend='native'")
    if private_native_surface not in _PRIVATE_NATIVE_SURFACES:
        raise ValueError(
            "private_native_surface must be one of " + ", ".join(sorted(_PRIVATE_NATIVE_SURFACES))
        )
    if not private_native_candidate and private_native_surface != "iterator":
        raise ValueError(
            "non-iterator private_native_surface requires private_native_candidate=True"
        )
    if private_native_candidate and private_native_surface == "digest" and order != "canonical":
        raise ValueError("private native digest measurements require order='canonical'")
    if require_private_native_candidate and not private_native_candidate:
        raise ValueError("require_private_native_candidate requires private_native_candidate=True")
    if private_native_candidate and require_encoded_native:
        raise ValueError(
            "private native candidate cannot satisfy the public require_encoded_native gate"
        )
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
            _sample(
                view,
                options=options,
                buffer_edges=buffer_edges,
                probe=probe,
                private_native_candidate=private_native_candidate,
                private_native_surface=private_native_surface,
            )
        samples = [
            _sample(
                view,
                options=options,
                buffer_edges=buffer_edges,
                probe=probe,
                private_native_candidate=private_native_candidate,
                private_native_surface=private_native_surface,
            )
            for _index in range(repetitions)
        ]

    identities = {
        (
            sample["edge_count"],
            sample["edge_sha256"],
            cast(Mapping[str, object], sample["ingestion"])["path"],
            sample["execution_surface"],
            sample["consumer_surface"],
        )
        for sample in samples
    }
    if len(identities) != 1:
        raise RuntimeError("benchmark repetitions changed edge identity or ingestion path")
    capabilities = getattr(view, "capabilities", None)
    runtime_binding = _runtime_binding(
        core_native_required=getattr(capabilities, "backend", None) == "native"
    )
    evidence = [
        _acceptance_evidence(
            sample,
            buffer_edges=buffer_edges,
            runtime_binding=runtime_binding,
            projector_revision=projector_revision,
            core_revision=core_revision,
        )
        for sample in samples
    ]
    if require_encoded_native and not all(item["acceptance_ready"] is True for item in evidence):
        raise RuntimeError("encoded-native acceptance evidence is incomplete or nonzero")
    if require_private_native_candidate and not all(
        item["private_candidate_evidence_ready"] is True for item in evidence
    ):
        raise RuntimeError(
            "private native candidate evidence requires exact counters, installed artifacts, "
            "and both source revisions"
        )

    report = getattr(view, "report", None)
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
        "source_revisions": {
            "projector": projector_revision,
            "core": core_revision,
        },
        "runtime_binding": runtime_binding,
        "runtime_binding_sha256": _json_sha256(runtime_binding),
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
            "execution_surface": (
                "private-native-candidate" if private_native_candidate else "public"
            ),
            "private_native_surface": (
                private_native_surface if private_native_candidate else None
            ),
        },
        "identity": {"projector_retained_input": True},
        "production_acceptance": {
            "private_candidate_is_public": False,
            "known_private_candidate_blockers": list(_PRIVATE_CANDIDATE_BLOCKERS),
        },
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
    parser.add_argument("--private-native-candidate", action="store_true")
    parser.add_argument(
        "--private-native-surface",
        default="iterator",
        choices=sorted(_PRIVATE_NATIVE_SURFACES),
    )
    parser.add_argument("--require-private-native-candidate", action="store_true")
    parser.add_argument("--projector-revision")
    parser.add_argument("--core-revision")
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
            private_native_candidate=args.private_native_candidate,
            private_native_surface=args.private_native_surface,
            require_private_native_candidate=args.require_private_native_candidate,
            projector_revision=args.projector_revision,
            core_revision=args.core_revision,
        )
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
