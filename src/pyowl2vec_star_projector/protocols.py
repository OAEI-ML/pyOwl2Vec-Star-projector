"""Minimal public-core boundary used by the projector.

``pyowl-core`` owns the concrete protocol.  This local typing protocol is only
the consumer-side structural view of that API; it deliberately defines no OWL
records and performs no conversion.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol, runtime_checkable

from .model import Edge


@runtime_checkable
class OntologyViewLike(Protocol):
    """Read-only subset of ``pyowl_core.OntologyView`` required by P2."""

    @property
    def capabilities(self) -> object: ...

    def iter_axioms(
        self,
        axiom_type: type[Any] | None = None,
        *,
        scope: object = ...,
    ) -> Iterable[Any]: ...

    def signature(
        self,
        kind: object | None = None,
        *,
        scope: object = ...,
        include_builtins: bool = True,
    ) -> tuple[object, ...]: ...


@runtime_checkable
class EdgeBatchSinkV1(Protocol):
    """Version-1 synchronous, naturally backpressured batch consumer."""

    protocol_version: int

    def write_batch(self, batch: tuple[Edge, ...]) -> object: ...


__all__ = ["EdgeBatchSinkV1", "OntologyViewLike"]
