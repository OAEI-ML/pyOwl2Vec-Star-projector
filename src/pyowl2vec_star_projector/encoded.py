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
from typing import Any, Literal, cast

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
_TAG_IRI = 1
_TAG_ENTITY = 2
_TAG_ANONYMOUS_INDIVIDUAL = 3
_TAG_LITERAL = 4
_TAG_ANNOTATION = 5
_TAG_OBJECT_ONE_OF = 33
_TAG_OBJECT_HAS_VALUE = 36
_TAG_SUB_CLASS_OF = 61
_TAG_OBJECT_PROPERTY_DOMAIN = 74
_TAG_OBJECT_PROPERTY_RANGE = 75
_TAG_SAME_INDIVIDUAL = 110
_TAG_DIFFERENT_INDIVIDUALS = 111
_TAG_CLASS_ASSERTION = 112
_TAG_OBJECT_PROPERTY_ASSERTION = 113
_TAG_NEGATIVE_OBJECT_PROPERTY_ASSERTION = 114
_TAG_DATA_PROPERTY_ASSERTION = 115
_TAG_NEGATIVE_DATA_PROPERTY_ASSERTION = 116
_TAG_ANNOTATION_ASSERTION = 120
_TAG_SWRL_RULE = 148
_ROOT_ONTOLOGY_ANNOTATION = 1
_ROOT_AXIOM = 2
_ROOT_EXTENSION = 3
_SCOPE_MAPPED_CONSTRUCT_ROOT_KINDS = {
    _TAG_ANNOTATION: _ROOT_ONTOLOGY_ANNOTATION,
    _TAG_SUB_CLASS_OF: _ROOT_AXIOM,
    _TAG_OBJECT_PROPERTY_DOMAIN: _ROOT_AXIOM,
    _TAG_OBJECT_PROPERTY_RANGE: _ROOT_AXIOM,
    _TAG_CLASS_ASSERTION: _ROOT_AXIOM,
    _TAG_OBJECT_PROPERTY_ASSERTION: _ROOT_AXIOM,
    _TAG_NEGATIVE_OBJECT_PROPERTY_ASSERTION: _ROOT_AXIOM,
    _TAG_DATA_PROPERTY_ASSERTION: _ROOT_AXIOM,
    _TAG_NEGATIVE_DATA_PROPERTY_ASSERTION: _ROOT_AXIOM,
    _TAG_SAME_INDIVIDUAL: _ROOT_AXIOM,
    _TAG_DIFFERENT_INDIVIDUALS: _ROOT_AXIOM,
    _TAG_ANNOTATION_ASSERTION: _ROOT_AXIOM,
    _TAG_SWRL_RULE: _ROOT_EXTENSION,
}
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
    """Request the direct schema through the explicit compatibility seam."""

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


def _resolve_private_overlay_aliases(
    lease: EncodedStructuralLease,
) -> (
    tuple[
        EncodedStructuralLease,
        tuple[EncodedStructuralLease, ...],
        memoryview | None,
    ]
    | None
):
    """Resolve a narrow canonical overlay chain to one retained direct source.

    This is intentionally narrower than general segment traversal.  It admits
    only iterative one-segment OVERLAY_BASE manifests whose local column tables
    and anonymous-scope maps are canonically empty.  Every segment is ALL, except
    that the terminal-adjacent segment may carry one sorted EXCLUDE posting
    table.  Public overlay-depth and cumulative canonical-work budgets bound
    resolution before the direct source and optional borrowed posting table are
    handed to Rust.
    """

    current = lease
    containers: list[EncodedStructuralLease] = []
    active: dict[int, object] = {id(lease.encoded_view): lease.encoded_view}
    top_owner = lease.owner
    canonical_work = 0
    excluded_root_ids: memoryview | None = None
    excluded_source_view: object | None = None
    while True:
        source_metadata = _private_overlay_alias_source(current)
        if source_metadata is None:
            if not containers:
                return None
            canonical_work += _private_encoded_lease_validation_work(current)
            _enforce_public_limit(top_owner, "max_canonical_work", canonical_work)
            if len(current.segments) != 1:
                return None
            try:
                terminal_role = cast(Any, current.segments[0]).role
            except Exception as error:
                raise SnapshotCompatibilityError(
                    "core encoded overlay alias terminal metadata is not readable"
                ) from error
            if type(terminal_role) is not int or terminal_role != _SEGMENT_DIRECT:
                return None
            if excluded_root_ids is not None and excluded_source_view is not current.encoded_view:
                return None
            return current, tuple(containers), excluded_root_ids

        owner, source, source_scope, segment_excluded_root_ids = source_metadata
        if segment_excluded_root_ids is not None:
            if excluded_root_ids is not None:
                return None
            excluded_root_ids = segment_excluded_root_ids
            excluded_source_view = source
        containers.append(current)
        _enforce_public_limit(top_owner, "max_overlay_depth", len(containers))
        canonical_work += _private_encoded_lease_validation_work(current)
        _enforce_public_limit(top_owner, "max_canonical_work", canonical_work)

        identity = id(source)
        if active.get(identity) is source:
            raise SnapshotCompatibilityError("core encoded empty-overlay alias graph is cyclic")
        active[identity] = source
        current = _validate_encoded_view(
            owner,
            source,
            type(lease.encoded_view),
            source_scope,
        )


def _resolve_private_single_overlay_delta(
    lease: EncodedStructuralLease,
) -> tuple[EncodedStructuralLease, memoryview | None, int | None, int | None] | None:
    """Resolve the bounded local overlay slice without general segment traversal.

    The private native seam admits only one direct ``ALL`` or ``EXCLUDE``
    source and one nonempty local ``ALL`` delta segment. Rust validates the
    complete source and local tables, their exact cross-table canonical order,
    the optional source posting table, and every local constructor before
    choosing the bounded emitting or domain/range plan. Every other valid
    segmented form remains a whole-operation scalar fallback.
    """

    if type(lease) is not EncodedStructuralLease or len(lease.segments) != 2:
        return None
    base = cast(Any, lease.segments[0])
    delta = cast(Any, lease.segments[1])
    try:
        base_role = base.role
        base_owner = base.owner
        source = base.source
        base_posting_mode = base.posting_mode
        base_root_ids = base.root_ids
        base_scope_map = base.anonymous_scope_map
        base_member_token = base.member_token
        delta_role = delta.role
        delta_owner = delta.owner
        delta_source = delta.source
        delta_posting_mode = delta.posting_mode
        delta_root_ids = delta.root_ids
        delta_scope_map = delta.anonymous_scope_map
        delta_member_token = delta.member_token
    except Exception as error:
        raise SnapshotCompatibilityError(
            "core encoded local-overlay metadata is not readable"
        ) from error
    if (
        type(base_role) is not int
        or base_role != _SEGMENT_OVERLAY_BASE
        or source is None
        or base_owner is not getattr(source, "owner", _MISSING)
        or type(base_posting_mode) is not int
        or type(base_root_ids) is not memoryview
        or type(base_scope_map) is not memoryview
        or base_scope_map.nbytes
        or base_member_token is not None
        or type(delta_role) is not int
        or delta_role != _SEGMENT_OVERLAY_DELTA
        or delta_owner is not lease.owner
        or delta_source is not None
        or type(delta_posting_mode) is not int
        or delta_posting_mode != _POSTINGS_ALL
        or type(delta_root_ids) is not memoryview
        or delta_root_ids.nbytes
        or type(delta_scope_map) is not memoryview
        or delta_scope_map.nbytes
        or delta_member_token is not None
        or lease.buffers["root_kinds"].nbytes < 1
        or lease.buffers["root_ids"].nbytes != 4 * lease.buffers["root_kinds"].nbytes
    ):
        return None
    if base_posting_mode == _POSTINGS_ALL:
        if base_root_ids.nbytes:
            return None
        excluded_root_ids = None
    elif base_posting_mode == _POSTINGS_EXCLUDE:
        if not base_root_ids.nbytes:
            return None
        excluded_root_ids = base_root_ids
    else:
        return None
    source_scope = getattr(source, "scope", _MISSING)
    if source_scope is not lease.scope:
        return None

    _enforce_public_limit(lease.owner, "max_overlay_depth", 1)
    source_lease = _validate_encoded_view(
        base_owner,
        source,
        type(lease.encoded_view),
        source_scope,
    )
    if len(source_lease.segments) != 1:
        return None
    try:
        source_role = cast(Any, source_lease.segments[0]).role
    except Exception as error:
        raise SnapshotCompatibilityError(
            "core encoded local-overlay source role is not readable"
        ) from error
    if type(source_role) is not int or source_role != _SEGMENT_DIRECT:
        return None

    validation_work = _private_encoded_lease_validation_work(
        lease
    ) + _private_encoded_lease_validation_work(source_lease)
    _enforce_public_limit(lease.owner, "max_canonical_work", validation_work)
    return (
        source_lease,
        excluded_root_ids,
        _public_limit(lease.owner, "max_canonical_work"),
        _public_limit(lease.owner, "max_index_bytes"),
    )


_CompositeRow = tuple[
    EncodedStructuralLease,
    memoryview | None,
    memoryview | None,
    memoryview | None,
]

_CompositePlanRow = tuple[
    EncodedStructuralLease,
    memoryview | None,
    memoryview | None,
    memoryview | tuple[memoryview, ...] | None,
]

_RecursiveCompositeRow = tuple[
    EncodedStructuralLease,
    memoryview | None,
    memoryview | None,
    tuple[memoryview, ...],
    tuple[int, ...],
]


class _RecursiveLeafPlanUnsupported(Exception):
    """Keep an unrepresentable recursive leaf plan on whole-call fallback."""


@dataclass(slots=True)
class _RecursiveLeafPlanFrame:
    """One suspended segmented manifest without using the Python call stack."""

    lease: EncodedStructuralLease
    identity: int
    segments: tuple[object, ...]
    roles: tuple[int, ...]
    overlay_depth: int
    local_only: bool
    resolved: list[_RecursiveCompositeRow]
    overlay_increment: int = 0
    relative_overlay_span: int = 0
    stage: int = 0
    member_count: int = 0
    bridge_count: int = 0
    member_index: int = 0
    pending_segment: object | None = None
    pending_source: EncodedStructuralLease | None = None
    pending_rows: tuple[_RecursiveCompositeRow, ...] | None = None
    pending_overlay_span: int | None = None


def _resolve_private_direct_composite_rows(
    lease: EncodedStructuralLease,
    *,
    member_count: int,
    require_direct_sources: bool = True,
    allow_scope_maps: bool = False,
) -> tuple[_CompositeRow, ...] | None:
    """Validate one exact member composite without flattening source tables."""

    if (
        type(member_count) is not int
        or member_count < 2
        or type(require_direct_sources) is not bool
        or type(allow_scope_maps) is not bool
        or type(lease) is not EncodedStructuralLease
        or len(lease.segments) != member_count
    ):
        return None
    offsets = lease.buffers["node_field_offsets"]
    if offsets.nbytes != 8 or any(offsets):
        return None
    if any(value.nbytes for name, value in lease.buffers.items() if name != "node_field_offsets"):
        return None

    rows: list[_CompositeRow] = []
    previous_token: bytes | None = None
    for raw_segment in lease.segments:
        segment = cast(Any, raw_segment)
        try:
            role = segment.role
            owner = segment.owner
            source = segment.source
            posting_mode = segment.posting_mode
            root_ids = segment.root_ids
            scope_map = segment.anonymous_scope_map
            member_token = segment.member_token
        except Exception as error:
            raise SnapshotCompatibilityError(
                "core encoded direct composite metadata is not readable"
            ) from error
        if (
            type(role) is not int
            or role != _SEGMENT_COMPOSITE_MEMBER
            or source is None
            or owner is not getattr(source, "owner", _MISSING)
            or type(posting_mode) is not int
            or type(root_ids) is not memoryview
            or type(scope_map) is not memoryview
            or (scope_map.nbytes and not allow_scope_maps)
            or type(member_token) is not bytes
            or len(member_token) != 32
            or (previous_token is not None and member_token <= previous_token)
        ):
            return None
        previous_token = member_token
        if posting_mode == _POSTINGS_ALL:
            if root_ids.nbytes:
                return None
            included_root_ids = None
            excluded_root_ids = None
        elif posting_mode == _POSTINGS_INCLUDE:
            if not root_ids.nbytes:
                return None
            included_root_ids = root_ids
            excluded_root_ids = None
        elif posting_mode == _POSTINGS_EXCLUDE:
            if not root_ids.nbytes:
                return None
            included_root_ids = None
            excluded_root_ids = root_ids
        else:
            return None
        source_scope = getattr(source, "scope", _MISSING)
        if source_scope is not lease.scope:
            return None
        source_lease = _validate_encoded_view(
            owner,
            source,
            type(lease.encoded_view),
            source_scope,
        )
        try:
            source_roles = tuple(
                cast(Any, source_segment).role for source_segment in source_lease.segments
            )
        except Exception as error:
            raise SnapshotCompatibilityError(
                "core encoded composite member roles are not readable"
            ) from error
        if any(type(source_role) is not int for source_role in source_roles) or (
            require_direct_sources and source_roles != (_SEGMENT_DIRECT,)
        ):
            return None
        if any(
            source_lease.encoded_view is prior.encoded_view or source_lease.owner is prior.owner
            for prior, _included, _excluded, _scope_map in rows
        ):
            return None
        rows.append(
            (
                source_lease,
                included_root_ids,
                excluded_root_ids,
                scope_map if scope_map.nbytes else None,
            )
        )

    return tuple(rows) if len(rows) == member_count else None


def _resolve_private_dynamic_member_composite(
    lease: EncodedStructuralLease,
) -> tuple[tuple[_CompositeRow, ...], int | None, int | None] | None:
    """Resolve an arbitrary bounded direct-member composite without flattening.

    The encoded provider has already enforced its public member-count limit.
    This resolver keeps every member's source-local INCLUDE/EXCLUDE posting and
    anonymous-scope map attached to that member; Rust performs the single
    canonical rule pass and rejects unsupported scope-map combinations before
    output.
    """

    if type(lease) is not EncodedStructuralLease:
        return None
    member_count = len(lease.segments)
    if member_count < 2:
        return None
    rows = _resolve_private_direct_composite_rows(
        lease,
        member_count=member_count,
        allow_scope_maps=True,
    )
    if rows is None:
        return None
    validation_work = _private_encoded_lease_validation_work(lease) + sum(
        _private_encoded_lease_validation_work(source)
        for source, _included, _excluded, _scope_map in rows
    )
    _enforce_public_limit(lease.owner, "max_canonical_work", validation_work)
    return (
        rows,
        _public_limit(lease.owner, "max_canonical_work"),
        _public_limit(lease.owner, "max_index_bytes"),
    )


def _recursive_leaf_source_lease(
    top_lease: EncodedStructuralLease,
    current_lease: EncodedStructuralLease,
    segment: object,
    *,
    retained: Mapping[int, EncodedStructuralLease] | None = None,
) -> EncodedStructuralLease:
    """Validate one exact referenced source while retaining closure identity."""

    typed = cast(Any, segment)
    source = typed.source
    owner = typed.owner
    if source is current_lease.encoded_view:
        raise SnapshotCompatibilityError("core encoded recursive leaf graph is cyclic")
    if source is None or owner is not getattr(source, "owner", _MISSING):
        raise SnapshotCompatibilityError(
            "core encoded recursive leaf segment lost its source owner"
        )
    if getattr(source, "scope", _MISSING) is not top_lease.scope:
        raise SnapshotCompatibilityError(
            "core encoded recursive leaf source changed closure scope"
        )
    if retained is not None:
        cached = retained.get(id(source))
        if cached is not None:
            if (
                cached.encoded_view is not source
                or cached.owner is not owner
                or cached.scope is not top_lease.scope
            ):
                raise SnapshotCompatibilityError(
                    "core encoded recursive DAG source changed retained identity"
                )
            return cached
    return _validate_encoded_view(
        owner,
        source,
        type(top_lease.encoded_view),
        top_lease.scope,
    )


def _apply_recursive_leaf_scope_map(
    rows: tuple[_RecursiveCompositeRow, ...],
    scope_map: memoryview,
) -> tuple[_RecursiveCompositeRow, ...]:
    """Append one exact map to each affected descendant's retained chain."""

    if not scope_map.nbytes:
        return rows
    mapped: list[_RecursiveCompositeRow] = []
    for lease, included, excluded, existing_maps, path in rows:
        if not _recursive_scope_map_applies(
            lease,
            scope_map,
            prior_maps=existing_maps,
        ):
            mapped.append((lease, included, excluded, existing_maps, path))
            continue
        mapped.append(
            (lease, included, excluded, (*existing_maps, scope_map), path)
        )
    return tuple(mapped)


def _recursive_scope_map_applies(
    lease: EncodedStructuralLease,
    scope_map: memoryview,
    *,
    prior_maps: tuple[memoryview, ...] = (),
) -> bool:
    """Return whether a map consumes one effective scope in this leaf table."""

    buffers = lease.buffers
    for node_index in range(_row_count(buffers, "node_tags")):
        if _read_uint(buffers["node_tags"], node_index, 2) != _TAG_ANONYMOUS_INDIVIDUAL:
            continue
        start = _read_uint(buffers["node_field_offsets"], node_index, 8)
        end = _read_uint(buffers["node_field_offsets"], node_index + 1, 8)
        if (
            end - start < 1
            or _read_uint(buffers["field_kinds"], start, 1) != _COMPONENT_BYTES
            or _read_uint(buffers["field_lengths"], start, 8) != 32
        ):
            continue
        scalar_offset = _read_uint(buffers["field_values"], start, 8)
        current_scope = buffers["scalar_bytes"][
            scalar_offset : scalar_offset + 32
        ]
        for prior_map in prior_maps:
            for map_offset in range(0, prior_map.nbytes, 64):
                if current_scope == prior_map[map_offset : map_offset + 32]:
                    current_scope = prior_map[
                        map_offset + 32 : map_offset + 64
                    ]
                    break
        for map_offset in range(0, scope_map.nbytes, 64):
            if current_scope == scope_map[map_offset : map_offset + 32]:
                return True
    return False


def _apply_recursive_leaf_postings(
    rows: tuple[_RecursiveCompositeRow, ...],
    source_lease: EncodedStructuralLease,
    segment: object,
) -> tuple[_RecursiveCompositeRow, ...]:
    """Apply source-local INCLUDE/EXCLUDE without flattening descendant tables."""

    typed = cast(Any, segment)
    posting_mode = typed.posting_mode
    postings = cast(memoryview, typed.root_ids)
    if posting_mode == _POSTINGS_ALL:
        return rows
    if posting_mode not in {_POSTINGS_INCLUDE, _POSTINGS_EXCLUDE}:
        raise SnapshotCompatibilityError(
            "core encoded recursive leaf posting mode is invalid"
        )
    selected: list[_RecursiveCompositeRow] = []
    for lease, included, excluded, scope_maps, path in rows:
        source_local = lease.encoded_view is source_lease.encoded_view
        if posting_mode == _POSTINGS_INCLUDE and not source_local:
            continue
        if not source_local:
            selected.append((lease, included, excluded, scope_maps, path))
            continue
        if included is not None or excluded is not None:
            raise _RecursiveLeafPlanUnsupported(
                "one recursive leaf received multiple source-local selectors"
            )
        selected.append(
            (
                lease,
                postings if posting_mode == _POSTINGS_INCLUDE else None,
                postings if posting_mode == _POSTINGS_EXCLUDE else None,
                scope_maps,
                path,
            )
        )
    return tuple(selected)


def _prepend_recursive_leaf_segment(
    rows: tuple[_RecursiveCompositeRow, ...],
    segment_index: int,
) -> tuple[_RecursiveCompositeRow, ...]:
    return tuple(
        (lease, included, excluded, scope_maps, (segment_index, *path))
        for lease, included, excluded, scope_maps, path in rows
    )


def _resolve_private_recursive_leaf_plan(
    lease: EncodedStructuralLease,
    *,
    retain_empty_leaves: bool = False,
) -> (
        tuple[
            tuple[_CompositePlanRow, ...],
            tuple[tuple[int, ...], ...],
        tuple[EncodedStructuralLease, ...],
        int | None,
        int | None,
    ]
    | None
):
    """Resolve a segmented graph into exact local tables for one Rust merge.

    Every emitted row is the local table of a canonical ``DIRECT``,
    ``OVERLAY_DELTA``, or ``COMPOSITE_BRIDGE`` segment. Source-local selectors
    remain attached to only that table. Ordered anonymous-scope map slices stay
    retained per leaf and are composed by Rust during the same canonical pass.
    Stable segment-index paths allow independently materialized CLOSURE and ROOT
    manifests to be paired without relying on Python object identities.
    """

    if type(lease) is not EncodedStructuralLease:
        return None

    try:
        direct_candidate = (
            len(lease.segments) >= 2
            and all(
                cast(Any, segment).role == _SEGMENT_COMPOSITE_MEMBER
                and tuple(
                    cast(Any, child).role
                    for child in cast(Any, segment).source.segments
                )
                == (_SEGMENT_DIRECT,)
                for segment in lease.segments
            )
        )
    except Exception:
        direct_candidate = False
    direct = (
        _resolve_private_dynamic_member_composite(lease)
        if direct_candidate
        else None
    )
    if direct is not None:
        rows, max_work, max_workspace = direct
        containers = (lease, *tuple(row[0] for row in rows))
        return rows, (), containers, max_work, max_workspace

    if type(retain_empty_leaves) is not bool:
        raise TypeError("retain_empty_leaves must be bool")

    active: set[int] = set()
    retained: dict[int, EncodedStructuralLease] = {}
    resolved_cache: dict[
        tuple[int, bool],
        tuple[tuple[_RecursiveCompositeRow, ...], int],
    ] = {}
    validation_work = 0
    leaf_count = 0

    def charge_leaf() -> None:
        nonlocal leaf_count
        leaf_count += 1
        _enforce_public_limit(lease.owner, "max_composite_members", leaf_count)

    def start(
        source_lease: EncodedStructuralLease,
        parent_overlay_depth: int,
        *,
        local_only: bool = False,
    ) -> _RecursiveLeafPlanFrame:
        nonlocal validation_work
        identity = id(source_lease.encoded_view)
        if identity in active:
            raise SnapshotCompatibilityError("core encoded recursive leaf graph is cyclic")
        if identity not in retained:
            validation_work += _private_encoded_lease_validation_work(
                source_lease
            )
            _enforce_public_limit(
                lease.owner,
                "max_canonical_work",
                validation_work,
            )
            retained[identity] = source_lease
        active.add(identity)
        try:
            roles = tuple(cast(Any, segment).role for segment in source_lease.segments)
        except Exception as error:
            active.remove(identity)
            raise SnapshotCompatibilityError(
                "core encoded recursive leaf roles are not readable"
            ) from error
        is_overlay = roles in {
            (_SEGMENT_OVERLAY_BASE,),
            (_SEGMENT_OVERLAY_BASE, _SEGMENT_OVERLAY_DELTA),
        }
        overlay_depth = parent_overlay_depth + int(is_overlay)
        _enforce_public_limit(lease.owner, "max_overlay_depth", overlay_depth)
        return _RecursiveLeafPlanFrame(
            lease=source_lease,
            identity=identity,
            segments=source_lease.segments,
            roles=roles,
            overlay_depth=overlay_depth,
            local_only=local_only,
            resolved=[],
            overlay_increment=int(is_overlay),
            relative_overlay_span=int(is_overlay),
        )

    stack = [start(lease, 0)]
    try:
        while stack:
            frame = stack[-1]
            result: tuple[_RecursiveCompositeRow, ...] | None = None

            if frame.local_only:
                if frame.roles == (_SEGMENT_DIRECT,):
                    if retain_empty_leaves or frame.lease.buffers["root_kinds"].nbytes:
                        charge_leaf()
                        frame.resolved.append(
                            (frame.lease, None, None, (), (0,))
                        )
                    result = tuple(frame.resolved)
                elif frame.roles in {
                    (_SEGMENT_OVERLAY_BASE,),
                    (_SEGMENT_OVERLAY_BASE, _SEGMENT_OVERLAY_DELTA),
                }:
                    if len(frame.segments) == 2:
                        charge_leaf()
                        frame.resolved.append(
                            (frame.lease, None, None, (), (1,))
                        )
                    result = tuple(frame.resolved)
                else:
                    member_count = frame.roles.count(_SEGMENT_COMPOSITE_MEMBER)
                    bridge_count = frame.roles.count(_SEGMENT_COMPOSITE_BRIDGE)
                    expected = (_SEGMENT_COMPOSITE_MEMBER,) * member_count + (
                        (_SEGMENT_COMPOSITE_BRIDGE,) if bridge_count else ()
                    )
                    if (
                        member_count < 2
                        or bridge_count > 1
                        or frame.roles != expected
                    ):
                        return None
                    if bridge_count:
                        charge_leaf()
                        frame.resolved.append(
                            (
                                frame.lease,
                                None,
                                None,
                                (),
                                (len(frame.segments) - 1,),
                            )
                        )
                    result = tuple(frame.resolved)
            elif frame.roles == (_SEGMENT_DIRECT,):
                if retain_empty_leaves or frame.lease.buffers["root_kinds"].nbytes:
                    charge_leaf()
                    frame.resolved.append(
                        (frame.lease, None, None, (), (0,))
                    )
                result = tuple(frame.resolved)
            elif frame.roles in {
                (_SEGMENT_OVERLAY_BASE,),
                (_SEGMENT_OVERLAY_BASE, _SEGMENT_OVERLAY_DELTA),
            }:
                if frame.stage == 0:
                    base = frame.segments[0]
                    source_lease = _recursive_leaf_source_lease(
                        lease,
                        frame.lease,
                        base,
                        retained=retained,
                    )
                    frame.pending_segment = base
                    frame.pending_source = source_lease
                    frame.stage = 1
                    cache_key = (
                        id(source_lease.encoded_view),
                        cast(Any, base).posting_mode
                        == _POSTINGS_INCLUDE,
                    )
                    if cache_key in resolved_cache:
                        (
                            frame.pending_rows,
                            frame.pending_overlay_span,
                        ) = resolved_cache[cache_key]
                        _enforce_public_limit(
                            lease.owner,
                            "max_overlay_depth",
                            frame.overlay_depth + frame.pending_overlay_span,
                        )
                        for _row in frame.pending_rows:
                            charge_leaf()
                        continue
                    stack.append(
                        start(
                            source_lease,
                            frame.overlay_depth,
                            local_only=cast(Any, base).posting_mode
                            == _POSTINGS_INCLUDE,
                        )
                    )
                    continue

                base = frame.pending_segment
                pending_source = frame.pending_source
                child_rows = frame.pending_rows
                child_overlay_span = frame.pending_overlay_span
                if (
                    base is None
                    or pending_source is None
                    or child_rows is None
                    or child_overlay_span is None
                ):
                    raise AssertionError("recursive overlay dependency was not resolved")
                frame.relative_overlay_span = max(
                    frame.relative_overlay_span,
                    frame.overlay_increment + child_overlay_span,
                )
                typed_base = cast(Any, base)
                child_rows = _apply_recursive_leaf_scope_map(
                    child_rows,
                    cast(memoryview, typed_base.anonymous_scope_map),
                )
                child_rows = _apply_recursive_leaf_postings(
                    child_rows,
                    pending_source,
                    base,
                )
                frame.resolved.extend(
                    _prepend_recursive_leaf_segment(child_rows, 0)
                )
                if len(frame.segments) == 2:
                    charge_leaf()
                    frame.resolved.append(
                        (frame.lease, None, None, (), (1,))
                    )
                result = tuple(frame.resolved)
            else:
                if frame.stage == 0:
                    frame.member_count = frame.roles.count(_SEGMENT_COMPOSITE_MEMBER)
                    frame.bridge_count = frame.roles.count(_SEGMENT_COMPOSITE_BRIDGE)
                    expected = (_SEGMENT_COMPOSITE_MEMBER,) * frame.member_count + (
                        (_SEGMENT_COMPOSITE_BRIDGE,) if frame.bridge_count else ()
                    )
                    if (
                        frame.member_count < 2
                        or frame.bridge_count > 1
                        or frame.roles != expected
                    ):
                        return None
                    frame.stage = 1

                if frame.pending_rows is not None:
                    member = frame.pending_segment
                    pending_source = frame.pending_source
                    child_overlay_span = frame.pending_overlay_span
                    if (
                        member is None
                        or pending_source is None
                        or child_overlay_span is None
                    ):
                        raise AssertionError(
                            "recursive composite dependency was not retained"
                        )
                    frame.relative_overlay_span = max(
                        frame.relative_overlay_span,
                        frame.overlay_increment + child_overlay_span,
                    )
                    typed_member = cast(Any, member)
                    child_rows = _apply_recursive_leaf_scope_map(
                        frame.pending_rows,
                        cast(memoryview, typed_member.anonymous_scope_map),
                    )
                    child_rows = _apply_recursive_leaf_postings(
                        child_rows,
                        pending_source,
                        member,
                    )
                    frame.resolved.extend(
                        _prepend_recursive_leaf_segment(
                            child_rows,
                            frame.member_index,
                        )
                    )
                    frame.pending_segment = None
                    frame.pending_source = None
                    frame.pending_rows = None
                    frame.pending_overlay_span = None
                    frame.member_index += 1
                    continue

                if frame.member_index < frame.member_count:
                    member = frame.segments[frame.member_index]
                    source_lease = _recursive_leaf_source_lease(
                        lease,
                        frame.lease,
                        member,
                        retained=retained,
                    )
                    frame.pending_segment = member
                    frame.pending_source = source_lease
                    cache_key = (
                        id(source_lease.encoded_view),
                        cast(Any, member).posting_mode
                        == _POSTINGS_INCLUDE,
                    )
                    if cache_key in resolved_cache:
                        (
                            frame.pending_rows,
                            frame.pending_overlay_span,
                        ) = resolved_cache[cache_key]
                        _enforce_public_limit(
                            lease.owner,
                            "max_overlay_depth",
                            frame.overlay_depth + frame.pending_overlay_span,
                        )
                        for _row in frame.pending_rows:
                            charge_leaf()
                        continue
                    stack.append(
                        start(
                            source_lease,
                            frame.overlay_depth,
                            local_only=cast(Any, member).posting_mode
                            == _POSTINGS_INCLUDE,
                        )
                    )
                    continue

                if frame.bridge_count:
                    charge_leaf()
                    frame.resolved.append(
                        (
                            frame.lease,
                            None,
                            None,
                            (),
                            (len(frame.segments) - 1,),
                        )
                    )
                result = tuple(frame.resolved)

            resolved_cache[(frame.identity, frame.local_only)] = (
                result,
                frame.relative_overlay_span,
            )
            active.remove(frame.identity)
            stack.pop()
            if not stack:
                recursive_rows = result
                break
            parent = stack[-1]
            if parent.pending_rows is not None:
                raise AssertionError(
                    "recursive leaf dependency result was already populated"
                )
            parent.pending_rows = result
            parent.pending_overlay_span = frame.relative_overlay_span
        else:  # pragma: no cover - the explicit stack always returns a root result.
            raise AssertionError("recursive leaf resolution completed without a result")
    except _RecursiveLeafPlanUnsupported:
        return None
    finally:
        for frame in stack:
            active.discard(frame.identity)

    if len(recursive_rows) < 2:
        return None
    simple_rows = tuple(
        (leaf, included, excluded, scope_maps)
        for leaf, included, excluded, scope_maps, _path in recursive_rows
    )
    paths = tuple(
        path
        for _leaf, _included, _excluded, _map, path in recursive_rows
    )
    return (
        simple_rows,
        paths,
        tuple(retained.values()),
        _public_limit(lease.owner, "max_canonical_work"),
        _public_limit(lease.owner, "max_index_bytes"),
    )


def _recursive_empty_local_table(lease: EncodedStructuralLease) -> bool:
    offsets = lease.buffers["node_field_offsets"]
    return (
        offsets.nbytes == 8
        and not any(offsets)
        and all(
            not value.nbytes
            for name, value in lease.buffers.items()
            if name != "node_field_offsets"
        )
    )


def _resolve_private_recursive_root_pair(
    closure_manifest: EncodedStructuralLease,
    closure_rows: tuple[_CompositePlanRow, ...],
    closure_paths: tuple[tuple[int, ...], ...],
    root_manifest: EncodedStructuralLease,
) -> (
    tuple[
        tuple[_CompositePlanRow, ...],
        tuple[tuple[int, ...], ...],
        tuple[EncodedStructuralLease, ...],
        int | None,
        int | None,
    ]
    | None
):
    """Pair ROOT tables to stable CLOSURE leaf coordinates without flattening.

    ROOT scope intentionally omits overlay deltas and composite bridges because
    those local additions exist only in CLOSURE scope.  A missing terminal local
    segment therefore binds the canonical empty ROOT container at that stable
    coordinate; every referenced segment before it remains exact and is checked
    against its independently materialized CLOSURE counterpart.
    """

    if (
        type(closure_manifest) is not EncodedStructuralLease
        or type(root_manifest) is not EncodedStructuralLease
        or closure_manifest.owner is not root_manifest.owner
        or len(closure_rows) != len(closure_paths)
        or len(closure_rows) < 2
    ):
        return None

    retained: dict[int, EncodedStructuralLease] = {}
    prefix_cache: dict[tuple[int, ...], EncodedStructuralLease] = {
        (): root_manifest
    }
    validation_work = 0

    def retain(item: EncodedStructuralLease) -> None:
        nonlocal validation_work
        identity = id(item.encoded_view)
        if identity in retained:
            return
        validation_work += _private_encoded_lease_validation_work(item)
        _enforce_public_limit(
            closure_manifest.owner,
            "max_canonical_work",
            validation_work,
        )
        retained[identity] = item

    retain(root_manifest)
    paired_rows: list[_CompositePlanRow] = []
    paired_paths: list[tuple[int, ...]] = []
    try:
        for closure_row, path in zip(
            closure_rows,
            closure_paths,
            strict=True,
        ):
            if type(path) is not tuple or not path:
                raise SnapshotCompatibilityError(
                    "encoded recursive CLOSURE leaf lost its stable coordinate"
                )
            closure_view = closure_manifest.encoded_view
            root_lease = root_manifest
            prefix: tuple[int, ...] = ()
            root_references: list[tuple[object, EncodedStructuralLease]] = []
            overlay_depth = int(
                tuple(cast(Any, segment).role for segment in root_lease.segments)
                in {
                    (_SEGMENT_OVERLAY_BASE,),
                    (_SEGMENT_OVERLAY_BASE, _SEGMENT_OVERLAY_DELTA),
                }
            )
            _enforce_public_limit(
                closure_manifest.owner,
                "max_overlay_depth",
                overlay_depth,
            )

            for depth, segment_index in enumerate(path):
                closure_segments = cast(Any, closure_view).segments
                if (
                    type(segment_index) is not int
                    or segment_index < 0
                    or segment_index >= len(closure_segments)
                ):
                    raise SnapshotCompatibilityError(
                        "encoded recursive CLOSURE coordinate is out of range"
                    )
                closure_segment = closure_segments[segment_index]
                closure_role = cast(Any, closure_segment).role
                root_segments = root_lease.segments
                terminal = depth + 1 == len(path)

                if terminal:
                    if closure_role not in {
                        _SEGMENT_DIRECT,
                        _SEGMENT_OVERLAY_DELTA,
                        _SEGMENT_COMPOSITE_BRIDGE,
                    }:
                        raise SnapshotCompatibilityError(
                            "encoded recursive CLOSURE coordinate is not a local leaf"
                        )
                    if segment_index < len(root_segments):
                        root_segment = root_segments[segment_index]
                        if cast(Any, root_segment).role != closure_role:
                            raise SnapshotCompatibilityError(
                                "encoded recursive ROOT leaf changed its stable role"
                            )
                    else:
                        root_roles = tuple(
                            cast(Any, segment).role for segment in root_segments
                        )
                        missing_delta = (
                            closure_role == _SEGMENT_OVERLAY_DELTA
                            and segment_index == 1
                            and root_roles == (_SEGMENT_OVERLAY_BASE,)
                        )
                        member_count = root_roles.count(
                            _SEGMENT_COMPOSITE_MEMBER
                        )
                        missing_bridge = (
                            closure_role == _SEGMENT_COMPOSITE_BRIDGE
                            and member_count >= 2
                            and segment_index == member_count
                            and root_roles
                            == (_SEGMENT_COMPOSITE_MEMBER,) * member_count
                        )
                        if (
                            not (missing_delta or missing_bridge)
                            or not _recursive_empty_local_table(root_lease)
                        ):
                            raise SnapshotCompatibilityError(
                                "encoded recursive ROOT coordinate lost a canonical local leaf"
                            )

                    if closure_row[0].owner is not root_lease.owner:
                        raise SnapshotCompatibilityError(
                            "encoded recursive ROOT leaf changed its source owner"
                        )
                    recursive_row: _RecursiveCompositeRow = (
                        root_lease,
                        None,
                        None,
                        (),
                        path,
                    )
                    selected: tuple[_RecursiveCompositeRow, ...] = (
                        recursive_row,
                    )
                    for root_segment, source_lease in reversed(root_references):
                        selected = _apply_recursive_leaf_scope_map(
                            selected,
                            cast(
                                memoryview,
                                cast(Any, root_segment).anonymous_scope_map,
                            ),
                        )
                        selected = _apply_recursive_leaf_postings(
                            selected,
                            source_lease,
                            root_segment,
                        )
                    if len(selected) != 1:
                        raise _RecursiveLeafPlanUnsupported(
                            "ROOT selection omitted one paired CLOSURE leaf"
                        )
                    leaf, included, excluded, scope_maps, _ = selected[0]
                    paired_rows.append(
                        (leaf, included, excluded, scope_maps)
                    )
                    paired_paths.append(path)
                    break

                if segment_index >= len(root_segments):
                    raise SnapshotCompatibilityError(
                        "encoded recursive ROOT reference coordinate is absent"
                    )
                root_segment = root_segments[segment_index]
                root_role = cast(Any, root_segment).role
                if (
                    root_role != closure_role
                    or root_role
                    not in {
                        _SEGMENT_OVERLAY_BASE,
                        _SEGMENT_COMPOSITE_MEMBER,
                    }
                    or cast(Any, root_segment).owner
                    is not cast(Any, closure_segment).owner
                    or cast(Any, root_segment).member_token
                    != cast(Any, closure_segment).member_token
                ):
                    raise SnapshotCompatibilityError(
                        "encoded recursive ROOT reference lost its stable pairing"
                    )
                next_prefix = (*prefix, segment_index)
                next_lease = prefix_cache.get(next_prefix)
                if next_lease is None:
                    next_lease = _recursive_leaf_source_lease(
                        root_manifest,
                        root_lease,
                        root_segment,
                        retained=retained,
                    )
                    prefix_cache[next_prefix] = next_lease
                    retain(next_lease)
                root_references.append((root_segment, next_lease))
                root_lease = next_lease
                closure_view = cast(Any, closure_segment).source
                prefix = next_prefix
                child_roles = tuple(
                    cast(Any, segment).role for segment in root_lease.segments
                )
                overlay_depth += int(
                    child_roles
                    in {
                        (_SEGMENT_OVERLAY_BASE,),
                        (
                            _SEGMENT_OVERLAY_BASE,
                            _SEGMENT_OVERLAY_DELTA,
                        ),
                    }
                )
                _enforce_public_limit(
                    closure_manifest.owner,
                    "max_overlay_depth",
                    overlay_depth,
                )
            else:  # pragma: no cover - nonempty paths always reach a terminal.
                raise AssertionError(
                    "recursive ROOT pairing completed without a terminal"
                )
    except _RecursiveLeafPlanUnsupported:
        return None

    if len(paired_rows) != len(closure_rows):
        return None
    return (
        tuple(paired_rows),
        tuple(paired_paths),
        tuple(retained.values()),
        _public_limit(closure_manifest.owner, "max_canonical_work"),
        _public_limit(closure_manifest.owner, "max_index_bytes"),
    )


def _enforce_private_dynamic_composite_pair_budget(
    closure_lease: EncodedStructuralLease,
    closure_rows: tuple[_CompositePlanRow, ...],
    root_lease: EncodedStructuralLease,
    root_rows: tuple[_CompositePlanRow, ...],
    *,
    closure_retained: tuple[EncodedStructuralLease, ...] = (),
    root_retained: tuple[EncodedStructuralLease, ...] = (),
) -> tuple[int | None, int | None]:
    """Charge paired CLOSURE/ROOT validation to one public operation budget."""

    if closure_lease.owner is not root_lease.owner:
        raise SnapshotCompatibilityError(
            "core encoded paired composite manifests have different owners"
        )
    retained = (
        closure_retained
        or (
            closure_lease,
            *(source for source, _included, _excluded, _map in closure_rows),
        )
    ) + (
        root_retained
        or (
            root_lease,
            *(source for source, _included, _excluded, _map in root_rows),
        )
    )
    unique: dict[int, EncodedStructuralLease] = {}
    validation_work = 0
    for source in retained:
        identity = id(source.encoded_view)
        if identity in unique:
            continue
        unique[identity] = source
        validation_work += _private_encoded_lease_validation_work(source)
        _enforce_public_limit(
            closure_lease.owner,
            "max_canonical_work",
            validation_work,
        )
    return (
        _public_limit(closure_lease.owner, "max_canonical_work"),
        _public_limit(closure_lease.owner, "max_index_bytes"),
    )


def _resolve_private_two_member_composite(
    lease: EncodedStructuralLease,
) -> (
    tuple[
        EncodedStructuralLease,
        EncodedStructuralLease,
        memoryview | None,
        memoryview | None,
        memoryview | None,
        int | None,
        int | None,
    ]
    | None
):
    """Resolve one exact two-table composite without flattening either member.

    This slice admits exactly two direct members, no bridge roots or
    anonymous-scope remapping, and either one nonempty ``INCLUDE`` table or up
    to two nonempty ``EXCLUDE`` tables. A lone selected member becomes the
    left merge table so its source-local posting cursor remains authoritative.
    Mixed or duplicate INCLUDE selectors, nested sources, bridges, and every
    other composite form stay on whole-operation fallback.
    """

    resolved_rows = _resolve_private_direct_composite_rows(lease, member_count=2)
    if resolved_rows is None:
        return None
    rows = list(resolved_rows)
    include_count = sum(included is not None for _lease, included, _excluded, _map in rows)
    exclude_count = sum(excluded is not None for _lease, _included, excluded, _map in rows)
    if len(rows) != 2:
        return None
    if include_count and exclude_count:
        return None
    if (include_count == 1 or exclude_count == 1) and (
        rows[1][1] is not None or rows[1][2] is not None
    ):
        rows.reverse()
    left, included_root_ids, excluded_root_ids, _left_scope_map = rows[0]
    right, right_included_root_ids, right_excluded_root_ids, _right_scope_map = rows[1]
    if right_included_root_ids is not None:
        return None

    validation_work = (
        _private_encoded_lease_validation_work(lease)
        + _private_encoded_lease_validation_work(left)
        + _private_encoded_lease_validation_work(right)
    )
    _enforce_public_limit(lease.owner, "max_canonical_work", validation_work)
    return (
        left,
        right,
        included_root_ids,
        excluded_root_ids,
        right_excluded_root_ids,
        _public_limit(lease.owner, "max_canonical_work"),
        _public_limit(lease.owner, "max_index_bytes"),
    )


def _is_exact_singleton_anonymous_nominal(
    buffers: Mapping[str, memoryview],
    expression_id: int,
    anonymous_node_id: int,
) -> bool:
    """Return whether one expression is exactly ``ObjectOneOf(_:scope)``."""

    if _read_uint(buffers["node_tags"], expression_id - 1, 2) != _TAG_OBJECT_ONE_OF:
        return False
    start = _read_uint(buffers["node_field_offsets"], expression_id - 1, 8)
    end = _read_uint(buffers["node_field_offsets"], expression_id, 8)
    if (
        end - start != 1
        or _read_uint(buffers["field_kinds"], start, 1) != _COMPONENT_SET
        or _read_uint(buffers["field_lengths"], start, 8) != 1
    ):
        return False
    item_start = _read_uint(buffers["field_values"], start, 8)
    return (
        _read_uint(buffers["item_kinds"], item_start, 1) == _COMPONENT_NODE
        and _read_uint(buffers["item_lengths"], item_start, 8) == 0
        and _read_uint(buffers["item_values"], item_start, 8) == anonymous_node_id
    )


def _is_exact_anonymous_has_value(
    buffers: Mapping[str, memoryview],
    expression_id: int,
    anonymous_node_id: int,
) -> bool:
    """Return whether one expression is exactly ``ObjectHasValue(:p _:scope)``."""

    if _read_uint(buffers["node_tags"], expression_id - 1, 2) != _TAG_OBJECT_HAS_VALUE:
        return False
    start = _read_uint(buffers["node_field_offsets"], expression_id - 1, 8)
    end = _read_uint(buffers["node_field_offsets"], expression_id, 8)
    if end - start != 2 or any(
        _read_uint(buffers["field_kinds"], start + offset, 1) != _COMPONENT_NODE
        for offset in (0, 1)
    ):
        return False
    property_id = _read_uint(buffers["field_values"], start, 8)
    value_id = _read_uint(buffers["field_values"], start + 1, 8)
    return (
        _read_uint(buffers["node_tags"], property_id - 1, 2) == _TAG_ENTITY
        and value_id == anonymous_node_id
    )


def _is_exact_anonymous_individual_class_expression(
    buffers: Mapping[str, memoryview],
    expression_id: int,
    anonymous_node_id: int,
) -> bool:
    """Return whether one class expression has exactly one remappable individual."""

    return _is_exact_singleton_anonymous_nominal(
        buffers,
        expression_id,
        anonymous_node_id,
    ) or _is_exact_anonymous_has_value(
        buffers,
        expression_id,
        anonymous_node_id,
    )


def _encoded_root_reaches_node(
    buffers: Mapping[str, memoryview],
    root_id: int,
    target_id: int,
) -> bool:
    """Traverse one validated structural row graph without materializing values."""

    node_count = _row_count(buffers, "node_tags")
    if not 0 < root_id <= node_count or not 0 < target_id <= node_count:
        return False
    visited = bytearray(node_count + 1)
    stack = [root_id]
    while stack:
        node_id = stack.pop()
        if node_id == target_id:
            return True
        if visited[node_id]:
            continue
        visited[node_id] = 1
        start = _read_uint(buffers["node_field_offsets"], node_id - 1, 8)
        end = _read_uint(buffers["node_field_offsets"], node_id, 8)
        for field_index in range(start, end):
            kind = _read_uint(buffers["field_kinds"], field_index, 1)
            if kind == _COMPONENT_NODE:
                stack.append(_read_uint(buffers["field_values"], field_index, 8))
                continue
            if kind not in {_COMPONENT_SET, _COMPONENT_SEQUENCE}:
                continue
            item_start = _read_uint(buffers["field_values"], field_index, 8)
            item_length = _read_uint(buffers["field_lengths"], field_index, 8)
            for item_index in range(item_start, item_start + item_length):
                if _read_uint(buffers["item_kinds"], item_index, 1) == _COMPONENT_NODE:
                    stack.append(_read_uint(buffers["item_values"], item_index, 8))
    return False


def _is_named_subclass_root(
    buffers: Mapping[str, memoryview],
    root_id: int,
) -> bool:
    """Return whether one root is a named-taxonomy rule."""

    start = _read_uint(buffers["node_field_offsets"], root_id - 1, 8)
    end = _read_uint(buffers["node_field_offsets"], root_id, 8)
    if end - start != 3 or any(
        _read_uint(buffers["field_kinds"], start + offset, 1) != _COMPONENT_NODE
        for offset in (0, 1)
    ):
        return False
    source_id = _read_uint(buffers["field_values"], start, 8)
    destination_id = _read_uint(buffers["field_values"], start + 1, 8)
    return (
        _read_uint(buffers["node_tags"], source_id - 1, 2) == _TAG_ENTITY
        and _read_uint(buffers["node_tags"], destination_id - 1, 2) == _TAG_ENTITY
        and _read_uint(buffers["field_kinds"], start + 2, 1) == _COMPONENT_SET
    )


def _is_exact_named_subclass_root(
    buffers: Mapping[str, memoryview],
    root_id: int,
) -> bool:
    """Return whether one root is the annotation-free named-taxonomy rule."""

    if not _is_named_subclass_root(buffers, root_id):
        return False
    start = _read_uint(buffers["node_field_offsets"], root_id - 1, 8)
    return _read_uint(buffers["field_lengths"], start + 2, 8) == 0


def _single_scope_mapped_construct_scope(
    lease: EncodedStructuralLease,
    *,
    construct_tag: int | None,
) -> tuple[int, memoryview, int] | None:
    """Classify and return the sole source scope in one rule-table scan."""

    if construct_tag is not None and construct_tag not in _SCOPE_MAPPED_CONSTRUCT_ROOT_KINDS:
        return None
    buffers = lease.buffers
    anonymous_node_id: int | None = None
    for index in range(_row_count(buffers, "node_tags")):
        if _read_uint(buffers["node_tags"], index, 2) != _TAG_ANONYMOUS_INDIVIDUAL:
            continue
        if anonymous_node_id is not None:
            return None
        anonymous_node_id = index + 1
    if anonymous_node_id is None:
        return None

    anonymous_start = _read_uint(
        buffers["node_field_offsets"],
        anonymous_node_id - 1,
        8,
    )
    anonymous_end = _read_uint(buffers["node_field_offsets"], anonymous_node_id, 8)
    if (
        anonymous_end - anonymous_start != 2
        or _read_uint(buffers["field_kinds"], anonymous_start, 1) != _COMPONENT_BYTES
        or _read_uint(buffers["field_lengths"], anonymous_start, 8) != 32
    ):
        return None
    scope_offset = _read_uint(buffers["field_values"], anonymous_start, 8)

    resolved_construct_tag: int | None = None
    for root_index in range(_row_count(buffers, "root_kinds")):
        root_kind = _read_uint(buffers["root_kinds"], root_index, 1)
        root_id = _read_uint(buffers["root_ids"], root_index, 4)
        root_tag = _read_uint(buffers["node_tags"], root_id - 1, 2)
        if (
            root_kind == _ROOT_AXIOM
            and root_tag == _TAG_SUB_CLASS_OF
            and _is_named_subclass_root(buffers, root_id)
        ):
            continue
        expected_root_kind = _SCOPE_MAPPED_CONSTRUCT_ROOT_KINDS.get(root_tag)
        if (
            expected_root_kind != root_kind
            or (construct_tag is not None and root_tag != construct_tag)
            or resolved_construct_tag is not None
        ):
            return None
        if root_tag == _TAG_SWRL_RULE:
            if not _encoded_root_reaches_node(
                buffers,
                root_id,
                anonymous_node_id,
            ):
                return None
            resolved_construct_tag = root_tag
            continue
        if root_tag == _TAG_ANNOTATION:
            start = _read_uint(buffers["node_field_offsets"], root_id - 1, 8)
            end = _read_uint(buffers["node_field_offsets"], root_id, 8)
            if end - start != 3 or any(
                _read_uint(buffers["field_kinds"], start + offset, 1) != _COMPONENT_NODE
                for offset in (0, 1)
            ):
                return None
            property_id = _read_uint(buffers["field_values"], start, 8)
            value_id = _read_uint(buffers["field_values"], start + 1, 8)
            if (
                _read_uint(buffers["node_tags"], property_id - 1, 2) != _TAG_ENTITY
                or value_id != anonymous_node_id
                or _read_uint(buffers["field_kinds"], start + 2, 1) != _COMPONENT_SET
            ):
                return None
            resolved_construct_tag = root_tag
            continue
        if root_tag == _TAG_SUB_CLASS_OF:
            start = _read_uint(buffers["node_field_offsets"], root_id - 1, 8)
            end = _read_uint(buffers["node_field_offsets"], root_id, 8)
            if end - start != 3 or any(
                _read_uint(buffers["field_kinds"], start + offset, 1) != _COMPONENT_NODE
                for offset in (0, 1)
            ):
                return None
            subclass_id = _read_uint(buffers["field_values"], start, 8)
            superclass_id = _read_uint(buffers["field_values"], start + 1, 8)
            subclass_tag = _read_uint(
                buffers["node_tags"],
                subclass_id - 1,
                2,
            )
            superclass_tag = _read_uint(
                buffers["node_tags"],
                superclass_id - 1,
                2,
            )
            if subclass_tag == superclass_tag == _TAG_ENTITY:
                return None
            if (
                _read_uint(buffers["field_kinds"], start + 2, 1) != _COMPONENT_SET
                or _read_uint(buffers["field_lengths"], start + 2, 8) != 0
                or not (
                    (
                        subclass_tag == _TAG_ENTITY
                        and _is_exact_anonymous_individual_class_expression(
                            buffers,
                            superclass_id,
                            anonymous_node_id,
                        )
                    )
                    or (
                        superclass_tag == _TAG_ENTITY
                        and _is_exact_anonymous_individual_class_expression(
                            buffers,
                            subclass_id,
                            anonymous_node_id,
                        )
                    )
                )
            ):
                return None
            resolved_construct_tag = root_tag
            continue
        start = _read_uint(buffers["node_field_offsets"], root_id - 1, 8)
        end = _read_uint(buffers["node_field_offsets"], root_id, 8)
        if root_tag in {_TAG_SAME_INDIVIDUAL, _TAG_DIFFERENT_INDIVIDUALS}:
            if (
                end - start != 2
                or _read_uint(buffers["field_kinds"], start, 1) != _COMPONENT_SET
                or _read_uint(buffers["field_lengths"], start, 8) != 2
                or _read_uint(buffers["field_kinds"], start + 1, 1) != _COMPONENT_SET
                or _read_uint(buffers["field_lengths"], start + 1, 8) != 0
            ):
                return None
            item_start = _read_uint(buffers["field_values"], start, 8)
            member_tags: list[int] = []
            for item_index in range(item_start, item_start + 2):
                if _read_uint(buffers["item_kinds"], item_index, 1) != _COMPONENT_NODE:
                    return None
                member_id = _read_uint(buffers["item_values"], item_index, 8)
                member_tags.append(_read_uint(buffers["node_tags"], member_id - 1, 2))
            if sorted(member_tags) != sorted([_TAG_ENTITY, _TAG_ANONYMOUS_INDIVIDUAL]):
                return None
        elif root_tag in {
            _TAG_OBJECT_PROPERTY_DOMAIN,
            _TAG_OBJECT_PROPERTY_RANGE,
        }:
            if end - start != 3 or any(
                _read_uint(buffers["field_kinds"], start + offset, 1) != _COMPONENT_NODE
                for offset in (0, 1)
            ):
                return None
            property_id = _read_uint(buffers["field_values"], start, 8)
            class_id = _read_uint(buffers["field_values"], start + 1, 8)
            if (
                _read_uint(buffers["node_tags"], property_id - 1, 2) != _TAG_ENTITY
                or not _is_exact_anonymous_individual_class_expression(
                    buffers,
                    class_id,
                    anonymous_node_id,
                )
                or _read_uint(buffers["field_kinds"], start + 2, 1) != _COMPONENT_SET
                or _read_uint(buffers["field_lengths"], start + 2, 8) != 0
            ):
                return None
        elif root_tag == _TAG_CLASS_ASSERTION:
            if end - start != 3 or any(
                _read_uint(buffers["field_kinds"], start + offset, 1) != _COMPONENT_NODE
                for offset in (0, 1)
            ):
                return None
            class_id = _read_uint(buffers["field_values"], start, 8)
            individual_id = _read_uint(buffers["field_values"], start + 1, 8)
            class_tag = _read_uint(buffers["node_tags"], class_id - 1, 2)
            individual_tag = _read_uint(
                buffers["node_tags"],
                individual_id - 1,
                2,
            )
            named_class_over_anonymous = (
                class_tag == _TAG_ENTITY and individual_id == anonymous_node_id
            )
            anonymous_class_expression_over_named = False
            if (
                class_tag in {_TAG_OBJECT_ONE_OF, _TAG_OBJECT_HAS_VALUE}
                and individual_tag == _TAG_ENTITY
            ):
                anonymous_class_expression_over_named = (
                    _is_exact_anonymous_individual_class_expression(
                        buffers,
                        class_id,
                        anonymous_node_id,
                    )
                )
            if (
                not (named_class_over_anonymous or anonymous_class_expression_over_named)
                or _read_uint(buffers["field_kinds"], start + 2, 1) != _COMPONENT_SET
                or _read_uint(buffers["field_lengths"], start + 2, 8) != 0
            ):
                return None
        elif root_tag in {
            _TAG_OBJECT_PROPERTY_ASSERTION,
            _TAG_NEGATIVE_OBJECT_PROPERTY_ASSERTION,
        }:
            if end - start != 4 or any(
                _read_uint(buffers["field_kinds"], start + offset, 1) != _COMPONENT_NODE
                for offset in (0, 1, 2)
            ):
                return None
            property_id = _read_uint(buffers["field_values"], start, 8)
            source_id = _read_uint(buffers["field_values"], start + 1, 8)
            destination_id = _read_uint(buffers["field_values"], start + 2, 8)
            other_id = destination_id if source_id == anonymous_node_id else source_id
            if (
                (source_id == anonymous_node_id) == (destination_id == anonymous_node_id)
                or _read_uint(buffers["node_tags"], property_id - 1, 2) != _TAG_ENTITY
                or _read_uint(buffers["node_tags"], other_id - 1, 2) != _TAG_ENTITY
                or _read_uint(buffers["field_kinds"], start + 3, 1) != _COMPONENT_SET
                or _read_uint(buffers["field_lengths"], start + 3, 8) != 0
            ):
                return None
        elif root_tag in {
            _TAG_DATA_PROPERTY_ASSERTION,
            _TAG_NEGATIVE_DATA_PROPERTY_ASSERTION,
        }:
            if end - start != 4 or any(
                _read_uint(buffers["field_kinds"], start + offset, 1) != _COMPONENT_NODE
                for offset in (0, 1, 2)
            ):
                return None
            property_id = _read_uint(buffers["field_values"], start, 8)
            source_id = _read_uint(buffers["field_values"], start + 1, 8)
            literal_id = _read_uint(buffers["field_values"], start + 2, 8)
            if (
                _read_uint(buffers["node_tags"], property_id - 1, 2) != _TAG_ENTITY
                or source_id != anonymous_node_id
                or _read_uint(buffers["node_tags"], literal_id - 1, 2) != _TAG_LITERAL
                or _read_uint(buffers["field_kinds"], start + 3, 1) != _COMPONENT_SET
                or _read_uint(buffers["field_lengths"], start + 3, 8) != 0
            ):
                return None
        elif root_tag == _TAG_ANNOTATION_ASSERTION:
            if end - start != 4 or any(
                _read_uint(buffers["field_kinds"], start + offset, 1) != _COMPONENT_NODE
                for offset in (0, 1, 2)
            ):
                return None
            property_id = _read_uint(buffers["field_values"], start, 8)
            subject_id = _read_uint(buffers["field_values"], start + 1, 8)
            value_id = _read_uint(buffers["field_values"], start + 2, 8)
            subject_tag = _read_uint(buffers["node_tags"], subject_id - 1, 2)
            value_tag = _read_uint(buffers["node_tags"], value_id - 1, 2)
            if (
                _read_uint(buffers["node_tags"], property_id - 1, 2) != _TAG_ENTITY
                or (subject_id == anonymous_node_id) == (value_id == anonymous_node_id)
                or (subject_id != anonymous_node_id and subject_tag != _TAG_IRI)
                or (value_id != anonymous_node_id and value_tag not in {_TAG_IRI, _TAG_LITERAL})
                or _read_uint(buffers["field_kinds"], start + 3, 1) != _COMPONENT_SET
            ):
                return None
        else:  # pragma: no cover - the rule-table membership check is exhaustive.
            return None
        resolved_construct_tag = root_tag
    if resolved_construct_tag is None:
        return None
    return resolved_construct_tag, buffers["scalar_bytes"], scope_offset


def _scope_maps_bind_constructs(
    left: EncodedStructuralLease,
    right: EncodedStructuralLease,
    left_scope_map: memoryview,
    right_scope_map: memoryview,
    *,
    construct_tag: int | None,
) -> bool:
    """Check two exact bytes32 remaps against one rule-table construct family."""

    if left_scope_map.nbytes != 64 or right_scope_map.nbytes != 64:
        return False
    left_scope = _single_scope_mapped_construct_scope(
        left,
        construct_tag=construct_tag,
    )
    right_scope = _single_scope_mapped_construct_scope(
        right,
        construct_tag=construct_tag,
    )
    if left_scope is None or right_scope is None:
        return False
    left_tag, left_scalars, left_offset = left_scope
    right_tag, right_scalars, right_offset = right_scope
    if left_tag != right_tag:
        return False
    return not (
        any(left_scope_map[index] != left_scalars[left_offset + index] for index in range(32))
        or any(right_scope_map[index] != right_scalars[right_offset + index] for index in range(32))
        or any(left_scope_map[index] != right_scope_map[index] for index in range(32))
        or all(left_scope_map[index] == left_scope_map[32 + index] for index in range(32))
        or all(right_scope_map[index] == right_scope_map[32 + index] for index in range(32))
        or all(left_scope_map[32 + index] == right_scope_map[32 + index] for index in range(32))
    )


def _named_subclass_only_table(lease: EncodedStructuralLease) -> bool:
    """Return whether a direct table contains only named taxonomy roots."""

    buffers = lease.buffers
    if not _row_count(buffers, "root_kinds"):
        return False
    if any(
        _read_uint(buffers["node_tags"], index, 2) == _TAG_ANONYMOUS_INDIVIDUAL
        for index in range(_row_count(buffers, "node_tags"))
    ):
        return False
    for root_index in range(_row_count(buffers, "root_kinds")):
        if _read_uint(buffers["root_kinds"], root_index, 1) != _ROOT_AXIOM:
            return False
        root_id = _read_uint(buffers["root_ids"], root_index, 4)
        if _read_uint(
            buffers["node_tags"], root_id - 1, 2
        ) != _TAG_SUB_CLASS_OF or not _is_exact_named_subclass_root(buffers, root_id):
            return False
    return True


def _selects_every_root(
    lease: EncodedStructuralLease,
    excluded_root_ids: memoryview,
) -> bool:
    """Return whether one EXCLUDE exporter selects the complete local table."""

    root_count = lease.buffers["root_kinds"].nbytes
    return (
        root_count > 0
        and excluded_root_ids.nbytes == 4 * root_count
        and all(
            _read_uint(excluded_root_ids, index, 4) == index + 1
            for index in range(root_count)
        )
    )


def _selects_only_named_subclass_roots(
    lease: EncodedStructuralLease,
    excluded_root_ids: memoryview,
) -> bool:
    """Return whether EXCLUDE selects only exact named taxonomy roots."""

    buffers = lease.buffers
    root_count = _row_count(buffers, "root_kinds")
    if (
        root_count < 2
        or excluded_root_ids.nbytes == 0
        or excluded_root_ids.nbytes % 4
    ):
        return False
    previous_position = 0
    for index in range(excluded_root_ids.nbytes // 4):
        position = _read_uint(excluded_root_ids, index, 4)
        if position <= previous_position or position > root_count:
            return False
        previous_position = position
        if _read_uint(buffers["root_kinds"], position - 1, 1) != _ROOT_AXIOM:
            return False
        root_id = _read_uint(buffers["root_ids"], position - 1, 4)
        if (
            _read_uint(buffers["node_tags"], root_id - 1, 2) != _TAG_SUB_CLASS_OF
            or not _is_exact_named_subclass_root(buffers, root_id)
        ):
            return False
    return True


def _resolve_private_scope_mapped_composite(
    lease: EncodedStructuralLease,
    *,
    construct_tag: int | None,
) -> (
    tuple[
        EncodedStructuralLease,
        EncodedStructuralLease,
        memoryview,
        memoryview,
        int | None,
        int | None,
    ]
    | None
):
    """Resolve two ALL members whose sole anonymous construct scopes diverge."""

    rows = _resolve_private_direct_composite_rows(
        lease,
        member_count=2,
        allow_scope_maps=True,
    )
    if rows is None:
        return None
    left, left_include, left_exclude, left_scope_map = rows[0]
    right, right_include, right_exclude, right_scope_map = rows[1]
    if (
        left_include is not None
        or left_exclude is not None
        or right_include is not None
        or right_exclude is not None
        or left_scope_map is None
        or right_scope_map is None
        or not _scope_maps_bind_constructs(
            left,
            right,
            left_scope_map,
            right_scope_map,
            construct_tag=construct_tag,
        )
    ):
        return None

    validation_work = (
        _private_encoded_lease_validation_work(lease)
        + _private_encoded_lease_validation_work(left)
        + _private_encoded_lease_validation_work(right)
    )
    _enforce_public_limit(lease.owner, "max_canonical_work", validation_work)
    return (
        left,
        right,
        left_scope_map,
        right_scope_map,
        _public_limit(lease.owner, "max_canonical_work"),
        _public_limit(lease.owner, "max_index_bytes"),
    )


def _resolve_private_scope_mapped_rule_composite(
    lease: EncodedStructuralLease,
) -> (
    tuple[
        EncodedStructuralLease,
        EncodedStructuralLease,
        memoryview,
        memoryview,
        int | None,
        int | None,
    ]
    | None
):
    """Resolve one supported scope-remap family through a single rule-table pass."""

    return _resolve_private_scope_mapped_composite(
        lease,
        construct_tag=None,
    )


def _resolve_private_scope_mapped_subclass_composite(
    lease: EncodedStructuralLease,
) -> (
    tuple[
        EncodedStructuralLease,
        EncodedStructuralLease,
        memoryview,
        memoryview,
        int | None,
        int | None,
    ]
    | None
):
    """Resolve one exact ignored-SubClassOf scope-remap composite."""

    return _resolve_private_scope_mapped_composite(
        lease,
        construct_tag=_TAG_SUB_CLASS_OF,
    )


def _resolve_private_scope_mapped_object_property_class_composite(
    lease: EncodedStructuralLease,
) -> (
    tuple[
        EncodedStructuralLease,
        EncodedStructuralLease,
        memoryview,
        memoryview,
        int | None,
        int | None,
    ]
    | None
):
    """Resolve one exact ignored object-property domain/range scope remap."""

    for construct_tag in (
        _TAG_OBJECT_PROPERTY_DOMAIN,
        _TAG_OBJECT_PROPERTY_RANGE,
    ):
        resolved = _resolve_private_scope_mapped_composite(
            lease,
            construct_tag=construct_tag,
        )
        if resolved is not None:
            return resolved
    return None


def _resolve_private_scope_mapped_class_assertion_composite(
    lease: EncodedStructuralLease,
) -> (
    tuple[
        EncodedStructuralLease,
        EncodedStructuralLease,
        memoryview,
        memoryview,
        int | None,
        int | None,
    ]
    | None
):
    """Resolve one exact ignored-ClassAssertion scope-remap composite."""

    return _resolve_private_scope_mapped_composite(
        lease,
        construct_tag=_TAG_CLASS_ASSERTION,
    )


def _resolve_private_scope_mapped_object_property_assertion_composite(
    lease: EncodedStructuralLease,
) -> (
    tuple[
        EncodedStructuralLease,
        EncodedStructuralLease,
        memoryview,
        memoryview,
        int | None,
        int | None,
    ]
    | None
):
    """Resolve one exact emitting ObjectPropertyAssertion scope-remap composite."""

    return _resolve_private_scope_mapped_composite(
        lease,
        construct_tag=_TAG_OBJECT_PROPERTY_ASSERTION,
    )


def _resolve_private_scope_mapped_negative_object_property_assertion_composite(
    lease: EncodedStructuralLease,
) -> (
    tuple[
        EncodedStructuralLease,
        EncodedStructuralLease,
        memoryview,
        memoryview,
        int | None,
        int | None,
    ]
    | None
):
    """Resolve one exact skipped NegativeObjectPropertyAssertion scope remap."""

    return _resolve_private_scope_mapped_composite(
        lease,
        construct_tag=_TAG_NEGATIVE_OBJECT_PROPERTY_ASSERTION,
    )


def _resolve_private_scope_mapped_data_property_assertion_composite(
    lease: EncodedStructuralLease,
) -> (
    tuple[
        EncodedStructuralLease,
        EncodedStructuralLease,
        memoryview,
        memoryview,
        int | None,
        int | None,
    ]
    | None
):
    """Resolve one exact skipped DataPropertyAssertion scope remap."""

    return _resolve_private_scope_mapped_composite(
        lease,
        construct_tag=_TAG_DATA_PROPERTY_ASSERTION,
    )


def _resolve_private_scope_mapped_negative_data_property_assertion_composite(
    lease: EncodedStructuralLease,
) -> (
    tuple[
        EncodedStructuralLease,
        EncodedStructuralLease,
        memoryview,
        memoryview,
        int | None,
        int | None,
    ]
    | None
):
    """Resolve one exact skipped NegativeDataPropertyAssertion scope remap."""

    return _resolve_private_scope_mapped_composite(
        lease,
        construct_tag=_TAG_NEGATIVE_DATA_PROPERTY_ASSERTION,
    )


def _resolve_private_scope_mapped_same_individual_composite(
    lease: EncodedStructuralLease,
) -> (
    tuple[
        EncodedStructuralLease,
        EncodedStructuralLease,
        memoryview,
        memoryview,
        int | None,
        int | None,
    ]
    | None
):
    """Resolve one exact skipped SameIndividual scope remap."""

    return _resolve_private_scope_mapped_composite(
        lease,
        construct_tag=_TAG_SAME_INDIVIDUAL,
    )


def _resolve_private_scope_mapped_different_individuals_composite(
    lease: EncodedStructuralLease,
) -> (
    tuple[
        EncodedStructuralLease,
        EncodedStructuralLease,
        memoryview,
        memoryview,
        int | None,
        int | None,
    ]
    | None
):
    """Resolve one exact skipped DifferentIndividuals scope remap."""

    return _resolve_private_scope_mapped_composite(
        lease,
        construct_tag=_TAG_DIFFERENT_INDIVIDUALS,
    )


def _resolve_private_scope_mapped_annotation_assertion_composite(
    lease: EncodedStructuralLease,
) -> (
    tuple[
        EncodedStructuralLease,
        EncodedStructuralLease,
        memoryview,
        memoryview,
        int | None,
        int | None,
    ]
    | None
):
    """Resolve one exact silent AnnotationAssertion scope remap."""

    return _resolve_private_scope_mapped_composite(
        lease,
        construct_tag=_TAG_ANNOTATION_ASSERTION,
    )


def _resolve_private_multi_member_composite(
    lease: EncodedStructuralLease,
    *,
    member_count: int,
) -> tuple[tuple[_CompositeRow, ...], int | None, int | None] | None:
    """Resolve one bounded direct-member composite without flattening.

    Three- and four-member composites admit either source-local ``ALL``/
    ``EXCLUDE`` selection or exactly one ``INCLUDE`` selector with every other
    member ``ALL``.  The included member is bound first because the fixed
    private ABI carries its one inclusion cursor on the first merge table.
    """

    if type(member_count) is not int or member_count not in {3, 4}:
        return None
    resolved_rows = _resolve_private_direct_composite_rows(lease, member_count=member_count)
    if resolved_rows is None:
        return None
    rows = list(resolved_rows)
    included_indexes = [
        index
        for index, (_source, included, _excluded, _map) in enumerate(rows)
        if included is not None
    ]
    exclude_count = sum(excluded is not None for _source, _included, excluded, _map in rows)
    if len(included_indexes) > 1 or (included_indexes and exclude_count):
        return None
    if included_indexes and included_indexes[0] != 0:
        rows.insert(0, rows.pop(included_indexes[0]))
    validation_work = _private_encoded_lease_validation_work(lease) + sum(
        _private_encoded_lease_validation_work(source)
        for source, _included, _excluded, _map in rows
    )
    _enforce_public_limit(lease.owner, "max_canonical_work", validation_work)
    return (
        tuple(rows),
        _public_limit(lease.owner, "max_canonical_work"),
        _public_limit(lease.owner, "max_index_bytes"),
    )


def _resolve_private_three_member_composite(
    lease: EncodedStructuralLease,
) -> (
    tuple[
        EncodedStructuralLease,
        EncodedStructuralLease,
        EncodedStructuralLease,
        memoryview | None,
        memoryview | None,
        memoryview | None,
        memoryview | None,
        int | None,
        int | None,
    ]
    | None
):
    """Resolve three direct members with bounded source-local selection."""

    resolved = _resolve_private_multi_member_composite(lease, member_count=3)
    if resolved is None:
        return None
    rows, max_work, max_workspace = resolved
    if len(rows) != 3:
        return None
    first, first_include, first_excluded, _first_map = rows[0]
    second, _second_include, second_excluded, _second_map = rows[1]
    third, _third_include, third_excluded, _third_map = rows[2]
    return (
        first,
        second,
        third,
        first_include,
        first_excluded,
        second_excluded,
        third_excluded,
        max_work,
        max_workspace,
    )


def _resolve_private_four_member_composite(
    lease: EncodedStructuralLease,
) -> (
    tuple[
        EncodedStructuralLease,
        EncodedStructuralLease,
        EncodedStructuralLease,
        EncodedStructuralLease,
        memoryview | None,
        memoryview | None,
        memoryview | None,
        memoryview | None,
        memoryview | None,
        int | None,
        int | None,
    ]
    | None
):
    """Resolve four direct members with bounded source-local selection."""

    resolved = _resolve_private_multi_member_composite(lease, member_count=4)
    if resolved is None:
        return None
    rows, max_work, max_workspace = resolved
    if len(rows) != 4:
        return None
    first, first_include, first_excluded, _first_map = rows[0]
    second, _second_include, second_excluded, _second_map = rows[1]
    third, _third_include, third_excluded, _third_map = rows[2]
    fourth, _fourth_include, fourth_excluded, _fourth_map = rows[3]
    return (
        first,
        second,
        third,
        fourth,
        first_include,
        first_excluded,
        second_excluded,
        third_excluded,
        fourth_excluded,
        max_work,
        max_workspace,
    )


def _resolve_private_scope_mapped_nested_overlay_composite(
    lease: EncodedStructuralLease,
) -> (
    tuple[
        EncodedStructuralLease,
        EncodedStructuralLease,
        EncodedStructuralLease,
        memoryview | None,
        memoryview | None,
        memoryview | None,
        memoryview,
        memoryview,
        int | None,
        int | None,
    ]
    | None
):
    """Resolve one remapped overlay member and one remapped direct sibling."""

    rows = _resolve_private_direct_composite_rows(
        lease,
        member_count=2,
        require_direct_sources=False,
        allow_scope_maps=True,
    )
    if rows is None or any(
        included is not None or scope_map is None
        for _source, included, _excluded, scope_map in rows
    ):
        return None
    overlay_row: tuple[EncodedStructuralLease, memoryview | None, memoryview] | None = None
    direct_row: tuple[
        EncodedStructuralLease,
        memoryview | None,
        memoryview,
    ] | None = None
    for source, _included, excluded, scope_map in rows:
        assert scope_map is not None
        roles = tuple(cast(Any, segment).role for segment in source.segments)
        if roles == (_SEGMENT_OVERLAY_BASE, _SEGMENT_OVERLAY_DELTA):
            if overlay_row is not None:
                return None
            overlay_row = (source, excluded, scope_map)
        elif roles == (_SEGMENT_DIRECT,):
            if direct_row is not None:
                return None
            direct_row = (source, excluded, scope_map)
        else:
            return None
    if overlay_row is None or direct_row is None:
        return None
    nested_overlay, nested_excluded_root_ids, nested_scope_map = overlay_row
    direct_member, direct_excluded_root_ids, direct_scope_map = direct_row
    if nested_excluded_root_ids is not None and not _selects_every_root(
        nested_overlay,
        nested_excluded_root_ids,
    ):
        return None
    resolved_overlay = _resolve_private_single_overlay_delta(nested_overlay)
    if resolved_overlay is None:
        return None
    base, excluded_root_ids, _overlay_work, _overlay_workspace = resolved_overlay
    if (
        (
            excluded_root_ids is not None
            and not _selects_only_named_subclass_roots(base, excluded_root_ids)
        )
        or (
            direct_excluded_root_ids is not None
            and not _selects_only_named_subclass_roots(
                direct_member,
                direct_excluded_root_ids,
            )
        )
        or base.encoded_view is direct_member.encoded_view
        or base.owner is direct_member.owner
        or not _named_subclass_only_table(nested_overlay)
        or not _scope_maps_bind_constructs(
            base,
            direct_member,
            nested_scope_map,
            direct_scope_map,
            construct_tag=None,
        )
    ):
        return None

    _enforce_public_limit(lease.owner, "max_overlay_depth", 1)
    validation_work = (
        _private_encoded_lease_validation_work(lease)
        + _private_encoded_lease_validation_work(nested_overlay)
        + _private_encoded_lease_validation_work(base)
        + _private_encoded_lease_validation_work(direct_member)
    )
    _enforce_public_limit(lease.owner, "max_canonical_work", validation_work)
    return (
        base,
        nested_overlay,
        direct_member,
        excluded_root_ids,
        nested_excluded_root_ids,
        direct_excluded_root_ids,
        nested_scope_map,
        direct_scope_map,
        _public_limit(lease.owner, "max_canonical_work"),
        _public_limit(lease.owner, "max_index_bytes"),
    )


def _resolve_private_scope_mapped_four_table_nested_composite(
    lease: EncodedStructuralLease,
) -> (
    tuple[
        EncodedStructuralLease,
        EncodedStructuralLease,
        EncodedStructuralLease,
        EncodedStructuralLease,
        memoryview | None,
        memoryview | None,
        memoryview | None,
        memoryview | None,
        memoryview,
        memoryview,
        int | None,
        int | None,
    ]
    | None
):
    """Resolve two remapped members separated by two named neutral tables."""

    rows = _resolve_private_direct_composite_rows(
        lease,
        member_count=3,
        require_direct_sources=False,
        allow_scope_maps=True,
    )
    if rows is None or any(included is not None for _source, included, _excluded, _map in rows):
        return None
    overlay_row: tuple[EncodedStructuralLease, memoryview | None, memoryview] | None = None
    mapped_row: tuple[
        EncodedStructuralLease,
        memoryview | None,
        memoryview,
    ] | None = None
    neutral_row: tuple[EncodedStructuralLease, memoryview | None] | None = None
    for source, _included, excluded, scope_map in rows:
        roles = tuple(cast(Any, segment).role for segment in source.segments)
        if roles == (_SEGMENT_OVERLAY_BASE, _SEGMENT_OVERLAY_DELTA):
            if overlay_row is not None or scope_map is None:
                return None
            overlay_row = (source, excluded, scope_map)
        elif roles == (_SEGMENT_DIRECT,) and scope_map is not None:
            if mapped_row is not None:
                return None
            mapped_row = (source, excluded, scope_map)
        elif roles == (_SEGMENT_DIRECT,):
            if neutral_row is not None:
                return None
            neutral_row = (source, excluded)
        else:
            return None
    if overlay_row is None or mapped_row is None or neutral_row is None:
        return None

    nested_overlay, nested_excluded_root_ids, nested_scope_map = overlay_row
    mapped_direct, mapped_excluded_root_ids, direct_scope_map = mapped_row
    neutral_direct, neutral_excluded_root_ids = neutral_row
    for source, selector in (
        (nested_overlay, nested_excluded_root_ids),
        (neutral_direct, neutral_excluded_root_ids),
    ):
        if selector is not None and not _selects_every_root(source, selector):
            return None

    resolved_overlay = _resolve_private_single_overlay_delta(nested_overlay)
    if resolved_overlay is None:
        return None
    base, excluded_root_ids, _overlay_work, _overlay_workspace = resolved_overlay
    direct_members = (mapped_direct, neutral_direct)
    if (
        (
            excluded_root_ids is not None
            and not _selects_only_named_subclass_roots(base, excluded_root_ids)
        )
        or (
            mapped_excluded_root_ids is not None
            and not _selects_only_named_subclass_roots(
                mapped_direct,
                mapped_excluded_root_ids,
            )
        )
        or any(
            base.encoded_view is direct.encoded_view or base.owner is direct.owner
            for direct in direct_members
        )
        or not _named_subclass_only_table(nested_overlay)
        or not _named_subclass_only_table(neutral_direct)
        or not _scope_maps_bind_constructs(
            base,
            mapped_direct,
            nested_scope_map,
            direct_scope_map,
            construct_tag=None,
        )
    ):
        return None

    _enforce_public_limit(lease.owner, "max_overlay_depth", 1)
    validation_work = (
        _private_encoded_lease_validation_work(lease)
        + _private_encoded_lease_validation_work(nested_overlay)
        + _private_encoded_lease_validation_work(base)
        + _private_encoded_lease_validation_work(mapped_direct)
        + _private_encoded_lease_validation_work(neutral_direct)
    )
    _enforce_public_limit(lease.owner, "max_canonical_work", validation_work)
    return (
        base,
        nested_overlay,
        mapped_direct,
        neutral_direct,
        excluded_root_ids,
        nested_excluded_root_ids,
        mapped_excluded_root_ids,
        neutral_excluded_root_ids,
        nested_scope_map,
        direct_scope_map,
        _public_limit(lease.owner, "max_canonical_work"),
        _public_limit(lease.owner, "max_index_bytes"),
    )


def _resolve_private_nested_overlay_composite(
    lease: EncodedStructuralLease,
) -> (
    tuple[
        EncodedStructuralLease,
        EncodedStructuralLease,
        EncodedStructuralLease,
        memoryview | None,
        memoryview | None,
        memoryview | None,
        int | None,
        int | None,
    ]
    | None
):
    """Resolve one exact overlay member and one direct member into three tables.

    The nested overlay may select its direct base with ``EXCLUDE`` and the
    outer composite may independently select the nested member's local delta
    and the direct sibling with one source-local ``EXCLUDE`` table each.
    ``INCLUDE`` and anonymous-scope remapping stay outside this bounded family.
    """

    rows = _resolve_private_direct_composite_rows(
        lease,
        member_count=2,
        require_direct_sources=False,
    )
    if rows is None:
        return None
    overlay_rows: list[_CompositeRow] = []
    direct_rows: list[_CompositeRow] = []
    for row in rows:
        source, included, _excluded, scope_map = row
        if included is not None or scope_map is not None:
            return None
        roles = tuple(cast(Any, segment).role for segment in source.segments)
        if roles == (_SEGMENT_OVERLAY_BASE, _SEGMENT_OVERLAY_DELTA):
            overlay_rows.append(row)
        elif roles == (_SEGMENT_DIRECT,):
            direct_rows.append(row)
        else:
            return None
    if len(overlay_rows) != 1 or len(direct_rows) != 1:
        return None
    (
        nested_overlay,
        _nested_included,
        nested_excluded_root_ids,
        _nested_scope_map,
    ) = overlay_rows[0]
    direct_member, _direct_included, direct_excluded_root_ids, _direct_scope_map = direct_rows[0]
    resolved_overlay = _resolve_private_single_overlay_delta(nested_overlay)
    if resolved_overlay is None:
        return None
    base, excluded_root_ids, _overlay_work, _overlay_workspace = resolved_overlay
    if base.encoded_view is direct_member.encoded_view or base.owner is direct_member.owner:
        return None

    _enforce_public_limit(lease.owner, "max_overlay_depth", 1)
    validation_work = (
        _private_encoded_lease_validation_work(lease)
        + _private_encoded_lease_validation_work(nested_overlay)
        + _private_encoded_lease_validation_work(base)
        + _private_encoded_lease_validation_work(direct_member)
    )
    _enforce_public_limit(lease.owner, "max_canonical_work", validation_work)
    return (
        base,
        nested_overlay,
        direct_member,
        excluded_root_ids,
        nested_excluded_root_ids,
        direct_excluded_root_ids,
        _public_limit(lease.owner, "max_canonical_work"),
        _public_limit(lease.owner, "max_index_bytes"),
    )


def _resolve_private_four_table_nested_composite(
    lease: EncodedStructuralLease,
) -> (
    tuple[
        EncodedStructuralLease,
        EncodedStructuralLease,
        EncodedStructuralLease,
        EncodedStructuralLease,
        memoryview | None,
        memoryview | None,
        memoryview | None,
        memoryview | None,
        int | None,
        int | None,
    ]
    | None
):
    """Resolve one exact overlay and two selected direct siblings into four tables.

    The nested overlay may select its direct base with ``EXCLUDE`` and the
    outer composite may independently select the nested member's local delta
    and either or both direct siblings with source-local ``EXCLUDE`` tables.
    ``INCLUDE`` and anonymous-scope remapping stay outside this bounded family.
    """

    rows = _resolve_private_direct_composite_rows(
        lease,
        member_count=3,
        require_direct_sources=False,
    )
    if rows is None:
        return None
    overlay_rows: list[_CompositeRow] = []
    direct_rows: list[tuple[EncodedStructuralLease, memoryview | None]] = []
    for source, included, excluded, scope_map in rows:
        if included is not None or scope_map is not None:
            return None
        roles = tuple(cast(Any, segment).role for segment in source.segments)
        if roles == (_SEGMENT_OVERLAY_BASE, _SEGMENT_OVERLAY_DELTA):
            overlay_rows.append((source, included, excluded, scope_map))
        elif roles == (_SEGMENT_DIRECT,):
            direct_rows.append((source, excluded))
        else:
            return None
    if len(overlay_rows) != 1 or len(direct_rows) != 2:
        return None
    (
        nested_overlay,
        _nested_included,
        nested_excluded_root_ids,
        _nested_scope_map,
    ) = overlay_rows[0]
    (
        (first_direct, first_excluded_root_ids),
        (
            second_direct,
            second_excluded_root_ids,
        ),
    ) = direct_rows
    resolved_overlay = _resolve_private_single_overlay_delta(nested_overlay)
    if resolved_overlay is None:
        return None
    base, excluded_root_ids, _overlay_work, _overlay_workspace = resolved_overlay
    if any(
        base.encoded_view is direct.encoded_view or base.owner is direct.owner
        for direct, _excluded in direct_rows
    ):
        return None

    _enforce_public_limit(lease.owner, "max_overlay_depth", 1)
    validation_work = (
        _private_encoded_lease_validation_work(lease)
        + _private_encoded_lease_validation_work(nested_overlay)
        + _private_encoded_lease_validation_work(base)
        + _private_encoded_lease_validation_work(first_direct)
        + _private_encoded_lease_validation_work(second_direct)
    )
    _enforce_public_limit(lease.owner, "max_canonical_work", validation_work)
    return (
        base,
        nested_overlay,
        first_direct,
        second_direct,
        excluded_root_ids,
        nested_excluded_root_ids,
        first_excluded_root_ids,
        second_excluded_root_ids,
        _public_limit(lease.owner, "max_canonical_work"),
        _public_limit(lease.owner, "max_index_bytes"),
    )


def _private_overlay_alias_source(
    lease: EncodedStructuralLease,
) -> tuple[object, object, object, memoryview | None] | None:
    if type(lease) is not EncodedStructuralLease or len(lease.segments) != 1:
        return None
    segment = cast(Any, lease.segments[0])
    try:
        role = segment.role
    except Exception as error:
        raise SnapshotCompatibilityError(
            "core encoded overlay alias role is not readable"
        ) from error
    if type(role) is not int or role != _SEGMENT_OVERLAY_BASE:
        return None
    try:
        owner = segment.owner
        source = segment.source
        posting_mode = segment.posting_mode
        root_ids = segment.root_ids
        anonymous_scope_map = segment.anonymous_scope_map
        member_token = segment.member_token
    except Exception as error:
        raise SnapshotCompatibilityError(
            "core encoded overlay alias metadata is not readable"
        ) from error
    if (
        source is None
        or owner is not getattr(source, "owner", _MISSING)
        or type(posting_mode) is not int
        or type(root_ids) is not memoryview
        or type(anonymous_scope_map) is not memoryview
        or anonymous_scope_map.nbytes
        or member_token is not None
    ):
        return None
    if posting_mode == _POSTINGS_ALL:
        if root_ids.nbytes:
            return None
        excluded_root_ids = None
    elif posting_mode == _POSTINGS_EXCLUDE:
        if not root_ids.nbytes:
            return None
        excluded_root_ids = root_ids
    else:
        return None
    offsets = lease.buffers["node_field_offsets"]
    if offsets.nbytes != 8 or any(offsets):
        return None
    if any(value.nbytes for name, value in lease.buffers.items() if name != "node_field_offsets"):
        return None
    source_scope = getattr(source, "scope", _MISSING)
    if source_scope is not lease.scope:
        return None
    return owner, source, source_scope, excluded_root_ids


def _private_encoded_lease_validation_work(lease: EncodedStructuralLease) -> int:
    buffer_bytes = sum(value.nbytes for value in lease.buffers.values())
    segment_bytes = 0
    for raw_segment in lease.segments:
        segment = cast(Any, raw_segment)
        segment_bytes += 128 + segment.root_ids.nbytes + segment.anonymous_scope_map.nbytes
    return buffer_bytes + segment_bytes


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
