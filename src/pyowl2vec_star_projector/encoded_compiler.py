"""Bounded encoded-column to projector-edge compiler slice.

The slice is intentionally narrow: one canonical direct segment containing
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

from collections.abc import Iterator
from dataclasses import dataclass, field

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
from .encoded import EncodedNegotiation, EncodedStructuralLease
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

_ROOT_AXIOM = 2
_COMPONENT_NONE = 0
_COMPONENT_NODE = 1
_COMPONENT_TEXT = 2
_COMPONENT_BYTES = 3
_COMPONENT_INTEGER = 4
_COMPONENT_ENUM = 5
_COMPONENT_SET = 6
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


@dataclass(frozen=True, slots=True)
class _EquivalentOperand:
    tag: int
    node_id: int
    first: str
    second: str | None = None


@dataclass(slots=True)
class EncodedSubsetCompilation:
    """Prepared direct-view slice that emits only existing projector ``Edge`` IR."""

    view: object
    options: ProjectionOptions
    lease: EncodedStructuralLease
    batch_edges: int
    asserted_taxonomy_only: bool
    role_state: RoleState
    _columns: _EncodedColumns
    _domains: dict[str, tuple[str, ...]]
    _ranges: dict[str, tuple[str, ...]]
    _role_axioms: tuple[_EncodedRoleAxiom, ...]
    _anonymous_ids: dict[int, str]
    _class_iris: frozenset[str]
    _counters: _MutableCounters
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
        for root_index in range(self._columns.root_count):
            root_id = self._columns.root_id(root_index)
            tag = self._columns.node_tag(root_id)
            if tag == _TAG_SUB_CLASS_OF:
                restriction = self._columns.restriction_subclass_iris(root_id)
                if restriction is None:
                    source, destination = self._columns.subclass_iris(root_id)
                    yield Edge(source, SUBCLASS_OF, destination)
                    if self.options.bidirectional_taxonomy:
                        yield Edge(destination, SUPERCLASS_OF, source)
                elif not self.asserted_taxonomy_only and not self.options.only_taxonomy:
                    yield from self._iter_role_edges(*restriction)
                continue
            if self.asserted_taxonomy_only:
                continue
            if tag == _TAG_EQUIVALENT_CLASSES:
                aggregate = self._columns.equivalent_aggregate(root_id)
                if aggregate is None:
                    source, destination = self._columns.equivalent_iris(root_id)
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
            for root_index in range(self._columns.root_count):
                root_id = self._columns.root_id(root_index)
                if self._columns.node_tag(root_id) != _TAG_ANNOTATION_ASSERTION:
                    continue
                edge, non_string_literal = self._columns.annotation_assertion_edge(
                    root_id,
                    self._class_iris,
                    self._anonymous_ids,
                )
                if edge is None:
                    self.statistics.ignored_shapes += 1
                    self._ignored_annotation_assertions += 1
                    continue
                if non_string_literal:
                    self._non_string_literal_renderings += 1
                yield edge

        for root_index in range(self._columns.root_count):
            root_id = self._columns.root_id(root_index)
            tag = self._columns.node_tag(root_id)
            if tag == _TAG_CLASS_ASSERTION:
                individual, class_iri = self._columns.class_assertion_iris(root_id)
                yield Edge(individual, RDF_TYPE, class_iri)
                continue
            if tag == _TAG_OBJECT_PROPERTY_ASSERTION:
                source, relation, destination = self._columns.object_property_assertion_iris(
                    root_id,
                    self._anonymous_ids,
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
    def __init__(self, lease: EncodedStructuralLease) -> None:
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

    def inspect(self) -> _Inspection:
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
            if root_kind != _ROOT_AXIOM:
                inspection.fallback("encoded subset supports axiom roots only")
                continue
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
        return inspection

    def root_id(self, index: int) -> int:
        return self._node_id(self._read("root_ids", index, 4))

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

    def class_iris(self) -> frozenset[str]:
        result: set[str] = set()
        for node_id in range(1, self.node_count + 1):
            iri = self._named_class_iri(node_id)
            if iri is not None:
                result.add(iri)
        return frozenset(result)

    def anonymous_ids(self) -> dict[int, str]:
        result: dict[int, str] = {}
        next_identifier = 2_147_483_648
        for node_id in range(1, self.node_count + 1):
            if self.node_tag(node_id) == _TAG_ANONYMOUS_INDIVIDUAL:
                result[node_id] = f"_:genid{next_identifier}"
                next_identifier += 1
        return result

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

    def domain_range_index(
        self,
    ) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
        domains: dict[str, list[str]] = {}
        ranges: dict[str, list[str]] = {}
        for root_index in range(self.root_count):
            root_id = self.root_id(root_index)
            tag = self.node_tag(root_id)
            if tag == _TAG_OBJECT_PROPERTY_DOMAIN:
                property_iri, class_iri = self.domain_iris(root_id)
                domains.setdefault(property_iri, []).append(class_iri)
            elif tag == _TAG_OBJECT_PROPERTY_RANGE:
                property_iri, class_iri = self.range_iris(root_id)
                ranges.setdefault(property_iri, []).append(class_iri)
        return (
            {key: tuple(values) for key, values in domains.items()},
            {key: tuple(values) for key, values in ranges.items()},
        )

    def role_axioms(self) -> tuple[_EncodedRoleAxiom, ...]:
        rows: list[_EncodedRoleAxiom] = []
        for root_index in range(self.root_count):
            node_id = self.root_id(root_index)
            tag = self.node_tag(node_id)
            if tag not in {_TAG_SUB_OBJECT_PROPERTY_OF, _TAG_INVERSE_OBJECT_PROPERTIES}:
                continue
            first, second = self._property_pair_iris(node_id, tag)
            first_hash = _combine(4153, _owlapi_iri_hash(first))
            second_hash = _combine(4153, _owlapi_iri_hash(second))
            owlapi_hash = (
                _combine(1823, first_hash, second_hash, 0)
                if tag == _TAG_SUB_OBJECT_PROPERTY_OF
                else _combine(1229, _int32(first_hash + second_hash), 0)
            )
            rows.append(_EncodedRoleAxiom(tag, node_id, first, second, owlapi_hash))
        capacity = 16
        while len(rows) > int(capacity * 0.75):
            capacity *= 2

        def order_key(row: _EncodedRoleAxiom) -> tuple[int, int, int]:
            unsigned = row.owlapi_hash & 0xFFFFFFFF
            spread = unsigned ^ (unsigned >> 16)
            return spread & (capacity - 1), spread, row.node_id

        rows.sort(key=order_key)
        return tuple(rows)

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

    columns = _EncodedColumns(lease)
    inspection = columns.inspect()
    counters = inspection.counters
    segment = lease.segments[0] if len(lease.segments) == 1 else None
    if segment is None or getattr(segment, "role", None) != 1:
        counters.scalar_fallbacks = 1
        reason = "encoded compiler slice supports canonical direct segments only"
        return None, EncodedNegotiation("scalar-native", reason), counters.freeze()

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

    domains, ranges = ({}, {}) if asserted_taxonomy_only else columns.domain_range_index()
    role_axioms = () if asserted_taxonomy_only else columns.role_axioms()
    anonymous_ids = {} if asserted_taxonomy_only else columns.anonymous_ids()
    class_iris = (
        columns.class_iris()
        if options.include_literals
        and counters.annotation_assertion_axioms
        and not asserted_taxonomy_only
        else frozenset()
    )
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
        _columns=columns,
        _domains=domains,
        _ranges=ranges,
        _role_axioms=role_axioms,
        _anonymous_ids=anonymous_ids,
        _class_iris=class_iris,
        _counters=counters,
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
