"""Structured, bounded projector diagnostics.

The compatibility profile has a number of intentionally ignored OWL shapes and
one historical literal-rendering warning.  Keeping those observations as data
avoids the reference implementation's stdout side effects and lets callers
decide how much detail to retain.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DiagnosticSeverity = Literal["info", "warning"]


@dataclass(frozen=True, slots=True)
class ProjectionDiagnostic:
    """One grouped diagnostic with a stable code."""

    code: str
    message: str
    severity: DiagnosticSeverity = "info"
    count: int = 1
    constructor: str | None = None

    def __post_init__(self) -> None:
        if not self.code or not isinstance(self.code, str):
            raise ValueError("diagnostic code must be a nonempty str")
        if not self.message or not isinstance(self.message, str):
            raise ValueError("diagnostic message must be a nonempty str")
        if self.severity not in ("info", "warning"):
            raise ValueError("diagnostic severity must be 'info' or 'warning'")
        if type(self.count) is not int or self.count < 1:
            raise ValueError("diagnostic count must be a positive int")


__all__ = ["DiagnosticSeverity", "ProjectionDiagnostic"]
