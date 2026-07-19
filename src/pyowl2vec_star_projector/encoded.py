"""Public pyowl-core encoded-view negotiation for the P7 native compiler.

This module deliberately knows only the public core capability and view
contracts.  It does not import ``pyowl_core._native`` or interpret structural
columns before WP17 publishes their generated schema ledger.
"""

from __future__ import annotations

import hashlib
import importlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from .errors import SnapshotCompatibilityError
from .provenance import IngestionPath

ENCODED_SCHEMA_NAME = "pyowl-core/structural-columns"
ENCODED_SCHEMA_VERSION = 1
ENCODED_NATIVE_FEATURE = "encoded-structural-compiler-v1"

_EMPTY_FEATURES: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class EncodedStructuralLease:
    """Validated public encoded view plus its owner-lifetime metadata."""

    encoded_view: object
    owner: object
    schema_name: str
    schema_version: int
    model_schema: int
    descriptor_sha256: str
    buffer_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EncodedNegotiation:
    """One whole-operation ingestion decision made before scalar traversal."""

    path: IngestionPath
    reason: str | None = None
    lease: EncodedStructuralLease | None = None

    def __post_init__(self) -> None:
        if self.path == "encoded-native" and self.lease is None:
            raise ValueError("encoded-native ingestion requires an encoded-view lease")
        if self.path != "encoded-native" and self.lease is not None:
            raise ValueError("scalar ingestion cannot retain an encoded-view lease")
        if self.path == "encoded-native" and self.reason is not None:
            raise ValueError("encoded-native ingestion cannot contain a fallback reason")
        if self.reason is not None and not self.reason:
            raise ValueError("ingestion reason must be nonempty when present")


def select_ingestion(
    view: object,
    *,
    selected_backend: Literal["native", "python"],
    native_features: frozenset[str] = _EMPTY_FEATURES,
    backend_fallback_reason: str | None = None,
    core_module: object | None = None,
) -> EncodedNegotiation:
    """Select encoded-native only after both sides advertise the public contract.

    An unavailable capability is a normal scalar compatibility case.  Once a
    provider advertises the exact supported schema, malformed public metadata or
    buffers are a typed compatibility failure rather than a silent fallback.
    """

    if selected_backend == "python":
        return EncodedNegotiation("scalar-python", backend_fallback_reason)
    if ENCODED_NATIVE_FEATURE not in native_features:
        return EncodedNegotiation(
            "scalar-native",
            "native extension does not advertise the P7 encoded compiler",
        )

    version = _advertised_schema_version(view)
    if version is None:
        return EncodedNegotiation(
            "scalar-native",
            f"core view does not advertise {ENCODED_SCHEMA_NAME}",
        )
    if version != ENCODED_SCHEMA_VERSION:
        return EncodedNegotiation(
            "scalar-native",
            "core encoded schema version is outside the projector-supported range",
        )

    core = core_module if core_module is not None else importlib.import_module("pyowl_core")
    encoded_type = getattr(core, "EncodedStructuralView", None)
    if not isinstance(encoded_type, type):
        raise SnapshotCompatibilityError(
            "core advertises encoded structural columns without the public view type",
            details={
                "schema_name": ENCODED_SCHEMA_NAME,
                "schema_version": version,
            },
        )
    factory = getattr(view, "view", None)
    if not callable(factory):
        raise SnapshotCompatibilityError(
            "core advertises encoded structural columns without OntologyView.view()"
        )
    scope_type = getattr(core, "AxiomScope", None)
    scope = getattr(scope_type, "CLOSURE", "closure")
    try:
        encoded = factory(
            encoded_type,
            schema_version=ENCODED_SCHEMA_VERSION,
            scope=scope,
        )
    except MemoryError:
        raise
    except Exception as error:
        raise SnapshotCompatibilityError(
            "core failed to publish its advertised encoded structural view",
            details={
                "schema_name": ENCODED_SCHEMA_NAME,
                "schema_version": version,
                "cause": type(error).__name__,
            },
        ) from error
    lease = _validate_encoded_view(view, encoded, encoded_type)
    return EncodedNegotiation("encoded-native", lease=lease)


def _advertised_schema_version(view: object) -> int | None:
    capabilities = getattr(view, "capabilities", None)
    schemas = getattr(capabilities, "encoded_view_schemas", None)
    if schemas is None:
        return None
    if not isinstance(schemas, Mapping):
        raise SnapshotCompatibilityError(
            "core encoded_view_schemas capability is not a mapping"
        )
    value = schemas.get(ENCODED_SCHEMA_NAME)
    if value is None:
        return None
    if type(value) is not int or value < 1:
        raise SnapshotCompatibilityError(
            "core encoded structural schema version is invalid",
            details={"schema_name": ENCODED_SCHEMA_NAME},
        )
    return value


def _validate_encoded_view(
    source_view: object,
    encoded: object,
    encoded_type: type[object],
) -> EncodedStructuralLease:
    if not isinstance(encoded, encoded_type):
        raise SnapshotCompatibilityError(
            "core encoded view factory returned the wrong public type"
        )
    schema_name = getattr(encoded, "schema_name", None)
    schema_version = getattr(encoded, "schema_version", None)
    model_schema = getattr(encoded, "model_schema", None)
    owner = getattr(encoded, "owner", None)
    descriptor = getattr(encoded, "descriptor", None)
    buffers = getattr(encoded, "buffers", None)
    fingerprint = getattr(encoded, "structural_fingerprint", None)

    if schema_name != ENCODED_SCHEMA_NAME or schema_version != ENCODED_SCHEMA_VERSION:
        raise SnapshotCompatibilityError(
            "core encoded view metadata does not match its advertised schema",
            details={
                "expected_schema_name": ENCODED_SCHEMA_NAME,
                "expected_schema_version": ENCODED_SCHEMA_VERSION,
                "actual_schema_name": str(schema_name),
                "actual_schema_version": schema_version if type(schema_version) is int else -1,
            },
        )
    expected_model = getattr(getattr(source_view, "capabilities", None), "model_schema", None)
    if type(model_schema) is not int or model_schema != expected_model:
        raise SnapshotCompatibilityError(
            "core encoded view model schema does not match its owner"
        )
    if owner is not source_view:
        raise SnapshotCompatibilityError(
            "core encoded view did not retain the exact source view identity"
        )
    if not isinstance(descriptor, bytes) or not descriptor:
        raise SnapshotCompatibilityError(
            "core encoded view descriptor must be nonempty immutable bytes"
        )
    authoritative_descriptor_digest = hashlib.sha256(descriptor).digest()
    descriptor_digest = getattr(
        encoded,
        "descriptor_digest",
        authoritative_descriptor_digest,
    )
    if (
        type(descriptor_digest) is not bytes
        or descriptor_digest != authoritative_descriptor_digest
    ):
        raise SnapshotCompatibilityError(
            "core encoded view descriptor digest does not match its immutable descriptor"
        )
    if not isinstance(buffers, Mapping):
        raise SnapshotCompatibilityError("core encoded view buffers are not a mapping")

    names: list[str] = []
    for name, buffer in buffers.items():
        if not isinstance(name, str) or not name:
            raise SnapshotCompatibilityError(
                "core encoded view buffer names must be nonempty strings"
            )
        if not isinstance(buffer, memoryview):
            raise SnapshotCompatibilityError(
                "core encoded view values must be memoryview instances",
                details={"buffer": name},
            )
        if not buffer.readonly:
            raise SnapshotCompatibilityError(
                "core encoded view exposed a writable buffer",
                details={"buffer": name},
            )
        if buffer.itemsize != 1 or not buffer.contiguous:
            raise SnapshotCompatibilityError(
                "core encoded view must expose contiguous byte buffers",
                details={"buffer": name},
            )
        names.append(name)
    source_fingerprint = getattr(source_view, "structural_fingerprint", None)
    if source_fingerprint is None or type(fingerprint) is not type(source_fingerprint):
        raise SnapshotCompatibilityError(
            "core encoded view fingerprint has the wrong public type"
        )
    return EncodedStructuralLease(
        encoded_view=encoded,
        owner=owner,
        schema_name=schema_name,
        schema_version=schema_version,
        model_schema=model_schema,
        descriptor_sha256=authoritative_descriptor_digest.hex(),
        buffer_names=tuple(sorted(names)),
    )


__all__ = [
    "ENCODED_NATIVE_FEATURE",
    "ENCODED_SCHEMA_NAME",
    "ENCODED_SCHEMA_VERSION",
    "EncodedNegotiation",
    "EncodedStructuralLease",
    "IngestionPath",
    "select_ingestion",
]
