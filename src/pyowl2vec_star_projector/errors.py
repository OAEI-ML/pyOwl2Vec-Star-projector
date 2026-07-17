"""Stable public exception and warning hierarchy."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType


class ProjectionError(Exception):
    """Base class for projector failures with a stable machine-readable code."""

    code = "PROJECTION_ERROR"

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, str | int | float | bool] | None = None,
    ) -> None:
        super().__init__(message)
        self.details = MappingProxyType(dict(details or {}))


class UnsupportedProfileError(ProjectionError):
    code = "UNSUPPORTED_PROFILE"


class InvalidProjectionOptionsError(ProjectionError, ValueError):
    code = "INVALID_PROJECTION_OPTIONS"


class UnsupportedAxiomShapeError(ProjectionError):
    code = "UNSUPPORTED_AXIOM_SHAPE"


class NativeBackendUnavailableError(ProjectionError):
    code = "NATIVE_BACKEND_UNAVAILABLE"


class SnapshotCompatibilityError(ProjectionError):
    code = "SNAPSHOT_COMPATIBILITY"


class ProjectionResourceError(ProjectionError):
    code = "PROJECTION_RESOURCE"


class ProjectionWarning(UserWarning):
    """Base warning category for non-fatal projector diagnostics."""


class NativeBackendFallbackWarning(ProjectionWarning):
    """Automatic selection used the complete Python backend."""
