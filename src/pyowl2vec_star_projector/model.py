"""Public immutable projector values."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, order=True)
class Edge:
    """One OWL2Vec* edge with value equality and deterministic ordering support."""

    source: str
    relation: str
    destination: str

    def __post_init__(self) -> None:
        for name, value in (
            ("source", self.source),
            ("relation", self.relation),
            ("destination", self.destination),
        ):
            if not isinstance(value, str):
                raise TypeError(f"{name} must be str, got {type(value).__name__}")

    def canonical_key(self) -> tuple[bytes, bytes, bytes]:
        """Return the specified locale-independent UTF-8 ordering key."""
        return (
            self.source.encode("utf-8"),
            self.relation.encode("utf-8"),
            self.destination.encode("utf-8"),
        )

    def as_tuple(self) -> tuple[str, str, str]:
        return self.source, self.relation, self.destination
