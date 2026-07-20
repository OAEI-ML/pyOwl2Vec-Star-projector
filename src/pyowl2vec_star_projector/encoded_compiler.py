"""Bounded encoded-column to projector-edge compiler slice.

The slice is intentionally narrow: one canonical direct segment; one
overlay-base segment referencing a canonical direct source and optionally
followed by one top-local delta segment; or recursively resolved composite
members with an optional top-local bridge.  Those roots may contain
declarations, simple named-class ``SubClassOf`` and ``EquivalentClasses``
axioms, named ``ClassAssertion`` axioms, direct ``ObjectPropertyAssertion``
axioms over named or anonymous individuals, and named object-property
domain/range and role axioms, plus named-property/named-filler ``SubClassOf``
restrictions, and named/aggregate ``EquivalentClasses`` pairs over the same
operands, with empty annotation sets on those declaration/logical axioms.
Selected class ``AnnotationAssertion`` edges are compiled when a single-document
closure proves the pinned root-only lookup semantics.  It preflights the
complete encoded view before yielding any edge.  A well-formed view outside
that subset selects the scalar compiler for the whole operation; malformed rows
fail closed.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol, cast

from pyowl_core.model import RDF_PLAIN_LITERAL_IRI, XSD_STRING_IRI

from .compiler import (
    _ANNOTATION_PROPERTIES,
    RDF_TYPE,
    RDFS_NAMESPACE,
    SUBCLASS_OF,
    SUPERCLASS_OF,
    CompileStatistics,
    RoleState,
    _combine,
    _int32,
    _owlapi_escape_literal,
    _owlapi_iri_hash,
    _render_datatype,
)
from .diagnostics import ProjectionDiagnostic
from .encoded import (
    ENCODED_BUFFER_WIDTHS,
    EncodedNegotiation,
    EncodedStructuralLease,
    _validate_encoded_view,
)
from .errors import SnapshotCompatibilityError
from .model import Edge
from .options import ProjectionOptions

_TAG_IRI = 1
_TAG_ENTITY = 2
_TAG_ANONYMOUS_INDIVIDUAL = 3
_TAG_LITERAL = 4
_TAG_ANNOTATION = 5
_TAG_OBJECT_INTERSECTION_OF = 30
_TAG_OBJECT_UNION_OF = 31
_TAG_OBJECT_SOME_VALUES_FROM = 34
_TAG_OBJECT_ALL_VALUES_FROM = 35
_TAG_OBJECT_MIN_CARDINALITY = 38
_TAG_OBJECT_MAX_CARDINALITY = 39
_TAG_DECLARATION = 60
_TAG_SUB_CLASS_OF = 61
_TAG_EQUIVALENT_CLASSES = 62
_TAG_SUB_OBJECT_PROPERTY_OF = 70
_TAG_INVERSE_OBJECT_PROPERTIES = 73
_TAG_OBJECT_PROPERTY_DOMAIN = 74
_TAG_OBJECT_PROPERTY_RANGE = 75
_TAG_CLASS_ASSERTION = 112
_TAG_OBJECT_PROPERTY_ASSERTION = 113
_TAG_ANNOTATION_ASSERTION = 120

_SEGMENT_DIRECT = 1
_SEGMENT_OVERLAY_BASE = 2
_SEGMENT_OVERLAY_DELTA = 3
_SEGMENT_COMPOSITE_MEMBER = 4
_SEGMENT_COMPOSITE_BRIDGE = 5
_POSTINGS_ALL = 0
_POSTINGS_INCLUDE = 1
_POSTINGS_EXCLUDE = 2

_ROOT_AXIOM = 2
_COMPONENT_NONE = 0
_COMPONENT_NODE = 1
_COMPONENT_TEXT = 2
_COMPONENT_BYTES = 3
_COMPONENT_INTEGER = 4
_COMPONENT_ENUM = 5
_COMPONENT_SET = 6
_COMPONENT_SEQUENCE = 7
_DEFAULT_MAX_IRI_BYTES = 1024 * 1024
_DEFAULT_MAX_LITERAL_BYTES = 64 * 1024**2

_RESTRICTION_TAGS = frozenset(
    {
        _TAG_OBJECT_SOME_VALUES_FROM,
        _TAG_OBJECT_ALL_VALUES_FROM,
        _TAG_OBJECT_MIN_CARDINALITY,
        _TAG_OBJECT_MAX_CARDINALITY,
    }
)
_AGGREGATE_TAGS = frozenset({_TAG_OBJECT_INTERSECTION_OF, _TAG_OBJECT_UNION_OF})
_EXPRESSION_ORDER = {
    _TAG_OBJECT_SOME_VALUES_FROM: 3005,
    _TAG_OBJECT_ALL_VALUES_FROM: 3006,
    _TAG_OBJECT_MIN_CARDINALITY: 3008,
    _TAG_OBJECT_MAX_CARDINALITY: 3010,
}

_SCHEMA_TAGS = frozenset(
    {
        1,
        2,
        3,
        4,
        5,
        10,
        11,
        20,
        21,
        22,
        23,
        24,
        25,
        30,
        31,
        32,
        33,
        34,
        35,
        36,
        37,
        38,
        39,
        40,
        41,
        42,
        43,
        44,
        45,
        46,
        60,
        61,
        62,
        63,
        64,
        70,
        71,
        72,
        73,
        74,
        75,
        76,
        77,
        78,
        79,
        80,
        81,
        82,
        90,
        91,
        92,
        93,
        94,
        95,
        100,
        101,
        110,
        111,
        112,
        113,
        114,
        115,
        116,
        120,
        121,
        122,
        123,
        140,
        141,
        142,
        143,
        144,
        145,
        146,
        147,
        148,
    }
)
_ENTITY_KINDS = frozenset(
    {
        b"class",
        b"datatype",
        b"object_property",
        b"data_property",
        b"annotation_property",
        b"named_individual",
    }
)
_MAX_ENTITY_KIND_BYTES = max(map(len, _ENTITY_KINDS))


class _SegmentLike(Protocol):
    role: object
    source: object | None
    owner: object
    posting_mode: object
    member_token: object


@dataclass(frozen=True, slots=True)
class EncodedSubsetCounters:
    """Test-visible bounded-work counters for the incomplete compiler slice."""

    roots_inspected: int = 0
    nodes_inspected: int = 0
    declaration_axioms: int = 0
    subclass_axioms: int = 0
    restriction_subclass_axioms: int = 0
    equivalent_axioms: int = 0
    aggregate_equivalent_axioms: int = 0
    class_assertion_axioms: int = 0
    sub_object_property_axioms: int = 0
    inverse_object_property_axioms: int = 0
    object_property_assertion_axioms: int = 0
    object_property_domain_axioms: int = 0
    object_property_range_axioms: int = 0
    annotation_assertion_axioms: int = 0
    anonymous_individuals: int = 0
    literal_nodes: int = 0
    annotation_nodes: int = 0
    scalar_bytes_checked: int = 0
    referenced_segments: int = 0
    posting_rows_inspected: int = 0
    scope_map_rows_inspected: int = 0
    source_roots_inspected: int = 0
    delta_roots_inspected: int = 0
    composite_member_segments: int = 0
    bridge_roots_inspected: int = 0
    selected_roots: int = 0
    deduplicated_roots: int = 0
    canonical_bytes_compared: int = 0
    edge_batches: int = 0
    raw_edges: int = 0
    scalar_fallbacks: int = 0

    def __post_init__(self) -> None:
        for value in (
            self.roots_inspected,
            self.nodes_inspected,
            self.declaration_axioms,
            self.subclass_axioms,
            self.restriction_subclass_axioms,
            self.equivalent_axioms,
            self.aggregate_equivalent_axioms,
            self.class_assertion_axioms,
            self.sub_object_property_axioms,
            self.inverse_object_property_axioms,
            self.object_property_assertion_axioms,
            self.object_property_domain_axioms,
            self.object_property_range_axioms,
            self.annotation_assertion_axioms,
            self.anonymous_individuals,
            self.literal_nodes,
            self.annotation_nodes,
            self.scalar_bytes_checked,
            self.referenced_segments,
            self.posting_rows_inspected,
            self.scope_map_rows_inspected,
            self.source_roots_inspected,
            self.delta_roots_inspected,
            self.composite_member_segments,
            self.bridge_roots_inspected,
            self.selected_roots,
            self.deduplicated_roots,
            self.canonical_bytes_compared,
            self.edge_batches,
            self.raw_edges,
            self.scalar_fallbacks,
        ):
            if type(value) is not int or value < 0:
                raise ValueError("encoded subset counters must be non-negative ints")


@dataclass(slots=True)
class _MutableCounters:
    roots_inspected: int = 0
    nodes_inspected: int = 0
    declaration_axioms: int = 0
    subclass_axioms: int = 0
    restriction_subclass_axioms: int = 0
    equivalent_axioms: int = 0
    aggregate_equivalent_axioms: int = 0
    class_assertion_axioms: int = 0
    sub_object_property_axioms: int = 0
    inverse_object_property_axioms: int = 0
    object_property_assertion_axioms: int = 0
    object_property_domain_axioms: int = 0
    object_property_range_axioms: int = 0
    annotation_assertion_axioms: int = 0
    anonymous_individuals: int = 0
    literal_nodes: int = 0
    annotation_nodes: int = 0
    scalar_bytes_checked: int = 0
    referenced_segments: int = 0
    posting_rows_inspected: int = 0
    scope_map_rows_inspected: int = 0
    source_roots_inspected: int = 0
    delta_roots_inspected: int = 0
    composite_member_segments: int = 0
    bridge_roots_inspected: int = 0
    selected_roots: int = 0
    deduplicated_roots: int = 0
    canonical_bytes_compared: int = 0
    edge_batches: int = 0
    raw_edges: int = 0
    scalar_fallbacks: int = 0

    def freeze(self) -> EncodedSubsetCounters:
        return EncodedSubsetCounters(
            roots_inspected=self.roots_inspected,
            nodes_inspected=self.nodes_inspected,
            declaration_axioms=self.declaration_axioms,
            subclass_axioms=self.subclass_axioms,
            restriction_subclass_axioms=self.restriction_subclass_axioms,
            equivalent_axioms=self.equivalent_axioms,
            aggregate_equivalent_axioms=self.aggregate_equivalent_axioms,
            class_assertion_axioms=self.class_assertion_axioms,
            sub_object_property_axioms=self.sub_object_property_axioms,
            inverse_object_property_axioms=self.inverse_object_property_axioms,
            object_property_assertion_axioms=self.object_property_assertion_axioms,
            object_property_domain_axioms=self.object_property_domain_axioms,
            object_property_range_axioms=self.object_property_range_axioms,
            annotation_assertion_axioms=self.annotation_assertion_axioms,
            anonymous_individuals=self.anonymous_individuals,
            literal_nodes=self.literal_nodes,
            annotation_nodes=self.annotation_nodes,
            scalar_bytes_checked=self.scalar_bytes_checked,
            referenced_segments=self.referenced_segments,
            posting_rows_inspected=self.posting_rows_inspected,
            scope_map_rows_inspected=self.scope_map_rows_inspected,
            source_roots_inspected=self.source_roots_inspected,
            delta_roots_inspected=self.delta_roots_inspected,
            composite_member_segments=self.composite_member_segments,
            bridge_roots_inspected=self.bridge_roots_inspected,
            selected_roots=self.selected_roots,
            deduplicated_roots=self.deduplicated_roots,
            canonical_bytes_compared=self.canonical_bytes_compared,
            edge_batches=self.edge_batches,
            raw_edges=self.raw_edges,
            scalar_fallbacks=self.scalar_fallbacks,
        )


@dataclass(slots=True)
class _Inspection:
    counters: _MutableCounters
    fallback_reason: str | None = None

    def fallback(self, reason: str) -> None:
        if self.fallback_reason is None:
            self.fallback_reason = reason


@dataclass(frozen=True, slots=True)
class _EncodedRoleAxiom:
    tag: int
    node_id: int
    first: str
    second: str
    owlapi_hash: int
    canonical_order: int


@dataclass(frozen=True, slots=True)
class _EncodedRootRef:
    columns: _EncodedColumns
    cursor: _CanonicalCursor
    root_index: int

    @property
    def node_id(self) -> int:
        return self.columns.root_id(self.root_index)

    @property
    def root_kind(self) -> int:
        return self.columns.root_kind(self.root_index)


@dataclass(frozen=True, slots=True)
class _EncodedNodeRef:
    columns: _EncodedColumns
    cursor: _CanonicalCursor
    node_id: int


@dataclass(frozen=True, slots=True)
class _ValidatedSegment:
    role: int
    owner: object
    source: object | None
    posting_mode: int
    postings: memoryview
    scope_map: Mapping[bytes, bytes]
    scope_map_rows: int
    member_token: object


@dataclass(frozen=True, slots=True)
class _ResolvedColumnGroup:
    """One source-local canonical root subsequence with its effective leaf map."""

    lease: EncodedStructuralLease
    root_indices: tuple[int, ...]
    scope_map: Mapping[bytes, bytes]


@dataclass(slots=True)
class _SegmentResolutionState:
    """Bound recursive view cache and pre-publication validation evidence."""

    top_lease: EncodedStructuralLease
    leases: dict[int, EncodedStructuralLease] = field(default_factory=dict)
    inspections: dict[int, _Inspection] = field(default_factory=dict)
    cache: dict[int, tuple[_ResolvedColumnGroup, ...]] = field(default_factory=dict)
    active: set[int] = field(default_factory=set)
    fallback_reason: str | None = None
    referenced_segments: int = 0
    posting_rows_inspected: int = 0
    scope_map_rows_inspected: int = 0
    source_roots_inspected: int = 0
    delta_roots_inspected: int = 0
    composite_member_segments: int = 0
    bridge_roots_inspected: int = 0

    def fallback(self, reason: str) -> None:
        if self.fallback_reason is None:
            self.fallback_reason = reason


@dataclass(frozen=True, slots=True)
class _EquivalentOperand:
    tag: int
    node_id: int
    first: str
    second: str | None = None


@dataclass(slots=True)
class EncodedSubsetCompilation:
    """Prepared encoded-view slice that emits only existing projector ``Edge`` IR."""

    view: object
    options: ProjectionOptions
    lease: EncodedStructuralLease
    batch_edges: int
    asserted_taxonomy_only: bool
    role_state: RoleState
    _roots: tuple[_EncodedRootRef, ...]
    _domains: dict[str, tuple[str, ...]]
    _ranges: dict[str, tuple[str, ...]]
    _role_axioms: tuple[_EncodedRoleAxiom, ...]
    _anonymous_ids: dict[_CanonicalCursor, dict[int, str]]
    _class_iris: frozenset[str]
    _counters: _MutableCounters
    _retained_leases: tuple[EncodedStructuralLease, ...] = ()
    _ignored_restriction_subclasses: int = 0
    _ignored_annotation_assertions: int = 0
    _non_string_literal_renderings: int = 0
    _roles_prepared: bool = False
    statistics: CompileStatistics = field(default_factory=CompileStatistics)

    @property
    def counters(self) -> EncodedSubsetCounters:
        return self._counters.freeze()

    @property
    def diagnostics(self) -> tuple[ProjectionDiagnostic, ...]:
        result: list[ProjectionDiagnostic] = []
        if self._ignored_annotation_assertions:
            result.append(
                ProjectionDiagnostic(
                    code="MOWL_IGNORED_SHAPE",
                    message="constructor does not emit an edge in the pinned profile",
                    count=self._ignored_annotation_assertions,
                    constructor="AnnotationAssertion",
                )
            )
        if self._ignored_restriction_subclasses:
            result.append(
                ProjectionDiagnostic(
                    code="MOWL_IGNORED_SHAPE",
                    message="constructor does not emit an edge in the pinned profile",
                    count=self._ignored_restriction_subclasses,
                    constructor="SubClassOf",
                )
            )
        if self._non_string_literal_renderings:
            result.append(
                ProjectionDiagnostic(
                    code="MOWL_NON_STRING_LITERAL_RENDERING",
                    message="pinned mOWL rendering preserves malformed datatype syntax",
                    severity="warning",
                    count=self._non_string_literal_renderings,
                    constructor="Literal",
                )
            )
        return tuple(result)

    def prepare_role_state(self) -> None:
        """Eagerly apply exact named role-map constructors for stateful calls."""
        self._ensure_roles()

    def iter_raw_edges(self) -> Iterator[Edge]:
        """Yield encounter-order edges through a caller-bounded local batch."""

        def generate() -> Iterator[Edge]:
            batch: list[Edge] = []
            try:
                for edge in self._iter_unbatched_edges():
                    batch.append(edge)
                    if len(batch) == self.batch_edges:
                        self._counters.edge_batches += 1
                        self._counters.raw_edges += len(batch)
                        yield from batch
                        batch.clear()
                if batch:
                    self._counters.edge_batches += 1
                    self._counters.raw_edges += len(batch)
                    yield from batch
            finally:
                batch.clear()

        return generate()

    def _iter_unbatched_edges(self) -> Iterator[Edge]:
        # The pinned Scala profile concatenates TBox, selected annotations,
        # ABox, then domain/range categories.  Schema tag order puts
        # AnnotationAssertion after ABox, so keep explicit bounded passes.
        for root in self._roots:
            columns = root.columns
            root_id = root.node_id
            tag = columns.node_tag(root_id)
            if tag == _TAG_SUB_CLASS_OF:
                restriction = columns.restriction_subclass_iris(root_id)
                if restriction is None:
                    source, destination = columns.subclass_iris(root_id)
                    yield Edge(source, SUBCLASS_OF, destination)
                    if self.options.bidirectional_taxonomy:
                        yield Edge(destination, SUPERCLASS_OF, source)
                elif not self.asserted_taxonomy_only and not self.options.only_taxonomy:
                    yield from self._iter_role_edges(*restriction)
                continue
            if self.asserted_taxonomy_only:
                continue
            if tag == _TAG_EQUIVALENT_CLASSES:
                aggregate = columns.equivalent_aggregate(root_id)
                if aggregate is None:
                    source, destination = columns.equivalent_iris(root_id)
                    yield Edge(source, SUBCLASS_OF, destination)
                    if self.options.bidirectional_taxonomy:
                        yield Edge(destination, SUPERCLASS_OF, source)
                else:
                    subject, operands = aggregate
                    for operand in operands:
                        if operand.second is None:
                            yield Edge(subject, SUBCLASS_OF, operand.first)
                            if self.options.bidirectional_taxonomy:
                                yield Edge(operand.first, SUPERCLASS_OF, subject)
                        elif not self.options.only_taxonomy:
                            yield from self._iter_role_edges(
                                subject,
                                operand.first,
                                operand.second,
                            )
        if self.asserted_taxonomy_only:
            return

        if self.options.include_literals:
            for root in self._roots:
                columns = root.columns
                root_id = root.node_id
                if columns.node_tag(root_id) != _TAG_ANNOTATION_ASSERTION:
                    continue
                edge, non_string_literal = columns.annotation_assertion_edge(
                    root_id,
                    self._class_iris,
                    self._anonymous_ids[root.cursor],
                )
                if edge is None:
                    self.statistics.ignored_shapes += 1
                    self._ignored_annotation_assertions += 1
                    continue
                if non_string_literal:
                    self._non_string_literal_renderings += 1
                yield edge

        for root in self._roots:
            columns = root.columns
            root_id = root.node_id
            tag = columns.node_tag(root_id)
            if tag == _TAG_CLASS_ASSERTION:
                individual, class_iri = columns.class_assertion_iris(root_id)
                yield Edge(individual, RDF_TYPE, class_iri)
                continue
            if tag == _TAG_OBJECT_PROPERTY_ASSERTION:
                source, relation, destination = columns.object_property_assertion_iris(
                    root_id,
                    self._anonymous_ids[root.cursor],
                )
                yield Edge(source, relation, destination)
        yield from self._iter_domain_range_edges()

    def _iter_domain_range_edges(self) -> Iterator[Edge]:
        properties = sorted(
            (property_iri for property_iri in self._domains if property_iri in self._ranges),
            key=lambda item: item.encode("utf-8"),
        )
        for property_iri in properties:
            for domain in self._domains[property_iri]:
                for range_iri in self._ranges[property_iri]:
                    yield from self._iter_role_edges(domain, property_iri, range_iri)

    def _iter_role_edges(
        self,
        source: str,
        relation: str,
        destination: str,
    ) -> Iterator[Edge]:
        self._ensure_roles()
        yield Edge(source, relation, destination)
        for subrole in self.role_state.subroles.get(relation, ()):
            yield Edge(source, subrole, destination)
        inverse = self.role_state.inverse_roles.get(relation)
        if inverse is not None:
            yield Edge(destination, inverse, source)

    def _ensure_roles(self) -> None:
        if self._roles_prepared:
            return
        for axiom in self._role_axioms:
            if axiom.tag == _TAG_SUB_OBJECT_PROPERTY_OF:
                self.role_state.subroles[axiom.second] = (
                    axiom.first,
                    *self.role_state.subroles.get(axiom.first, ()),
                )
            else:
                self.role_state.inverse_roles[axiom.first] = axiom.second
                self.role_state.inverse_roles[axiom.second] = axiom.first
        self._roles_prepared = True


class _EncodedColumns:
    def __init__(
        self,
        lease: EncodedStructuralLease,
        *,
        root_indices: tuple[int, ...] | None = None,
    ) -> None:
        self._buffers = lease.buffers
        self._max_iri_bytes = _owner_limit(
            lease.owner,
            "max_iri_bytes",
            _DEFAULT_MAX_IRI_BYTES,
        )
        self._max_literal_bytes = _owner_limit(
            lease.owner,
            "max_literal_bytes",
            _DEFAULT_MAX_LITERAL_BYTES,
        )
        self.root_count = self._buffers["root_kinds"].nbytes
        self.node_count = self._buffers["node_tags"].nbytes // 2
        self.field_count = self._buffers["field_kinds"].nbytes
        self.item_count = self._buffers["item_kinds"].nbytes
        self._root_indices = tuple(range(self.root_count)) if root_indices is None else root_indices
        previous = -1
        for index in self._root_indices:
            if type(index) is not int or index <= previous or index >= self.root_count:
                raise SnapshotCompatibilityError(
                    "encoded subset selected roots are not sorted unique in-range indices"
                )
            previous = index

    @property
    def selected_root_count(self) -> int:
        return len(self._root_indices)

    def iter_root_indices(self) -> Iterator[int]:
        return iter(self._root_indices)

    def inspect(self, *, classify_roots: bool = True) -> _Inspection:
        counters = _MutableCounters(
            roots_inspected=self.root_count,
            nodes_inspected=self.node_count,
        )
        inspection = _Inspection(counters)
        for node_id in range(1, self.node_count + 1):
            self._inspect_node(node_id, inspection)
        previous_root: tuple[int, int] | None = None
        for root_index in range(self.root_count):
            root_kind = self._read("root_kinds", root_index, 1)
            root_id = self.root_id(root_index)
            root_key = (root_kind, root_id)
            if previous_root is not None and root_key <= previous_root:
                raise SnapshotCompatibilityError(
                    "encoded subset roots are not canonical and unique"
                )
            previous_root = root_key
        if classify_roots:
            for root_index in self._root_indices:
                self.inspect_root(root_index, inspection)
        return inspection

    def inspect_root(self, root_index: int, inspection: _Inspection) -> None:
        root_kind = self.root_kind(root_index)
        root_id = self.root_id(root_index)
        counters = inspection.counters
        if root_kind != _ROOT_AXIOM:
            inspection.fallback("encoded subset supports axiom roots only")
            return
        tag = self.node_tag(root_id)
        if tag == _TAG_DECLARATION:
            counters.declaration_axioms += 1
        elif tag == _TAG_SUB_CLASS_OF:
            counters.subclass_axioms += 1
            start = self._exact_fields(root_id, 3)
            sub_tag = self.node_tag(self._field_node(start))
            super_tag = self.node_tag(self._field_node(start + 1))
            if sub_tag in _RESTRICTION_TAGS or super_tag in _RESTRICTION_TAGS:
                counters.restriction_subclass_axioms += 1
        elif tag == _TAG_EQUIVALENT_CLASSES:
            counters.equivalent_axioms += 1
            if self._equivalent_aggregate_id(root_id) is not None:
                counters.aggregate_equivalent_axioms += 1
        elif tag == _TAG_SUB_OBJECT_PROPERTY_OF:
            counters.sub_object_property_axioms += 1
        elif tag == _TAG_INVERSE_OBJECT_PROPERTIES:
            counters.inverse_object_property_axioms += 1
        elif tag == _TAG_OBJECT_PROPERTY_DOMAIN:
            counters.object_property_domain_axioms += 1
        elif tag == _TAG_OBJECT_PROPERTY_RANGE:
            counters.object_property_range_axioms += 1
        elif tag == _TAG_CLASS_ASSERTION:
            counters.class_assertion_axioms += 1
        elif tag == _TAG_OBJECT_PROPERTY_ASSERTION:
            counters.object_property_assertion_axioms += 1
        elif tag == _TAG_ANNOTATION_ASSERTION:
            counters.annotation_assertion_axioms += 1
        else:
            inspection.fallback("encoded subset root is outside the executable axiom slice")

    def root_id(self, index: int) -> int:
        return self._node_id(self._read("root_ids", index, 4))

    def root_kind(self, index: int) -> int:
        return self._read("root_kinds", index, 1)

    def node_tag(self, node_id: int) -> int:
        self._node_id(node_id)
        return self._read("node_tags", node_id - 1, 2)

    def subclass_iris(self, node_id: int) -> tuple[str, str]:
        if self.node_tag(node_id) != _TAG_SUB_CLASS_OF:
            raise SnapshotCompatibilityError(
                "encoded subset batch cursor does not reference SubClassOf"
            )
        start = self._exact_fields(node_id, 3)
        sub_id = self._field_node(start)
        super_id = self._field_node(start + 1)
        sub = self._named_class_iri(sub_id)
        sup = self._named_class_iri(super_id)
        if sub is None or sup is None:  # pragma: no cover - guarded by preflight
            raise SnapshotCompatibilityError(
                "encoded subset SubClassOf shape changed after successful preflight"
            )
        return sub, sup

    def restriction_subclass_iris(self, node_id: int) -> tuple[str, str, str] | None:
        if self.node_tag(node_id) != _TAG_SUB_CLASS_OF:
            raise SnapshotCompatibilityError(
                "encoded subset batch cursor does not reference SubClassOf"
            )
        start = self._exact_fields(node_id, 3)
        sub_id = self._field_node(start)
        super_id = self._field_node(start + 1)
        if self.node_tag(super_id) in _RESTRICTION_TAGS:
            subject = self._named_class_iri(sub_id)
            expression_id = super_id
        elif self.node_tag(sub_id) in _RESTRICTION_TAGS:
            subject = self._named_class_iri(super_id)
            expression_id = sub_id
        else:
            return None
        if subject is None:  # pragma: no cover - guarded by preflight
            raise SnapshotCompatibilityError(
                "encoded subset restriction SubClassOf shape changed after preflight"
            )
        relation, destination = self._restriction_iris(expression_id)
        return subject, relation, destination

    def _restriction_iris(self, node_id: int) -> tuple[str, str]:
        tag = self.node_tag(node_id)
        if tag in {_TAG_OBJECT_SOME_VALUES_FROM, _TAG_OBJECT_ALL_VALUES_FROM}:
            start = self._exact_fields(node_id, 2)
            property_index = start
            filler_index = start + 1
        elif tag in {_TAG_OBJECT_MIN_CARDINALITY, _TAG_OBJECT_MAX_CARDINALITY}:
            start = self._exact_fields(node_id, 3)
            self._canonical_integer_bytes(start)
            property_index = start + 1
            filler_index = start + 2
        else:  # pragma: no cover - guarded by caller
            raise SnapshotCompatibilityError(
                "encoded subset batch cursor does not reference a supported restriction"
            )
        relation = self._named_object_property_iri(self._field_node(property_index))
        destination = self._named_class_iri(self._field_node(filler_index))
        if relation is None or destination is None:  # pragma: no cover - preflight
            raise SnapshotCompatibilityError(
                "encoded subset restriction shape changed after successful preflight"
            )
        return relation, destination

    def equivalent_iris(self, node_id: int) -> tuple[str, str]:
        if self.node_tag(node_id) != _TAG_EQUIVALENT_CLASSES:
            raise SnapshotCompatibilityError(
                "encoded subset batch cursor does not reference EquivalentClasses"
            )
        start = self._exact_fields(node_id, 2)
        item_start, length = self._node_set_range(start, minimum=2)
        first: tuple[bytes, str] | None = None
        second: tuple[bytes, str] | None = None
        for item_index in range(item_start, item_start + length):
            iri = self._named_class_iri(self._item_node(item_index))
            if iri is None:  # pragma: no cover - guarded by preflight
                raise SnapshotCompatibilityError(
                    "encoded subset EquivalentClasses shape changed after successful preflight"
                )
            candidate = iri.encode("utf-8"), iri
            if first is None or candidate[0] < first[0]:
                second = first
                first = candidate
            elif second is None or candidate[0] < second[0]:
                second = candidate
        if first is None or second is None:  # pragma: no cover - minimum guarded above
            raise SnapshotCompatibilityError(
                "encoded subset EquivalentClasses lost its required expressions"
            )
        return first[1], second[1]

    def equivalent_aggregate(
        self,
        node_id: int,
    ) -> tuple[str, tuple[_EquivalentOperand, ...]] | None:
        aggregate_id = self._equivalent_aggregate_id(node_id)
        if aggregate_id is None:
            return None
        start = self._exact_fields(node_id, 2)
        item_start, length = self._node_set_range(start, minimum=2)
        subject: str | None = None
        for item_index in range(item_start, item_start + length):
            item_id = self._item_node(item_index)
            if self.node_tag(item_id) == _TAG_ENTITY:
                subject = self._named_class_iri(item_id)
                if subject is not None:
                    break
        if subject is None:  # pragma: no cover - guarded by preflight
            raise SnapshotCompatibilityError(
                "encoded subset aggregate EquivalentClasses lost its named class"
            )
        return subject, self._aggregate_operands(aggregate_id)

    def _equivalent_aggregate_id(self, node_id: int) -> int | None:
        if self.node_tag(node_id) != _TAG_EQUIVALENT_CLASSES:
            raise SnapshotCompatibilityError(
                "encoded subset batch cursor does not reference EquivalentClasses"
            )
        start = self._exact_fields(node_id, 2)
        item_start, length = self._node_set_range(start, minimum=2)
        aggregate_id: int | None = None
        for item_index in range(item_start, item_start + length):
            item_id = self._item_node(item_index)
            if self.node_tag(item_id) in _AGGREGATE_TAGS:
                aggregate_id = item_id
        return aggregate_id

    def _aggregate_operands(self, node_id: int) -> tuple[_EquivalentOperand, ...]:
        if self.node_tag(node_id) not in _AGGREGATE_TAGS:
            raise SnapshotCompatibilityError(
                "encoded subset batch cursor does not reference an aggregate expression"
            )
        start = self._exact_fields(node_id, 1)
        item_start, length = self._node_set_range(start, minimum=2)
        operands: list[_EquivalentOperand] = []
        for item_index in range(item_start, item_start + length):
            item_id = self._item_node(item_index)
            tag = self.node_tag(item_id)
            if tag == _TAG_ENTITY:
                iri = self._named_class_iri(item_id)
                if iri is None:  # pragma: no cover - guarded by preflight
                    raise SnapshotCompatibilityError(
                        "encoded subset aggregate operand changed after preflight"
                    )
                operands.append(_EquivalentOperand(tag, item_id, iri))
            else:
                relation, destination = self._restriction_iris(item_id)
                operands.append(_EquivalentOperand(tag, item_id, relation, destination))

        def order_key(operand: _EquivalentOperand) -> tuple[int, bytes]:
            if operand.tag == _TAG_ENTITY:
                return 1001, operand.first.encode("utf-8")
            return _EXPRESSION_ORDER[operand.tag], operand.node_id.to_bytes(4, "big")

        operands.sort(key=order_key)
        return tuple(operands)

    def class_assertion_iris(self, node_id: int) -> tuple[str, str]:
        if self.node_tag(node_id) != _TAG_CLASS_ASSERTION:
            raise SnapshotCompatibilityError(
                "encoded subset batch cursor does not reference ClassAssertion"
            )
        start = self._exact_fields(node_id, 3)
        class_iri = self._named_class_iri(self._field_node(start))
        individual = self._named_individual_iri(self._field_node(start + 1))
        if class_iri is None or individual is None:  # pragma: no cover - preflight
            raise SnapshotCompatibilityError(
                "encoded subset ClassAssertion shape changed after successful preflight"
            )
        return individual, class_iri

    def object_property_assertion_iris(
        self,
        node_id: int,
        anonymous_ids: dict[int, str],
    ) -> tuple[str, str, str]:
        if self.node_tag(node_id) != _TAG_OBJECT_PROPERTY_ASSERTION:
            raise SnapshotCompatibilityError(
                "encoded subset batch cursor does not reference ObjectPropertyAssertion"
            )
        start = self._exact_fields(node_id, 4)
        property_iri = self._named_object_property_iri(self._field_node(start))
        source = self._individual_id(self._field_node(start + 1), anonymous_ids)
        target = self._individual_id(self._field_node(start + 2), anonymous_ids)
        if property_iri is None or source is None or target is None:  # pragma: no cover
            raise SnapshotCompatibilityError(
                "encoded subset ObjectPropertyAssertion shape changed after preflight"
            )
        return source, property_iri, target

    def annotation_assertion_edge(
        self,
        node_id: int,
        class_iris: frozenset[str],
        anonymous_ids: dict[int, str],
    ) -> tuple[Edge | None, bool]:
        if self.node_tag(node_id) != _TAG_ANNOTATION_ASSERTION:
            raise SnapshotCompatibilityError(
                "encoded subset batch cursor does not reference AnnotationAssertion"
            )
        start = self._exact_fields(node_id, 4)
        property_iri = self._named_annotation_property_iri(self._field_node(start))
        subject_id = self._field_node(start + 1)
        subject = self._iri_text(subject_id)[0] if self.node_tag(subject_id) == _TAG_IRI else None
        if property_iri is None or self.node_tag(subject_id) not in {
            _TAG_IRI,
            _TAG_ANONYMOUS_INDIVIDUAL,
        }:  # pragma: no cover - preflight
            raise SnapshotCompatibilityError(
                "encoded subset AnnotationAssertion shape changed after preflight"
            )
        if (
            subject is None
            or subject not in class_iris
            or property_iri not in _ANNOTATION_PROPERTIES
        ):
            return None, False
        relation = (
            "rdfs:" + property_iri.removeprefix(RDFS_NAMESPACE)
            if property_iri.startswith(RDFS_NAMESPACE)
            else property_iri
        )
        destination, non_string_literal = self._annotation_value(
            self._field_node(start + 2),
            anonymous_ids,
        )
        return Edge(subject, relation, destination), non_string_literal

    def reachable_node_ids(self) -> frozenset[int]:
        """Return nodes reachable from the posting-selected root subset."""

        reachable: set[int] = set()
        pending = [self.root_id(index) for index in self._root_indices]
        while pending:
            node_id = pending.pop()
            if node_id in reachable:
                continue
            reachable.add(node_id)
            start, end = self._field_range(node_id)
            for field_index in range(start, end):
                kind = self._read("field_kinds", field_index, 1)
                if kind == _COMPONENT_NODE:
                    pending.append(self._field_node(field_index))
                elif kind == _COMPONENT_SET:
                    item_start, length = self._node_set_range(field_index)
                    for item_index in range(item_start, item_start + length):
                        pending.append(self._item_node(item_index))
        return frozenset(reachable)

    def class_iris(self, reachable: frozenset[int] | None = None) -> frozenset[str]:
        result: set[str] = set()
        selected = range(1, self.node_count + 1) if reachable is None else sorted(reachable)
        for node_id in selected:
            iri = self._named_class_iri(node_id)
            if iri is not None:
                result.add(iri)
        return frozenset(result)

    def anonymous_scopes(
        self,
        reachable: frozenset[int],
    ) -> tuple[bytes, ...]:
        """Return distinct source scopes in canonical anonymous-node order."""

        scopes: list[bytes] = []
        previous: bytes | None = None
        for node_id in sorted(reachable):
            if self.node_tag(node_id) != _TAG_ANONYMOUS_INDIVIDUAL:
                continue
            start = self._exact_fields(node_id, 2)
            scope = bytes(self._scalar_payload(start, _COMPONENT_BYTES))
            if scope != previous:
                scopes.append(scope)
                previous = scope
        return tuple(scopes)

    def _individual_id(self, node_id: int, anonymous_ids: dict[int, str]) -> str | None:
        named = self._named_individual_iri(node_id)
        if named is not None:
            return named
        if self.node_tag(node_id) != _TAG_ANONYMOUS_INDIVIDUAL:
            return None
        try:
            return anonymous_ids[node_id]
        except KeyError as error:  # pragma: no cover - prepared from immutable columns
            raise SnapshotCompatibilityError(
                "encoded subset anonymous individual lost its generated identifier"
            ) from error

    def _annotation_value(
        self,
        node_id: int,
        anonymous_ids: dict[int, str],
    ) -> tuple[str, bool]:
        tag = self.node_tag(node_id)
        if tag == _TAG_IRI:
            return self._iri_text(node_id)[0], False
        if tag == _TAG_ANONYMOUS_INDIVIDUAL:
            try:
                return anonymous_ids[node_id], False
            except KeyError as error:  # pragma: no cover - prepared from immutable columns
                raise SnapshotCompatibilityError(
                    "encoded subset anonymous annotation value lost its generated identifier"
                ) from error
        lexical, datatype, _checked = self._literal_parts(node_id)
        if datatype in {XSD_STRING_IRI, RDF_PLAIN_LITERAL_IRI}:
            return lexical, False
        rendered = f'"{_owlapi_escape_literal(lexical)}"^^{_render_datatype(datatype)}'
        stripped = rendered.replace("\\", "")
        if stripped.startswith('"'):
            stripped = stripped[1:-1]
        elif stripped.startswith("<"):
            stripped = stripped[1:-1]
        return stripped, True

    def domain_iris(self, node_id: int) -> tuple[str, str]:
        return self._property_class_iris(node_id, _TAG_OBJECT_PROPERTY_DOMAIN)

    def range_iris(self, node_id: int) -> tuple[str, str]:
        return self._property_class_iris(node_id, _TAG_OBJECT_PROPERTY_RANGE)

    def _property_pair_iris(self, node_id: int, expected_tag: int) -> tuple[str, str]:
        if self.node_tag(node_id) != expected_tag:
            raise SnapshotCompatibilityError(
                "encoded subset batch cursor does not reference a role axiom"
            )
        start = self._exact_fields(node_id, 3)
        first = self._named_object_property_iri(self._field_node(start))
        second = self._named_object_property_iri(self._field_node(start + 1))
        if first is None or second is None:  # pragma: no cover - preflight
            raise SnapshotCompatibilityError(
                "encoded subset role axiom shape changed after successful preflight"
            )
        return first, second

    def _property_class_iris(self, node_id: int, expected_tag: int) -> tuple[str, str]:
        if self.node_tag(node_id) != expected_tag:
            raise SnapshotCompatibilityError(
                "encoded subset batch cursor does not reference a domain/range axiom"
            )
        start = self._exact_fields(node_id, 3)
        property_iri = self._named_object_property_iri(self._field_node(start))
        class_iri = self._named_class_iri(self._field_node(start + 1))
        if property_iri is None or class_iri is None:  # pragma: no cover - preflight
            raise SnapshotCompatibilityError(
                "encoded subset domain/range shape changed after successful preflight"
            )
        return property_iri, class_iri

    def _inspect_node(self, node_id: int, inspection: _Inspection) -> None:
        tag = self.node_tag(node_id)
        if tag not in _SCHEMA_TAGS:
            raise SnapshotCompatibilityError(
                "encoded subset constructor tag is outside the frozen schema",
                details={"node_id": node_id, "tag": tag},
            )
        if tag == _TAG_IRI:
            _text, checked = self._iri_text(node_id)
            inspection.counters.scalar_bytes_checked += checked
            return
        if tag == _TAG_ENTITY:
            _kind, _iri_id, checked = self._entity(node_id)
            inspection.counters.scalar_bytes_checked += checked
            return
        if tag == _TAG_LITERAL:
            _lexical, _datatype, checked = self._literal_parts(node_id)
            inspection.counters.literal_nodes += 1
            inspection.counters.scalar_bytes_checked += checked
            return
        if tag == _TAG_ANONYMOUS_INDIVIDUAL:
            start = self._exact_fields(node_id, 2)
            document_scope = self._scalar_payload(start, _COMPONENT_BYTES)
            local_key = self._scalar_payload(start + 1, _COMPONENT_BYTES)
            if document_scope.nbytes != 32:
                raise SnapshotCompatibilityError(
                    "encoded subset anonymous document scope is not bytes32"
                )
            if not local_key.nbytes:
                raise SnapshotCompatibilityError("encoded subset anonymous local key is empty")
            inspection.counters.anonymous_individuals += 1
            inspection.counters.scalar_bytes_checked += document_scope.nbytes + local_key.nbytes
            return
        if tag == _TAG_ANNOTATION:
            start = self._exact_fields(node_id, 3)
            if not self._is_named_annotation_property(self._field_node(start)):
                raise SnapshotCompatibilityError(
                    "encoded subset Annotation property is not a named annotation property"
                )
            if self.node_tag(self._field_node(start + 1)) not in {
                _TAG_IRI,
                _TAG_ANONYMOUS_INDIVIDUAL,
                _TAG_LITERAL,
            }:
                raise SnapshotCompatibilityError(
                    "encoded subset Annotation value has the wrong constructor"
                )
            self._annotation_set_range(start + 2)
            inspection.counters.annotation_nodes += 1
            return
        if tag in _AGGREGATE_TAGS:
            start = self._exact_fields(node_id, 1)
            item_start, length = self._node_set_range(start, minimum=2)
            supported = True
            for item_index in range(item_start, item_start + length):
                item_id = self._item_node(item_index)
                item_tag = self.node_tag(item_id)
                if not self._is_named_class(item_id) and item_tag not in _RESTRICTION_TAGS:
                    supported = False
            if not supported:
                inspection.fallback(
                    "encoded subset aggregate expressions require named or restriction operands"
                )
            return
        if tag in _RESTRICTION_TAGS:
            if tag in {_TAG_OBJECT_SOME_VALUES_FROM, _TAG_OBJECT_ALL_VALUES_FROM}:
                start = self._exact_fields(node_id, 2)
                property_index = start
                filler_index = start + 1
            else:
                start = self._exact_fields(node_id, 3)
                checked = self._canonical_integer_bytes(start)
                inspection.counters.scalar_bytes_checked += checked
                property_index = start + 1
                filler_index = start + 2
            named_property = self._is_named_object_property(self._field_node(property_index))
            named_filler = self._is_named_class(self._field_node(filler_index))
            if not named_property or not named_filler:
                inspection.fallback(
                    "encoded subset requires a named property and filler in restrictions"
                )
            return
        if tag == _TAG_DECLARATION:
            start = self._exact_fields(node_id, 2)
            self._entity(self._field_node(start))
            if not self._empty_annotation_set(start + 1):
                inspection.fallback("encoded subset does not yet support annotated declarations")
            return
        if tag == _TAG_SUB_CLASS_OF:
            start = self._exact_fields(node_id, 3)
            sub_id = self._field_node(start)
            super_id = self._field_node(start + 1)
            sub_named = self._is_named_class(sub_id)
            super_named = self._is_named_class(super_id)
            sub_restriction = self.node_tag(sub_id) in _RESTRICTION_TAGS
            super_restriction = self.node_tag(super_id) in _RESTRICTION_TAGS
            if not (
                (sub_named and super_named)
                or (sub_named and super_restriction)
                or (sub_restriction and super_named)
            ):
                inspection.fallback(
                    "encoded subset requires a named taxonomy or named restriction SubClassOf"
                )
            if not self._empty_annotation_set(start + 2):
                inspection.fallback(
                    "encoded subset does not yet support annotated SubClassOf axioms"
                )
            return
        if tag == _TAG_EQUIVALENT_CLASSES:
            start = self._exact_fields(node_id, 2)
            item_start, length = self._node_set_range(start, minimum=2)
            named_count = 0
            aggregate_count = 0
            other_count = 0
            for item_index in range(item_start, item_start + length):
                item_id = self._item_node(item_index)
                if self._is_named_class(item_id):
                    named_count += 1
                elif self.node_tag(item_id) in _AGGREGATE_TAGS:
                    aggregate_count += 1
                else:
                    other_count += 1
            all_named = named_count == length
            named_aggregate_pair = (
                length == 2 and named_count == 1 and aggregate_count == 1 and other_count == 0
            )
            if not all_named and not named_aggregate_pair:
                inspection.fallback(
                    "encoded subset requires named classes or one named/aggregate "
                    "EquivalentClasses pair"
                )
            if not self._empty_annotation_set(start + 1):
                inspection.fallback(
                    "encoded subset does not yet support annotated EquivalentClasses axioms"
                )
            return
        if tag in {_TAG_SUB_OBJECT_PROPERTY_OF, _TAG_INVERSE_OBJECT_PROPERTIES}:
            start = self._exact_fields(node_id, 3)
            first_named = self._is_named_object_property(self._field_node(start))
            second_named = self._is_named_object_property(self._field_node(start + 1))
            if not first_named or not second_named:
                inspection.fallback(
                    "encoded subset requires named object properties in role axioms"
                )
            if not self._empty_annotation_set(start + 2):
                inspection.fallback("encoded subset does not yet support annotated role axioms")
            return
        if tag in {_TAG_OBJECT_PROPERTY_DOMAIN, _TAG_OBJECT_PROPERTY_RANGE}:
            start = self._exact_fields(node_id, 3)
            named_property = self._is_named_object_property(self._field_node(start))
            named_class = self._is_named_class(self._field_node(start + 1))
            if not named_property or not named_class:
                inspection.fallback(
                    "encoded subset requires a named object property and named class "
                    "in domain/range axioms"
                )
            if not self._empty_annotation_set(start + 2):
                inspection.fallback(
                    "encoded subset does not yet support annotated domain/range axioms"
                )
            return
        if tag == _TAG_CLASS_ASSERTION:
            start = self._exact_fields(node_id, 3)
            named_class = self._is_named_class(self._field_node(start))
            named_individual = self._is_named_individual(self._field_node(start + 1))
            if not named_class or not named_individual:
                inspection.fallback(
                    "encoded subset requires a named class and named individual in ClassAssertion"
                )
            if not self._empty_annotation_set(start + 2):
                inspection.fallback(
                    "encoded subset does not yet support annotated ClassAssertion axioms"
                )
            return
        if tag == _TAG_OBJECT_PROPERTY_ASSERTION:
            start = self._exact_fields(node_id, 4)
            named_property = self._is_named_object_property(self._field_node(start))
            supported_source = self._is_supported_individual(self._field_node(start + 1))
            supported_target = self._is_supported_individual(self._field_node(start + 2))
            if not named_property or not supported_source or not supported_target:
                inspection.fallback(
                    "encoded subset requires a named object property and supported individuals "
                    "in ObjectPropertyAssertion"
                )
            if not self._empty_annotation_set(start + 3):
                inspection.fallback(
                    "encoded subset does not yet support annotated ObjectPropertyAssertion axioms"
                )
            return
        if tag == _TAG_ANNOTATION_ASSERTION:
            start = self._exact_fields(node_id, 4)
            if not self._is_named_annotation_property(self._field_node(start)):
                raise SnapshotCompatibilityError(
                    "encoded subset AnnotationAssertion property is not an annotation property"
                )
            if self.node_tag(self._field_node(start + 1)) not in {
                _TAG_IRI,
                _TAG_ANONYMOUS_INDIVIDUAL,
            }:
                raise SnapshotCompatibilityError(
                    "encoded subset AnnotationAssertion subject has the wrong constructor"
                )
            if self.node_tag(self._field_node(start + 2)) not in {
                _TAG_IRI,
                _TAG_ANONYMOUS_INDIVIDUAL,
                _TAG_LITERAL,
            }:
                raise SnapshotCompatibilityError(
                    "encoded subset AnnotationAssertion value has the wrong constructor"
                )
            self._annotation_set_range(start + 3)
            return
        inspection.fallback(
            "encoded subset contains a constructor outside the executable axiom slice"
        )

    def _field_range(self, node_id: int) -> tuple[int, int]:
        self._node_id(node_id)
        start = self._read("node_field_offsets", node_id - 1, 8)
        end = self._read("node_field_offsets", node_id, 8)
        if start > end or end > self.field_count:
            raise SnapshotCompatibilityError("encoded subset field range is invalid")
        return start, end

    def _exact_fields(self, node_id: int, arity: int) -> int:
        start, end = self._field_range(node_id)
        if end - start != arity:
            raise SnapshotCompatibilityError(
                "encoded subset constructor arity is invalid",
                details={"node_id": node_id, "expected_arity": arity},
            )
        return start

    def _field_node(self, index: int) -> int:
        if self._read("field_kinds", index, 1) != _COMPONENT_NODE or self._read(
            "field_lengths", index, 8
        ):
            raise SnapshotCompatibilityError(
                "encoded subset constructor field is not a node reference"
            )
        return self._node_id(self._read("field_values", index, 8))

    def _empty_annotation_set(self, index: int) -> bool:
        _start, length = self._annotation_set_range(index)
        return length == 0

    def _annotation_set_range(self, index: int) -> tuple[int, int]:
        start, length = self._node_set_range(index)
        for item_index in range(start, start + length):
            if self.node_tag(self._item_node(item_index)) != _TAG_ANNOTATION:
                raise SnapshotCompatibilityError(
                    "encoded subset annotation set contains a non-Annotation node"
                )
        return start, length

    def _node_set_range(self, index: int, *, minimum: int = 0) -> tuple[int, int]:
        if self._read("field_kinds", index, 1) != _COMPONENT_SET:
            raise SnapshotCompatibilityError(
                "encoded subset collection field is not a canonical set"
            )
        start = self._read("field_values", index, 8)
        length = self._read("field_lengths", index, 8)
        if start > self.item_count or length > self.item_count - start:
            raise SnapshotCompatibilityError("encoded subset canonical-set range is out of bounds")
        if length < minimum:
            raise SnapshotCompatibilityError(
                "encoded subset canonical set has too few items",
                details={"minimum": minimum, "actual": length},
            )
        previous = 0
        for item_index in range(start, start + length):
            node_id = self._item_node(item_index)
            if node_id <= previous:
                raise SnapshotCompatibilityError(
                    "encoded subset canonical-set items are not sorted and unique"
                )
            previous = node_id
        return start, length

    def _item_node(self, index: int) -> int:
        if self._read("item_kinds", index, 1) != _COMPONENT_NODE or self._read(
            "item_lengths", index, 8
        ):
            raise SnapshotCompatibilityError(
                "encoded subset canonical-set item is not a node reference"
            )
        return self._node_id(self._read("item_values", index, 8))

    def _scalar_payload(self, index: int, expected_kind: int) -> memoryview:
        if self._read("field_kinds", index, 1) != expected_kind:
            raise SnapshotCompatibilityError("encoded subset scalar field kind is invalid")
        offset = self._read("field_values", index, 8)
        length = self._read("field_lengths", index, 8)
        end = offset + length
        arena = self._buffers["scalar_bytes"]
        if end > arena.nbytes:
            raise SnapshotCompatibilityError("encoded subset scalar range is out of bounds")
        return arena[offset:end]

    def _canonical_integer_bytes(self, index: int) -> int:
        payload = self._scalar_payload(index, _COMPONENT_INTEGER)
        if not payload.nbytes or (payload.nbytes > 1 and payload[-1] == 0):
            raise SnapshotCompatibilityError(
                "encoded subset integer field is not minimally encoded"
            )
        return payload.nbytes

    def _literal_parts(self, node_id: int) -> tuple[str, str, int]:
        if self.node_tag(node_id) != _TAG_LITERAL:
            raise SnapshotCompatibilityError(
                "encoded subset annotation value does not reference a Literal"
            )
        start = self._exact_fields(node_id, 3)
        lexical, checked = self._text_scalar(
            start,
            maximum=self._max_literal_bytes,
            label="literal lexical form",
        )
        datatype = self._named_datatype_iri(self._field_node(start + 1))
        if datatype is None:
            raise SnapshotCompatibilityError(
                "encoded subset Literal datatype is not a named datatype"
            )
        language_kind = self._read("field_kinds", start + 2, 1)
        if language_kind == _COMPONENT_NONE:
            if self._read("field_values", start + 2, 8) or self._read(
                "field_lengths", start + 2, 8
            ):
                raise SnapshotCompatibilityError(
                    "encoded subset Literal language none field is not canonical"
                )
        elif language_kind == _COMPONENT_TEXT:
            language, language_checked = self._text_scalar(
                start + 2,
                maximum=self._max_literal_bytes,
                label="literal language",
            )
            if not language or language != language.lower() or datatype != RDF_PLAIN_LITERAL_IRI:
                raise SnapshotCompatibilityError("encoded subset Literal language is not canonical")
            checked += language_checked
        else:
            raise SnapshotCompatibilityError(
                "encoded subset Literal language field kind is invalid"
            )
        return lexical, datatype, checked

    def _text_scalar(self, index: int, *, maximum: int, label: str) -> tuple[str, int]:
        payload = self._scalar_payload(index, _COMPONENT_TEXT)
        if payload.nbytes > maximum:
            raise SnapshotCompatibilityError(
                f"encoded subset {label} exceeds its public byte limit",
                details={"allowed": maximum, "actual": payload.nbytes},
            )
        try:
            return bytes(payload).decode("utf-8"), payload.nbytes
        except UnicodeDecodeError as error:
            raise SnapshotCompatibilityError(f"encoded subset {label} is not UTF-8") from error

    def _iri_text(self, node_id: int) -> tuple[str, int]:
        if self.node_tag(node_id) != _TAG_IRI:
            raise SnapshotCompatibilityError("encoded subset entity does not reference an IRI node")
        payload = self._scalar_payload(self._exact_fields(node_id, 1), _COMPONENT_TEXT)
        if payload.nbytes > self._max_iri_bytes:
            raise SnapshotCompatibilityError(
                "encoded subset IRI exceeds public max_iri_bytes",
                details={"allowed": self._max_iri_bytes, "actual": payload.nbytes},
            )
        try:
            return bytes(payload).decode("utf-8"), payload.nbytes
        except UnicodeDecodeError as error:
            raise SnapshotCompatibilityError("encoded subset IRI text is not UTF-8") from error

    def _entity(self, node_id: int) -> tuple[bytes, int, int]:
        if self.node_tag(node_id) != _TAG_ENTITY:
            raise SnapshotCompatibilityError(
                "encoded subset axiom does not reference an entity node"
            )
        start = self._exact_fields(node_id, 2)
        kind_view = self._scalar_payload(start, _COMPONENT_ENUM)
        if kind_view.nbytes > _MAX_ENTITY_KIND_BYTES:
            raise SnapshotCompatibilityError("encoded subset entity kind is invalid")
        kind = bytes(kind_view)
        if kind not in _ENTITY_KINDS:
            raise SnapshotCompatibilityError("encoded subset entity kind is invalid")
        iri_id = self._field_node(start + 1)
        if self.node_tag(iri_id) != _TAG_IRI:
            raise SnapshotCompatibilityError(
                "encoded subset entity IRI reference has the wrong tag"
            )
        return kind, iri_id, kind_view.nbytes

    def _named_class_iri(self, node_id: int) -> str | None:
        if self.node_tag(node_id) != _TAG_ENTITY:
            return None
        kind, iri_id, _checked = self._entity(node_id)
        return self._iri_text(iri_id)[0] if kind == b"class" else None

    def _named_individual_iri(self, node_id: int) -> str | None:
        if self.node_tag(node_id) != _TAG_ENTITY:
            return None
        kind, iri_id, _checked = self._entity(node_id)
        return self._iri_text(iri_id)[0] if kind == b"named_individual" else None

    def _named_datatype_iri(self, node_id: int) -> str | None:
        if self.node_tag(node_id) != _TAG_ENTITY:
            return None
        kind, iri_id, _checked = self._entity(node_id)
        return self._iri_text(iri_id)[0] if kind == b"datatype" else None

    def _named_annotation_property_iri(self, node_id: int) -> str | None:
        if self.node_tag(node_id) != _TAG_ENTITY:
            return None
        kind, iri_id, _checked = self._entity(node_id)
        return self._iri_text(iri_id)[0] if kind == b"annotation_property" else None

    def _named_object_property_iri(self, node_id: int) -> str | None:
        if self.node_tag(node_id) != _TAG_ENTITY:
            return None
        kind, iri_id, _checked = self._entity(node_id)
        return self._iri_text(iri_id)[0] if kind == b"object_property" else None

    def _is_named_class(self, node_id: int) -> bool:
        if self.node_tag(node_id) != _TAG_ENTITY:
            return False
        kind, _iri_id, _checked = self._entity(node_id)
        return kind == b"class"

    def _is_named_individual(self, node_id: int) -> bool:
        if self.node_tag(node_id) != _TAG_ENTITY:
            return False
        kind, _iri_id, _checked = self._entity(node_id)
        return kind == b"named_individual"

    def _is_named_annotation_property(self, node_id: int) -> bool:
        if self.node_tag(node_id) != _TAG_ENTITY:
            return False
        kind, _iri_id, _checked = self._entity(node_id)
        return kind == b"annotation_property"

    def _is_supported_individual(self, node_id: int) -> bool:
        return self.node_tag(node_id) == _TAG_ANONYMOUS_INDIVIDUAL or self._is_named_individual(
            node_id
        )

    def _is_named_object_property(self, node_id: int) -> bool:
        if self.node_tag(node_id) != _TAG_ENTITY:
            return False
        kind, _iri_id, _checked = self._entity(node_id)
        return kind == b"object_property"

    def _node_id(self, value: int) -> int:
        if not 1 <= value <= self.node_count:
            raise SnapshotCompatibilityError("encoded subset node reference is out of range")
        return value

    def _read(self, name: str, index: int, width: int) -> int:
        offset = index * width
        end = offset + width
        value = self._buffers[name]
        if offset < 0 or end > value.nbytes:
            raise SnapshotCompatibilityError(
                "encoded subset column read is out of bounds",
                details={"buffer": name},
            )
        return int.from_bytes(value[offset:end], "little")


def _encode_varint(value: int) -> bytes:
    if type(value) is not int or value < 0:
        raise SnapshotCompatibilityError(
            "encoded subset canonical cursor received an invalid unsigned integer"
        )
    result = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        result.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(result)


class _CanonicalCursor:
    """Stream exact canonical-model bytes from one borrowed column table.

    Lengths are memoized, while bytes are yielded incrementally.  This keeps
    cross-segment comparisons bounded by graph depth and the dense length map,
    without reconstructing OWL objects or an ontology-sized canonical arena.
    """

    def __init__(
        self,
        columns: _EncodedColumns,
        scope_map: Mapping[bytes, bytes],
    ) -> None:
        self.columns = columns
        self._scope_map = scope_map
        self._lengths: dict[int, int] = {}

    def node_length(self, node_id: int) -> int:
        try:
            return self._node_length(node_id, set())
        except RecursionError as error:
            raise SnapshotCompatibilityError(
                "encoded subset canonical cursor exceeds the safe graph depth"
            ) from error

    def _node_length(self, node_id: int, active: set[int]) -> int:
        cached = self._lengths.get(node_id)
        if cached is not None:
            return cached
        self.columns.node_tag(node_id)
        if node_id in active:
            raise SnapshotCompatibilityError(
                "encoded subset canonical cursor found a cyclic node graph"
            )
        active.add(node_id)
        try:
            result = len(_encode_varint(self.columns.node_tag(node_id)))
            start, end = self.columns._field_range(node_id)
            for field_index in range(start, end):
                result += self._component_length(
                    node_id,
                    field_index,
                    active,
                )
            self._lengths[node_id] = result
            return result
        finally:
            active.remove(node_id)

    def _component_length(
        self,
        owner_node_id: int | None,
        index: int,
        active: set[int],
        *,
        item: bool = False,
    ) -> int:
        prefix = "item" if item else "field"
        kind = self.columns._read(f"{prefix}_kinds", index, 1)
        value = self.columns._read(f"{prefix}_values", index, 8)
        length = self.columns._read(f"{prefix}_lengths", index, 8)
        if kind == _COMPONENT_NONE:
            if value or length:
                raise SnapshotCompatibilityError(
                    "encoded subset canonical cursor found a noncanonical none component"
                )
            return 1
        if kind == _COMPONENT_NODE:
            if length:
                raise SnapshotCompatibilityError(
                    "encoded subset canonical cursor found a sized node component"
                )
            child_length = self._node_length(self.columns._node_id(value), active)
            return 1 + len(_encode_varint(child_length)) + child_length
        if kind in {_COMPONENT_TEXT, _COMPONENT_BYTES, _COMPONENT_ENUM}:
            payload = self._scalar_payload(owner_node_id, index, kind, item=item)
            return 1 + len(_encode_varint(payload.nbytes)) + payload.nbytes
        if kind == _COMPONENT_INTEGER:
            payload = self._scalar_payload(owner_node_id, index, kind, item=item)
            if not payload.nbytes or (payload.nbytes > 1 and payload[-1] == 0):
                raise SnapshotCompatibilityError(
                    "encoded subset canonical cursor found a nonminimal integer"
                )
            return 1 + len(_encode_varint(int.from_bytes(payload, "little")))
        if kind not in {_COMPONENT_SET, _COMPONENT_SEQUENCE} or item:
            raise SnapshotCompatibilityError(
                "encoded subset canonical cursor found an invalid component kind"
            )
        if value > self.columns.item_count or length > self.columns.item_count - value:
            raise SnapshotCompatibilityError(
                "encoded subset canonical cursor collection is out of bounds"
            )
        result = 1 + len(_encode_varint(length))
        for item_index in range(value, value + length):
            if kind == _COMPONENT_SET:
                child_id = self.columns._item_node(item_index)
                child_length = self._node_length(child_id, active)
                result += len(_encode_varint(child_length)) + child_length
            else:
                result += self._component_length(
                    None,
                    item_index,
                    active,
                    item=True,
                )
        return result

    def _scalar_payload(
        self,
        owner_node_id: int | None,
        index: int,
        expected_kind: int,
        *,
        item: bool,
    ) -> memoryview:
        prefix = "item" if item else "field"
        if self.columns._read(f"{prefix}_kinds", index, 1) != expected_kind:
            raise SnapshotCompatibilityError("encoded subset canonical cursor scalar kind changed")
        offset = self.columns._read(f"{prefix}_values", index, 8)
        length = self.columns._read(f"{prefix}_lengths", index, 8)
        arena = self.columns._buffers["scalar_bytes"]
        if offset > arena.nbytes or length > arena.nbytes - offset:
            raise SnapshotCompatibilityError(
                "encoded subset canonical cursor scalar is out of bounds"
            )
        payload = arena[offset : offset + length]
        if (
            not item
            and owner_node_id is not None
            and expected_kind == _COMPONENT_BYTES
            and self.columns.node_tag(owner_node_id) == _TAG_ANONYMOUS_INDIVIDUAL
        ):
            start, _end = self.columns._field_range(owner_node_id)
            if index == start:
                replacement = self._scope_map.get(bytes(payload))
                if replacement is not None:
                    return memoryview(replacement)
        return payload

    def iter_node_bytes(self, node_id: int) -> Iterator[int]:
        self.node_length(node_id)
        return self._iter_node_bytes(node_id, set())

    def _iter_node_bytes(self, node_id: int, active: set[int]) -> Iterator[int]:
        if node_id in active:  # pragma: no cover - length preflight rejects cycles
            raise SnapshotCompatibilityError(
                "encoded subset canonical cursor found a cyclic node graph"
            )
        active.add(node_id)
        try:
            yield from _encode_varint(self.columns.node_tag(node_id))
            start, end = self.columns._field_range(node_id)
            for field_index in range(start, end):
                yield from self._iter_component_bytes(
                    node_id,
                    field_index,
                    active,
                )
        finally:
            active.remove(node_id)

    def _iter_component_bytes(
        self,
        owner_node_id: int | None,
        index: int,
        active: set[int],
        *,
        item: bool = False,
    ) -> Iterator[int]:
        prefix = "item" if item else "field"
        kind = self.columns._read(f"{prefix}_kinds", index, 1)
        value = self.columns._read(f"{prefix}_values", index, 8)
        length = self.columns._read(f"{prefix}_lengths", index, 8)
        yield kind
        if kind == _COMPONENT_NONE:
            return
        if kind == _COMPONENT_NODE:
            child_id = self.columns._node_id(value)
            yield from _encode_varint(self.node_length(child_id))
            yield from self._iter_node_bytes(child_id, active)
            return
        if kind in {_COMPONENT_TEXT, _COMPONENT_BYTES, _COMPONENT_ENUM}:
            payload = self._scalar_payload(owner_node_id, index, kind, item=item)
            yield from _encode_varint(payload.nbytes)
            yield from payload
            return
        if kind == _COMPONENT_INTEGER:
            payload = self._scalar_payload(owner_node_id, index, kind, item=item)
            yield from _encode_varint(int.from_bytes(payload, "little"))
            return
        if kind not in {_COMPONENT_SET, _COMPONENT_SEQUENCE} or item:
            raise SnapshotCompatibilityError(
                "encoded subset canonical cursor found an invalid component kind"
            )
        yield from _encode_varint(length)
        for item_index in range(value, value + length):
            if kind == _COMPONENT_SET:
                child_id = self.columns._item_node(item_index)
                yield from _encode_varint(self.node_length(child_id))
                yield from self._iter_node_bytes(child_id, active)
            else:
                yield from self._iter_component_bytes(
                    None,
                    item_index,
                    active,
                    item=True,
                )


@dataclass(slots=True)
class _CanonicalComparator:
    bytes_compared: int = 0

    def compare_nodes(self, left: _EncodedNodeRef, right: _EncodedNodeRef) -> int:
        if left.cursor is right.cursor and left.node_id == right.node_id:
            return 0
        left_bytes = left.cursor.iter_node_bytes(left.node_id)
        right_bytes = right.cursor.iter_node_bytes(right.node_id)
        while True:
            try:
                left_byte = next(left_bytes)
                left_done = False
            except StopIteration:
                left_byte = -1
                left_done = True
            try:
                right_byte = next(right_bytes)
                right_done = False
            except StopIteration:
                right_byte = -1
                right_done = True
            if left_done or right_done:
                if left_done and right_done:
                    return 0
                return -1 if left_done else 1
            self.bytes_compared += 1
            if left_byte != right_byte:
                return -1 if left_byte < right_byte else 1

    def compare_roots(self, left: _EncodedRootRef, right: _EncodedRootRef) -> int:
        if left.root_kind != right.root_kind:
            return -1 if left.root_kind < right.root_kind else 1
        return self.compare_nodes(
            _EncodedNodeRef(left.columns, left.cursor, left.node_id),
            _EncodedNodeRef(right.columns, right.cursor, right.node_id),
        )


def _root_group(
    columns: _EncodedColumns,
    cursor: _CanonicalCursor,
) -> tuple[_EncodedRootRef, ...]:
    return tuple(
        _EncodedRootRef(columns, cursor, root_index) for root_index in columns.iter_root_indices()
    )


def _merge_root_groups(
    groups: tuple[tuple[_EncodedRootRef, ...], ...],
    comparator: _CanonicalComparator,
) -> tuple[tuple[_EncodedRootRef, ...], int]:
    """Merge canonical root cursors and structurally deduplicate equal roots."""

    for group in groups:
        for index in range(1, len(group)):
            if comparator.compare_roots(group[index - 1], group[index]) >= 0:
                raise SnapshotCompatibilityError(
                    "encoded subset canonical root group is not strictly sorted and unique"
                )
    positions = [0] * len(groups)
    merged: list[_EncodedRootRef] = []
    deduplicated = 0
    while True:
        active = [index for index, group in enumerate(groups) if positions[index] < len(group)]
        if not active:
            return tuple(merged), deduplicated
        selected = active[0]
        for candidate in active[1:]:
            if (
                comparator.compare_roots(
                    groups[candidate][positions[candidate]],
                    groups[selected][positions[selected]],
                )
                < 0
            ):
                selected = candidate
        selected_root = groups[selected][positions[selected]]
        merged.append(selected_root)
        for candidate in active:
            if candidate == selected:
                positions[candidate] += 1
                continue
            if (
                comparator.compare_roots(
                    groups[candidate][positions[candidate]],
                    selected_root,
                )
                == 0
            ):
                positions[candidate] += 1
                deduplicated += 1


def _merge_node_groups(
    groups: tuple[tuple[_EncodedNodeRef, ...], ...],
    comparator: _CanonicalComparator,
) -> tuple[tuple[tuple[_EncodedNodeRef, ...], str], ...]:
    """Assign one scalar-compatible blank ID to each structural identity."""

    for group in groups:
        for index in range(1, len(group)):
            if comparator.compare_nodes(group[index - 1], group[index]) >= 0:
                raise SnapshotCompatibilityError(
                    "encoded subset canonical node group is not strictly sorted and unique"
                )
    positions = [0] * len(groups)
    merged: list[tuple[tuple[_EncodedNodeRef, ...], str]] = []
    next_identifier = 2_147_483_648
    while True:
        active = [index for index, group in enumerate(groups) if positions[index] < len(group)]
        if not active:
            return tuple(merged)
        selected = active[0]
        for candidate in active[1:]:
            if (
                comparator.compare_nodes(
                    groups[candidate][positions[candidate]],
                    groups[selected][positions[selected]],
                )
                < 0
            ):
                selected = candidate
        selected_node = groups[selected][positions[selected]]
        identities = [selected_node]
        positions[selected] += 1
        for candidate in active:
            if candidate == selected:
                continue
            candidate_node = groups[candidate][positions[candidate]]
            if comparator.compare_nodes(candidate_node, selected_node) == 0:
                positions[candidate] += 1
                identities.append(candidate_node)
        merged.append(
            (
                tuple(identities),
                f"_:genid{next_identifier}",
            )
        )
        next_identifier += 1


def _borrowed_segment_bytes(segment: object, name: str, width: int) -> memoryview:
    try:
        value = getattr(segment, name)
    except Exception as error:
        raise SnapshotCompatibilityError(
            "encoded subset segment metadata is not readable",
            details={"buffer": name},
        ) from error
    if type(value) is not memoryview or not value.readonly:
        raise SnapshotCompatibilityError(
            "encoded subset segment buffer is not a readonly memoryview",
            details={"buffer": name},
        )
    if (
        value.format != "B"
        or value.ndim != 1
        or value.itemsize != 1
        or not value.c_contiguous
        or value.shape != (value.nbytes,)
        or value.strides != (1,)
        or value.nbytes % width
    ):
        raise SnapshotCompatibilityError(
            "encoded subset segment buffer has an invalid fixed-width layout",
            details={"buffer": name, "width": width},
        )
    return value


def _validated_segment(segment: object) -> _ValidatedSegment:
    try:
        typed_segment = cast(_SegmentLike, segment)
        role = typed_segment.role
        source = typed_segment.source
        owner = typed_segment.owner
        posting_mode = typed_segment.posting_mode
        member_token = typed_segment.member_token
    except Exception as error:
        raise SnapshotCompatibilityError(
            "encoded subset segment metadata is not readable"
        ) from error
    if type(role) is not int or role not in {
        _SEGMENT_DIRECT,
        _SEGMENT_OVERLAY_BASE,
        _SEGMENT_OVERLAY_DELTA,
        _SEGMENT_COMPOSITE_MEMBER,
        _SEGMENT_COMPOSITE_BRIDGE,
    }:
        raise SnapshotCompatibilityError("encoded subset segment role is invalid")
    if type(posting_mode) is not int or posting_mode not in {
        _POSTINGS_ALL,
        _POSTINGS_INCLUDE,
        _POSTINGS_EXCLUDE,
    }:
        raise SnapshotCompatibilityError("encoded subset segment posting mode is invalid")
    postings = _borrowed_segment_bytes(segment, "root_ids", 4)
    raw_scope_map = _borrowed_segment_bytes(segment, "anonymous_scope_map", 64)
    scope_map: dict[bytes, bytes] = {}
    previous_scope: bytes | None = None
    for offset in range(0, raw_scope_map.nbytes, 64):
        source_scope = bytes(raw_scope_map[offset : offset + 32])
        target_scope = bytes(raw_scope_map[offset + 32 : offset + 64])
        if source_scope == target_scope:
            raise SnapshotCompatibilityError(
                "encoded subset anonymous scope map contains an identity row"
            )
        if previous_scope is not None and source_scope <= previous_scope:
            raise SnapshotCompatibilityError(
                "encoded subset anonymous scope-map sources are not sorted unique"
            )
        scope_map[source_scope] = target_scope
        previous_scope = source_scope
    return _ValidatedSegment(
        role,
        owner,
        source,
        posting_mode,
        postings,
        MappingProxyType(scope_map),
        raw_scope_map.nbytes // 64,
        member_token,
    )


def _register_resolution_lease(
    state: _SegmentResolutionState,
    lease: EncodedStructuralLease,
    *,
    referenced: bool,
) -> None:
    identity = id(lease.encoded_view)
    retained = state.leases.get(identity)
    if retained is not None:
        if retained.encoded_view is not lease.encoded_view:  # pragma: no cover - retained id
            raise SnapshotCompatibilityError("encoded subset segment resolution identity changed")
        return
    columns = _EncodedColumns(lease)
    state.leases[identity] = lease
    state.inspections[identity] = columns.inspect(classify_roots=False)
    if referenced:
        state.source_roots_inspected += columns.root_count


def _reference_segment_lease(
    state: _SegmentResolutionState,
    current_lease: EncodedStructuralLease,
    segment: _ValidatedSegment,
) -> EncodedStructuralLease:
    source = segment.source
    if source is current_lease.encoded_view:
        raise SnapshotCompatibilityError("encoded subset segment graph contains a direct cycle")
    if source is None or segment.owner is not getattr(source, "owner", None):
        raise SnapshotCompatibilityError(
            "encoded subset referenced segment does not retain its source owner"
        )
    state.referenced_segments += 1
    retained = state.leases.get(id(source))
    if retained is not None and retained.encoded_view is source:
        return retained
    source_lease = _validate_encoded_view(
        segment.owner,
        source,
        type(state.top_lease.encoded_view),
        state.top_lease.scope,
    )
    _register_resolution_lease(state, source_lease, referenced=True)
    return source_lease


def _posting_indices(
    postings: memoryview,
    posting_mode: int,
    root_count: int,
) -> frozenset[int]:
    if posting_mode == _POSTINGS_ALL:
        if postings.nbytes:
            raise SnapshotCompatibilityError("encoded subset ALL segment requires empty postings")
        return frozenset()
    if posting_mode not in {_POSTINGS_INCLUDE, _POSTINGS_EXCLUDE}:
        raise SnapshotCompatibilityError("encoded subset posting mode is invalid")
    if not postings.nbytes:
        raise SnapshotCompatibilityError("encoded subset INCLUDE/EXCLUDE segment requires postings")
    selected: set[int] = set()
    previous_root_id = 0
    for offset in range(0, postings.nbytes, 4):
        root_id = int.from_bytes(postings[offset : offset + 4], "little")
        if root_id <= previous_root_id or root_id > root_count:
            raise SnapshotCompatibilityError(
                "encoded subset postings are not sorted unique source-local references"
            )
        selected.add(root_id - 1)
        previous_root_id = root_id
    return frozenset(selected)


def _apply_resolved_postings(
    groups: tuple[_ResolvedColumnGroup, ...],
    source_lease: EncodedStructuralLease,
    posting_mode: int,
    postings: memoryview,
) -> tuple[_ResolvedColumnGroup, ...]:
    selected = _posting_indices(
        postings,
        posting_mode,
        source_lease.buffers["root_kinds"].nbytes,
    )
    if posting_mode == _POSTINGS_ALL:
        return groups
    result: list[_ResolvedColumnGroup] = []
    for group in groups:
        is_source_local = group.lease.encoded_view is source_lease.encoded_view
        if posting_mode == _POSTINGS_INCLUDE and not is_source_local:
            continue
        if not is_source_local:
            result.append(group)
            continue
        if posting_mode == _POSTINGS_INCLUDE:
            root_indices = tuple(index for index in group.root_indices if index in selected)
        else:
            root_indices = tuple(index for index in group.root_indices if index not in selected)
        if root_indices:
            result.append(_ResolvedColumnGroup(group.lease, root_indices, group.scope_map))
    return tuple(result)


def _compose_group_scope_map(
    group: _ResolvedColumnGroup,
    scope_map: Mapping[bytes, bytes],
    state: _SegmentResolutionState,
) -> _ResolvedColumnGroup:
    if not scope_map:
        return group
    columns = _EncodedColumns(group.lease, root_indices=group.root_indices)
    reachable = columns.reachable_node_ids()
    composed: dict[bytes, bytes] = {}
    for original_scope in columns.anonymous_scopes(reachable):
        current_scope = group.scope_map.get(original_scope, original_scope)
        target_scope = scope_map.get(current_scope, current_scope)
        if target_scope != original_scope:
            composed[original_scope] = target_scope
    frozen: Mapping[bytes, bytes] = MappingProxyType(composed)
    if not _scope_remap_preserves_order(columns, reachable, frozen):
        state.fallback(
            "encoded recursive segment anonymous scope remap does not preserve canonical order"
        )
    return _ResolvedColumnGroup(group.lease, group.root_indices, frozen)


def _apply_group_scope_map(
    groups: tuple[_ResolvedColumnGroup, ...],
    scope_map: Mapping[bytes, bytes],
    state: _SegmentResolutionState,
) -> tuple[_ResolvedColumnGroup, ...]:
    return tuple(_compose_group_scope_map(group, scope_map, state) for group in groups)


def _local_resolved_group(lease: EncodedStructuralLease) -> _ResolvedColumnGroup:
    root_count = lease.buffers["root_kinds"].nbytes
    return _ResolvedColumnGroup(
        lease,
        tuple(range(root_count)),
        MappingProxyType({}),
    )


def _resolve_segment_groups(
    lease: EncodedStructuralLease,
    state: _SegmentResolutionState,
) -> tuple[_ResolvedColumnGroup, ...]:
    """Resolve segment occurrences while retaining their source-local table identity."""

    identity = id(lease.encoded_view)
    if identity in state.active:
        raise SnapshotCompatibilityError("encoded subset segment graph is cyclic")
    cached = state.cache.get(identity)
    if cached is not None:
        return cached
    _register_resolution_lease(
        state,
        lease,
        referenced=lease.encoded_view is not state.top_lease.encoded_view,
    )
    state.active.add(identity)
    try:
        raw_segments = lease.segments
        if type(raw_segments) is not tuple or not raw_segments:
            raise SnapshotCompatibilityError(
                "encoded subset segment manifest is not a nonempty exact tuple"
            )
        segments = tuple(_validated_segment(segment) for segment in raw_segments)
        state.posting_rows_inspected += sum(segment.postings.nbytes // 4 for segment in segments)
        state.scope_map_rows_inspected += sum(segment.scope_map_rows for segment in segments)
        roles = tuple(segment.role for segment in segments)
        local = _local_resolved_group(lease)
        resolved: list[_ResolvedColumnGroup] = []

        if roles == (_SEGMENT_DIRECT,):
            segment = segments[0]
            if (
                segment.owner is not lease.owner
                or segment.source is not None
                or segment.posting_mode != _POSTINGS_ALL
                or segment.postings.nbytes
                or segment.scope_map
                or segment.member_token is not None
            ):
                raise SnapshotCompatibilityError(
                    "encoded subset direct segment metadata is not canonical"
                )
            if local.root_indices:
                resolved.append(local)
        elif roles in {
            (_SEGMENT_OVERLAY_BASE,),
            (_SEGMENT_OVERLAY_BASE, _SEGMENT_OVERLAY_DELTA),
        }:
            base = segments[0]
            if (
                base.source is None
                or base.owner is not getattr(base.source, "owner", None)
                or base.posting_mode not in {_POSTINGS_ALL, _POSTINGS_EXCLUDE}
                or base.member_token is not None
            ):
                raise SnapshotCompatibilityError(
                    "encoded subset overlay base segment metadata is invalid"
                )
            source_lease = _reference_segment_lease(state, lease, base)
            source_groups = _resolve_segment_groups(source_lease, state)
            source_groups = _apply_group_scope_map(
                source_groups,
                base.scope_map,
                state,
            )
            resolved.extend(
                _apply_resolved_postings(
                    source_groups,
                    source_lease,
                    base.posting_mode,
                    base.postings,
                )
            )
            if len(segments) == 1:
                _validate_empty_local_columns(lease, family="overlay without delta")
            else:
                delta = segments[1]
                if (
                    delta.owner is not lease.owner
                    or delta.source is not None
                    or delta.posting_mode != _POSTINGS_ALL
                    or delta.postings.nbytes
                    or delta.scope_map
                    or delta.member_token is not None
                    or not local.root_indices
                ):
                    raise SnapshotCompatibilityError(
                        "encoded subset overlay delta segment metadata is invalid"
                    )
                state.delta_roots_inspected += len(local.root_indices)
                resolved.append(local)
        else:
            member_count = roles.count(_SEGMENT_COMPOSITE_MEMBER)
            bridge_count = roles.count(_SEGMENT_COMPOSITE_BRIDGE)
            expected = (_SEGMENT_COMPOSITE_MEMBER,) * member_count + (
                (_SEGMENT_COMPOSITE_BRIDGE,) if bridge_count else ()
            )
            if member_count < 2 or bridge_count > 1 or roles != expected:
                raise SnapshotCompatibilityError(
                    "encoded subset composite segment roles are invalid"
                )
            previous_token: bytes | None = None
            for member in segments[:member_count]:
                token = member.member_token
                if (
                    member.source is None
                    or member.owner is not getattr(member.source, "owner", None)
                    or member.posting_mode
                    not in {_POSTINGS_ALL, _POSTINGS_INCLUDE, _POSTINGS_EXCLUDE}
                    or type(token) is not bytes
                    or len(token) != 32
                ):
                    raise SnapshotCompatibilityError(
                        "encoded subset composite member metadata is invalid"
                    )
                if previous_token is not None and token <= previous_token:
                    raise SnapshotCompatibilityError(
                        "encoded subset composite member tokens are not sorted unique"
                    )
                previous_token = token
                state.composite_member_segments += 1
                source_lease = _reference_segment_lease(state, lease, member)
                member_groups = _resolve_segment_groups(source_lease, state)
                member_groups = _apply_group_scope_map(
                    member_groups,
                    member.scope_map,
                    state,
                )
                resolved.extend(
                    _apply_resolved_postings(
                        member_groups,
                        source_lease,
                        member.posting_mode,
                        member.postings,
                    )
                )
            if bridge_count:
                bridge = segments[-1]
                if (
                    bridge.owner is not lease.owner
                    or bridge.source is not None
                    or bridge.posting_mode != _POSTINGS_ALL
                    or bridge.postings.nbytes
                    or bridge.scope_map
                    or bridge.member_token is not None
                    or not local.root_indices
                ):
                    raise SnapshotCompatibilityError(
                        "encoded subset composite bridge metadata is invalid"
                    )
                state.bridge_roots_inspected += len(local.root_indices)
                resolved.append(local)
            else:
                _validate_empty_local_columns(lease, family="composite without bridge")

        result = tuple(resolved)
        state.cache[identity] = result
        return result
    finally:
        state.active.remove(identity)


def _validate_empty_local_columns(
    lease: EncodedStructuralLease,
    *,
    family: str,
) -> None:
    if set(lease.buffers) != set(ENCODED_BUFFER_WIDTHS):
        raise SnapshotCompatibilityError(
            f"encoded subset {family} local buffer set differs from schema 1"
        )
    for name, width in ENCODED_BUFFER_WIDTHS.items():
        value = lease.buffers[name]
        if type(value) is not memoryview or not value.readonly:
            raise SnapshotCompatibilityError(
                f"encoded subset {family} local buffer is not readonly",
                details={"buffer": name},
            )
        if (
            value.format != "B"
            or value.ndim != 1
            or value.itemsize != 1
            or not value.c_contiguous
            or value.shape != (value.nbytes,)
            or value.strides != (1,)
            or value.nbytes % width
        ):
            raise SnapshotCompatibilityError(
                f"encoded subset {family} local buffer layout is invalid",
                details={"buffer": name},
            )
        expected = 8 if name == "node_field_offsets" else 0
        if value.nbytes != expected or (expected and bytes(value) != b"\x00" * expected):
            raise SnapshotCompatibilityError(
                f"encoded subset {family} has nonempty local columns",
                details={"buffer": name},
            )


def _validate_overlay_delta_segment(
    lease: EncodedStructuralLease,
    segment: object,
) -> None:
    try:
        typed_segment = cast(_SegmentLike, segment)
        role = typed_segment.role
        source = typed_segment.source
        owner = typed_segment.owner
        posting_mode = typed_segment.posting_mode
        member_token = typed_segment.member_token
    except Exception as error:
        raise SnapshotCompatibilityError(
            "encoded subset overlay delta metadata is not readable"
        ) from error
    if (
        type(role) is not int
        or role != _SEGMENT_OVERLAY_DELTA
        or owner is not lease.owner
        or source is not None
        or type(posting_mode) is not int
        or posting_mode != _POSTINGS_ALL
        or member_token is not None
    ):
        raise SnapshotCompatibilityError("encoded subset overlay delta metadata is invalid")
    postings = _borrowed_segment_bytes(segment, "root_ids", 4)
    scope_map = _borrowed_segment_bytes(segment, "anonymous_scope_map", 64)
    if postings.nbytes or scope_map.nbytes:
        raise SnapshotCompatibilityError(
            "encoded subset overlay delta has unexpected postings or scope mappings"
        )
    if not lease.buffers["root_kinds"].nbytes:
        raise SnapshotCompatibilityError("encoded subset overlay delta has no local roots")


def _overlay_base_source(
    lease: EncodedStructuralLease,
    segment: object,
) -> tuple[
    EncodedStructuralLease,
    tuple[int, ...],
    Mapping[bytes, bytes],
    int,
    int,
]:
    """Resolve a direct overlay base or carry its validated segmented source."""

    try:
        typed_segment = cast(_SegmentLike, segment)
        role = typed_segment.role
        source = typed_segment.source
        owner = typed_segment.owner
        posting_mode = typed_segment.posting_mode
        member_token = typed_segment.member_token
    except Exception as error:
        raise SnapshotCompatibilityError(
            "encoded subset overlay base metadata is not readable"
        ) from error
    if type(role) is not int or role != _SEGMENT_OVERLAY_BASE:
        raise SnapshotCompatibilityError("encoded subset overlay base role is invalid")
    if source is lease.encoded_view:
        raise SnapshotCompatibilityError(
            "encoded subset overlay segment graph contains a direct cycle"
        )
    if source is None or owner is not getattr(source, "owner", None):
        raise SnapshotCompatibilityError(
            "encoded subset overlay base does not retain its referenced owner"
        )
    if type(posting_mode) is not int or posting_mode not in {
        _POSTINGS_ALL,
        _POSTINGS_EXCLUDE,
    }:
        raise SnapshotCompatibilityError("encoded subset overlay base posting mode is invalid")
    if member_token is not None:
        raise SnapshotCompatibilityError(
            "encoded subset overlay base unexpectedly has a member token"
        )

    postings = _borrowed_segment_bytes(segment, "root_ids", 4)
    raw_scope_map = _borrowed_segment_bytes(segment, "anonymous_scope_map", 64)
    if posting_mode == _POSTINGS_ALL and postings.nbytes:
        raise SnapshotCompatibilityError("encoded subset ALL overlay base requires empty postings")
    if posting_mode == _POSTINGS_EXCLUDE and not postings.nbytes:
        raise SnapshotCompatibilityError("encoded subset EXCLUDE overlay base requires postings")

    scope_map: dict[bytes, bytes] = {}
    previous_scope: bytes | None = None
    for offset in range(0, raw_scope_map.nbytes, 64):
        source_scope = bytes(raw_scope_map[offset : offset + 32])
        target_scope = bytes(raw_scope_map[offset + 32 : offset + 64])
        if source_scope == target_scope:
            raise SnapshotCompatibilityError(
                "encoded subset anonymous scope map contains an identity row"
            )
        if previous_scope is not None and source_scope <= previous_scope:
            raise SnapshotCompatibilityError(
                "encoded subset anonymous scope-map sources are not sorted unique"
            )
        scope_map[source_scope] = target_scope
        previous_scope = source_scope

    source_lease = _validate_encoded_view(
        owner,
        source,
        type(lease.encoded_view),
        lease.scope,
    )
    source_segments = source_lease.segments
    if (
        type(source_segments) is not tuple
        or len(source_segments) != 1
        or getattr(source_segments[0], "role", None) != _SEGMENT_DIRECT
    ):
        raise _ReferencedSegmentFallback(
            "encoded compiler overlay slice requires recursive segment resolution",
            source_lease,
        )
    root_count = source_lease.buffers["root_kinds"].nbytes
    excluded: set[int] = set()
    previous_root_id = 0
    for offset in range(0, postings.nbytes, 4):
        root_id = int.from_bytes(postings[offset : offset + 4], "little")
        if root_id <= previous_root_id or root_id > root_count:
            raise SnapshotCompatibilityError(
                "encoded subset overlay postings are not sorted unique in-range references"
            )
        excluded.add(root_id - 1)
        previous_root_id = root_id
    selected = tuple(index for index in range(root_count) if index not in excluded)
    return (
        source_lease,
        selected,
        MappingProxyType(scope_map),
        postings.nbytes // 4,
        raw_scope_map.nbytes // 64,
    )


class _ReferencedSegmentFallback(Exception):
    """Internal signal carrying a validated source into recursive resolution."""

    def __init__(self, reason: str, source_lease: EncodedStructuralLease) -> None:
        super().__init__(reason)
        self.source_lease = source_lease


def _scope_remap_preserves_order(
    columns: _EncodedColumns,
    reachable: frozenset[int],
    scope_map: Mapping[bytes, bytes],
) -> bool:
    previous: bytes | None = None
    for source_scope in columns.anonymous_scopes(reachable):
        target_scope = scope_map.get(source_scope, source_scope)
        if previous is not None and target_scope <= previous:
            return False
        previous = target_scope
    return True


def _merge_unclassified_inspections(
    inspections: tuple[_Inspection, ...],
) -> _Inspection:
    counters = _MutableCounters(
        roots_inspected=sum(item.counters.roots_inspected for item in inspections),
        nodes_inspected=sum(item.counters.nodes_inspected for item in inspections),
        anonymous_individuals=sum(item.counters.anonymous_individuals for item in inspections),
        literal_nodes=sum(item.counters.literal_nodes for item in inspections),
        annotation_nodes=sum(item.counters.annotation_nodes for item in inspections),
        scalar_bytes_checked=sum(item.counters.scalar_bytes_checked for item in inspections),
    )
    result = _Inspection(counters)
    for inspection in inspections:
        if inspection.fallback_reason is not None:
            result.fallback(inspection.fallback_reason)
    return result


def _domain_range_index(
    roots: tuple[_EncodedRootRef, ...],
) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    domains: dict[str, list[str]] = {}
    ranges: dict[str, list[str]] = {}
    for root in roots:
        tag = root.columns.node_tag(root.node_id)
        if tag == _TAG_OBJECT_PROPERTY_DOMAIN:
            property_iri, class_iri = root.columns.domain_iris(root.node_id)
            domains.setdefault(property_iri, []).append(class_iri)
        elif tag == _TAG_OBJECT_PROPERTY_RANGE:
            property_iri, class_iri = root.columns.range_iris(root.node_id)
            ranges.setdefault(property_iri, []).append(class_iri)
    return (
        {key: tuple(values) for key, values in domains.items()},
        {key: tuple(values) for key, values in ranges.items()},
    )


def _role_axioms(roots: tuple[_EncodedRootRef, ...]) -> tuple[_EncodedRoleAxiom, ...]:
    rows: list[_EncodedRoleAxiom] = []
    for canonical_order, root in enumerate(roots):
        tag = root.columns.node_tag(root.node_id)
        if tag not in {_TAG_SUB_OBJECT_PROPERTY_OF, _TAG_INVERSE_OBJECT_PROPERTIES}:
            continue
        first, second = root.columns._property_pair_iris(root.node_id, tag)
        first_hash = _combine(4153, _owlapi_iri_hash(first))
        second_hash = _combine(4153, _owlapi_iri_hash(second))
        owlapi_hash = (
            _combine(1823, first_hash, second_hash, 0)
            if tag == _TAG_SUB_OBJECT_PROPERTY_OF
            else _combine(1229, _int32(first_hash + second_hash), 0)
        )
        rows.append(
            _EncodedRoleAxiom(
                tag,
                root.node_id,
                first,
                second,
                owlapi_hash,
                canonical_order,
            )
        )
    capacity = 16
    while len(rows) > int(capacity * 0.75):
        capacity *= 2

    def order_key(row: _EncodedRoleAxiom) -> tuple[int, int, int]:
        unsigned = row.owlapi_hash & 0xFFFFFFFF
        spread = unsigned ^ (unsigned >> 16)
        return spread & (capacity - 1), spread, row.canonical_order

    rows.sort(key=order_key)
    return tuple(rows)


def _anonymous_id_maps(
    groups: tuple[
        tuple[_EncodedColumns, _CanonicalCursor, frozenset[int]],
        ...,
    ],
    comparator: _CanonicalComparator,
) -> dict[_CanonicalCursor, dict[int, str]]:
    node_groups: list[tuple[_EncodedNodeRef, ...]] = []
    result: dict[_CanonicalCursor, dict[int, str]] = {}
    for columns, cursor, reachable in groups:
        result[cursor] = {}
        node_groups.append(
            tuple(
                _EncodedNodeRef(columns, cursor, node_id)
                for node_id in sorted(reachable)
                if columns.node_tag(node_id) == _TAG_ANONYMOUS_INDIVIDUAL
            )
        )
    for identities, blank_id in _merge_node_groups(tuple(node_groups), comparator):
        for identity in identities:
            result[identity.cursor][identity.node_id] = blank_id
    return result


def _class_iris(
    groups: tuple[tuple[_EncodedColumns, frozenset[int]], ...],
) -> frozenset[str]:
    result: set[str] = set()
    for columns, reachable in groups:
        result.update(columns.class_iris(reachable))
    return frozenset(result)


def prepare_encoded_subset_compilation(
    view: object,
    options: ProjectionOptions,
    ingestion: EncodedNegotiation,
    *,
    batch_edges: int,
    asserted_taxonomy_only: bool = False,
    role_state: RoleState | None = None,
) -> tuple[
    EncodedSubsetCompilation | None,
    EncodedNegotiation,
    EncodedSubsetCounters | None,
]:
    """Prepare the exact slice or select scalar-native before any edge output."""

    if ingestion.path != "encoded-native":
        return None, ingestion, None
    lease = ingestion.lease
    if lease is None:  # pragma: no cover - guarded by EncodedNegotiation
        raise SnapshotCompatibilityError("encoded-native ingestion lost its validated lease")
    if batch_edges < 1:
        raise ValueError("batch_edges must be positive")
    if type(asserted_taxonomy_only) is not bool:
        raise TypeError("asserted_taxonomy_only must be bool")

    if lease.owner is not view:
        raise SnapshotCompatibilityError(
            "encoded subset lease does not retain the exact compiled view"
        )
    segments = lease.segments
    if type(segments) is not tuple or not segments:
        raise SnapshotCompatibilityError(
            "encoded subset segment manifest is not a nonempty exact tuple"
        )
    try:
        segment_roles = tuple(cast(_SegmentLike, segment).role for segment in segments)
    except Exception as error:
        raise SnapshotCompatibilityError("encoded subset segment roles are not readable") from error
    first_role = segment_roles[0]
    if type(first_role) is not int or first_role not in {1, 2, 3, 4, 5}:
        raise SnapshotCompatibilityError("encoded subset segment role is invalid")

    empty_scope_map: Mapping[bytes, bytes] = MappingProxyType({})
    comparator = _CanonicalComparator()
    retained_leases: tuple[EncodedStructuralLease, ...] = ()
    is_direct = len(segments) == 1 and first_role == _SEGMENT_DIRECT
    is_overlay_base = first_role == _SEGMENT_OVERLAY_BASE and len(segments) in {1, 2}
    has_overlay_delta = is_overlay_base and len(segments) == 2
    is_composite = any(
        role in {_SEGMENT_COMPOSITE_MEMBER, _SEGMENT_COMPOSITE_BRIDGE} for role in segment_roles
    )
    overlay_source: (
        tuple[
            EncodedStructuralLease,
            tuple[int, ...],
            Mapping[bytes, bytes],
            int,
            int,
        ]
        | None
    ) = None
    recursive_source: EncodedStructuralLease | None = None
    recursively_segmented = is_composite
    column_groups: tuple[
        tuple[_EncodedColumns, _CanonicalCursor, frozenset[int]],
        ...,
    ]

    if is_overlay_base:
        if has_overlay_delta:
            second_role = segment_roles[1]
            if type(second_role) is not int or second_role != _SEGMENT_OVERLAY_DELTA:
                raise SnapshotCompatibilityError("encoded subset overlay delta role is invalid")
            _validate_overlay_delta_segment(lease, segments[1])
        else:
            _validate_empty_local_columns(lease, family="overlay without delta")
        try:
            overlay_source = _overlay_base_source(lease, segments[0])
        except _ReferencedSegmentFallback as fallback:
            recursive_source = fallback.source_lease
            recursively_segmented = True

    if overlay_source is not None:
        (
            source_lease,
            selected_roots,
            scope_map,
            posting_rows,
            scope_map_rows,
        ) = overlay_source
        source_columns = _EncodedColumns(source_lease, root_indices=selected_roots)
        source_cursor = _CanonicalCursor(source_columns, scope_map)
        source_reachable = source_columns.reachable_node_ids()
        source_inspection = source_columns.inspect(classify_roots=False)
        retained_leases = (source_lease,)
        root_groups: tuple[tuple[_EncodedRootRef, ...], ...] = (
            _root_group(source_columns, source_cursor),
        )
        inspections: tuple[_Inspection, ...] = (source_inspection,)
        column_groups = ((source_columns, source_cursor, source_reachable),)
        delta_root_count = 0
        if has_overlay_delta:
            delta_columns = _EncodedColumns(lease)
            delta_cursor = _CanonicalCursor(delta_columns, empty_scope_map)
            delta_reachable = delta_columns.reachable_node_ids()
            delta_inspection = delta_columns.inspect(classify_roots=False)
            root_groups = (*root_groups, _root_group(delta_columns, delta_cursor))
            inspections = (*inspections, delta_inspection)
            column_groups = (
                *column_groups,
                (delta_columns, delta_cursor, delta_reachable),
            )
            delta_root_count = delta_columns.root_count
        inspection = _merge_unclassified_inspections(inspections)
        if not _scope_remap_preserves_order(
            source_columns,
            source_reachable,
            scope_map,
        ):
            counters = inspection.counters
            counters.referenced_segments = 1
            counters.posting_rows_inspected = posting_rows
            counters.scope_map_rows_inspected = scope_map_rows
            counters.source_roots_inspected = source_columns.root_count
            counters.delta_roots_inspected = delta_root_count
            counters.selected_roots = source_columns.selected_root_count + delta_root_count
            counters.scalar_fallbacks = 1
            reason = (
                "encoded overlay anonymous scope remap does not preserve canonical order; "
                "selected whole-operation scalar compiler"
            )
            return None, EncodedNegotiation("scalar-native", reason), counters.freeze()

        roots, deduplicated_roots = _merge_root_groups(root_groups, comparator)
        for root in roots:
            root.columns.inspect_root(root.root_index, inspection)
        counters = inspection.counters
        counters.referenced_segments = 1
        counters.posting_rows_inspected = posting_rows
        counters.scope_map_rows_inspected = scope_map_rows
        counters.source_roots_inspected = source_columns.root_count
        counters.delta_roots_inspected = delta_root_count
        counters.selected_roots = len(roots)
        counters.deduplicated_roots = deduplicated_roots
        counters.canonical_bytes_compared = comparator.bytes_compared
    elif recursively_segmented:
        resolution = _SegmentResolutionState(lease)
        if recursive_source is not None:
            _register_resolution_lease(
                resolution,
                recursive_source,
                referenced=True,
            )
        try:
            resolved_groups = _resolve_segment_groups(lease, resolution)
        except RecursionError as error:
            raise SnapshotCompatibilityError(
                "encoded subset segment graph exceeds the safe recursion depth"
            ) from error
        inspection = _merge_unclassified_inspections(tuple(resolution.inspections.values()))
        counters = inspection.counters
        counters.referenced_segments = resolution.referenced_segments
        counters.posting_rows_inspected = resolution.posting_rows_inspected
        counters.scope_map_rows_inspected = resolution.scope_map_rows_inspected
        counters.source_roots_inspected = resolution.source_roots_inspected
        counters.delta_roots_inspected = resolution.delta_roots_inspected
        counters.composite_member_segments = resolution.composite_member_segments
        counters.bridge_roots_inspected = resolution.bridge_roots_inspected
        retained_leases = tuple(
            retained
            for retained in resolution.leases.values()
            if retained.encoded_view is not lease.encoded_view
        )
        if resolution.fallback_reason is not None:
            counters.selected_roots = sum(len(group.root_indices) for group in resolved_groups)
            counters.scalar_fallbacks = 1
            reason = f"{resolution.fallback_reason}; selected whole-operation scalar compiler"
            return None, EncodedNegotiation("scalar-native", reason), counters.freeze()

        prepared_root_groups: list[tuple[_EncodedRootRef, ...]] = []
        prepared_column_groups: list[tuple[_EncodedColumns, _CanonicalCursor, frozenset[int]]] = []
        for group in resolved_groups:
            columns = _EncodedColumns(group.lease, root_indices=group.root_indices)
            cursor = _CanonicalCursor(columns, group.scope_map)
            reachable = columns.reachable_node_ids()
            prepared_root_groups.append(_root_group(columns, cursor))
            prepared_column_groups.append((columns, cursor, reachable))
        root_groups = tuple(prepared_root_groups)
        column_groups = tuple(prepared_column_groups)
        roots, deduplicated_roots = _merge_root_groups(root_groups, comparator)
        for root in roots:
            root.columns.inspect_root(root.root_index, inspection)
        counters.selected_roots = len(roots)
        counters.deduplicated_roots = deduplicated_roots
        counters.canonical_bytes_compared = comparator.bytes_compared
    elif is_direct:
        columns = _EncodedColumns(lease)
        cursor = _CanonicalCursor(columns, empty_scope_map)
        reachable = columns.reachable_node_ids()
        inspection = columns.inspect(classify_roots=False)
        roots = _root_group(columns, cursor)
        for root in roots:
            columns.inspect_root(root.root_index, inspection)
        counters = inspection.counters
        column_groups = ((columns, cursor, reachable),)
    else:
        columns = _EncodedColumns(lease)
        columns.inspect()
        raise SnapshotCompatibilityError(
            "encoded subset segment manifest is outside the canonical direct, overlay, "
            "and composite families"
        )

    if inspection.fallback_reason is not None:
        counters.scalar_fallbacks = 1
        reason = f"{inspection.fallback_reason}; selected whole-operation scalar compiler"
        return None, EncodedNegotiation("scalar-native", reason), counters.freeze()
    if (
        counters.annotation_assertion_axioms
        and options.include_literals
        and not asserted_taxonomy_only
        and not _single_document_closure(view)
    ):
        counters.scalar_fallbacks = 1
        reason = (
            "encoded subset cannot prove root-only annotation provenance for a "
            "multi-document closure; selected whole-operation scalar compiler"
        )
        return None, EncodedNegotiation("scalar-native", reason), counters.freeze()

    domains, ranges = ({}, {}) if asserted_taxonomy_only else _domain_range_index(roots)
    role_axioms = () if asserted_taxonomy_only else _role_axioms(roots)
    anonymous_ids = (
        {cursor: {} for _columns, cursor, _reachable in column_groups}
        if asserted_taxonomy_only
        else _anonymous_id_maps(column_groups, comparator)
    )
    class_iris = (
        _class_iris(tuple((columns, reachable) for columns, _cursor, reachable in column_groups))
        if options.include_literals
        and counters.annotation_assertion_axioms
        and not asserted_taxonomy_only
        else frozenset()
    )
    counters.canonical_bytes_compared = comparator.bytes_compared
    ignored_restrictions = (
        counters.restriction_subclass_axioms
        if options.only_taxonomy and not asserted_taxonomy_only
        else 0
    )
    compilation = EncodedSubsetCompilation(
        view=view,
        options=options,
        lease=lease,
        batch_edges=batch_edges,
        asserted_taxonomy_only=asserted_taxonomy_only,
        role_state=RoleState.empty() if role_state is None else role_state,
        _roots=roots,
        _domains=domains,
        _ranges=ranges,
        _role_axioms=role_axioms,
        _anonymous_ids=anonymous_ids,
        _class_iris=class_iris,
        _counters=counters,
        _retained_leases=retained_leases,
        _ignored_restriction_subclasses=ignored_restrictions,
        statistics=CompileStatistics(ignored_shapes=ignored_restrictions),
    )
    return compilation, ingestion, compilation.counters


def _single_document_closure(view: object) -> bool:
    manifest = getattr(view, "import_manifest", None)
    documents = getattr(manifest, "documents", None)
    return type(documents) is tuple and len(documents) == 1


def _owner_limit(owner: object, name: str, default: int) -> int:
    limits = getattr(owner, "limits", None)
    if limits is None:
        limits = getattr(getattr(owner, "load_options", None), "limits", None)
    value = getattr(limits, name, None)
    return value if type(value) is int and value >= 0 else default


__all__ = [
    "EncodedSubsetCompilation",
    "EncodedSubsetCounters",
    "prepare_encoded_subset_compilation",
]
