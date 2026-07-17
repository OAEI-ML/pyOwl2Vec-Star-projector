"""Immutable, strictly validated projection configuration."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal, cast

from ._version import REFERENCE_PROFILE
from .errors import InvalidProjectionOptionsError, UnsupportedProfileError

Backend = Literal["auto", "native", "python"]
DuplicatePolicy = Literal["preserve", "unique"]
EdgeOrder = Literal["canonical", "encounter"]
CompatibilityState = Literal["isolated", "scala-instance"]

BACKENDS: tuple[Backend, ...] = ("auto", "native", "python")
DUPLICATE_POLICIES: tuple[DuplicatePolicy, ...] = ("preserve", "unique")
EDGE_ORDERS: tuple[EdgeOrder, ...] = ("canonical", "encounter")
COMPATIBILITY_STATES: tuple[CompatibilityState, ...] = (
    "isolated",
    "scala-instance",
)
SUPPORTED_PROFILES = frozenset({REFERENCE_PROFILE})


def _strict_bool(name: str, value: object) -> None:
    if type(value) is not bool:
        raise InvalidProjectionOptionsError(f"{name} must be bool, got {type(value).__name__}")


def _choice(name: str, value: str, choices: tuple[str, ...]) -> None:
    if value not in choices:
        allowed = ", ".join(choices)
        raise InvalidProjectionOptionsError(f"{name} must be one of {allowed}; got {value!r}")


@dataclass(frozen=True, slots=True)
class ProjectionOptions:
    profile: str = REFERENCE_PROFILE
    bidirectional_taxonomy: bool = False
    only_taxonomy: bool = False
    include_literals: bool = False
    duplicates: DuplicatePolicy = "preserve"
    order: EdgeOrder = "canonical"
    compatibility_state: CompatibilityState = "isolated"
    backend: Backend = "auto"

    def __post_init__(self) -> None:
        if self.profile not in SUPPORTED_PROFILES:
            raise UnsupportedProfileError(
                f"unsupported projection profile {self.profile!r}",
                details={"profile": self.profile},
            )
        _strict_bool("bidirectional_taxonomy", self.bidirectional_taxonomy)
        _strict_bool("only_taxonomy", self.only_taxonomy)
        _strict_bool("include_literals", self.include_literals)
        _choice("duplicates", self.duplicates, cast(tuple[str, ...], DUPLICATE_POLICIES))
        _choice("order", self.order, cast(tuple[str, ...], EDGE_ORDERS))
        _choice(
            "compatibility_state",
            self.compatibility_state,
            cast(tuple[str, ...], COMPATIBILITY_STATES),
        )
        _choice("backend", self.backend, cast(tuple[str, ...], BACKENDS))

    def to_dict(self) -> dict[str, str | bool]:
        """Return a normalized JSON-compatible option record."""
        return cast(dict[str, str | bool], asdict(self))
