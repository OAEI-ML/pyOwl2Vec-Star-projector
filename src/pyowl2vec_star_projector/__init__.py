"""Java-free OWL2Vec* projection over shared pyowl-core ontology views."""

from ._version import (
    COMPILER_CACHE_SCHEMA,
    EDGE_ARTIFACT_SCHEMA,
    PROJECTOR_API_VERSION,
    REFERENCE_PROFILE,
    __version__,
)
from .api import (
    EdgeBatchSink,
    Projector,
    iter_source_edges,
    iter_taxonomy_edges,
    project_source,
    project_taxonomy,
)
from .backend import (
    BackendSelection,
    NativeBackendStatus,
    probe_native_backend,
    select_backend,
)
from .diagnostics import DiagnosticSeverity, ProjectionDiagnostic
from .errors import (
    InvalidProjectionOptionsError,
    NativeBackendFallbackWarning,
    NativeBackendUnavailableError,
    ProjectionError,
    ProjectionResourceError,
    ProjectionWarning,
    SnapshotCompatibilityError,
    UnsupportedAxiomShapeError,
    UnsupportedProfileError,
)
from .model import Edge
from .options import ProjectionOptions
from .provenance import (
    CoreProvenance,
    ProjectionCounts,
    ProjectionProvenance,
    ProjectionReport,
    ProjectionResult,
)

__all__ = [
    "COMPILER_CACHE_SCHEMA",
    "EDGE_ARTIFACT_SCHEMA",
    "PROJECTOR_API_VERSION",
    "REFERENCE_PROFILE",
    "BackendSelection",
    "CoreProvenance",
    "DiagnosticSeverity",
    "Edge",
    "EdgeBatchSink",
    "InvalidProjectionOptionsError",
    "NativeBackendFallbackWarning",
    "NativeBackendStatus",
    "NativeBackendUnavailableError",
    "ProjectionCounts",
    "ProjectionDiagnostic",
    "ProjectionError",
    "ProjectionOptions",
    "ProjectionProvenance",
    "ProjectionReport",
    "ProjectionResourceError",
    "ProjectionResult",
    "ProjectionWarning",
    "Projector",
    "SnapshotCompatibilityError",
    "UnsupportedAxiomShapeError",
    "UnsupportedProfileError",
    "__version__",
    "iter_source_edges",
    "iter_taxonomy_edges",
    "probe_native_backend",
    "project_source",
    "project_taxonomy",
    "select_backend",
]
