"""Public pyowl-core encoded-view negotiation for the P7 native compiler.

This module deliberately knows only the public core capability and view
contracts.  It validates the frozen WP17 schema ledger and generic column
bounds/references in place without importing ``pyowl_core._native`` or
reconstructing OWL values in Python.
"""

from __future__ import annotations

import hashlib
import importlib
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from .errors import SnapshotCompatibilityError
from .provenance import IngestionPath

ENCODED_SCHEMA_NAME = "pyowl-core/structural-columns"
ENCODED_SCHEMA_VERSION = 1
ENCODED_NATIVE_FEATURE = "encoded-structural-compiler-v1"
ENCODED_DESCRIPTOR_SHA256 = bytes.fromhex(
    "9ad29db6a7e616f65cea2957bc5ba8d1f9b99ef0eb1fe1432c09be25786267b5"
)
ENCODED_BUFFER_WIDTHS: Mapping[str, int] = MappingProxyType(
    {
        "field_kinds": 1,
        "field_lengths": 8,
        "field_values": 8,
        "item_kinds": 1,
        "item_lengths": 8,
        "item_values": 8,
        "node_field_offsets": 8,
        "node_tags": 2,
        "root_ids": 4,
        "root_kinds": 1,
        "scalar_bytes": 1,
    }
)

_EMPTY_FEATURES: frozenset[str] = frozenset()
_MISSING = object()

_COMPONENT_NONE = 0
_COMPONENT_NODE = 1
_COMPONENT_TEXT = 2
_COMPONENT_BYTES = 3
_COMPONENT_INTEGER = 4
_COMPONENT_ENUM = 5
_COMPONENT_SET = 6
_COMPONENT_SEQUENCE = 7

_SEGMENT_DIRECT = 1
_SEGMENT_OVERLAY_BASE = 2
_SEGMENT_OVERLAY_DELTA = 3
_SEGMENT_COMPOSITE_MEMBER = 4
_SEGMENT_COMPOSITE_BRIDGE = 5
_POSTINGS_ALL = 0
_POSTINGS_INCLUDE = 1
_POSTINGS_EXCLUDE = 2
_DEFAULT_MAX_SEGMENTS = 1_025


@dataclass(frozen=True, slots=True)
class EncodedStructuralLease:
    """Validated public encoded view plus its owner-lifetime metadata."""

    encoded_view: object
    owner: object
    schema_name: str
    schema_version: int
    model_schema: int
    descriptor_sha256: str
    scope: object
    buffers: Mapping[str, memoryview]
    buffer_names: tuple[str, ...]
    segments: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class _SegmentRecord:
    role: int
    owner: object
    source: object | None
    posting_mode: int
    root_ids: memoryview
    anonymous_scope_map: memoryview
    member_token: bytes | None


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
    lease = _validate_encoded_view(view, encoded, encoded_type, scope)
    return EncodedNegotiation("encoded-native", lease=lease)


def select_private_direct_ingestion(
    view: object,
    *,
    selected_backend: Literal["native", "python"],
    backend_fallback_reason: str | None = None,
    core_module: object | None = None,
) -> EncodedNegotiation:
    """Request the unadvertised direct schema without changing public negotiation."""

    if selected_backend == "python":
        return EncodedNegotiation("scalar-python", backend_fallback_reason)
    core = core_module if core_module is not None else importlib.import_module("pyowl_core")
    encoded_type = getattr(core, "EncodedStructuralView", None)
    if not isinstance(encoded_type, type):
        return EncodedNegotiation(
            "scalar-native",
            "core does not expose the private candidate encoded view type",
        )
    factory = getattr(view, "view", None)
    if not callable(factory):
        return EncodedNegotiation(
            "scalar-native",
            "core view cannot publish the private candidate encoded view",
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
        return EncodedNegotiation(
            "scalar-native",
            f"core declined the private candidate encoded view: {type(error).__name__}",
        )
    lease = _validate_encoded_view(view, encoded, encoded_type, scope)
    return EncodedNegotiation("encoded-native", lease=lease)


def _acquire_root_encoded_lease(
    source_view: object,
    closure_lease: EncodedStructuralLease,
) -> EncodedStructuralLease | None:
    """Acquire the same schema at public root scope for annotation provenance."""

    if closure_lease.owner is not source_view:
        raise SnapshotCompatibilityError("encoded root-scope request lost the exact closure owner")
    encoded_type = type(closure_lease.encoded_view)
    root_scope = getattr(type(closure_lease.scope), "ROOT", _MISSING)
    if root_scope is _MISSING or root_scope is closure_lease.scope:
        raise SnapshotCompatibilityError(
            "core encoded scope does not expose a distinct public root selection"
        )
    factory = getattr(source_view, "view", None)
    if not callable(factory):
        raise SnapshotCompatibilityError(
            "core encoded owner cannot publish a root-scoped structural view"
        )
    try:
        encoded = factory(
            encoded_type,
            schema_version=closure_lease.schema_version,
            scope=root_scope,
        )
    except MemoryError:
        raise
    except ValueError:
        # Some bounded view families publicly support closure selection only.
        # Preserve their scalar root-scope behavior through one-shot fallback.
        return None
    except Exception as error:
        raise SnapshotCompatibilityError(
            "core failed to publish root-scoped encoded annotation provenance",
            details={"cause": type(error).__name__},
        ) from error
    return _validate_encoded_view(source_view, encoded, encoded_type, root_scope)


def _advertised_schema_version(view: object) -> int | None:
    capabilities = getattr(view, "capabilities", None)
    schemas = getattr(capabilities, "encoded_view_schemas", None)
    if schemas is None:
        return None
    if not isinstance(schemas, Mapping):
        raise SnapshotCompatibilityError("core encoded_view_schemas capability is not a mapping")
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
    requested_scope: object,
) -> EncodedStructuralLease:
    if not isinstance(encoded, encoded_type):
        raise SnapshotCompatibilityError("core encoded view factory returned the wrong public type")
    schema_name = getattr(encoded, "schema_name", None)
    schema_version = getattr(encoded, "schema_version", None)
    model_schema = getattr(encoded, "model_schema", None)
    owner = getattr(encoded, "owner", None)
    descriptor = getattr(encoded, "descriptor", None)
    buffers = getattr(encoded, "buffers", None)
    fingerprint = getattr(encoded, "structural_fingerprint", None)
    scope = getattr(encoded, "scope", _MISSING)
    document_key = getattr(encoded, "document_key", None)
    raw_segments = getattr(encoded, "segments", _MISSING)

    if (
        type(schema_name) is not str
        or schema_name != ENCODED_SCHEMA_NAME
        or type(schema_version) is not int
        or schema_version != ENCODED_SCHEMA_VERSION
    ):
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
        raise SnapshotCompatibilityError("core encoded view model schema does not match its owner")
    if owner is not source_view:
        raise SnapshotCompatibilityError(
            "core encoded view did not retain the exact source view identity"
        )
    if scope is not requested_scope or document_key is not None:
        raise SnapshotCompatibilityError(
            "core encoded view scope does not match the requested closure selection"
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
    if type(descriptor_digest) is not bytes or descriptor_digest != authoritative_descriptor_digest:
        raise SnapshotCompatibilityError(
            "core encoded view descriptor digest does not match its immutable descriptor"
        )
    if authoritative_descriptor_digest != ENCODED_DESCRIPTOR_SHA256:
        raise SnapshotCompatibilityError(
            "core encoded view descriptor does not match the frozen "
            "pyowl-core structural-columns v1 ledger"
        )
    if not isinstance(buffers, Mapping):
        raise SnapshotCompatibilityError("core encoded view buffers are not a mapping")

    validated_buffers: dict[str, memoryview] = {}
    for name, buffer in buffers.items():
        if (
            len(validated_buffers) >= len(ENCODED_BUFFER_WIDTHS)
            or type(name) is not str
            or not name
            or name in validated_buffers
        ):
            raise SnapshotCompatibilityError(
                "core encoded view buffer names must be bounded unique nonempty strings"
            )
        validated_buffers[name] = _borrowed_bytes(name, buffer)
    if set(validated_buffers) != set(ENCODED_BUFFER_WIDTHS):
        missing = sorted(set(ENCODED_BUFFER_WIDTHS) - set(validated_buffers))
        extra = sorted(set(validated_buffers) - set(ENCODED_BUFFER_WIDTHS))
        raise SnapshotCompatibilityError(
            "core encoded schema 1 buffer set differs",
            details={"missing": repr(missing), "extra": repr(extra)},
        )
    for name, width in ENCODED_BUFFER_WIDTHS.items():
        if validated_buffers[name].nbytes % width:
            raise SnapshotCompatibilityError(
                "core encoded buffer length is not divisible by its schema scalar width",
                details={"buffer": name, "width": width},
            )
    frozen_buffers: Mapping[str, memoryview] = MappingProxyType(
        dict(sorted(validated_buffers.items()))
    )
    local_buffer_bytes = sum(value.nbytes for value in frozen_buffers.values())
    local_root_count = _validate_column_references(
        frozen_buffers,
        source_view,
        local_buffer_bytes=local_buffer_bytes,
    )
    segments = _validate_segments(
        raw_segments,
        top_owner=source_view,
        encoded_view=encoded,
        encoded_type=encoded_type,
        local_root_count=local_root_count,
        local_buffer_bytes=local_buffer_bytes,
    )
    source_fingerprint = getattr(source_view, "structural_fingerprint", None)
    if source_fingerprint is None or type(fingerprint) is not type(source_fingerprint):
        raise SnapshotCompatibilityError("core encoded view fingerprint has the wrong public type")
    return EncodedStructuralLease(
        encoded_view=encoded,
        owner=owner,
        schema_name=schema_name,
        schema_version=schema_version,
        model_schema=model_schema,
        descriptor_sha256=authoritative_descriptor_digest.hex(),
        scope=scope,
        buffers=frozen_buffers,
        buffer_names=tuple(frozen_buffers),
        segments=segments,
    )


def _borrowed_bytes(name: str, value: object) -> memoryview:
    if type(value) is not memoryview:
        raise SnapshotCompatibilityError(
            "core encoded view values must be memoryview instances",
            details={"buffer": name},
        )
    if not value.readonly:
        raise SnapshotCompatibilityError(
            "core encoded view exposed a writable buffer",
            details={"buffer": name},
        )
    if (
        value.format != "B"
        or value.ndim != 1
        or value.itemsize != 1
        or not value.c_contiguous
        or value.shape != (value.nbytes,)
        or value.strides != (1,)
    ):
        raise SnapshotCompatibilityError(
            "core encoded view must expose one-dimensional unsigned-byte C-contiguous buffers",
            details={"buffer": name},
        )
    return value


def _validate_column_references(
    buffers: Mapping[str, memoryview],
    owner: object,
    *,
    local_buffer_bytes: int,
) -> int:
    """Scan schema-local scalars in place without constructing OWL values."""

    root_count = _row_count(buffers, "root_kinds")
    node_count = _row_count(buffers, "node_tags")
    offset_count = _row_count(buffers, "node_field_offsets")
    field_count = _row_count(buffers, "field_kinds")
    item_count = _row_count(buffers, "item_kinds")
    _enforce_public_limit(owner, "max_index_bytes", local_buffer_bytes)
    _enforce_public_limit(owner, "max_canonical_work", local_buffer_bytes)
    _enforce_public_limit(
        owner,
        "max_index_rows",
        max(root_count, node_count, field_count, item_count),
    )
    _enforce_public_limit(owner, "max_terms", node_count)
    _enforce_public_limit(
        owner,
        "max_canonical_work",
        node_count + field_count + item_count,
    )
    if node_count >= 2**32:
        raise SnapshotCompatibilityError("core encoded node IDs exhaust their u32 range")
    if _row_count(buffers, "root_ids") != root_count:
        raise SnapshotCompatibilityError("core encoded root columns differ in length")
    if offset_count != node_count + 1:
        raise SnapshotCompatibilityError(
            "core encoded node-field offsets do not match the node count"
        )
    if (
        _row_count(buffers, "field_values") != field_count
        or _row_count(buffers, "field_lengths") != field_count
    ):
        raise SnapshotCompatibilityError("core encoded field columns differ in length")
    if (
        _row_count(buffers, "item_values") != item_count
        or _row_count(buffers, "item_lengths") != item_count
    ):
        raise SnapshotCompatibilityError("core encoded item columns differ in length")

    previous_offset = 0
    for index in range(offset_count):
        offset = _read_uint(buffers["node_field_offsets"], index, 8)
        if (index == 0 and offset != 0) or offset < previous_offset or offset > field_count:
            raise SnapshotCompatibilityError(
                "core encoded node-field offsets are not monotone in-range boundaries"
            )
        previous_offset = offset
    if previous_offset != field_count:
        raise SnapshotCompatibilityError(
            "core encoded node-field offsets do not cover every field row"
        )

    for index in range(root_count):
        root_kind = _read_uint(buffers["root_kinds"], index, 1)
        root_id = _read_uint(buffers["root_ids"], index, 4)
        if root_kind not in {1, 2, 3}:
            raise SnapshotCompatibilityError("core encoded root kind is invalid")
        if not 1 <= root_id <= node_count:
            raise SnapshotCompatibilityError("core encoded root reference is out of range")

    item_cursor = 0
    scalar_cursor = 0
    scalar_count = buffers["scalar_bytes"].nbytes
    for field_index in range(field_count):
        kind = _read_uint(buffers["field_kinds"], field_index, 1)
        value = _read_uint(buffers["field_values"], field_index, 8)
        length = _read_uint(buffers["field_lengths"], field_index, 8)
        if kind in {_COMPONENT_SET, _COMPONENT_SEQUENCE}:
            if value != item_cursor or length > item_count - item_cursor:
                raise SnapshotCompatibilityError(
                    "core encoded collection offset or bounds are invalid"
                )
            end = item_cursor + length
            while item_cursor < end:
                item_kind = _read_uint(buffers["item_kinds"], item_cursor, 1)
                if kind == _COMPONENT_SET and item_kind != _COMPONENT_NODE:
                    raise SnapshotCompatibilityError(
                        "core encoded canonical-set item is not a node reference"
                    )
                scalar_cursor = _validate_leaf_reference(
                    item_kind,
                    _read_uint(buffers["item_values"], item_cursor, 8),
                    _read_uint(buffers["item_lengths"], item_cursor, 8),
                    node_count=node_count,
                    scalar_count=scalar_count,
                    scalar_cursor=scalar_cursor,
                )
                item_cursor += 1
        else:
            scalar_cursor = _validate_leaf_reference(
                kind,
                value,
                length,
                node_count=node_count,
                scalar_count=scalar_count,
                scalar_cursor=scalar_cursor,
            )
    if item_cursor != item_count:
        raise SnapshotCompatibilityError(
            "core encoded collection offsets do not cover every item row"
        )
    if scalar_cursor != scalar_count:
        raise SnapshotCompatibilityError(
            "core encoded scalar offsets do not cover the borrowed byte arena"
        )
    return root_count


def _validate_leaf_reference(
    kind: int,
    value: int,
    length: int,
    *,
    node_count: int,
    scalar_count: int,
    scalar_cursor: int,
) -> int:
    if kind == _COMPONENT_NONE:
        if value != 0 or length != 0:
            raise SnapshotCompatibilityError(
                "core encoded none component has a nonzero value or length"
            )
        return scalar_cursor
    if kind == _COMPONENT_NODE:
        if length != 0 or not 1 <= value <= node_count:
            raise SnapshotCompatibilityError(
                "core encoded node component reference is out of range"
            )
        return scalar_cursor
    if kind not in {
        _COMPONENT_TEXT,
        _COMPONENT_BYTES,
        _COMPONENT_INTEGER,
        _COMPONENT_ENUM,
    }:
        raise SnapshotCompatibilityError("core encoded component kind is invalid")
    if value != scalar_cursor or length > scalar_count - scalar_cursor:
        raise SnapshotCompatibilityError("core encoded scalar offset or bounds are invalid")
    return scalar_cursor + length


def _validate_segments(
    raw_segments: object,
    *,
    top_owner: object,
    encoded_view: object,
    encoded_type: type[object],
    local_root_count: int,
    local_buffer_bytes: int,
) -> tuple[object, ...]:
    if type(raw_segments) is not tuple or not raw_segments:
        raise SnapshotCompatibilityError(
            "core encoded segment manifest must be a nonempty exact tuple"
        )
    segment_limit = _public_limit(top_owner, "max_composite_members")
    maximum_segments = _DEFAULT_MAX_SEGMENTS if segment_limit is None else segment_limit + 1
    if len(raw_segments) > maximum_segments:
        raise SnapshotCompatibilityError("core encoded segment manifest exceeds its bound")
    _enforce_public_limit(top_owner, "max_index_rows", len(raw_segments))
    records: list[_SegmentRecord] = []
    previous_token: bytes | None = None
    posting_bytes = 0
    posting_rows = 0
    for index, segment in enumerate(raw_segments):
        try:
            role = segment.role
            owner = segment.owner
            source = segment.source
            posting_mode = segment.posting_mode
            raw_root_ids = segment.root_ids
            raw_scope_map = segment.anonymous_scope_map
            member_token = segment.member_token
        except Exception as error:
            raise SnapshotCompatibilityError(
                "core encoded segment metadata is not readable",
                details={"segment": index},
            ) from error
        if type(role) is not int or role not in {
            _SEGMENT_DIRECT,
            _SEGMENT_OVERLAY_BASE,
            _SEGMENT_OVERLAY_DELTA,
            _SEGMENT_COMPOSITE_MEMBER,
            _SEGMENT_COMPOSITE_BRIDGE,
        }:
            raise SnapshotCompatibilityError("core encoded segment role is invalid")
        if type(posting_mode) is not int or posting_mode not in {
            _POSTINGS_ALL,
            _POSTINGS_INCLUDE,
            _POSTINGS_EXCLUDE,
        }:
            raise SnapshotCompatibilityError("core encoded segment posting mode is invalid")
        root_ids = _borrowed_segment_bytes("root_ids", raw_root_ids, 4)
        anonymous_scope_map = _borrowed_segment_bytes("anonymous_scope_map", raw_scope_map, 64)
        posting_bytes += root_ids.nbytes + anonymous_scope_map.nbytes
        posting_rows += root_ids.nbytes // 4 + anonymous_scope_map.nbytes // 64
        _enforce_public_limit(top_owner, "max_index_rows", posting_rows)
        _enforce_public_limit(
            top_owner,
            "max_index_bytes",
            local_buffer_bytes + posting_bytes,
        )
        _enforce_public_limit(
            top_owner,
            "max_canonical_work",
            local_buffer_bytes + posting_bytes,
        )
        referenced_root_count = _segment_root_count(
            owner,
            source,
            top_owner=top_owner,
            encoded_view=encoded_view,
            encoded_type=encoded_type,
            local_root_count=local_root_count,
        )
        previous_root_id = 0
        for posting_index in range(root_ids.nbytes // 4):
            root_id = _read_uint(root_ids, posting_index, 4)
            if root_id <= previous_root_id or root_id > referenced_root_count:
                raise SnapshotCompatibilityError(
                    "core encoded segment postings are not sorted unique in-range references"
                )
            previous_root_id = root_id
        if posting_mode == _POSTINGS_ALL and root_ids.nbytes:
            raise SnapshotCompatibilityError(
                "core encoded ALL segment mode requires empty postings"
            )
        if posting_mode in {_POSTINGS_INCLUDE, _POSTINGS_EXCLUDE} and not root_ids.nbytes:
            raise SnapshotCompatibilityError(
                "core encoded INCLUDE/EXCLUDE segment mode requires postings"
            )
        _validate_scope_map(anonymous_scope_map)
        if role == _SEGMENT_COMPOSITE_MEMBER:
            if type(member_token) is not bytes or len(member_token) != 32:
                raise SnapshotCompatibilityError(
                    "core encoded composite member requires an exact bytes32 token"
                )
            if previous_token is not None and member_token <= previous_token:
                raise SnapshotCompatibilityError(
                    "core encoded composite member tokens are not sorted unique"
                )
            previous_token = member_token
        elif member_token is not None:
            raise SnapshotCompatibilityError(
                "core encoded non-member segment unexpectedly has a member token"
            )
        records.append(
            _SegmentRecord(
                role,
                owner,
                source,
                posting_mode,
                root_ids,
                anonymous_scope_map,
                member_token,
            )
        )
    metadata_bytes = len(records) * 128
    _enforce_public_limit(
        top_owner,
        "max_index_bytes",
        local_buffer_bytes + posting_bytes + metadata_bytes,
    )
    _enforce_public_limit(
        top_owner,
        "max_canonical_work",
        posting_bytes + metadata_bytes,
    )
    _validate_segment_family(tuple(records), top_owner, local_root_count)
    return raw_segments


def _borrowed_segment_bytes(name: str, value: object, width: int) -> memoryview:
    result = _borrowed_bytes(f"segment.{name}", value)
    if result.nbytes % width:
        raise SnapshotCompatibilityError(
            "core encoded segment buffer contains a partial fixed-width row",
            details={"buffer": name, "width": width},
        )
    return result


def _segment_root_count(
    owner: object,
    source: object | None,
    *,
    top_owner: object,
    encoded_view: object,
    encoded_type: type[object],
    local_root_count: int,
) -> int:
    if source is None:
        if owner is not top_owner:
            raise SnapshotCompatibilityError(
                "core encoded local segment does not retain the top owner"
            )
        return local_root_count
    if source is encoded_view:
        raise SnapshotCompatibilityError("core encoded segment graph contains a direct cycle")
    if not isinstance(source, encoded_type) or getattr(source, "owner", _MISSING) is not owner:
        raise SnapshotCompatibilityError(
            "core encoded segment source does not retain its referenced owner"
        )
    if (
        getattr(source, "schema_name", None) != ENCODED_SCHEMA_NAME
        or type(getattr(source, "schema_version", None)) is not int
        or getattr(source, "schema_version", None) != ENCODED_SCHEMA_VERSION
        or type(getattr(source, "model_schema", None)) is not int
        or getattr(source, "model_schema", None) != 1
    ):
        raise SnapshotCompatibilityError("core encoded segment source schema is incompatible")
    source_descriptor = getattr(source, "descriptor", None)
    source_digest = (
        hashlib.sha256(source_descriptor).digest() if type(source_descriptor) is bytes else None
    )
    advertised_digest = getattr(source, "descriptor_digest", source_digest)
    if (
        source_digest != ENCODED_DESCRIPTOR_SHA256
        or type(advertised_digest) is not bytes
        or advertised_digest != source_digest
    ):
        raise SnapshotCompatibilityError("core encoded segment source descriptor is incompatible")
    source_buffers = getattr(source, "buffers", None)
    if not isinstance(source_buffers, Mapping):
        raise SnapshotCompatibilityError("core encoded segment source buffers are invalid")
    root_kinds = _borrowed_segment_bytes("source.root_kinds", source_buffers.get("root_kinds"), 1)
    root_ids = _borrowed_segment_bytes("source.root_ids", source_buffers.get("root_ids"), 4)
    if root_kinds.nbytes != root_ids.nbytes // 4:
        raise SnapshotCompatibilityError(
            "core encoded segment source root columns differ in length"
        )
    return root_kinds.nbytes


def _validate_scope_map(value: memoryview) -> None:
    previous_offset: int | None = None
    for offset in range(0, value.nbytes, 64):
        if _rows_equal(value, offset, offset + 32, 32):
            raise SnapshotCompatibilityError(
                "core encoded anonymous scope map contains an identity row"
            )
        if previous_offset is not None and _compare_rows(value, previous_offset, offset, 32) >= 0:
            raise SnapshotCompatibilityError(
                "core encoded anonymous scope-map sources are not sorted unique"
            )
        previous_offset = offset


def _validate_segment_family(
    segments: tuple[_SegmentRecord, ...],
    top_owner: object,
    local_root_count: int,
) -> None:
    roles = tuple(segment.role for segment in segments)
    if roles == (_SEGMENT_DIRECT,):
        segment = segments[0]
        if (
            segment.owner is not top_owner
            or segment.source is not None
            or segment.posting_mode != _POSTINGS_ALL
            or segment.root_ids.nbytes
            or segment.anonymous_scope_map.nbytes
            or segment.member_token is not None
        ):
            raise SnapshotCompatibilityError(
                "core encoded direct segment metadata is not canonical"
            )
        return
    if roles in {
        (_SEGMENT_OVERLAY_BASE,),
        (_SEGMENT_OVERLAY_BASE, _SEGMENT_OVERLAY_DELTA),
    }:
        base = segments[0]
        if (
            base.source is None
            or base.owner is not getattr(base.source, "owner", _MISSING)
            or base.posting_mode not in {_POSTINGS_ALL, _POSTINGS_EXCLUDE}
            or base.member_token is not None
        ):
            raise SnapshotCompatibilityError("core encoded overlay base segment is invalid")
        if len(segments) == 1:
            if local_root_count:
                raise SnapshotCompatibilityError(
                    "core encoded overlay without delta has local roots"
                )
        else:
            delta = segments[1]
            if (
                delta.owner is not top_owner
                or delta.source is not None
                or delta.posting_mode != _POSTINGS_ALL
                or delta.anonymous_scope_map.nbytes
                or delta.member_token is not None
                or not local_root_count
            ):
                raise SnapshotCompatibilityError("core encoded overlay delta segment is invalid")
        return
    member_count = roles.count(_SEGMENT_COMPOSITE_MEMBER)
    bridge_count = roles.count(_SEGMENT_COMPOSITE_BRIDGE)
    expected = (_SEGMENT_COMPOSITE_MEMBER,) * member_count + (
        (_SEGMENT_COMPOSITE_BRIDGE,) if bridge_count else ()
    )
    if member_count < 2 or bridge_count > 1 or roles != expected:
        raise SnapshotCompatibilityError("core encoded composite segment family is invalid")
    for member in segments[:member_count]:
        if (
            member.source is None
            or member.owner is not getattr(member.source, "owner", _MISSING)
            or member.posting_mode not in {_POSTINGS_ALL, _POSTINGS_INCLUDE, _POSTINGS_EXCLUDE}
        ):
            raise SnapshotCompatibilityError("core encoded composite member is invalid")
    if bridge_count:
        bridge = segments[-1]
        if (
            bridge.owner is not top_owner
            or bridge.source is not None
            or bridge.posting_mode != _POSTINGS_ALL
            or bridge.anonymous_scope_map.nbytes
            or bridge.member_token is not None
            or not local_root_count
        ):
            raise SnapshotCompatibilityError("core encoded composite bridge is invalid")
    elif local_root_count:
        raise SnapshotCompatibilityError("core encoded composite without bridge has local roots")


def _row_count(buffers: Mapping[str, memoryview], name: str) -> int:
    return buffers[name].nbytes // ENCODED_BUFFER_WIDTHS[name]


def _enforce_public_limit(owner: object, name: str, actual: int) -> None:
    maximum = _public_limit(owner, name)
    if maximum is not None and actual > maximum:
        raise SnapshotCompatibilityError(
            f"core encoded validation exceeds public {name}",
            details={"allowed": maximum, "actual": actual},
        )


def _public_limit(owner: object, name: str) -> int | None:
    limits = getattr(owner, "limits", None)
    if limits is None:
        limits = getattr(getattr(owner, "load_options", None), "limits", None)
    value = getattr(limits, name, None)
    return value if type(value) is int and value >= 0 else None


def _read_uint(value: memoryview, index: int, width: int) -> int:
    offset = index * width
    result = 0
    for shift in range(width):
        result |= value[offset + shift] << (shift * 8)
    return result


def _rows_equal(value: memoryview, first: int, second: int, width: int) -> bool:
    return all(value[first + index] == value[second + index] for index in range(width))


def _compare_rows(value: memoryview, first: int, second: int, width: int) -> int:
    for index in range(width):
        left = value[first + index]
        right = value[second + index]
        if left != right:
            return -1 if left < right else 1
    return 0


__all__ = [
    "ENCODED_BUFFER_WIDTHS",
    "ENCODED_DESCRIPTOR_SHA256",
    "ENCODED_NATIVE_FEATURE",
    "ENCODED_SCHEMA_NAME",
    "ENCODED_SCHEMA_VERSION",
    "EncodedNegotiation",
    "EncodedStructuralLease",
    "IngestionPath",
    "select_ingestion",
]
