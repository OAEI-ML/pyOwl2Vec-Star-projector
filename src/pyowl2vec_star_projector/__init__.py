"""Java-free OWL2Vec* projection contracts.

WP-P0 freezes public values and configuration only. Projection algorithms are
implemented by later work packages; this module deliberately exposes no fake
``project`` function.
"""

from ._version import (
    COMPILER_CACHE_SCHEMA,
    EDGE_ARTIFACT_SCHEMA,
    PROJECTOR_API_VERSION,
    REFERENCE_PROFILE,
    __version__,
)
from .backend import (
    BackendSelection,
    NativeBackendStatus,
    probe_native_backend,
    select_backend,
)
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
from .provenance import CoreProvenance, ProjectionCounts, ProjectionProvenance

__all__ = [
    "COMPILER_CACHE_SCHEMA",
    "EDGE_ARTIFACT_SCHEMA",
    "PROJECTOR_API_VERSION",
    "REFERENCE_PROFILE",
    "BackendSelection",
    "CoreProvenance",
    "Edge",
    "InvalidProjectionOptionsError",
    "NativeBackendFallbackWarning",
    "NativeBackendStatus",
    "NativeBackendUnavailableError",
    "ProjectionCounts",
    "ProjectionError",
    "ProjectionOptions",
    "ProjectionProvenance",
    "ProjectionResourceError",
    "ProjectionWarning",
    "SnapshotCompatibilityError",
    "UnsupportedAxiomShapeError",
    "UnsupportedProfileError",
    "__version__",
    "probe_native_backend",
    "select_backend",
]
