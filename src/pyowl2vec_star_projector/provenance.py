"""Serializable provenance records shared by future Python and native engines."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

from ._version import (
    COMPILER_CACHE_SCHEMA,
    EDGE_ARTIFACT_SCHEMA,
    PROJECTOR_API_VERSION,
    __version__,
)
from .diagnostics import ProjectionDiagnostic
from .model import Edge
from .options import ProjectionOptions

SourceKind = Literal["direct", "provider", "wire"]


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

    def __post_init__(self) -> None:
        if type(self.invocation_count) is not int or self.invocation_count < 1:
            raise ValueError("invocation_count must be a positive int")

    def to_dict(self) -> dict[str, object]:
        """Return a deterministic JSON-compatible record without machine paths."""
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
