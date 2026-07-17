"""Public view-first and standalone projection APIs."""

from __future__ import annotations

import hashlib
import importlib
import json
import threading
from collections.abc import Callable, Iterator
from dataclasses import asdict
from os import PathLike
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
    RoleState,
    iter_asserted_taxonomy,
    prepare_streaming_compilation,
    validate_view,
)
from .errors import (
    InvalidProjectionOptionsError,
    NativeBackendUnavailableError,
    SnapshotCompatibilityError,
)
from .model import Edge
from .native import (
    iter_native_passthrough,
    native_implementation_version,
)
from .options import Backend, DuplicatePolicy, EdgeOrder, ProjectionOptions
from .protocols import EdgeBatchSinkV1
from .provenance import (
    CoreProvenance,
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
            source_kind="direct",
            native_batch_edges=buffer_edges,
            buffer_edges=buffer_edges,
            temp_directory=temp_directory,
            streaming_limits=_streaming_limits(streaming_limits),
            cancellation_token=cancellation_token,
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
        selection, _ = _activate_selection(selection)
        warn_if_auto_fallback(selection)
        checked = validate_view(view)
        with self._metadata_lock:
            self._last_view = checked
        raw = iter_asserted_taxonomy(
            checked,
            bidirectional=bidirectional,
            duplicates="preserve",
            order="encounter",
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
    ) -> Iterator[Edge]:
        def generate() -> Iterator[Edge]:
            acquired = False
            if options.compatibility_state == "scala-instance":
                acquired = self._scala_lock.acquire(blocking=False)
                if not acquired:
                    raise InvalidProjectionOptionsError(
                        "compatibility_state='scala-instance' is non-concurrent",
                        details={"compatibility_state": "scala-instance"},
                    )
            try:
                selection = select_backend(options.backend)
                selection, native_version = _activate_selection(selection)
                warn_if_auto_fallback(selection)
                role_state = (
                    self._scala_state
                    if options.compatibility_state == "scala-instance"
                    else RoleState.empty()
                )
                compilation = prepare_streaming_compilation(view, options, role_state)
                if options.compatibility_state == "scala-instance":
                    compilation.prepare_role_state()
                with self._metadata_lock:
                    self._last_view = compilation.view
                    self._last_report = None
                    if options.compatibility_state == "scala-instance":
                        self._scala_invocation_count += 1
                        invocation = self._scala_invocation_count
                    else:
                        invocation = 1
                output_count = 0
                raw_edges: Iterator[Edge] = compilation.iter_raw_edges()
                if selection.selected == "native":
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
                    limits=_streaming_limits(streaming_limits),
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
                )
                with self._metadata_lock:
                    self._last_report = report
            finally:
                if acquired:
                    self._scala_lock.release()

        return generate()

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


def _activate_selection(selection: BackendSelection) -> tuple[BackendSelection, str | None]:
    """Load only at dispatch and preserve auto fallback if a wheel is broken."""
    if selection.selected == "python":
        return selection, None
    try:
        return selection, native_implementation_version()
    except NativeBackendUnavailableError as error:
        if selection.requested == "native":
            raise
        fallback = BackendSelection(
            requested="auto",
            selected="python",
            fallback_reason=f"native load failed: {error}",
        )
        return fallback, None


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
    source_kind: SourceKind = (
        "provider" if callable(getattr(source, "owl_snapshot", None)) else "direct"
    )
    if getattr(source, "wire_verified", False) is True:
        source_kind = "wire"
    view = coerce(source, options=load_options, resolver=resolver)
    return validate_view(view), source_kind


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


def _manifest_digest(view: object) -> str:
    manifest = getattr(view, "import_manifest", None)
    for name in ("digest", "fingerprint", "manifest_digest"):
        value = getattr(manifest, name, None)
        if value is not None:
            if isinstance(value, bytes):
                return value.hex()
            fingerprint = getattr(value, "hex", None)
            if isinstance(fingerprint, str):
                return fingerprint
            return str(value)
    report = getattr(view, "report", None)
    value = getattr(report, "import_manifest_digest", "")
    return str(value) if value is not None else ""


def _core_provenance(view: object) -> CoreProvenance:
    core = importlib.import_module("pyowl_core")
    capabilities = view.capabilities  # type: ignore[attr-defined]
    wire = getattr(capabilities, "wire_format", getattr(core, "WIRE_FORMAT_VERSION", (1, 0)))
    return CoreProvenance(
        package_version=str(getattr(core, "__version__", "unknown")),
        api_version=tuple(getattr(core, "API_VERSION", (0, 0))),
        model_schema_version=int(capabilities.model_schema),
        wire_format_version=tuple(wire),
        adapter_protocol_version=int(capabilities.adapter_protocol),
        structural_fingerprint=_fingerprint(view, "structural_fingerprint"),
        logical_fingerprint=_fingerprint(view, "logical_fingerprint"),
        signature_fingerprint=_fingerprint(view, "signature_fingerprint"),
        import_manifest_digest=_manifest_digest(view),
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
