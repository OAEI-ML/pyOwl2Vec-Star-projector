"""Public view-first and standalone projection APIs."""

from __future__ import annotations

import hashlib
import importlib
import json
import threading
from collections.abc import Callable, Iterator
from dataclasses import asdict
from os import PathLike
from time import perf_counter
from typing import Any, BinaryIO

from ._version import BATCH_SINK_PROTOCOL_VERSION
from .artifact import (
    CanonicalEdgeDigest,
    EdgeArtifactResult,
)
from .artifact import (
    canonical_edge_digest as _canonical_edge_digest,
)
from .artifact import (
    write_edge_artifact as _write_edge_artifact,
)
from .backend import BackendSelection, select_backend, warn_if_auto_fallback
from .compiler import (
    Compilation,
    RoleState,
    iter_asserted_taxonomy,
    prepare_streaming_compilation,
    validate_view,
)
from .encoded import EncodedNegotiation, select_ingestion, select_private_direct_ingestion
from .encoded_compiler import (
    EncodedSubsetCompilation,
    EncodedSubsetCounters,
    prepare_encoded_subset_compilation,
)
from .errors import (
    InvalidProjectionOptionsError,
    NativeBackendUnavailableError,
    SnapshotCompatibilityError,
)
from .model import Edge
from .native import (
    NativeEncodedDirectCompilation,
    NativeEncodedDirectUnsupported,
    iter_native_passthrough,
    native_runtime_metadata,
    prepare_native_encoded_compilation,
)
from .options import Backend, DuplicatePolicy, EdgeOrder, ProjectionOptions
from .protocols import EdgeBatchSinkV1
from .provenance import (
    CoreProvenance,
    IngestionProvenance,
    ProjectionCounts,
    ProjectionProvenance,
    ProjectionReport,
    ProjectionResult,
    SourceKind,
)
from .streaming import (
    CancellationTokenLike,
    SpillMetrics,
    StreamingLimits,
    iter_edge_policy,
)

EdgeBatchSink = Callable[[tuple[Edge, ...]], object] | EdgeBatchSinkV1


class Projector:
    """Compile shared core views without reparsing or ontology materialization."""

    def __init__(self) -> None:
        self._scala_state = RoleState.empty()
        self._scala_lock = threading.Lock()
        self._metadata_lock = threading.Lock()
        self._last_view: object | None = None
        self._last_report: ProjectionReport | None = None
        self._last_spill_metrics = SpillMetrics(0, 0, 0, 0)
        self._last_encoded_counters: EncodedSubsetCounters | None = None
        self._scala_invocation_count = 0
        self._scala_call_history_digest = ""

    @property
    def last_view(self) -> object | None:
        """Identity-preserving diagnostic hook for integration tests."""
        with self._metadata_lock:
            return self._last_view

    @property
    def last_report(self) -> ProjectionReport | None:
        with self._metadata_lock:
            return self._last_report

    @property
    def last_spill_metrics(self) -> SpillMetrics:
        """Path-free spill accounting for the most recently active iterator."""
        with self._metadata_lock:
            return self._last_spill_metrics

    @property
    def last_encoded_counters(self) -> EncodedSubsetCounters | None:
        """Bounded-work counters for the incomplete encoded compiler slice."""
        with self._metadata_lock:
            return self._last_encoded_counters

    def project(
        self,
        view: object,
        *,
        options: ProjectionOptions | None = None,
    ) -> list[Edge]:
        """Materialize a projection of an existing ``OntologyView``."""
        return list(self.iter_edges(view, options=options))

    def project_with_report(
        self,
        view: object,
        *,
        options: ProjectionOptions | None = None,
    ) -> ProjectionResult:
        edges = self.project(view, options=options)
        report = self.last_report
        if report is None:  # pragma: no cover - guarded by complete consumption
            raise RuntimeError("projection completed without a report")
        return ProjectionResult(tuple(edges), report)

    def iter_edges(
        self,
        view: object,
        *,
        options: ProjectionOptions | None = None,
        buffer_edges: int = 250_000,
        temp_directory: PathLike[str] | None = None,
        streaming_limits: StreamingLimits | None = None,
        cancellation_token: CancellationTokenLike | None = None,
    ) -> Iterator[Edge]:
        """Return a backpressured iterator with bounded canonical spill."""
        _positive_int("buffer_edges", buffer_edges)
        effective = options or ProjectionOptions()
        return self._iter_view(
            view,
            effective,
            source_kind=_view_source_kind(view),
            native_batch_edges=buffer_edges,
            buffer_edges=buffer_edges,
            temp_directory=temp_directory,
            streaming_limits=_streaming_limits(streaming_limits),
            cancellation_token=cancellation_token,
        )

    def _iter_native_encoded_edges(
        self,
        view: object,
        *,
        options: ProjectionOptions | None = None,
        buffer_edges: int = 250_000,
        temp_directory: PathLike[str] | None = None,
        streaming_limits: StreamingLimits | None = None,
        cancellation_token: CancellationTokenLike | None = None,
    ) -> Iterator[Edge]:
        """Exercise the hidden P7 direct-batch iterator with whole-call fallback."""

        _positive_int("buffer_edges", buffer_edges)
        effective = options or ProjectionOptions(backend="native")
        return self._iter_view(
            view,
            effective,
            source_kind=_view_source_kind(view),
            native_batch_edges=buffer_edges,
            buffer_edges=buffer_edges,
            temp_directory=temp_directory,
            streaming_limits=_streaming_limits(streaming_limits),
            cancellation_token=cancellation_token,
            private_encoded_direct=True,
        )

    def project_to_sink(
        self,
        view: object,
        sink: EdgeBatchSink,
        *,
        options: ProjectionOptions | None = None,
        batch_size: int = 65_536,
        buffer_edges: int = 250_000,
        temp_directory: PathLike[str] | None = None,
        streaming_limits: StreamingLimits | None = None,
        cancellation_token: CancellationTokenLike | None = None,
    ) -> ProjectionReport:
        """Push bounded immutable batches and return the completed report."""
        write_batch, finish = _sink_operations(sink)
        _positive_int("batch_size", batch_size)
        batch: list[Edge] = []
        iterator = self.iter_edges(
            view,
            options=options,
            buffer_edges=buffer_edges,
            temp_directory=temp_directory,
            streaming_limits=streaming_limits,
            cancellation_token=cancellation_token,
        )
        try:
            for edge in iterator:
                batch.append(edge)
                if len(batch) == batch_size:
                    write_batch(tuple(batch))
                    batch.clear()
            if batch:
                write_batch(tuple(batch))
        finally:
            _close_iterator(iterator)
        report = self.last_report
        if report is None:  # pragma: no cover - guarded by complete consumption
            raise RuntimeError("projection completed without a report")
        if finish is not None:
            finish(report)
        return report

    def write_artifact(
        self,
        view: object,
        destination: PathLike[str] | BinaryIO,
        *,
        options: ProjectionOptions | None = None,
        buffer_edges: int = 250_000,
        temp_directory: PathLike[str] | None = None,
        streaming_limits: StreamingLimits | None = None,
        cancellation_token: CancellationTokenLike | None = None,
    ) -> EdgeArtifactResult:
        """Write a portable version-1 JSONL artifact without an edge list."""
        return _write_edge_artifact(
            self,
            view,
            destination,
            options=options,
            buffer_edges=buffer_edges,
            temp_directory=temp_directory,
            streaming_limits=streaming_limits,
            cancellation_token=cancellation_token,
        )

    def canonical_digest(
        self,
        view: object,
        *,
        options: ProjectionOptions | None = None,
        buffer_edges: int = 250_000,
        temp_directory: PathLike[str] | None = None,
        streaming_limits: StreamingLimits | None = None,
        cancellation_token: CancellationTokenLike | None = None,
    ) -> CanonicalEdgeDigest:
        """Hash canonical JSON edge records in one ontology traversal."""
        return _canonical_edge_digest(
            self,
            view,
            options=options,
            buffer_edges=buffer_edges,
            temp_directory=temp_directory,
            streaming_limits=streaming_limits,
            cancellation_token=cancellation_token,
        )

    def project_taxonomy(
        self,
        view: object,
        *,
        bidirectional: bool = False,
        duplicates: DuplicatePolicy = "preserve",
        order: EdgeOrder = "canonical",
        backend: Backend = "auto",
        buffer_edges: int = 250_000,
        temp_directory: PathLike[str] | None = None,
        streaming_limits: StreamingLimits | None = None,
        cancellation_token: CancellationTokenLike | None = None,
    ) -> list[Edge]:
        return list(
            self.iter_taxonomy_edges(
                view,
                bidirectional=bidirectional,
                duplicates=duplicates,
                order=order,
                backend=backend,
                buffer_edges=buffer_edges,
                temp_directory=temp_directory,
                streaming_limits=streaming_limits,
                cancellation_token=cancellation_token,
            )
        )

    def iter_taxonomy_edges(
        self,
        view: object,
        *,
        bidirectional: bool = False,
        duplicates: DuplicatePolicy = "preserve",
        order: EdgeOrder = "canonical",
        backend: Backend = "auto",
        buffer_edges: int = 250_000,
        temp_directory: PathLike[str] | None = None,
        streaming_limits: StreamingLimits | None = None,
        cancellation_token: CancellationTokenLike | None = None,
    ) -> Iterator[Edge]:
        _positive_int("buffer_edges", buffer_edges)
        if type(bidirectional) is not bool:
            raise InvalidProjectionOptionsError("bidirectional must be bool")
        if duplicates not in ("preserve", "unique"):
            raise InvalidProjectionOptionsError("duplicates must be 'preserve' or 'unique'")
        if order not in ("canonical", "encounter"):
            raise InvalidProjectionOptionsError("order must be 'canonical' or 'encounter'")
        selection = select_backend(backend)
        selection, _native_version, native_features = _activate_selection(selection)
        warn_if_auto_fallback(selection)
        checked = validate_view(view)
        ingestion = select_ingestion(
            checked,
            selected_backend=selection.selected,
            native_features=native_features,
            backend_fallback_reason=selection.fallback_reason,
        )
        encoded_compilation, ingestion, encoded_counters = prepare_encoded_subset_compilation(
            checked,
            ProjectionOptions(
                bidirectional_taxonomy=bidirectional,
                only_taxonomy=True,
                duplicates="preserve",
                order="encounter",
                backend=backend,
            ),
            ingestion,
            batch_edges=buffer_edges,
            asserted_taxonomy_only=True,
        )
        with self._metadata_lock:
            self._last_view = checked
            self._last_encoded_counters = encoded_counters
        raw = (
            self._iter_encoded_raw(encoded_compilation)
            if encoded_compilation is not None
            else iter_asserted_taxonomy(
                checked,
                bidirectional=bidirectional,
                duplicates="preserve",
                order="encounter",
            )
        )
        if selection.selected == "native":
            raw = iter_native_passthrough(raw, batch_edges=buffer_edges)
        return iter_edge_policy(
            raw,
            duplicates=duplicates,
            order=order,
            buffer_edges=buffer_edges,
            temp_directory=temp_directory,
            limits=_streaming_limits(streaming_limits),
            cancellation_token=cancellation_token,
            metrics_sink=self._remember_spill_metrics,
        )

    def _iter_view(
        self,
        view: object,
        options: ProjectionOptions,
        *,
        source_kind: SourceKind,
        native_batch_edges: int = 250_000,
        buffer_edges: int = 250_000,
        temp_directory: PathLike[str] | None = None,
        streaming_limits: StreamingLimits | None = None,
        cancellation_token: CancellationTokenLike | None = None,
        private_encoded_direct: bool = False,
    ) -> Iterator[Edge]:
        def generate() -> Iterator[Edge]:
            acquired = False
            native_encoded_compilation: NativeEncodedDirectCompilation | None = None
            if options.compatibility_state == "scala-instance":
                acquired = self._scala_lock.acquire(blocking=False)
                if not acquired:
                    raise InvalidProjectionOptionsError(
                        "compatibility_state='scala-instance' is non-concurrent",
                        details={"compatibility_state": "scala-instance"},
                    )
            try:
                selection = select_backend(options.backend)
                selection, native_version, native_features = _activate_selection(selection)
                warn_if_auto_fallback(selection)
                checked = validate_view(view)
                publication_started = perf_counter()
                if private_encoded_direct:
                    ingestion = select_private_direct_ingestion(
                        checked,
                        selected_backend=selection.selected,
                        backend_fallback_reason=selection.fallback_reason,
                    )
                else:
                    ingestion = select_ingestion(
                        checked,
                        selected_backend=selection.selected,
                        native_features=native_features,
                        backend_fallback_reason=selection.fallback_reason,
                    )
                publication_seconds = perf_counter() - publication_started
                encoded_view_publication_seconds = (
                    publication_seconds if ingestion.path == "encoded-native" else None
                )
                role_state = (
                    self._scala_state
                    if options.compatibility_state == "scala-instance"
                    else RoleState.empty()
                )
                compile_started = perf_counter()
                limits = _streaming_limits(streaming_limits)
                if private_encoded_direct and ingestion.path == "encoded-native":
                    lease = ingestion.lease
                    if lease is None:  # pragma: no cover - guarded by negotiation
                        raise SnapshotCompatibilityError(
                            "hidden native encoded ingestion lost its validated lease"
                        )
                    try:
                        native_encoded_compilation, private_fallback_reason = (
                            prepare_native_encoded_compilation(
                                checked,
                                lease,
                                options,
                                batch_edges=native_batch_edges,
                                max_total_edges=limits.max_total_edges,
                                cancellation_token=cancellation_token,
                            )
                        )
                    except (
                        NativeBackendUnavailableError,
                        NativeEncodedDirectUnsupported,
                    ) as error:
                        private_fallback_reason = (
                            f"private native direct compiler unavailable: {error}"
                        )
                    if native_encoded_compilation is None:
                        reason = private_fallback_reason or (
                            "private native direct compiler declined the encoded view"
                        )
                        ingestion = EncodedNegotiation(
                            "scalar-native",
                            f"{reason}; selected whole-operation scalar compiler",
                        )
                if native_encoded_compilation is None:
                    encoded_compilation, ingestion, encoded_counters = (
                        prepare_encoded_subset_compilation(
                            checked,
                            options,
                            ingestion,
                            batch_edges=native_batch_edges,
                            role_state=role_state,
                        )
                    )
                else:
                    encoded_compilation = None
                    encoded_counters = None
                compilation: (
                    Compilation
                    | EncodedSubsetCompilation
                    | NativeEncodedDirectCompilation
                )
                if native_encoded_compilation is not None:
                    compilation = native_encoded_compilation
                elif encoded_compilation is None:
                    compilation = prepare_streaming_compilation(checked, options, role_state)
                else:
                    compilation = encoded_compilation
                if options.compatibility_state == "scala-instance":
                    compilation.prepare_role_state()
                consumer_compile_seconds = perf_counter() - compile_started
                with self._metadata_lock:
                    self._last_view = compilation.view
                    self._last_report = None
                    self._last_encoded_counters = encoded_counters
                    if options.compatibility_state == "scala-instance":
                        self._scala_invocation_count += 1
                        invocation = self._scala_invocation_count
                    else:
                        invocation = 1
                output_count = 0
                if native_encoded_compilation is not None:
                    raw_edges = native_encoded_compilation.iter_raw_edges(cancellation_token)
                elif encoded_compilation is not None:
                    raw_edges = self._iter_encoded_raw(encoded_compilation)
                else:
                    raw_edges = compilation.iter_raw_edges()
                if selection.selected == "native" and native_encoded_compilation is None:
                    raw_edges = iter_native_passthrough(
                        raw_edges,
                        batch_edges=native_batch_edges,
                    )
                compiled_edges = iter_edge_policy(
                    raw_edges,
                    duplicates=options.duplicates,
                    order=options.order,
                    buffer_edges=buffer_edges,
                    temp_directory=temp_directory,
                    limits=limits,
                    statistics=compilation.statistics,
                    cancellation_token=cancellation_token,
                    metrics_sink=self._remember_spill_metrics,
                )
                for edge in compiled_edges:
                    output_count += 1
                    yield edge
                report = self._report(
                    compilation.view,
                    options,
                    source_kind,
                    output_count,
                    compilation.statistics.duplicate_edges,
                    compilation.statistics.skipped_axioms,
                    compilation.statistics.ignored_shapes,
                    compilation.diagnostics,
                    selection.fallback_reason,
                    invocation,
                    selection.selected,
                    native_version,
                    ingestion,
                    encoded_view_publication_seconds,
                    consumer_compile_seconds,
                    encoded_compilation,
                    native_encoded_compilation,
                )
                with self._metadata_lock:
                    self._last_report = report
            finally:
                if native_encoded_compilation is not None:
                    native_encoded_compilation.batches.close()
                if acquired:
                    self._scala_lock.release()

        return generate()

    def _iter_encoded_raw(
        self,
        compilation: EncodedSubsetCompilation,
    ) -> Iterator[Edge]:
        try:
            yield from compilation.iter_raw_edges()
        finally:
            with self._metadata_lock:
                self._last_encoded_counters = compilation.counters

    def _remember_spill_metrics(self, metrics: SpillMetrics) -> None:
        with self._metadata_lock:
            self._last_spill_metrics = metrics

    def _report(
        self,
        view: object,
        options: ProjectionOptions,
        source_kind: SourceKind,
        edge_count: int,
        duplicate_count: int,
        skipped_axioms: int,
        ignored_shapes: int,
        diagnostics: tuple[Any, ...],
        fallback_reason: str | None,
        invocation: int,
        selected_backend: str,
        native_version: str | None,
        ingestion: EncodedNegotiation,
        encoded_view_publication_seconds: float | None,
        consumer_compile_seconds: float,
        encoded_compilation: EncodedSubsetCompilation | None,
        native_encoded_compilation: NativeEncodedDirectCompilation | None,
    ) -> ProjectionReport:
        diagnostic_payload = [asdict(item) for item in diagnostics]
        diagnostic_bytes = json.dumps(
            diagnostic_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        diagnostics_digest = hashlib.sha256(diagnostic_bytes).hexdigest()
        warning_count = sum(
            item.count for item in diagnostics if getattr(item, "severity", None) == "warning"
        )
        if fallback_reason is not None:
            warning_count += 1
        counts = ProjectionCounts(
            edges=edge_count,
            duplicates=duplicate_count,
            skipped_axioms=skipped_axioms,
            ignored_shapes=ignored_shapes,
            warnings=warning_count,
        )
        history_item = json.dumps(
            {
                "fingerprint": _fingerprint(view, "structural_fingerprint"),
                # Backend identity is recorded separately and must not change
                # the semantic call-history digest used for parity checks.
                "options": {
                    name: value for name, value in options.to_dict().items() if name != "backend"
                },
                "invocation": invocation,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        with self._metadata_lock:
            prior = (
                self._scala_call_history_digest.encode("ascii")
                if options.compatibility_state == "scala-instance"
                else b""
            )
            history_digest = hashlib.sha256(prior + b"\0" + history_item).hexdigest()
            if options.compatibility_state == "scala-instance":
                self._scala_call_history_digest = history_digest
        provenance = ProjectionProvenance(
            options=options,
            selected_backend=selected_backend,  # type: ignore[arg-type]
            source_kind=source_kind,
            core=_core_provenance(view),
            counts=counts,
            diagnostics_digest=diagnostics_digest,
            invocation_count=invocation,
            call_history_digest=history_digest,
            native_implementation_version=native_version,
            ingestion=_ingestion_provenance(
                ingestion,
                encoded_view_publication_seconds=encoded_view_publication_seconds,
                consumer_compile_seconds=consumer_compile_seconds,
                encoded_compilation=encoded_compilation,
                native_encoded_compilation=native_encoded_compilation,
            ),
        )
        return ProjectionReport(provenance, diagnostics)


def project_source(
    source: object,
    *,
    options: ProjectionOptions | None = None,
    load_options: object | None = None,
    resolver: object | None = None,
) -> list[Edge]:
    """Coerce any core ``OntologyInput`` exactly once, then project by identity."""
    projector = Projector()
    view, source_kind = _coerce_once(source, load_options=load_options, resolver=resolver)
    effective = options or ProjectionOptions()
    return list(
        projector._iter_view(
            view,
            effective,
            source_kind=source_kind,
            native_batch_edges=250_000,
        )
    )


def iter_source_edges(
    source: object,
    *,
    options: ProjectionOptions | None = None,
    load_options: object | None = None,
    resolver: object | None = None,
    buffer_edges: int = 250_000,
    temp_directory: PathLike[str] | None = None,
    streaming_limits: StreamingLimits | None = None,
    cancellation_token: CancellationTokenLike | None = None,
) -> Iterator[Edge]:
    _positive_int("buffer_edges", buffer_edges)
    projector = Projector()
    view, source_kind = _coerce_once(source, load_options=load_options, resolver=resolver)
    return projector._iter_view(
        view,
        options or ProjectionOptions(),
        source_kind=source_kind,
        native_batch_edges=buffer_edges,
        buffer_edges=buffer_edges,
        temp_directory=temp_directory,
        streaming_limits=_streaming_limits(streaming_limits),
        cancellation_token=cancellation_token,
    )


def write_edge_artifact(
    view: object,
    destination: PathLike[str] | BinaryIO,
    *,
    options: ProjectionOptions | None = None,
    buffer_edges: int = 250_000,
    temp_directory: PathLike[str] | None = None,
    streaming_limits: StreamingLimits | None = None,
    cancellation_token: CancellationTokenLike | None = None,
) -> EdgeArtifactResult:
    """Convenience artifact writer for an existing shared view."""
    return Projector().write_artifact(
        view,
        destination,
        options=options,
        buffer_edges=buffer_edges,
        temp_directory=temp_directory,
        streaming_limits=streaming_limits,
        cancellation_token=cancellation_token,
    )


def _activate_selection(
    selection: BackendSelection,
) -> tuple[BackendSelection, str | None, frozenset[str]]:
    """Load only at dispatch and preserve auto fallback if a wheel is broken."""
    if selection.selected == "python":
        return selection, None, frozenset()
    try:
        version, features = native_runtime_metadata()
        return selection, version, features
    except NativeBackendUnavailableError as error:
        if selection.requested == "native":
            raise
        fallback = BackendSelection(
            requested="auto",
            selected="python",
            fallback_reason=f"native load failed: {error}",
        )
        return fallback, None, frozenset()


def _ingestion_provenance(
    ingestion: EncodedNegotiation,
    *,
    encoded_view_publication_seconds: float | None,
    consumer_compile_seconds: float,
    encoded_compilation: EncodedSubsetCompilation | None,
    native_encoded_compilation: NativeEncodedDirectCompilation | None,
) -> IngestionProvenance:
    lease = ingestion.lease
    return IngestionProvenance(
        path=ingestion.path,
        reason=ingestion.reason,
        encoded_schema_name=None if lease is None else lease.schema_name,
        encoded_schema_version=None if lease is None else lease.schema_version,
        encoded_descriptor_sha256=None if lease is None else lease.descriptor_sha256,
        encoded_view_publication_seconds=(
            encoded_view_publication_seconds if ingestion.path == "encoded-native" else None
        ),
        consumer_compile_seconds=consumer_compile_seconds,
        counters=(
            native_encoded_compilation.ingestion_counters
            if native_encoded_compilation is not None
            else (
                _empty_ingestion_counters()
                if encoded_compilation is None
                else encoded_compilation.ingestion_counters
            )
        ),
    )


def _empty_ingestion_counters() -> dict[str, int | bool]:
    return {
        "base_flattening_bytes": 0,
        "encoded_buffer_bytes": 0,
        "encoded_buffer_count": 0,
        "encoded_compiler_gil_released": False,
        "encoded_detached_buffer_count": 0,
        "encoded_indexed_buffer_count": 0,
        "encoded_posting_bytes": 0,
        "encoded_referenced_view_count": 0,
        "encoded_segment_count": 0,
        "encoded_staging_copy_bytes": 0,
        "encoded_zero_copy_buffers": 0,
        "materialized_scalar_rows": 0,
        "parser_calls": 0,
        "per_row_ffi_calls": 0,
        "resolver_calls": 0,
        "scalar_axiom_materializations": 0,
        "scalar_term_materializations": 0,
        "structural_copy_bytes": 0,
        "wire_decoder_calls": 0,
        "wire_encoder_calls": 0,
    }


def project_taxonomy(
    source: object,
    *,
    bidirectional: bool = False,
    duplicates: DuplicatePolicy = "preserve",
    order: EdgeOrder = "canonical",
    backend: Backend = "auto",
    load_options: object | None = None,
    resolver: object | None = None,
    buffer_edges: int = 250_000,
    temp_directory: PathLike[str] | None = None,
    streaming_limits: StreamingLimits | None = None,
    cancellation_token: CancellationTokenLike | None = None,
) -> list[Edge]:
    view, _ = _coerce_once(source, load_options=load_options, resolver=resolver)
    return Projector().project_taxonomy(
        view,
        bidirectional=bidirectional,
        duplicates=duplicates,
        order=order,
        backend=backend,
        buffer_edges=buffer_edges,
        temp_directory=temp_directory,
        streaming_limits=streaming_limits,
        cancellation_token=cancellation_token,
    )


def iter_taxonomy_edges(
    source: object,
    *,
    bidirectional: bool = False,
    duplicates: DuplicatePolicy = "preserve",
    order: EdgeOrder = "canonical",
    backend: Backend = "auto",
    load_options: object | None = None,
    resolver: object | None = None,
    buffer_edges: int = 250_000,
    temp_directory: PathLike[str] | None = None,
    streaming_limits: StreamingLimits | None = None,
    cancellation_token: CancellationTokenLike | None = None,
) -> Iterator[Edge]:
    view, _ = _coerce_once(source, load_options=load_options, resolver=resolver)
    return Projector().iter_taxonomy_edges(
        view,
        bidirectional=bidirectional,
        duplicates=duplicates,
        order=order,
        backend=backend,
        buffer_edges=buffer_edges,
        temp_directory=temp_directory,
        streaming_limits=streaming_limits,
        cancellation_token=cancellation_token,
    )


def _coerce_once(
    source: object,
    *,
    load_options: object | None,
    resolver: object | None,
) -> tuple[object, SourceKind]:
    try:
        core = importlib.import_module("pyowl_core")
    except ImportError as error:  # pragma: no cover - declared dependency
        raise SnapshotCompatibilityError("pyowl-core is not installed") from error
    coerce = getattr(core, "coerce_snapshot", None)
    if not callable(coerce):
        raise SnapshotCompatibilityError(
            "installed pyowl-core does not yet expose coerce_snapshot; "
            "use Projector.project(existing_view) until core WP03 is active"
        )
    supplied_by_provider = callable(getattr(source, "owl_snapshot", None))
    view = coerce(source, options=load_options, resolver=resolver)
    checked = validate_view(view)
    source_kind: SourceKind = "provider" if supplied_by_provider else _view_source_kind(checked)
    return checked, source_kind


def _positive_int(name: str, value: object) -> None:
    if type(value) is not int or value < 1:
        raise InvalidProjectionOptionsError(f"{name} must be a positive int")


def _streaming_limits(value: StreamingLimits | None) -> StreamingLimits:
    if value is None:
        return StreamingLimits()
    if not isinstance(value, StreamingLimits):
        raise InvalidProjectionOptionsError("streaming_limits must be StreamingLimits or None")
    return value


def _sink_operations(
    sink: EdgeBatchSink,
) -> tuple[
    Callable[[tuple[Edge, ...]], object],
    Callable[[ProjectionReport], object] | None,
]:
    if callable(sink):
        return sink, None
    writer = getattr(sink, "write_batch", None)
    if not callable(writer):
        raise TypeError("sink must be callable or expose write_batch()")
    version = getattr(sink, "protocol_version", None)
    if type(version) is not int or version != BATCH_SINK_PROTOCOL_VERSION:
        raise InvalidProjectionOptionsError(
            "batch sink protocol version is incompatible",
            details={
                "expected_protocol_version": BATCH_SINK_PROTOCOL_VERSION,
                "actual_protocol_version": version if type(version) is int else -1,
            },
        )
    finish = getattr(sink, "finish", None)
    if finish is not None and not callable(finish):
        raise TypeError("sink.finish must be callable when present")
    return writer, finish


def _close_iterator(iterator: object) -> None:
    close = getattr(iterator, "close", None)
    if callable(close):
        close()


def _fingerprint(view: object, name: str) -> str:
    value = getattr(view, name, "")
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, str):
        return value
    hex_value = getattr(value, "hex", None)
    if isinstance(hex_value, str):
        return hex_value
    if callable(hex_value):
        result = hex_value()
        if isinstance(result, str):
            return result
    return str(value) if value is not None else ""


def _view_source_kind(view: object) -> SourceKind:
    if getattr(view, "wire_verified", False) is True:
        return "wire"
    capabilities = getattr(view, "capabilities", None)
    features = getattr(capabilities, "features", ())
    try:
        if "wire-verified" in features:
            return "wire"
    except TypeError:
        pass
    return "direct"


def _core_manifests(view: object) -> tuple[object, ...]:
    """Collect public leaf import manifests without materializing a view."""
    pending = [view]
    observed_views: set[int] = set()
    observed_manifests: set[int] = set()
    manifests: list[object] = []
    while pending:
        current = pending.pop()
        identity = id(current)
        if identity in observed_views:
            continue
        observed_views.add(identity)
        capabilities = getattr(current, "capabilities", None)
        features = getattr(capabilities, "features", ())
        try:
            lazy_mapped = "mmap-snapshot" in features
        except TypeError:
            lazy_mapped = False
        # The current core mapping exposes import_manifest by materializing the
        # complete model. Wait for its public summary provenance instead.
        manifest = None if lazy_mapped else getattr(current, "import_manifest", None)
        if manifest is not None and id(manifest) not in observed_manifests:
            observed_manifests.add(id(manifest))
            manifests.append(manifest)
        base = getattr(current, "base", None)
        if base is not None:
            pending.append(base)
        members = getattr(current, "members", ())
        if isinstance(members, tuple):
            for member in reversed(members):
                member_view = getattr(member, "view", None)
                if member_view is not None:
                    pending.append(member_view)
    return tuple(manifests)


def _manifest_payload(manifest: object) -> bytes | None:
    canonical = getattr(manifest, "canonical_bytes", None)
    if callable(canonical):
        value = canonical()
        if not isinstance(value, bytes):
            raise SnapshotCompatibilityError("core import manifest bytes are not immutable bytes")
        return value
    return None


def _manifest_provenance(view: object) -> tuple[str, tuple[str, ...]]:
    report = getattr(view, "report", None)
    reported_digest = getattr(report, "import_manifest_digest", "")
    reported_identities = getattr(report, "closure_document_identities", ())
    if isinstance(reported_digest, str) and reported_digest:
        if not isinstance(reported_identities, (tuple, list)) or not all(
            isinstance(item, str) and item for item in reported_identities
        ):
            raise SnapshotCompatibilityError(
                "core closure document identities are not a string sequence"
            )
        return reported_digest, tuple(reported_identities)

    manifests = _core_manifests(view)
    payloads = tuple(
        (manifest, payload)
        for manifest in manifests
        if (payload := _manifest_payload(manifest)) is not None
    )
    if not payloads:
        return "", ()

    ordered = tuple(
        sorted(
            (payload for _manifest, payload in payloads),
            key=lambda value: hashlib.sha256(value).digest(),
        )
    )
    if len(ordered) == 1:
        digest = hashlib.sha256(ordered[0]).hexdigest()
    else:
        combined = hashlib.sha256(b"pyowl-projector:import-manifests:v1\0")
        for payload in ordered:
            combined.update(len(payload).to_bytes(8, "big"))
            combined.update(payload)
        digest = combined.hexdigest()

    identities: list[str] = []
    multiple = len(payloads) > 1
    for manifest, payload in payloads:
        prefix = hashlib.sha256(payload).hexdigest() + ":" if multiple else ""
        records = getattr(manifest, "documents", ())
        if not isinstance(records, tuple):
            continue
        for record in records:
            key = getattr(record, "document_key", None)
            if isinstance(key, str) and key:
                identities.append(prefix + key)
    return digest, tuple(sorted(set(identities)))


def _identity_index_provenance(
    view: object,
    core: object,
) -> tuple[str, tuple[str, ...], str] | None:
    """Read the core's immutable whole-view summary without model materialization."""
    capabilities = getattr(view, "capabilities", None)
    features = getattr(capabilities, "features", ())
    try:
        advertised = "ontology-identity-index" in features
    except TypeError:
        advertised = False
    if not advertised:
        return None
    index_type = getattr(core, "OntologyIdentityIndex", None)
    index_factory = getattr(view, "view", None)
    if not isinstance(index_type, type) or not callable(index_factory):
        raise SnapshotCompatibilityError(
            "core advertises ontology identity provenance without its public index"
        )
    identity = index_factory(index_type)
    manifest_digest = getattr(identity, "import_manifest_digest", None)
    diagnostics_digest = getattr(identity, "loader_diagnostics_digest", None)
    document_keys = getattr(identity, "document_keys", None)
    if not isinstance(manifest_digest, bytes) or len(manifest_digest) != 32:
        raise SnapshotCompatibilityError("core import manifest digest is not bytes32")
    if not isinstance(diagnostics_digest, bytes) or len(diagnostics_digest) != 32:
        raise SnapshotCompatibilityError("core loader diagnostics digest is not bytes32")
    if not isinstance(document_keys, tuple) or not all(
        isinstance(item, str) and item for item in document_keys
    ):
        raise SnapshotCompatibilityError("core closure document keys are not immutable strings")
    return manifest_digest.hex(), document_keys, diagnostics_digest.hex()


def _loader_diagnostics_digest(view: object) -> str:
    report = getattr(view, "report", None)
    diagnostics = getattr(report, "diagnostics", None)
    if diagnostics is None:
        return ""
    payload: list[object] = []
    try:
        values = tuple(diagnostics)
    except TypeError as error:
        raise SnapshotCompatibilityError("core loader diagnostics are not iterable") from error
    for diagnostic in values:
        serialize = getattr(diagnostic, "to_dict", None)
        if not callable(serialize):
            raise SnapshotCompatibilityError("core loader diagnostic is not serializable")
        payload.append(serialize())
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _core_provenance(view: object) -> CoreProvenance:
    core = importlib.import_module("pyowl_core")
    capabilities = view.capabilities  # type: ignore[attr-defined]
    wire = getattr(capabilities, "wire_format", getattr(core, "WIRE_FORMAT_VERSION", (1, 0)))
    identity = _identity_index_provenance(view, core)
    if identity is None:
        manifest_digest, document_identities = _manifest_provenance(view)
        loader_diagnostics_digest = _loader_diagnostics_digest(view)
    else:
        manifest_digest, document_identities, loader_diagnostics_digest = identity
    return CoreProvenance(
        package_version=str(getattr(core, "__version__", "unknown")),
        api_version=tuple(getattr(core, "API_VERSION", (0, 0))),
        model_schema_version=int(capabilities.model_schema),
        wire_format_version=tuple(wire),
        adapter_protocol_version=int(capabilities.adapter_protocol),
        structural_fingerprint=_fingerprint(view, "structural_fingerprint"),
        logical_fingerprint=_fingerprint(view, "logical_fingerprint"),
        signature_fingerprint=_fingerprint(view, "signature_fingerprint"),
        import_manifest_digest=manifest_digest,
        closure_document_identities=document_identities,
        loader_diagnostics_digest=loader_diagnostics_digest,
    )


__all__ = [
    "EdgeBatchSink",
    "Projector",
    "iter_source_edges",
    "iter_taxonomy_edges",
    "project_source",
    "project_taxonomy",
    "write_edge_artifact",
]
