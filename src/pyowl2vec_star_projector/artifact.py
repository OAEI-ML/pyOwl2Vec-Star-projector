"""Portable, deterministic JSONL edge artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import BinaryIO, Protocol

from ._version import EDGE_ARTIFACT_SCHEMA, PROJECTOR_API_VERSION, __version__
from .errors import InvalidProjectionOptionsError, ProjectionResourceError
from .model import Edge
from .options import ProjectionOptions
from .provenance import ProjectionReport
from .streaming import (
    CancellationTokenLike,
    StreamingLimits,
    _restrict_owner_file_permissions,
)

_COPY_CHUNK = 1024 * 1024
_NATIVE_SEMANTIC_API_VERSION = 1


class ProjectorArtifactSource(Protocol):
    @property
    def last_report(self) -> ProjectionReport | None: ...

    def iter_edges(
        self,
        view: object,
        *,
        options: ProjectionOptions | None = None,
        buffer_edges: int = 250_000,
        temp_directory: os.PathLike[str] | None = None,
        streaming_limits: StreamingLimits | None = None,
        cancellation_token: CancellationTokenLike | None = None,
    ) -> Iterator[Edge]: ...


@dataclass(frozen=True, slots=True)
class EdgeArtifactResult:
    """Deterministic artifact outcome without a machine-specific path."""

    artifact_sha256: str
    canonical_edges_sha256: str
    edge_count: int
    duplicate_count: int
    bytes_written: int
    metadata: Mapping[str, object]
    report: ProjectionReport


@dataclass(frozen=True, slots=True)
class CanonicalEdgeDigest:
    sha256: str
    edge_count: int
    duplicate_count: int
    report: ProjectionReport


class _EdgePayload:
    def __init__(
        self,
        parent: os.PathLike[str] | None,
        limits: StreamingLimits,
    ) -> None:
        selected = None if parent is None else Path(parent)
        if selected is not None and not selected.is_dir():
            raise ProjectionResourceError(
                "artifact temporary directory is not an existing directory",
                details={"stage": "artifact-temporary-directory"},
            )
        self.directory: Path | None = None
        self.path: Path | None = None
        self.stream: BinaryIO | None = None
        self.limits = limits
        self.bytes_written = 0
        self.edge_count = 0
        self.digest = hashlib.sha256()
        self._external_bytes = 0
        descriptor: int | None = None
        try:
            self.directory = Path(tempfile.mkdtemp(prefix="pyowl2vec-artifact-", dir=selected))
            os.chmod(self.directory, 0o700)
            descriptor, name = tempfile.mkstemp(
                prefix="edges-", suffix=".jsonl", dir=self.directory
            )
            _restrict_owner_file_permissions(descriptor)
            self.path = Path(name)
            os.close(descriptor)
            descriptor = None
            self.stream = None
            self._available = shutil.disk_usage(self.directory).free
        except OSError as error:
            if descriptor is not None:
                os.close(descriptor)
            self.cleanup()
            raise _artifact_io_error("artifact-payload-open", error) from error

    def write(self, edge: Edge) -> None:
        try:
            record = edge_json_record(edge)
        except (MemoryError, OverflowError) as error:
            raise ProjectionResourceError(
                "artifact could not allocate one bounded edge record",
                details={"stage": "artifact-edge-encode", "cause": type(error).__name__},
            ) from error
        following = self.bytes_written + len(record)
        self._enforce(following, len(record))
        stream = self._open_stream()
        try:
            _write_all(stream, record)
        except OSError as error:
            raise _artifact_io_error("artifact-payload-write", error) from error
        self.digest.update(record)
        self.bytes_written = following
        self.edge_count += 1
        self._available -= len(record)

    def rewind(self) -> BinaryIO:
        stream = self._open_stream()
        try:
            stream.flush()
            stream.seek(0)
        except OSError as error:
            raise _artifact_io_error("artifact-payload-rewind", error) from error
        return stream

    def cleanup(self) -> None:
        stream = self.stream
        self.stream = None
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass
        path = self.path
        self.path = None
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        directory = self.directory
        self.directory = None
        if directory is not None:
            shutil.rmtree(directory, ignore_errors=True)

    def _open_stream(self) -> BinaryIO:
        stream = self.stream
        if stream is not None:
            return stream
        path = self.path
        if path is None:
            raise RuntimeError("artifact payload is closed")
        try:
            stream = path.open("r+b", buffering=_COPY_CHUNK)
            stream.seek(0, os.SEEK_END)
        except OSError as error:
            raise _artifact_io_error("artifact-payload-open", error) from error
        self.stream = stream
        return stream

    def _enforce(self, following: int, amount: int) -> None:
        if self.edge_count % self.limits.cancellation_check_interval == 0:
            self._external_bytes = self._other_private_bytes()
        combined = following + self._external_bytes
        for name, allowed in (
            ("max_temporary_bytes", self.limits.max_temporary_bytes),
            ("max_spill_bytes", self.limits.max_spill_bytes),
        ):
            if allowed is not None and combined > allowed:
                raise ProjectionResourceError(
                    f"artifact exceeded {name}",
                    details={
                        "limit": name,
                        "observed": combined,
                        "allowed": allowed,
                        "stage": "artifact-payload-write",
                    },
                )
        if amount > self._available:
            directory = self.directory
            if directory is None:
                raise RuntimeError("artifact payload is closed")
            try:
                self._available = shutil.disk_usage(directory).free
            except OSError as error:
                raise _artifact_io_error("artifact-payload-space", error) from error
        if amount > self._available:
            raise ProjectionResourceError(
                "artifact temporary space is exhausted",
                details={
                    "required": amount,
                    "available": self._available,
                    "stage": "artifact-payload-write",
                },
            )

    def _other_private_bytes(self) -> int:
        directory = self.directory
        payload = self.path
        if directory is None:
            return 0
        total = 0
        try:
            for candidate in directory.rglob("*"):
                if candidate != payload and candidate.is_file():
                    total += candidate.stat().st_size
        except OSError as error:
            raise _artifact_io_error("artifact-payload-space", error) from error
        return total


def write_edge_artifact(
    projector: ProjectorArtifactSource,
    view: object,
    destination: os.PathLike[str] | BinaryIO,
    *,
    options: ProjectionOptions | None = None,
    buffer_edges: int = 250_000,
    temp_directory: os.PathLike[str] | None = None,
    streaming_limits: StreamingLimits | None = None,
    cancellation_token: CancellationTokenLike | None = None,
) -> EdgeArtifactResult:
    """Project once and write the version-1 JSONL artifact with bounded memory.

    ``artifact_sha256`` hashes the metadata record with that field omitted,
    followed by the exact edge records.  This self-excluding preimage makes the
    digest verifiable while retaining it in the mandatory first record.
    """
    if streaming_limits is not None and not isinstance(streaming_limits, StreamingLimits):
        raise InvalidProjectionOptionsError("streaming_limits must be StreamingLimits or None")
    limits = streaming_limits if streaming_limits is not None else StreamingLimits()
    payload = _EdgePayload(temp_directory, limits)
    try:
        for edge in projector.iter_edges(
            view,
            options=options,
            buffer_edges=buffer_edges,
            temp_directory=payload.directory,
            streaming_limits=limits,
            cancellation_token=cancellation_token,
        ):
            payload.write(edge)
        report = projector.last_report
        if report is None:
            raise RuntimeError("artifact projection completed without a report")
        if payload.edge_count != report.provenance.counts.edges:
            raise RuntimeError("artifact edge count disagrees with projection report")
        metadata = _artifact_metadata(
            report,
            canonical_edges_sha256=payload.digest.hexdigest(),
        )
        provisional = json_record(metadata)
        artifact_digest = hashlib.sha256()
        artifact_digest.update(provisional)
        _hash_payload(payload, artifact_digest, cancellation_token)
        artifact_sha256 = artifact_digest.hexdigest()
        metadata["artifact_sha256"] = artifact_sha256
        metadata_record = json_record(metadata)
        bytes_written = _write_destination(
            destination,
            metadata_record,
            payload,
            cancellation_token,
        )
        return EdgeArtifactResult(
            artifact_sha256=artifact_sha256,
            canonical_edges_sha256=payload.digest.hexdigest(),
            edge_count=payload.edge_count,
            duplicate_count=report.provenance.counts.duplicates,
            bytes_written=bytes_written,
            metadata=metadata,
            report=report,
        )
    finally:
        payload.cleanup()


def canonical_edge_digest(
    projector: ProjectorArtifactSource,
    view: object,
    *,
    options: ProjectionOptions | None = None,
    buffer_edges: int = 250_000,
    temp_directory: os.PathLike[str] | None = None,
    streaming_limits: StreamingLimits | None = None,
    cancellation_token: CancellationTokenLike | None = None,
) -> CanonicalEdgeDigest:
    """Compute canonical edge-record bytes in one ontology traversal."""
    effective = options or ProjectionOptions()
    if effective.order != "canonical":
        effective = replace(effective, order="canonical")
    digest = hashlib.sha256()
    count = 0
    for edge in projector.iter_edges(
        view,
        options=effective,
        buffer_edges=buffer_edges,
        temp_directory=temp_directory,
        streaming_limits=streaming_limits,
        cancellation_token=cancellation_token,
    ):
        digest.update(edge_json_record(edge))
        count += 1
    report = projector.last_report
    if report is None:
        raise RuntimeError("canonical digest completed without a report")
    return CanonicalEdgeDigest(
        sha256=digest.hexdigest(),
        edge_count=count,
        duplicate_count=report.provenance.counts.duplicates,
        report=report,
    )


def edge_json_record(edge: Edge) -> bytes:
    """Return one canonical UTF-8 edge record, including ``\n``."""
    return (
        json.dumps(
            {
                "source": edge.source,
                "relation": edge.relation,
                "destination": edge.destination,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def json_record(value: Mapping[str, object]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        + b"\n"
    )


def _artifact_metadata(
    report: ProjectionReport,
    *,
    canonical_edges_sha256: str,
) -> dict[str, object]:
    provenance = report.provenance
    core = provenance.core
    # Backend selection is deliberately excluded from the hashed portable
    # semantics.  Both implementation versions are described independently so
    # Python/native execution yields byte-identical artifacts.
    semantic_options = {
        name: value for name, value in provenance.options.to_dict().items() if name != "backend"
    }
    warnings: dict[str, int] = {}
    for diagnostic in report.diagnostics:
        if diagnostic.severity == "warning":
            warnings[diagnostic.code] = warnings.get(diagnostic.code, 0) + diagnostic.count
    semantic_warning_count = sum(warnings.values())
    return {
        "schema": EDGE_ARTIFACT_SCHEMA,
        "profile": provenance.options.profile,
        "options": semantic_options,
        "snapshot": {
            "structural_fingerprint": core.structural_fingerprint,
            "logical_fingerprint": core.logical_fingerprint,
            "signature_fingerprint": core.signature_fingerprint,
            "import_manifest_digest": core.import_manifest_digest,
            "closure_document_identities": list(core.closure_document_identities),
            "loader_diagnostics_digest": core.loader_diagnostics_digest,
        },
        "provenance": {
            "projector_version": __version__,
            "projector_api_version": PROJECTOR_API_VERSION,
            "compiler_cache_schema": provenance.compiler_cache_schema,
            "backend_versions": {
                "python": __version__,
                "native_semantic_api": _NATIVE_SEMANTIC_API_VERSION,
            },
            "core_package_version": core.package_version,
            "core_api_version": list(core.api_version),
            "core_model_schema_version": core.model_schema_version,
            "core_wire_format_version": list(core.wire_format_version),
            "core_adapter_protocol_version": core.adapter_protocol_version,
            "diagnostics_digest": provenance.diagnostics_digest,
            "invocation_count": provenance.invocation_count,
            "call_history_digest": provenance.call_history_digest,
        },
        "counts": {
            "edges": provenance.counts.edges,
            "duplicates": provenance.counts.duplicates,
            "skipped_axioms": provenance.counts.skipped_axioms,
            "ignored_shapes": provenance.counts.ignored_shapes,
            "warnings": semantic_warning_count,
        },
        "warning_summary": warnings,
        "canonical_edges_sha256": canonical_edges_sha256,
        "artifact_hash_preimage": "metadata-without-artifact_sha256+edge-records",
    }


def _hash_payload(
    payload: _EdgePayload,
    digest: hashlib._Hash,
    cancellation_token: CancellationTokenLike | None,
) -> None:
    stream = payload.rewind()
    while True:
        if cancellation_token is not None:
            cancellation_token.check()
        chunk = stream.read(_COPY_CHUNK)
        if not chunk:
            return
        digest.update(chunk)


def _write_destination(
    destination: os.PathLike[str] | BinaryIO,
    metadata: bytes,
    payload: _EdgePayload,
    cancellation_token: CancellationTokenLike | None,
) -> int:
    if hasattr(destination, "write"):
        writer = destination
        if not callable(getattr(writer, "write", None)):
            raise TypeError("destination must be a path or binary writer")
        return _copy_artifact(writer, metadata, payload, cancellation_token)  # type: ignore[arg-type]

    target = Path(destination)
    if not target.parent.is_dir():
        raise ProjectionResourceError(
            "artifact destination parent is not an existing directory",
            details={"stage": "artifact-destination"},
        )
    descriptor: int | None = None
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".pyowl2vec-artifact-", dir=target.parent)
        temporary = Path(name)
        _restrict_owner_file_permissions(descriptor)
        with os.fdopen(descriptor, "wb", buffering=_COPY_CHUNK) as writer:
            descriptor = None
            written = _copy_artifact(writer, metadata, payload, cancellation_token)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temporary, target)
        temporary = None
        return written
    except OSError as error:
        raise _artifact_io_error("artifact-destination-write", error) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _copy_artifact(
    writer: BinaryIO,
    metadata: bytes,
    payload: _EdgePayload,
    cancellation_token: CancellationTokenLike | None,
) -> int:
    try:
        _write_all(writer, metadata)
        written = len(metadata)
        source = payload.rewind()
        while True:
            if cancellation_token is not None:
                cancellation_token.check()
            chunk = source.read(_COPY_CHUNK)
            if not chunk:
                return written
            _write_all(writer, chunk)
            written += len(chunk)
    except OSError as error:
        raise _artifact_io_error("artifact-destination-write", error) from error


def _write_all(writer: BinaryIO, value: bytes) -> None:
    offset = 0
    while offset < len(value):
        written = writer.write(value[offset:])
        if written is None:
            raise OSError("binary writer returned no progress result")
        if type(written) is not int or written <= 0:
            raise OSError("binary writer made no progress")
        if written > len(value) - offset:
            raise OSError("binary writer reported impossible progress")
        offset += written


def _artifact_io_error(stage: str, error: OSError) -> ProjectionResourceError:
    return ProjectionResourceError(
        "edge artifact I/O failed",
        details={
            "stage": stage,
            "cause": type(error).__name__,
            "errno": -1 if error.errno is None else error.errno,
        },
    )


__all__ = [
    "CanonicalEdgeDigest",
    "EdgeArtifactResult",
    "canonical_edge_digest",
    "edge_json_record",
    "json_record",
    "write_edge_artifact",
]
