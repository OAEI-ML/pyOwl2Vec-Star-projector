"""Serializable provenance records shared by future Python and native engines."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from types import MappingProxyType
from typing import Literal

from ._version import (
    COMPILER_CACHE_SCHEMA,
    EDGE_ARTIFACT_SCHEMA,
    INGESTION_PROVENANCE_SCHEMA,
    PROJECTOR_API_VERSION,
    __version__,
)
from .diagnostics import ProjectionDiagnostic
from .model import Edge
from .options import ProjectionOptions

SourceKind = Literal["direct", "provider", "wire"]
IngestionPath = Literal["scalar-python", "scalar-native", "encoded-native"]

_INGESTION_COUNTERS = frozenset(
    {
        "base_flattening_bytes",
        "encoded_buffer_bytes",
        "encoded_buffer_count",
        "encoded_compiler_gil_released",
        "encoded_detached_buffer_count",
        "encoded_indexed_buffer_count",
        "encoded_posting_bytes",
        "encoded_referenced_view_count",
        "encoded_segment_count",
        "encoded_staging_copy_bytes",
        "encoded_zero_copy_buffers",
        "materialized_scalar_rows",
        "native_batch_edges",
        "native_boundary_calls",
        "native_edge_batches",
        "native_output_vector_edges",
        "parser_calls",
        "per_row_ffi_calls",
        "resolver_calls",
        "scalar_axiom_materializations",
        "scalar_term_materializations",
        "structural_copy_bytes",
        "wire_decoder_calls",
        "wire_encoder_calls",
    }
)
_ENCODED_COUNTER_DEFAULTS: Mapping[str, int | bool] = {
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
}


@dataclass(frozen=True, slots=True)
class CoreProvenance:
    package_version: str
    api_version: tuple[int, int]
    model_schema_version: int
    wire_format_version: tuple[int, int]
    adapter_protocol_version: int
    structural_fingerprint: str
    logical_fingerprint: str
    signature_fingerprint: str
    import_manifest_digest: str
    closure_document_identities: tuple[str, ...] = ()
    loader_diagnostics_digest: str = ""


@dataclass(frozen=True, slots=True)
class ProjectionCounts:
    edges: int = 0
    duplicates: int = 0
    skipped_axioms: int = 0
    ignored_shapes: int = 0
    warnings: int = 0

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative int")


@dataclass(frozen=True, slots=True)
class IngestionProvenance:
    """Execution-only, path-safe ontology handoff and phase diagnostics."""

    schema: str = INGESTION_PROVENANCE_SCHEMA
    path: IngestionPath = "scalar-python"
    reason: str | None = None
    encoded_schema_name: str | None = None
    encoded_schema_version: int | None = None
    encoded_descriptor_sha256: str | None = None
    encoded_view_publication_seconds: float | None = None
    consumer_compile_seconds: float | None = None
    counters: Mapping[str, int | bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.schema != INGESTION_PROVENANCE_SCHEMA:
            raise ValueError("unsupported ingestion provenance schema")
        if self.path not in ("scalar-python", "scalar-native", "encoded-native"):
            raise ValueError("unsupported ingestion path")
        if self.reason is not None and not self.reason:
            raise ValueError("ingestion reason must be nonempty when present")
        if self.path == "encoded-native" and self.reason is not None:
            raise ValueError("encoded-native provenance cannot contain a fallback reason")
        encoded = (
            self.encoded_schema_name,
            self.encoded_schema_version,
            self.encoded_descriptor_sha256,
        )
        if self.path == "encoded-native":
            if (
                not isinstance(self.encoded_schema_name, str)
                or not self.encoded_schema_name
                or type(self.encoded_schema_version) is not int
                or self.encoded_schema_version < 1
                or not _is_sha256(self.encoded_descriptor_sha256)
            ):
                raise ValueError("encoded-native provenance requires complete schema metadata")
        elif any(value is not None for value in encoded):
            raise ValueError("scalar ingestion cannot claim encoded schema metadata")
        for name in (
            "encoded_view_publication_seconds",
            "consumer_compile_seconds",
        ):
            value = getattr(self, name)
            if value is None:
                continue
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(f"{name} must be a finite non-negative duration or None")
            object.__setattr__(self, name, float(value))
        if self.path != "encoded-native" and self.encoded_view_publication_seconds is not None:
            raise ValueError("scalar ingestion cannot claim encoded-view publication")
        if not isinstance(self.counters, Mapping):
            raise TypeError("ingestion counters must be a mapping")
        counters = dict(self.counters)
        if set(counters) - _INGESTION_COUNTERS:
            raise ValueError("ingestion counters contain unsupported fields")
        for name, value in counters.items():
            if name == "encoded_compiler_gil_released":
                if type(value) is not bool:
                    raise ValueError("encoded compiler GIL counter must be bool")
            elif type(value) is not int or value < 0:
                raise ValueError("ingestion counters must be non-negative ints")
        if self.path != "encoded-native" and any(
            name in counters and counters[name] != expected
            for name, expected in _ENCODED_COUNTER_DEFAULTS.items()
        ):
            raise ValueError("scalar ingestion cannot claim nonzero encoded resources")
        object.__setattr__(self, "counters", MappingProxyType(dict(sorted(counters.items()))))

    def to_dict(self) -> dict[str, object]:
        """Return the bounded public handoff record without machine-local data."""

        return {
            "schema": self.schema,
            "path": self.path,
            "reason": self.reason,
            "encoded_schema_name": self.encoded_schema_name,
            "encoded_schema_version": self.encoded_schema_version,
            "encoded_descriptor_sha256": self.encoded_descriptor_sha256,
            "encoded_view_publication_seconds": self.encoded_view_publication_seconds,
            "consumer_compile_seconds": self.consumer_compile_seconds,
            "counters": dict(self.counters),
        }


@dataclass(frozen=True, slots=True)
class ProjectionProvenance:
    options: ProjectionOptions
    selected_backend: Literal["native", "python"]
    source_kind: SourceKind
    core: CoreProvenance
    counts: ProjectionCounts = field(default_factory=ProjectionCounts)
    projector_version: str = __version__
    projector_api_version: int = PROJECTOR_API_VERSION
    edge_artifact_schema: str = EDGE_ARTIFACT_SCHEMA
    compiler_cache_schema: str = COMPILER_CACHE_SCHEMA
    native_implementation_version: str | None = None
    diagnostics_digest: str = ""
    invocation_count: int = 1
    call_history_digest: str = ""
    ingestion: IngestionProvenance = field(default_factory=IngestionProvenance)

    def __post_init__(self) -> None:
        if type(self.invocation_count) is not int or self.invocation_count < 1:
            raise ValueError("invocation_count must be a positive int")
        expected_backend: Literal["native", "python"] = (
            "python" if self.ingestion.path == "scalar-python" else "native"
        )
        if self.selected_backend != expected_backend:
            raise ValueError("selected backend and ingestion path are inconsistent")

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible record without machine paths."""
        return {
            "projector_version": self.projector_version,
            "projector_api_version": self.projector_api_version,
            "edge_artifact_schema": self.edge_artifact_schema,
            "compiler_cache_schema": self.compiler_cache_schema,
            "profile": self.options.profile,
            "options": self.options.to_dict(),
            "selected_backend": self.selected_backend,
            "native_implementation_version": self.native_implementation_version,
            "source_kind": self.source_kind,
            "ingestion": self.ingestion.to_dict(),
            "core": asdict(self.core),
            "counts": asdict(self.counts),
            "diagnostics_digest": self.diagnostics_digest,
            "invocation_count": self.invocation_count,
            "call_history_digest": self.call_history_digest,
        }


@dataclass(frozen=True, slots=True)
class ProjectionReport:
    """Complete report for one successful projection."""

    provenance: ProjectionProvenance
    diagnostics: tuple[ProjectionDiagnostic, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "provenance": self.provenance.to_dict(),
            "diagnostics": [asdict(item) for item in self.diagnostics],
        }


@dataclass(frozen=True, slots=True)
class ProjectionResult:
    """Materialized edges paired with their report."""

    edges: tuple[Edge, ...]
    report: ProjectionReport


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64 or value != value.lower():
        return False
    try:
        return len(bytes.fromhex(value)) == 32
    except ValueError:
        return False
