"""Bounded encoded-column to projector-edge compiler slice.

The slice is intentionally narrow: one canonical direct segment containing
declarations, simple named-class ``SubClassOf`` and ``EquivalentClasses``
axioms, and simple named ``ClassAssertion`` axioms, all with empty annotation
sets.  It preflights the complete encoded view before yielding any edge.  A
well-formed view outside that subset selects the scalar compiler for the whole
operation; malformed rows fail closed.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from .compiler import RDF_TYPE, SUBCLASS_OF, SUPERCLASS_OF, CompileStatistics
from .diagnostics import ProjectionDiagnostic
from .encoded import EncodedNegotiation, EncodedStructuralLease
from .errors import SnapshotCompatibilityError
from .model import Edge
from .options import ProjectionOptions

_TAG_IRI = 1
_TAG_ENTITY = 2
_TAG_DECLARATION = 60
_TAG_SUB_CLASS_OF = 61
_TAG_EQUIVALENT_CLASSES = 62
_TAG_CLASS_ASSERTION = 112

_ROOT_AXIOM = 2
_COMPONENT_NODE = 1
_COMPONENT_TEXT = 2
_COMPONENT_ENUM = 5
_COMPONENT_SET = 6
_DEFAULT_MAX_IRI_BYTES = 1024 * 1024

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
    equivalent_axioms: int = 0
    class_assertion_axioms: int = 0
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
            self.equivalent_axioms,
            self.class_assertion_axioms,
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
    equivalent_axioms: int = 0
    class_assertion_axioms: int = 0
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
            equivalent_axioms=self.equivalent_axioms,
            class_assertion_axioms=self.class_assertion_axioms,
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


@dataclass(slots=True)
class EncodedSubsetCompilation:
    """Prepared direct-view slice that emits only existing projector ``Edge`` IR."""

    view: object
    options: ProjectionOptions
    lease: EncodedStructuralLease
    batch_edges: int
    asserted_taxonomy_only: bool
    _columns: _EncodedColumns
    _counters: _MutableCounters
    statistics: CompileStatistics = field(default_factory=CompileStatistics)

    @property
    def counters(self) -> EncodedSubsetCounters:
        return self._counters.freeze()

    @property
    def diagnostics(self) -> tuple[ProjectionDiagnostic, ...]:
        return ()

    def prepare_role_state(self) -> None:
        """The exact slice contains no role-map constructors."""

    def iter_raw_edges(self) -> Iterator[Edge]:
        """Yield encounter-order edges through a caller-bounded local batch."""

        def generate() -> Iterator[Edge]:
            batch: list[Edge] = []
            try:
                for root_index in range(self._columns.root_count):
                    root_id = self._columns.root_id(root_index)
                    tag = self._columns.node_tag(root_id)
                    if tag == _TAG_DECLARATION:
                        continue
                    if tag == _TAG_SUB_CLASS_OF:
                        source, destination = self._columns.subclass_iris(root_id)
                        edges = (
                            Edge(source, SUBCLASS_OF, destination),
                            *(
                                (Edge(destination, SUPERCLASS_OF, source),)
                                if self.options.bidirectional_taxonomy
                                else ()
                            ),
                        )
                    elif tag == _TAG_EQUIVALENT_CLASSES:
                        if self.asserted_taxonomy_only:
                            continue
                        source, destination = self._columns.equivalent_iris(root_id)
                        edges = (
                            Edge(source, SUBCLASS_OF, destination),
                            *(
                                (Edge(destination, SUPERCLASS_OF, source),)
                                if self.options.bidirectional_taxonomy
                                else ()
                            ),
                        )
                    elif tag == _TAG_CLASS_ASSERTION:
                        if self.asserted_taxonomy_only:
                            continue
                        individual, class_iri = self._columns.class_assertion_iris(root_id)
                        edges = (Edge(individual, RDF_TYPE, class_iri),)
                    else:  # pragma: no cover - guarded by preflight
                        raise SnapshotCompatibilityError(
                            "encoded subset root changed after successful preflight"
                        )
                    for edge in edges:
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


class _EncodedColumns:
    def __init__(self, lease: EncodedStructuralLease) -> None:
        self._buffers = lease.buffers
        self._max_iri_bytes = _owner_limit(
            lease.owner,
            "max_iri_bytes",
            _DEFAULT_MAX_IRI_BYTES,
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
            elif tag == _TAG_EQUIVALENT_CLASSES:
                counters.equivalent_axioms += 1
            elif tag == _TAG_CLASS_ASSERTION:
                counters.class_assertion_axioms += 1
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
            if not self._is_named_class(sub_id) or not self._is_named_class(super_id):
                inspection.fallback(
                    "encoded subset requires named classes on both SubClassOf sides"
                )
            if not self._empty_annotation_set(start + 2):
                inspection.fallback(
                    "encoded subset does not yet support annotated SubClassOf axioms"
                )
            return
        if tag == _TAG_EQUIVALENT_CLASSES:
            start = self._exact_fields(node_id, 2)
            item_start, length = self._node_set_range(start, minimum=2)
            all_named = True
            for item_index in range(item_start, item_start + length):
                if not self._is_named_class(self._item_node(item_index)):
                    all_named = False
            if not all_named:
                inspection.fallback("encoded subset requires named classes in EquivalentClasses")
            if not self._empty_annotation_set(start + 1):
                inspection.fallback(
                    "encoded subset does not yet support annotated EquivalentClasses axioms"
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
        _start, length = self._node_set_range(index)
        return length == 0

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

    compilation = EncodedSubsetCompilation(
        view=view,
        options=options,
        lease=lease,
        batch_edges=batch_edges,
        asserted_taxonomy_only=asserted_taxonomy_only,
        _columns=columns,
        _counters=counters,
    )
    return compilation, ingestion, compilation.counters


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
