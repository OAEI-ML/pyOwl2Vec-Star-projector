from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast
from unittest.mock import patch

import pyowl_core
import pytest
from pyowl_core import (
    BackendPreference,
    CanonicalSet,
    ImportPolicy,
    LoadOptions,
    OntologyDelta,
    apply_delta,
    canonical_bytes,
    compose_views,
)
from pyowl_core.model import AnonymousIndividual, ObjectPropertyAssertion

from pyowl2vec_star_projector import Edge, ProjectionOptions, Projector
from pyowl2vec_star_projector import api as api_module
from pyowl2vec_star_projector.backend import BackendSelection
from pyowl2vec_star_projector.compiler import (
    RDF_TYPE,
    SUBCLASS_OF,
    _owlapi_hash,
)
from pyowl2vec_star_projector.compiler import (
    prepare_streaming_compilation as scalar_compilation,
)
from pyowl2vec_star_projector.diagnostics import ProjectionDiagnostic
from pyowl2vec_star_projector.encoded import (
    ENCODED_NATIVE_FEATURE,
    EncodedNegotiation,
    EncodedStructuralLease,
    _validate_encoded_view,
)
from pyowl2vec_star_projector.encoded_compiler import (
    EncodedSubsetCounters,
    _CanonicalCursor,
    _EncodedColumns,
    prepare_encoded_subset_compilation,
)
from pyowl2vec_star_projector.errors import (
    SnapshotCompatibilityError,
    UnsupportedAxiomShapeError,
)
from tests.support.core_views import ConformingView

ROOT = Path(__file__).resolve().parents[1]

_VALIDATED_COMPLEX_EXPRESSIONS = (
    ("intersection", "ObjectIntersectionOf(:B :C)"),
    ("union", "ObjectUnionOf(:B :C)"),
    ("some", "ObjectSomeValuesFrom(:p :B)"),
    ("all", "ObjectAllValuesFrom(:p :B)"),
    ("minimum", "ObjectMinCardinality(1 :p :B)"),
    ("maximum", "ObjectMaxCardinality(1 :p :B)"),
)

_IGNORED_COMPLEX_ROOTS = (
    (
        "subclass",
        "SubClassOf({expression} ObjectUnionOf(:Y :Z))",
        "SubClassOf",
    ),
    (
        "equivalent",
        "EquivalentClasses({expression} ObjectMaxCardinality(7 :q :Z))",
        "EquivalentClasses",
    ),
    ("class-assertion", "ClassAssertion({expression} :i)", "ClassAssertion"),
    (
        "domain",
        "ObjectPropertyDomain(:q {expression})",
        "ObjectPropertyDomain",
    ),
    (
        "range",
        "ObjectPropertyRange(:q {expression})",
        "ObjectPropertyRange",
    ),
)


def _snapshot(body: str) -> object:
    source = f"Prefix(:=<urn:slice#>) Ontology(<urn:slice> {body})".encode()
    return pyowl_core.load_snapshot(
        source,
        options=LoadOptions(
            imports=ImportPolicy.IGNORE,
            backend=BackendPreference.PYTHON,
        ),
    )


def _lease(view: object) -> EncodedStructuralLease:
    encoded = view.view(  # type: ignore[attr-defined]
        pyowl_core.EncodedStructuralView,
        schema_version=1,
        scope=pyowl_core.AxiomScope.CLOSURE,
    )
    return _validate_encoded_view(
        view,
        encoded,
        pyowl_core.EncodedStructuralView,
        pyowl_core.AxiomScope.CLOSURE,
    )


@dataclass(slots=True)
class _SegmentFixture:
    role: int
    owner: object
    source: object | None
    posting_mode: int
    root_ids: memoryview
    member_token: bytes | None = None
    anonymous_scope_map: memoryview = field(default_factory=lambda: memoryview(b""))


@dataclass(frozen=True, slots=True)
class _CompositeMemberFixture:
    lease: EncodedStructuralLease
    token: bytes
    posting_mode: int = 0
    postings: memoryview = field(default_factory=lambda: memoryview(b""))
    scope_map: memoryview = field(default_factory=lambda: memoryview(b""))


def _postings(*root_ids: int) -> memoryview:
    return memoryview(b"".join(root_id.to_bytes(4, "little") for root_id in root_ids))


def _scope_map(*rows: tuple[bytes, bytes]) -> memoryview:
    return memoryview(b"".join(source + target for source, target in sorted(rows)))


def _overlay_base_lease(
    view: object,
    source: EncodedStructuralLease,
    *,
    posting_mode: int = 0,
    postings: memoryview | None = None,
    scope_map: memoryview | None = None,
    local_columns_empty: bool = True,
) -> EncodedStructuralLease:
    top = _lease(view) if hasattr(view, "view") else _lease(_snapshot(""))
    empty = _lease(_snapshot(""))
    segment = _SegmentFixture(
        2,
        source.owner,
        source.encoded_view,
        posting_mode,
        memoryview(b"") if postings is None else postings,
        anonymous_scope_map=memoryview(b"") if scope_map is None else scope_map,
    )
    buffers = empty.buffers if local_columns_empty else top.buffers
    original_encoded = empty.encoded_view if not hasattr(view, "view") else top.encoded_view
    encoded_view = replace(
        original_encoded,
        owner=view,
        buffers=buffers,
        segments=(segment,),
    )
    return replace(
        top,
        encoded_view=encoded_view,
        owner=view,
        buffers=buffers,
        segments=(segment,),
    )


def _overlay_delta_lease(
    view: object,
    source: EncodedStructuralLease,
    delta: EncodedStructuralLease,
    *,
    posting_mode: int = 0,
    postings: memoryview | None = None,
    scope_map: memoryview | None = None,
) -> EncodedStructuralLease:
    top = _lease(view) if hasattr(view, "view") else _lease(_snapshot(""))
    base_segment = _SegmentFixture(
        2,
        source.owner,
        source.encoded_view,
        posting_mode,
        memoryview(b"") if postings is None else postings,
        anonymous_scope_map=memoryview(b"") if scope_map is None else scope_map,
    )
    delta_segment = _SegmentFixture(
        3,
        view,
        None,
        0,
        memoryview(b""),
    )
    segments = (base_segment, delta_segment)
    encoded_view = replace(
        top.encoded_view,
        owner=view,
        buffers=delta.buffers,
        segments=segments,
    )
    return replace(
        top,
        encoded_view=encoded_view,
        owner=view,
        buffers=delta.buffers,
        segments=segments,
    )


def _composite_lease(
    view: object,
    members: tuple[_CompositeMemberFixture, ...],
    *,
    bridge: EncodedStructuralLease | None = None,
) -> EncodedStructuralLease:
    top = _lease(view) if hasattr(view, "view") else _lease(_snapshot(""))
    empty = _lease(_snapshot(""))
    segments: tuple[_SegmentFixture, ...] = tuple(
        _SegmentFixture(
            4,
            member.lease.owner,
            member.lease.encoded_view,
            member.posting_mode,
            member.postings,
            member.token,
            member.scope_map,
        )
        for member in members
    )
    if bridge is not None:
        segments = (
            *segments,
            _SegmentFixture(5, view, None, 0, memoryview(b"")),
        )
    buffers = empty.buffers if bridge is None else bridge.buffers
    encoded_view = replace(
        top.encoded_view,
        owner=view,
        buffers=buffers,
        segments=segments,
    )
    return replace(
        top,
        encoded_view=encoded_view,
        owner=view,
        buffers=buffers,
        segments=segments,
    )


def _semantic_composite_lease(
    view: object,
    sources: tuple[EncodedStructuralLease, ...],
    *,
    bridge: EncodedStructuralLease | None = None,
) -> EncodedStructuralLease:
    tokens = cast(tuple[bytes, ...], cast(Any, view)._source_tokens())
    mappings = cast(
        tuple[Mapping[bytes, bytes], ...] | None,
        cast(Any, view)._scope_replacements(),
    )
    assert mappings is not None
    rows = sorted(zip(tokens, sources, mappings, strict=True), key=lambda row: row[0])
    return _composite_lease(
        view,
        tuple(
            _CompositeMemberFixture(
                source,
                token,
                scope_map=_scope_map(*mapping.items()),
            )
            for token, source, mapping in rows
        ),
        bridge=bridge,
    )


def _anonymous_scope_span(
    lease: EncodedStructuralLease,
    node_id: int,
) -> tuple[int, int]:
    buffers = lease.buffers
    offsets = buffers["node_field_offsets"]
    field_index = int.from_bytes(
        offsets[(node_id - 1) * 8 : node_id * 8],
        "little",
    )
    values = buffers["field_values"]
    lengths = buffers["field_lengths"]
    scalar_offset = int.from_bytes(
        values[field_index * 8 : (field_index + 1) * 8],
        "little",
    )
    scalar_length = int.from_bytes(
        lengths[field_index * 8 : (field_index + 1) * 8],
        "little",
    )
    return scalar_offset, scalar_length


def _anonymous_local_key_span(
    lease: EncodedStructuralLease,
    node_id: int,
) -> tuple[int, int]:
    buffers = lease.buffers
    offsets = buffers["node_field_offsets"]
    field_index = (
        int.from_bytes(
            offsets[(node_id - 1) * 8 : node_id * 8],
            "little",
        )
        + 1
    )
    values = buffers["field_values"]
    lengths = buffers["field_lengths"]
    scalar_offset = int.from_bytes(
        values[field_index * 8 : (field_index + 1) * 8],
        "little",
    )
    scalar_length = int.from_bytes(
        lengths[field_index * 8 : (field_index + 1) * 8],
        "little",
    )
    return scalar_offset, scalar_length


def _anonymous_scope(lease: EncodedStructuralLease, node_id: int) -> bytes:
    scalar_offset, scalar_length = _anonymous_scope_span(lease, node_id)
    return bytes(lease.buffers["scalar_bytes"][scalar_offset : scalar_offset + scalar_length])


def _anonymous_node_ids(lease: EncodedStructuralLease) -> tuple[int, ...]:
    tags = lease.buffers["node_tags"]
    return tuple(
        node_id
        for node_id in range(1, tags.nbytes // 2 + 1)
        if int.from_bytes(tags[(node_id - 1) * 2 : node_id * 2], "little") == 3
    )


def _semantic_report(report: dict[str, object]) -> dict[str, object]:
    normalized = cast(dict[str, Any], json.loads(json.dumps(report)))
    provenance = normalized["provenance"]
    provenance.pop("selected_backend")
    provenance.pop("native_implementation_version")
    provenance.pop("ingestion")
    provenance["options"].pop("backend")
    return cast(dict[str, object], normalized)


@contextmanager
def _forced_encoded(lease: EncodedStructuralLease) -> Iterator[None]:
    selection = BackendSelection("native", "native")

    def passthrough(edges: Iterable[Edge], *, batch_edges: int) -> Iterator[Edge]:
        assert batch_edges > 0
        return iter(edges)

    with (
        patch.object(api_module, "select_backend", return_value=selection),
        patch.object(
            api_module,
            "_activate_selection",
            return_value=(
                selection,
                "test-native",
                frozenset({ENCODED_NATIVE_FEATURE}),
            ),
        ),
        patch.object(
            api_module,
            "select_ingestion",
            return_value=EncodedNegotiation("encoded-native", lease=lease),
        ),
        patch.object(api_module, "iter_native_passthrough", side_effect=passthrough),
    ):
        yield


def test_simple_subset_matches_scalar_without_scalar_axiom_traversal() -> None:
    view = _snapshot(
        "Declaration(Class(:A)) Declaration(Class(:B)) Declaration(Class(:C)) "
        "SubClassOf(:A :B) SubClassOf(:B :C)"
    )
    lease = _lease(view)
    cases = (
        ProjectionOptions(backend="python", order="encounter"),
        ProjectionOptions(
            backend="python",
            bidirectional_taxonomy=True,
            duplicates="unique",
            order="canonical",
        ),
        ProjectionOptions(
            backend="python",
            only_taxonomy=True,
            include_literals=True,
            order="canonical",
        ),
    )
    expected: list[tuple[list[Edge], dict[str, object]]] = []
    for options in cases:
        scalar = Projector()
        edges = scalar.project(view, options=options)
        assert scalar.last_report is not None
        expected.append((edges, scalar.last_report.to_dict()))

    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("encoded slice crossed scalar axiom traversal"),
        ),
    ):
        for options, (scalar_edges, scalar_report) in zip(cases, expected, strict=True):
            projector = Projector()
            encoded_options = replace(options, backend="native")
            actual = list(projector.iter_edges(view, options=encoded_options, buffer_edges=1))

            assert actual == scalar_edges
            assert projector.last_report is not None
            assert projector.last_report.provenance.ingestion.path == "encoded-native"
            assert projector.last_report.provenance.counts.edges == len(actual)
            assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(
                scalar_report
            )
            assert projector.last_encoded_counters == EncodedSubsetCounters(
                roots_inspected=5,
                nodes_inspected=11,
                declaration_axioms=3,
                subclass_axioms=2,
                scalar_bytes_checked=48,
                edge_batches=len(actual),
                raw_edges=len(actual),
                scalar_fallbacks=0,
            )


def test_named_equivalence_and_class_assertion_match_scalar_in_bounded_batches() -> None:
    view = _snapshot(
        "Declaration(Class(:Z)) Declaration(Class(:AA)) Declaration(Class(:B)) "
        "Declaration(Class(:Top)) Declaration(NamedIndividual(:i)) "
        "SubClassOf(:Z :Top) EquivalentClasses(:Z :AA :B) ClassAssertion(:Z :i)"
    )
    lease = _lease(view)
    cases = (
        ProjectionOptions(backend="python", order="encounter"),
        ProjectionOptions(
            backend="python",
            bidirectional_taxonomy=True,
            duplicates="unique",
            order="canonical",
        ),
        ProjectionOptions(
            backend="python",
            only_taxonomy=True,
            order="encounter",
        ),
    )
    expected: list[tuple[list[Edge], dict[str, object]]] = []
    for options in cases:
        scalar = Projector()
        edges = scalar.project(view, options=options)
        assert scalar.last_report is not None
        expected.append((edges, scalar.last_report.to_dict()))

    lexical_equivalence = Edge("urn:slice#AA", SUBCLASS_OF, "urn:slice#B")
    class_assertion = Edge("urn:slice#i", RDF_TYPE, "urn:slice#Z")
    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("encoded slice crossed scalar axiom traversal"),
        ),
    ):
        for options, (scalar_edges, scalar_report) in zip(cases, expected, strict=True):
            projector = Projector()
            actual = list(
                projector.iter_edges(
                    view,
                    options=replace(options, backend="native"),
                    buffer_edges=1,
                )
            )

            assert actual == scalar_edges
            assert lexical_equivalence in actual
            assert class_assertion in actual
            assert projector.last_report is not None
            assert projector.last_report.provenance.ingestion.path == "encoded-native"
            assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(
                scalar_report
            )
            counters = projector.last_encoded_counters
            assert counters is not None
            assert counters.roots_inspected == 8
            assert counters.declaration_axioms == 5
            assert counters.subclass_axioms == 1
            assert counters.equivalent_axioms == 1
            assert counters.class_assertion_axioms == 1
            assert counters.edge_batches == len(actual)
            assert counters.raw_edges == len(actual)
            assert counters.scalar_fallbacks == 0


def test_named_object_property_slice_matches_scalar_cross_product_batches() -> None:
    view = _snapshot(
        "Declaration(ObjectProperty(:p)) Declaration(ObjectProperty(:q)) "
        "Declaration(NamedIndividual(:i)) Declaration(NamedIndividual(:j)) "
        "ObjectPropertyAssertion(:p :i :j) "
        "ObjectPropertyDomain(:p :D2) ObjectPropertyDomain(:p :D1) "
        "ObjectPropertyRange(:p :R2) ObjectPropertyRange(:p :R1) "
        "ObjectPropertyDomain(:q :QD) ObjectPropertyRange(:q :QR)"
    )
    lease = _lease(view)
    cases = (
        ProjectionOptions(backend="python", order="encounter"),
        ProjectionOptions(
            backend="python",
            bidirectional_taxonomy=True,
            duplicates="unique",
            order="canonical",
        ),
    )
    expected: list[tuple[list[Edge], dict[str, object]]] = []
    for options in cases:
        scalar = Projector()
        edges = scalar.project(view, options=options)
        assert scalar.last_report is not None
        expected.append((edges, scalar.last_report.to_dict()))

    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("encoded property slice crossed scalar traversal"),
        ),
    ):
        for options, (scalar_edges, scalar_report) in zip(cases, expected, strict=True):
            projector = Projector()
            actual = list(
                projector.iter_edges(
                    view,
                    options=replace(options, backend="native"),
                    buffer_edges=2,
                )
            )

            assert actual == scalar_edges
            assert len(actual) == 6
            assert Edge("urn:slice#i", "urn:slice#p", "urn:slice#j") in actual
            assert projector.last_report is not None
            assert projector.last_report.provenance.ingestion.path == "encoded-native"
            assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(
                scalar_report
            )
            counters = projector.last_encoded_counters
            assert counters is not None
            assert counters.roots_inspected == 11
            assert counters.declaration_axioms == 4
            assert counters.object_property_assertion_axioms == 1
            assert counters.object_property_domain_axioms == 3
            assert counters.object_property_range_axioms == 3
            assert counters.edge_batches == 3
            assert counters.raw_edges == 6
            assert counters.scalar_fallbacks == 0


def test_anonymous_object_property_assertions_match_scalar_blank_ids() -> None:
    view = _snapshot(
        "ObjectPropertyAssertion(:p _:z :i) "
        "ObjectPropertyAssertion(:p :i _:a) "
        "ObjectPropertyAssertion(:p _:z _:a)"
    )
    lease = _lease(view)
    cases = (
        ProjectionOptions(backend="python", order="encounter"),
        ProjectionOptions(
            backend="python",
            duplicates="unique",
            order="canonical",
        ),
    )
    expected: list[tuple[list[Edge], dict[str, object]]] = []
    for options in cases:
        scalar = Projector()
        edges = scalar.project(view, options=options)
        assert scalar.last_report is not None
        expected.append((edges, scalar.last_report.to_dict()))

    expected_edges = {
        Edge("_:genid2147483648", "urn:slice#p", "_:genid2147483649"),
        Edge("_:genid2147483648", "urn:slice#p", "urn:slice#i"),
        Edge("urn:slice#i", "urn:slice#p", "_:genid2147483649"),
    }
    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("encoded anonymous slice crossed scalar traversal"),
        ),
    ):
        for options, (scalar_edges, scalar_report) in zip(cases, expected, strict=True):
            projector = Projector()
            actual = list(
                projector.iter_edges(
                    view,
                    options=replace(options, backend="native"),
                    buffer_edges=1,
                )
            )

            assert actual == scalar_edges
            assert set(actual) == expected_edges
            assert projector.last_report is not None
            assert projector.last_report.provenance.ingestion.path == "encoded-native"
            assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(
                scalar_report
            )
            counters = projector.last_encoded_counters
            assert counters is not None
            assert counters.roots_inspected == 3
            assert counters.object_property_assertion_axioms == 3
            assert counters.anonymous_individuals == 2
            assert counters.edge_batches == 3
            assert counters.raw_edges == 3
            assert counters.scalar_fallbacks == 0


def test_canonical_cursor_matches_core_bytes_for_the_executable_graph() -> None:
    view = _snapshot(
        "SubClassOf(:A ObjectMinCardinality(2 :p :B)) "
        "EquivalentClasses(:C ObjectIntersectionOf(:D ObjectSomeValuesFrom(:p :E))) "
        "ObjectPropertyAssertion(:p _:anon :i) "
        'AnnotationAssertion(Annotation(<urn:meta> "m") '
        '<http://www.w3.org/2000/01/rdf-schema#label> :A "text")'
    )
    lease = _lease(view)
    columns = _EncodedColumns(lease)
    assert columns.inspect().fallback_reason is None
    cursor = _CanonicalCursor(columns, {})

    actual = tuple(
        bytes(cursor.iter_node_bytes(columns.root_id(root_index)))
        for root_index in columns.iter_root_indices()
    )
    expected = tuple(
        canonical_bytes(axiom)
        for axiom in view.iter_axioms()  # type: ignore[attr-defined]
    )

    assert actual == expected
    assert tuple(map(len, actual)) == tuple(
        cursor.node_length(columns.root_id(root_index))
        for root_index in columns.iter_root_indices()
    )


def test_canonical_cursor_matches_core_bytes_for_sequence_components() -> None:
    view = _snapshot("SubObjectPropertyOf(ObjectPropertyChain(:z ObjectInverseOf(:a) :m) :super)")
    lease = _lease(view)
    columns = _EncodedColumns(lease)
    assert 7 in bytes(lease.buffers["field_kinds"])
    assert columns.inspect().fallback_reason is None
    chain_id = next(
        node_id for node_id in range(1, columns.node_count + 1) if columns.node_tag(node_id) == 11
    )
    field_offsets = lease.buffers["node_field_offsets"]
    field_index = int.from_bytes(
        field_offsets[(chain_id - 1) * 8 : chain_id * 8],
        "little",
    )
    item_start = int.from_bytes(
        lease.buffers["field_values"][field_index * 8 : (field_index + 1) * 8],
        "little",
    )
    item_length = int.from_bytes(
        lease.buffers["field_lengths"][field_index * 8 : (field_index + 1) * 8],
        "little",
    )
    item_ids = tuple(
        int.from_bytes(
            lease.buffers["item_values"][item_index * 8 : (item_index + 1) * 8],
            "little",
        )
        for item_index in range(item_start, item_start + item_length)
    )
    assert item_ids != tuple(sorted(item_ids))
    cursor = _CanonicalCursor(columns, {})

    actual = tuple(
        bytes(cursor.iter_node_bytes(columns.root_id(root_index)))
        for root_index in columns.iter_root_indices()
    )
    expected = tuple(
        canonical_bytes(axiom)
        for axiom in view.iter_axioms()  # type: ignore[attr-defined]
    )

    assert actual == expected


def test_overlay_base_exclusion_matches_scalar_and_retains_direct_owner() -> None:
    base = _snapshot("ObjectPropertyAssertion(:p _:a :i) ObjectPropertyAssertion(:p _:z :j)")
    source_lease = _lease(base)
    axioms = tuple(base.iter_axioms())  # type: ignore[attr-defined]
    assert len(axioms) == 2
    overlay = apply_delta(
        base,  # type: ignore[arg-type]
        OntologyDelta(remove_axioms=CanonicalSet((axioms[0],))),
    )
    lease = _overlay_base_lease(
        overlay,
        source_lease,
        posting_mode=2,
        postings=_postings(1),
    )
    options = ProjectionOptions(backend="python", order="encounter")
    expected = Projector().project(overlay, options=options)

    prepared, negotiation, initial = prepare_encoded_subset_compilation(
        overlay,
        replace(options, backend="native"),
        EncodedNegotiation("encoded-native", lease=lease),
        batch_edges=1,
    )
    assert prepared is not None
    assert negotiation.path == "encoded-native"
    assert initial is not None
    assert prepared._retained_leases == (source_lease,)
    assert prepared._retained_leases[0].owner is base

    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("overlay base crossed scalar axiom traversal"),
        ),
    ):
        projector = Projector()
        actual = projector.project(overlay, options=replace(options, backend="native"))

    assert actual == expected
    assert len(actual) == 1
    assert actual[0].source == "_:genid2147483648"
    counters = projector.last_encoded_counters
    assert counters is not None
    assert counters.referenced_segments == 1
    assert counters.posting_rows_inspected == 1
    assert counters.scope_map_rows_inspected == 0
    assert counters.source_roots_inspected == 2
    assert counters.selected_roots == 1
    assert counters.roots_inspected == 2
    assert counters.object_property_assertion_axioms == 1
    assert counters.anonymous_individuals == 2
    assert counters.scalar_fallbacks == 0


def test_overlay_base_delta_merges_exact_order_and_cross_segment_indexes() -> None:
    base = _snapshot(
        "Declaration(Class(:Removed)) SubClassOf(:Z :Top) "
        "ObjectPropertyDomain(:p :D) SubObjectPropertyOf(:child :p)"
    )
    delta = _snapshot(
        "SubClassOf(:AA :Top) ObjectPropertyRange(:p :R) "
        "InverseObjectProperties(:p :pinv) Declaration(Class(:A)) "
        "ClassAssertion(:A :i)"
    )
    base_axioms = tuple(base.iter_axioms())  # type: ignore[attr-defined]
    removed = next(axiom for axiom in base_axioms if type(axiom).__name__ == "Declaration")
    removed_posting = base_axioms.index(removed) + 1
    delta_axioms = tuple(delta.iter_axioms())  # type: ignore[attr-defined]
    overlay = apply_delta(
        base,  # type: ignore[arg-type]
        OntologyDelta(
            add_axioms=CanonicalSet(delta_axioms),
            remove_axioms=CanonicalSet((removed,)),
        ),
    )
    lease = _overlay_delta_lease(
        overlay,
        _lease(base),
        _lease(delta),
        posting_mode=2,
        postings=_postings(removed_posting),
    )
    prepared, negotiation, initial = prepare_encoded_subset_compilation(
        overlay,
        ProjectionOptions(backend="native", order="encounter"),
        EncodedNegotiation("encoded-native", lease=lease),
        batch_edges=1,
    )
    assert prepared is not None
    assert negotiation.path == "encoded-native"
    assert initial is not None
    assert len(prepared._retained_leases) == 1
    assert prepared._retained_leases[0].owner is base
    cases = (
        ProjectionOptions(backend="python", order="encounter"),
        ProjectionOptions(
            backend="python",
            bidirectional_taxonomy=True,
            duplicates="unique",
            order="canonical",
        ),
    )
    expected: list[tuple[list[Edge], dict[str, object]]] = []
    for options in cases:
        scalar = Projector()
        edges = scalar.project(overlay, options=options)
        assert scalar.last_report is not None
        expected.append((edges, scalar.last_report.to_dict()))

    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("overlay delta crossed scalar traversal"),
        ),
    ):
        for options, (scalar_edges, scalar_report) in zip(cases, expected, strict=True):
            projector = Projector()
            actual = projector.project(
                overlay,
                options=replace(options, backend="native"),
            )

            assert actual == scalar_edges
            assert Edge("urn:slice#D", "urn:slice#p", "urn:slice#R") in actual
            assert Edge("urn:slice#D", "urn:slice#child", "urn:slice#R") in actual
            assert Edge("urn:slice#R", "urn:slice#pinv", "urn:slice#D") in actual
            assert projector.last_report is not None
            assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(
                scalar_report
            )
            counters = projector.last_encoded_counters
            assert counters is not None
            assert counters.referenced_segments == 1
            assert counters.posting_rows_inspected == 1
            assert counters.source_roots_inspected == 4
            assert counters.delta_roots_inspected == 5
            assert counters.selected_roots == 8
            assert counters.deduplicated_roots == 0
            assert counters.canonical_bytes_compared > 0
            assert counters.subclass_axioms == 2
            assert counters.scalar_fallbacks == 0


def test_overlay_base_delta_structurally_deduplicates_equal_roots() -> None:
    source = _snapshot("SubClassOf(:A :B)")
    delta = _snapshot("SubClassOf(:A :B) SubClassOf(:B :C)")
    target = _snapshot("SubClassOf(:A :B) SubClassOf(:B :C)")
    lease = _overlay_delta_lease(target, _lease(source), _lease(delta))
    options = ProjectionOptions(backend="python", order="encounter")
    expected = Projector().project(target, options=options)

    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("deduplicated overlay crossed scalar traversal"),
        ),
    ):
        projector = Projector()
        actual = projector.project(target, options=replace(options, backend="native"))

    assert (
        actual
        == expected
        == [
            Edge("urn:slice#A", SUBCLASS_OF, "urn:slice#B"),
            Edge("urn:slice#B", SUBCLASS_OF, "urn:slice#C"),
        ]
    )
    counters = projector.last_encoded_counters
    assert counters is not None
    assert counters.roots_inspected == 3
    assert counters.source_roots_inspected == 1
    assert counters.delta_roots_inspected == 2
    assert counters.selected_roots == counters.subclass_axioms == 2
    assert counters.deduplicated_roots == 1
    assert counters.canonical_bytes_compared > 0
    assert counters.raw_edges == 2
    assert counters.scalar_fallbacks == 0


def test_overlay_base_scope_remap_matches_scalar_blank_identity() -> None:
    source = _snapshot("ObjectPropertyAssertion(:p _:anon :i)")
    source_lease = _lease(source)
    axiom = next(source.iter_axioms())  # type: ignore[attr-defined]
    assert isinstance(axiom, ObjectPropertyAssertion)
    assert isinstance(axiom.source, AnonymousIndividual)
    source_scope = axiom.source.document_scope
    target_scope = bytes((source_scope[0] ^ 0xFF,)) + source_scope[1:]
    mapped_axiom = replace(
        axiom,
        source=AnonymousIndividual(target_scope, axiom.source.local_key),
    )
    target_document = replace(
        source.root,  # type: ignore[attr-defined]
        axioms=CanonicalSet((mapped_axiom,)),
    )
    target = ConformingView((target_document,))
    lease = _overlay_base_lease(
        target,
        source_lease,
        scope_map=_scope_map((source_scope, target_scope)),
    )
    source_columns = _EncodedColumns(source_lease)
    remapped_cursor = _CanonicalCursor(
        source_columns,
        {source_scope: target_scope},
    )
    assert bytes(remapped_cursor.iter_node_bytes(source_columns.root_id(0))) == canonical_bytes(
        mapped_axiom
    )
    options = ProjectionOptions(backend="python", order="encounter")
    expected = Projector().project(target, options=options)

    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("scope-remapped overlay crossed scalar traversal"),
        ),
    ):
        projector = Projector()
        actual = projector.project(target, options=replace(options, backend="native"))

    assert actual == expected == [Edge("_:genid2147483648", "urn:slice#p", "urn:slice#i")]
    counters = projector.last_encoded_counters
    assert counters is not None
    assert counters.referenced_segments == 1
    assert counters.posting_rows_inspected == 0
    assert counters.scope_map_rows_inspected == 1
    assert counters.source_roots_inspected == counters.selected_roots == 1
    assert counters.scalar_fallbacks == 0


def test_overlay_delta_deduplicates_after_anonymous_scope_remap() -> None:
    source = _snapshot("ObjectPropertyAssertion(:p _:anon :i)")
    source_lease = _lease(source)
    axiom = next(source.iter_axioms())  # type: ignore[attr-defined]
    assert isinstance(axiom, ObjectPropertyAssertion)
    assert isinstance(axiom.source, AnonymousIndividual)
    source_scope = axiom.source.document_scope
    target_scope = bytes((source_scope[0] ^ 0xFF,)) + source_scope[1:]
    mapped_axiom = replace(
        axiom,
        source=AnonymousIndividual(target_scope, axiom.source.local_key),
    )
    target_document = replace(
        source.root,  # type: ignore[attr-defined]
        axioms=CanonicalSet((mapped_axiom,)),
    )
    target = ConformingView((target_document,))

    delta_buffers = dict(source_lease.buffers)
    anonymous_id = _anonymous_node_ids(source_lease)[0]
    scope_offset, scope_length = _anonymous_scope_span(source_lease, anonymous_id)
    assert scope_length == len(target_scope) == 32
    scalar_bytes = bytearray(delta_buffers["scalar_bytes"])
    scalar_bytes[scope_offset : scope_offset + scope_length] = target_scope
    delta_buffers["scalar_bytes"] = memoryview(bytes(scalar_bytes))
    delta_lease = replace(source_lease, buffers=MappingProxyType(delta_buffers))
    lease = _overlay_delta_lease(
        target,
        source_lease,
        delta_lease,
        scope_map=_scope_map((source_scope, target_scope)),
    )
    options = ProjectionOptions(backend="python", order="encounter")
    expected = Projector().project(target, options=options)

    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("scope-deduplicated overlay crossed scalar traversal"),
        ),
    ):
        projector = Projector()
        actual = projector.project(target, options=replace(options, backend="native"))

    assert actual == expected == [Edge("_:genid2147483648", "urn:slice#p", "urn:slice#i")]
    counters = projector.last_encoded_counters
    assert counters is not None
    assert counters.scope_map_rows_inspected == 1
    assert counters.source_roots_inspected == counters.delta_roots_inspected == 1
    assert counters.selected_roots == counters.deduplicated_roots == 1
    assert counters.anonymous_individuals == 2
    assert counters.canonical_bytes_compared > 0
    assert counters.raw_edges == 1
    assert counters.scalar_fallbacks == 0


def test_annotated_overlay_roots_preserve_semantic_duplicates_and_owner_lifetime() -> None:
    source = _snapshot(
        'SubClassOf(Annotation(<urn:meta> "base") :A :B) '
        'SubClassOf(Annotation(<urn:meta> "shared") :A :B)'
    )
    delta = _snapshot(
        'SubClassOf(Annotation(<urn:meta> "delta") :A :B) '
        'SubClassOf(Annotation(<urn:meta> "shared") :A :B)'
    )
    overlay = _snapshot(
        'SubClassOf(Annotation(<urn:meta> "base") :A :B) '
        'SubClassOf(Annotation(<urn:meta> "delta") :A :B) '
        'SubClassOf(Annotation(<urn:meta> "shared") :A :B)'
    )
    lease = _overlay_delta_lease(overlay, _lease(source), _lease(delta))
    prepared, negotiation, initial = prepare_encoded_subset_compilation(
        overlay,
        ProjectionOptions(backend="native", order="encounter"),
        EncodedNegotiation("encoded-native", lease=lease),
        batch_edges=1,
    )
    assert prepared is not None
    assert negotiation.path == "encoded-native"
    assert initial is not None
    assert len(prepared._retained_leases) == 1
    assert prepared._retained_leases[0].owner is source
    cases = (
        ProjectionOptions(backend="python", order="encounter"),
        ProjectionOptions(backend="python", duplicates="unique", order="canonical"),
    )
    expected: list[tuple[list[Edge], dict[str, object]]] = []
    for options in cases:
        scalar = Projector()
        edges = scalar.project(overlay, options=options)
        assert scalar.last_report is not None
        expected.append((edges, scalar.last_report.to_dict()))

    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("annotated overlay crossed scalar traversal"),
        ),
    ):
        for options, (scalar_edges, scalar_report) in zip(cases, expected, strict=True):
            projector = Projector()
            actual = projector.project(
                overlay,
                options=replace(options, backend="native"),
            )

            assert actual == scalar_edges
            assert len(actual) == (1 if options.duplicates == "unique" else 3)
            assert set(actual) == {Edge("urn:slice#A", SUBCLASS_OF, "urn:slice#B")}
            assert projector.last_report is not None
            assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(
                scalar_report
            )
            counters = projector.last_encoded_counters
            assert counters is not None
            assert counters.roots_inspected == 4
            assert counters.source_roots_inspected == 2
            assert counters.delta_roots_inspected == 2
            assert counters.selected_roots == counters.subclass_axioms == 3
            assert counters.deduplicated_roots == 1
            assert counters.annotation_nodes == counters.literal_nodes == 4
            assert counters.raw_edges == 3
            assert counters.scalar_fallbacks == 0


def test_overlay_recursively_resolves_segmented_overlay_source() -> None:
    base = _snapshot("SubClassOf(:A :B)")
    inner_delta = _snapshot("SubClassOf(:B :C)")
    inner = apply_delta(
        base,  # type: ignore[arg-type]
        OntologyDelta(add_axioms=CanonicalSet(tuple(inner_delta.iter_axioms()))),  # type: ignore[attr-defined]
    )
    inner_lease = _overlay_delta_lease(
        inner,
        _lease(base),
        _lease(inner_delta),
    )
    outer_delta = _snapshot("SubClassOf(:C :D)")
    outer = apply_delta(
        inner,
        OntologyDelta(add_axioms=CanonicalSet(tuple(outer_delta.iter_axioms()))),  # type: ignore[attr-defined]
    )
    lease = _overlay_delta_lease(
        outer,
        inner_lease,
        _lease(outer_delta),
    )
    prepared, negotiation, initial = prepare_encoded_subset_compilation(
        outer,
        ProjectionOptions(backend="native", order="encounter"),
        EncodedNegotiation("encoded-native", lease=lease),
        batch_edges=1,
    )
    assert prepared is not None
    assert negotiation.path == "encoded-native"
    assert initial is not None
    assert {id(item.owner) for item in prepared._retained_leases} == {
        id(inner),
        id(base),
    }
    cases = (
        ProjectionOptions(backend="python", order="encounter"),
        ProjectionOptions(
            backend="python",
            bidirectional_taxonomy=True,
            duplicates="unique",
            order="canonical",
        ),
    )
    expected = tuple(Projector().project(outer, options=options) for options in cases)

    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("recursive overlay crossed scalar traversal"),
        ),
    ):
        for options, scalar_edges in zip(cases, expected, strict=True):
            projector = Projector()
            actual = projector.project(
                outer,
                options=replace(options, backend="native"),
            )

            assert actual == scalar_edges
            counters = projector.last_encoded_counters
            assert counters is not None
            assert counters.referenced_segments == 2
            assert counters.composite_member_segments == 0
            assert counters.source_roots_inspected == 2
            assert counters.delta_roots_inspected == 2
            assert counters.roots_inspected == counters.selected_roots == 3
            assert counters.scalar_fallbacks == 0


def test_overlay_recursively_resolves_composite_source_indexes_and_lifetime() -> None:
    left = _snapshot("ObjectPropertyDomain(:p :D) SubObjectPropertyOf(:child :p)")
    right = _snapshot(
        "ObjectPropertyRange(:p :R) InverseObjectProperties(:p :pinv) ClassAssertion(:R :i)"
    )
    bridge = _snapshot("Declaration(Class(:Bridge))")
    inner = compose_views(
        left,
        right,
        delta=OntologyDelta(
            add_axioms=CanonicalSet(tuple(bridge.iter_axioms())),  # type: ignore[attr-defined]
        ),
    )
    inner_lease = _semantic_composite_lease(
        inner,
        (_lease(left), _lease(right)),
        bridge=_lease(bridge),
    )
    delta = _snapshot("SubClassOf(:AA :Top)")
    overlay = apply_delta(
        inner,
        OntologyDelta(add_axioms=CanonicalSet(tuple(delta.iter_axioms()))),  # type: ignore[attr-defined]
    )
    lease = _overlay_delta_lease(overlay, inner_lease, _lease(delta))
    prepared, negotiation, initial = prepare_encoded_subset_compilation(
        overlay,
        ProjectionOptions(backend="native", order="encounter"),
        EncodedNegotiation("encoded-native", lease=lease),
        batch_edges=1,
    )
    assert prepared is not None
    assert negotiation.path == "encoded-native"
    assert initial is not None
    assert {id(item.owner) for item in prepared._retained_leases} == {
        id(inner),
        id(left),
        id(right),
    }
    cases = (
        ProjectionOptions(backend="python", order="encounter"),
        ProjectionOptions(
            backend="python",
            bidirectional_taxonomy=True,
            duplicates="unique",
            order="canonical",
        ),
    )
    expected = tuple(Projector().project(overlay, options=options) for options in cases)

    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("composite-source overlay crossed scalar traversal"),
        ),
    ):
        for options, scalar_edges in zip(cases, expected, strict=True):
            projector = Projector()
            actual = projector.project(
                overlay,
                options=replace(options, backend="native"),
            )

            assert actual == scalar_edges
            assert Edge("urn:slice#D", "urn:slice#p", "urn:slice#R") in actual
            assert Edge("urn:slice#D", "urn:slice#child", "urn:slice#R") in actual
            assert Edge("urn:slice#R", "urn:slice#pinv", "urn:slice#D") in actual
            counters = projector.last_encoded_counters
            assert counters is not None
            assert counters.referenced_segments == 3
            assert counters.composite_member_segments == 2
            assert counters.bridge_roots_inspected == 1
            assert counters.source_roots_inspected == 6
            assert counters.delta_roots_inspected == 1
            assert counters.roots_inspected == counters.selected_roots == 7
            assert counters.scalar_fallbacks == 0


def test_overlay_exclude_postings_address_only_composite_local_roots() -> None:
    left = _snapshot("SubClassOf(:NestedA :Top)")
    right = _snapshot("SubClassOf(:NestedB :Top)")
    bridge = _snapshot("SubClassOf(:LocalDrop :Top) SubClassOf(:LocalKeep :Top)")
    bridge_axioms = tuple(bridge.iter_axioms())  # type: ignore[attr-defined]
    drop = next(axiom for axiom in bridge_axioms if b"LocalDrop" in canonical_bytes(axiom))
    drop_id = bridge_axioms.index(drop) + 1
    inner = compose_views(
        left,
        right,
        delta=OntologyDelta(add_axioms=CanonicalSet(bridge_axioms)),
    )
    inner_lease = _semantic_composite_lease(
        inner,
        (_lease(left), _lease(right)),
        bridge=_lease(bridge),
    )
    overlay = apply_delta(
        inner,
        OntologyDelta(remove_axioms=CanonicalSet((drop,))),
    )
    lease = _overlay_base_lease(
        overlay,
        inner_lease,
        posting_mode=2,
        postings=_postings(drop_id),
    )
    options = ProjectionOptions(backend="python", order="encounter")
    expected = Projector().project(overlay, options=options)
    prepared, negotiation, initial = prepare_encoded_subset_compilation(
        overlay,
        replace(options, backend="native"),
        EncodedNegotiation("encoded-native", lease=lease),
        batch_edges=1,
    )
    assert prepared is not None
    assert negotiation.path == "encoded-native"
    assert initial is not None
    assert {id(item.owner) for item in prepared._retained_leases} == {
        id(inner),
        id(left),
        id(right),
    }

    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("posted composite overlay crossed scalar traversal"),
        ),
    ):
        projector = Projector()
        actual = projector.project(
            overlay,
            options=replace(options, backend="native"),
        )

    assert actual == expected
    assert {edge.source for edge in actual} == {
        "urn:slice#NestedA",
        "urn:slice#NestedB",
        "urn:slice#LocalKeep",
    }
    counters = projector.last_encoded_counters
    assert counters is not None
    assert counters.referenced_segments == 3
    assert counters.composite_member_segments == 2
    assert counters.bridge_roots_inspected == 2
    assert counters.posting_rows_inspected == 1
    assert counters.source_roots_inspected == counters.roots_inspected == 4
    assert counters.selected_roots == 3
    assert counters.scalar_fallbacks == 0


def test_overlay_composes_scope_maps_across_composite_source() -> None:
    body = "ObjectPropertyAssertion(:p _:x :i)"
    left = _snapshot(body)
    right = _snapshot(body)
    inner = compose_views(left, right)
    inner_lease = _semantic_composite_lease(
        inner,
        (_lease(left), _lease(right)),
    )
    inner_axioms = tuple(inner.iter_axioms())  # type: ignore[attr-defined]
    current_scopes = sorted(
        axiom.source.document_scope
        for axiom in inner_axioms
        if isinstance(axiom, ObjectPropertyAssertion)
        and isinstance(axiom.source, AnonymousIndividual)
    )
    assert len(current_scopes) == 2
    target_scopes = (b"\x10" * 32, b"\xf0" * 32)
    top_scope_map = dict(zip(current_scopes, target_scopes, strict=True))
    mapped_axioms = tuple(
        replace(
            axiom,
            source=AnonymousIndividual(
                top_scope_map[axiom.source.document_scope],
                axiom.source.local_key,
            ),
        )
        for axiom in inner_axioms
        if isinstance(axiom, ObjectPropertyAssertion)
        and isinstance(axiom.source, AnonymousIndividual)
    )
    target_document = replace(
        left.root,  # type: ignore[attr-defined]
        axioms=CanonicalSet(mapped_axioms),
    )
    target = ConformingView((target_document,))
    lease = _overlay_base_lease(
        target,
        inner_lease,
        scope_map=_scope_map(*top_scope_map.items()),
    )
    options = ProjectionOptions(backend="python", order="encounter")
    expected = Projector().project(target, options=options)

    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("scope-composed overlay crossed scalar traversal"),
        ),
    ):
        projector = Projector()
        actual = projector.project(
            target,
            options=replace(options, backend="native"),
        )

    assert actual == expected
    assert len(actual) == 2
    assert {edge.source for edge in actual} == {
        "_:genid2147483648",
        "_:genid2147483649",
    }
    counters = projector.last_encoded_counters
    assert counters is not None
    assert counters.referenced_segments == 3
    assert counters.composite_member_segments == 2
    assert counters.scope_map_rows_inspected >= 2
    assert counters.source_roots_inspected == counters.roots_inspected == 2
    assert counters.selected_roots == 2
    assert counters.anonymous_individuals == 2
    assert counters.scalar_fallbacks == 0


def test_composite_members_merge_exact_order_indexes_and_bridge() -> None:
    left = _snapshot(
        "SubClassOf(:Z :Top) ObjectPropertyDomain(:p :D) SubObjectPropertyOf(:child :p)"
    )
    right = _snapshot(
        "SubClassOf(:AA :Top) ObjectPropertyRange(:p :R) "
        "InverseObjectProperties(:p :pinv) ClassAssertion(:AA :i)"
    )
    bridge = _snapshot("Declaration(Class(:Bridge))")
    bridge_axioms = tuple(bridge.iter_axioms())  # type: ignore[attr-defined]
    composite = compose_views(
        left,
        right,
        delta=OntologyDelta(add_axioms=CanonicalSet(bridge_axioms)),
    )
    left_lease = _lease(left)
    right_lease = _lease(right)
    lease = _semantic_composite_lease(
        composite,
        (left_lease, right_lease),
        bridge=_lease(bridge),
    )
    prepared, negotiation, initial = prepare_encoded_subset_compilation(
        composite,
        ProjectionOptions(backend="native", order="encounter"),
        EncodedNegotiation("encoded-native", lease=lease),
        batch_edges=1,
    )
    assert prepared is not None
    assert negotiation.path == "encoded-native"
    assert initial is not None
    assert {item.owner for item in prepared._retained_leases} == {left, right}
    cases = (
        ProjectionOptions(backend="python", order="encounter"),
        ProjectionOptions(
            backend="python",
            bidirectional_taxonomy=True,
            duplicates="unique",
            order="canonical",
        ),
    )
    expected: list[tuple[list[Edge], dict[str, object]]] = []
    for options in cases:
        scalar = Projector()
        edges = scalar.project(composite, options=options)
        assert scalar.last_report is not None
        expected.append((edges, scalar.last_report.to_dict()))

    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("composite crossed scalar axiom traversal"),
        ),
    ):
        for options, (scalar_edges, scalar_report) in zip(cases, expected, strict=True):
            projector = Projector()
            actual = projector.project(
                composite,
                options=replace(options, backend="native"),
            )

            assert actual == scalar_edges
            assert Edge("urn:slice#D", "urn:slice#p", "urn:slice#R") in actual
            assert Edge("urn:slice#D", "urn:slice#child", "urn:slice#R") in actual
            assert Edge("urn:slice#R", "urn:slice#pinv", "urn:slice#D") in actual
            assert projector.last_report is not None
            assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(
                scalar_report
            )
            counters = projector.last_encoded_counters
            assert counters is not None
            assert counters.referenced_segments == 2
            assert counters.composite_member_segments == 2
            assert counters.bridge_roots_inspected == 1
            assert counters.source_roots_inspected == 7
            assert counters.roots_inspected == counters.selected_roots == 8
            assert counters.deduplicated_roots == 0
            assert counters.canonical_bytes_compared > 0
            assert counters.scalar_fallbacks == 0


def test_composite_structurally_deduplicates_equal_member_roots() -> None:
    left = _snapshot("SubClassOf(:A :B)")
    right = _snapshot("SubClassOf(:A :B)")
    composite = compose_views(left, right)
    lease = _semantic_composite_lease(
        composite,
        (_lease(left), _lease(right)),
    )
    expected = Projector().project(
        composite,
        options=ProjectionOptions(backend="python", order="encounter"),
    )

    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("deduplicated composite crossed scalar traversal"),
        ),
    ):
        projector = Projector()
        actual = projector.project(
            composite,
            options=ProjectionOptions(backend="native", order="encounter"),
        )

    assert actual == expected == [Edge("urn:slice#A", SUBCLASS_OF, "urn:slice#B")]
    counters = projector.last_encoded_counters
    assert counters is not None
    assert counters.roots_inspected == 2
    assert counters.source_roots_inspected == 2
    assert counters.selected_roots == counters.subclass_axioms == 1
    assert counters.deduplicated_roots == 1
    assert counters.composite_member_segments == counters.referenced_segments == 2
    assert counters.scalar_fallbacks == 0


def test_recursive_composite_postings_address_only_source_local_roots() -> None:
    nested_left = _snapshot("SubClassOf(:NestedA :Top)")
    nested_right = _snapshot("SubClassOf(:NestedB :Top)")
    inner_bridge = _snapshot("SubClassOf(:LocalDrop :Top) SubClassOf(:LocalKeep :Top)")
    bridge_axioms = tuple(inner_bridge.iter_axioms())  # type: ignore[attr-defined]
    keep_id = next(
        index
        for index, axiom in enumerate(bridge_axioms, 1)
        if b"LocalKeep" in canonical_bytes(axiom)
    )
    drop_id = next(
        index
        for index, axiom in enumerate(bridge_axioms, 1)
        if b"LocalDrop" in canonical_bytes(axiom)
    )
    inner = compose_views(
        nested_left,
        nested_right,
        delta=OntologyDelta(add_axioms=CanonicalSet(bridge_axioms)),
    )
    inner_lease = _semantic_composite_lease(
        inner,
        (_lease(nested_left), _lease(nested_right)),
        bridge=_lease(inner_bridge),
    )
    other = _snapshot("SubClassOf(:Other :Top)")
    other_lease = _lease(other)
    cases = (
        (
            1,
            keep_id,
            "SubClassOf(:LocalKeep :Top) SubClassOf(:Other :Top)",
            2,
        ),
        (
            2,
            drop_id,
            "SubClassOf(:NestedA :Top) SubClassOf(:NestedB :Top) "
            "SubClassOf(:LocalKeep :Top) SubClassOf(:Other :Top)",
            4,
        ),
    )

    for posting_mode, posting_id, body, expected_roots in cases:
        target = _snapshot(body)
        lease = _composite_lease(
            target,
            (
                _CompositeMemberFixture(
                    inner_lease,
                    b"a" * 32,
                    posting_mode=posting_mode,
                    postings=_postings(posting_id),
                ),
                _CompositeMemberFixture(other_lease, b"b" * 32),
            ),
        )
        scalar_options = ProjectionOptions(backend="python", order="encounter")
        expected = Projector().project(target, options=scalar_options)
        prepared, negotiation, initial = prepare_encoded_subset_compilation(
            target,
            ProjectionOptions(backend="native", order="encounter"),
            EncodedNegotiation("encoded-native", lease=lease),
            batch_edges=1,
        )
        assert prepared is not None
        assert negotiation.path == "encoded-native"
        assert initial is not None
        assert {id(item.owner) for item in prepared._retained_leases} == {
            id(inner),
            id(nested_left),
            id(nested_right),
            id(other),
        }

        with (
            _forced_encoded(lease),
            patch.object(
                api_module,
                "prepare_streaming_compilation",
                side_effect=AssertionError("recursive composite crossed scalar traversal"),
            ),
        ):
            projector = Projector()
            actual = projector.project(
                target,
                options=replace(scalar_options, backend="native"),
            )

        assert actual == expected
        assert len(actual) == expected_roots
        counters = projector.last_encoded_counters
        assert counters is not None
        assert counters.referenced_segments == 4
        assert counters.composite_member_segments == 4
        assert counters.bridge_roots_inspected == 2
        assert counters.posting_rows_inspected == 1
        assert counters.source_roots_inspected == counters.roots_inspected == 5
        assert counters.selected_roots == expected_roots
        assert counters.scalar_fallbacks == 0


def test_composite_recursively_resolves_overlay_member_and_retains_every_lease() -> None:
    base = _snapshot("SubClassOf(:Base :Top)")
    delta = _snapshot("SubClassOf(:Delta :Top)")
    delta_axioms = tuple(delta.iter_axioms())  # type: ignore[attr-defined]
    overlay = apply_delta(
        base,  # type: ignore[arg-type]
        OntologyDelta(add_axioms=CanonicalSet(delta_axioms)),
    )
    overlay_lease = _overlay_delta_lease(
        overlay,
        _lease(base),
        _lease(delta),
    )
    other = _snapshot("SubClassOf(:Other :Top)")
    target = compose_views(overlay, other)
    lease = _composite_lease(
        target,
        (
            _CompositeMemberFixture(overlay_lease, b"a" * 32),
            _CompositeMemberFixture(_lease(other), b"b" * 32),
        ),
    )
    options = ProjectionOptions(backend="python", order="encounter")
    expected = Projector().project(target, options=options)
    prepared, negotiation, initial = prepare_encoded_subset_compilation(
        target,
        replace(options, backend="native"),
        EncodedNegotiation("encoded-native", lease=lease),
        batch_edges=1,
    )
    assert prepared is not None
    assert negotiation.path == "encoded-native"
    assert initial is not None
    assert {id(item.owner) for item in prepared._retained_leases} == {
        id(overlay),
        id(base),
        id(other),
    }

    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("overlay-member composite crossed scalar traversal"),
        ),
    ):
        projector = Projector()
        actual = projector.project(
            target,
            options=replace(options, backend="native"),
        )

    assert actual == expected
    assert len(actual) == 3
    counters = projector.last_encoded_counters
    assert counters is not None
    assert counters.referenced_segments == 3
    assert counters.composite_member_segments == 2
    assert counters.delta_roots_inspected == 1
    assert counters.source_roots_inspected == counters.roots_inspected == 3
    assert counters.selected_roots == 3
    assert counters.scalar_fallbacks == 0


def test_recursive_composite_composes_anonymous_scope_maps_exactly() -> None:
    body = "ObjectPropertyAssertion(:p _:x :i)"
    left = _snapshot(body)
    right = _snapshot(body)
    third = _snapshot(body)
    inner = compose_views(left, right)
    inner_lease = _semantic_composite_lease(
        inner,
        (_lease(left), _lease(right)),
    )
    outer = compose_views(inner, third)
    inner_mappings = cast(
        tuple[Mapping[bytes, bytes], ...],
        cast(Any, inner)._scope_replacements(),
    )
    outer_mappings = cast(
        tuple[Mapping[bytes, bytes], ...],
        cast(Any, outer)._scope_replacements(),
    )
    assert len(inner_mappings) == 2
    assert len(outer_mappings) == 3
    inner_to_outer: dict[bytes, bytes] = {}
    for inner_mapping, outer_mapping in zip(
        inner_mappings,
        outer_mappings[:2],
        strict=True,
    ):
        for original_scope in inner_mapping.keys() | outer_mapping.keys():
            current_scope = inner_mapping.get(original_scope, original_scope)
            target_scope = outer_mapping.get(original_scope, original_scope)
            if current_scope != target_scope:
                inner_to_outer[current_scope] = target_scope
    lease = _composite_lease(
        outer,
        (
            _CompositeMemberFixture(
                inner_lease,
                b"a" * 32,
                scope_map=_scope_map(*inner_to_outer.items()),
            ),
            _CompositeMemberFixture(
                _lease(third),
                b"b" * 32,
                scope_map=_scope_map(*outer_mappings[2].items()),
            ),
        ),
    )
    options = ProjectionOptions(backend="python", order="encounter")
    expected = Projector().project(outer, options=options)

    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("scope-composed composite crossed scalar traversal"),
        ),
    ):
        projector = Projector()
        actual = projector.project(
            outer,
            options=replace(options, backend="native"),
        )

    assert actual == expected
    assert len(actual) == 3
    assert {edge.source for edge in actual} == {
        "_:genid2147483648",
        "_:genid2147483649",
        "_:genid2147483650",
    }
    counters = projector.last_encoded_counters
    assert counters is not None
    assert counters.referenced_segments == counters.composite_member_segments == 4
    assert counters.scope_map_rows_inspected > 0
    assert counters.source_roots_inspected == counters.roots_inspected == 3
    assert counters.selected_roots == 3
    assert counters.anonymous_individuals == 3
    assert counters.canonical_bytes_compared > 0
    assert counters.scalar_fallbacks == 0


def test_composite_order_changing_scope_map_falls_back_after_full_preflight() -> None:
    left = _snapshot("ObjectPropertyAssertion(:left _:a :i)")
    right = _snapshot("ObjectPropertyAssertion(:right _:b :j)")
    source = compose_views(left, right)  # type: ignore[arg-type]
    source_lease = _lease(source)
    anonymous_nodes = _anonymous_node_ids(source_lease)
    assert len(anonymous_nodes) == 2
    scopes = tuple(_anonymous_scope(source_lease, node_id) for node_id in anonymous_nodes)
    assert scopes[0] < scopes[1]
    other = _snapshot("SubClassOf(:Other :Top)")
    target = compose_views(source, other)  # type: ignore[arg-type]
    lease = _composite_lease(
        target,
        (
            _CompositeMemberFixture(
                source_lease,
                b"a" * 32,
                scope_map=_scope_map(
                    (scopes[0], b"\xff" * 32),
                    (scopes[1], b"\x00" * 32),
                ),
            ),
            _CompositeMemberFixture(_lease(other), b"b" * 32),
        ),
    )

    compilation, negotiation, counters = prepare_encoded_subset_compilation(
        target,
        ProjectionOptions(backend="native", order="encounter"),
        EncodedNegotiation("encoded-native", lease=lease),
        batch_edges=1,
    )

    assert compilation is None
    assert negotiation.path == "scalar-native"
    assert "scope remap does not preserve canonical order" in (negotiation.reason or "")
    assert counters is not None
    assert counters.referenced_segments == counters.composite_member_segments == 2
    assert counters.scope_map_rows_inspected == 2
    assert counters.source_roots_inspected == counters.roots_inspected == 3
    assert counters.selected_roots == 3
    assert counters.scalar_fallbacks == 1
    assert counters.edge_batches == counters.raw_edges == 0


def test_composite_scope_fallback_does_not_mask_later_hostile_source() -> None:
    left = _snapshot("ObjectPropertyAssertion(:left _:a :i)")
    right = _snapshot("ObjectPropertyAssertion(:right _:b :j)")
    source = compose_views(left, right)  # type: ignore[arg-type]
    source_lease = _lease(source)
    anonymous_nodes = _anonymous_node_ids(source_lease)
    scopes = tuple(_anonymous_scope(source_lease, node_id) for node_id in anonymous_nodes)
    assert len(scopes) == 2
    other = _snapshot("SubClassOf(:A :Top) SubClassOf(:B :Top)")
    other_lease = _lease(other)
    hostile_buffers = dict(other_lease.buffers)
    root_ids = bytes(hostile_buffers["root_ids"])
    assert len(root_ids) == 8
    hostile_buffers["root_ids"] = memoryview(root_ids[4:] + root_ids[:4])
    frozen_buffers = MappingProxyType(hostile_buffers)
    hostile_encoded = replace(other_lease.encoded_view, buffers=frozen_buffers)
    hostile_other = replace(
        other_lease,
        encoded_view=hostile_encoded,
        buffers=frozen_buffers,
    )
    target = compose_views(source, other)  # type: ignore[arg-type]
    lease = _composite_lease(
        target,
        (
            _CompositeMemberFixture(
                source_lease,
                b"a" * 32,
                scope_map=_scope_map(
                    (scopes[0], b"\xff" * 32),
                    (scopes[1], b"\x00" * 32),
                ),
            ),
            _CompositeMemberFixture(hostile_other, b"b" * 32),
        ),
    )

    with pytest.raises(SnapshotCompatibilityError, match="roots are not canonical"):
        prepare_encoded_subset_compilation(
            target,
            ProjectionOptions(backend="native"),
            EncodedNegotiation("encoded-native", lease=lease),
            batch_edges=1,
        )


def test_composite_supported_family_falls_back_for_unsupported_axiom_slice() -> None:
    left = _snapshot("DisjointClasses(:A :B)")
    right = _snapshot("SubClassOf(:B :Top)")
    target = compose_views(left, right)
    lease = _semantic_composite_lease(
        target,
        (_lease(left), _lease(right)),
    )

    compilation, negotiation, counters = prepare_encoded_subset_compilation(
        target,
        ProjectionOptions(backend="native"),
        EncodedNegotiation("encoded-native", lease=lease),
        batch_edges=1,
    )

    assert compilation is None
    assert negotiation.path == "scalar-native"
    assert "outside the executable axiom slice" in (negotiation.reason or "")
    assert counters is not None
    assert counters.referenced_segments == counters.composite_member_segments == 2
    assert counters.roots_inspected == counters.source_roots_inspected == 2
    assert counters.scalar_fallbacks == 1
    assert counters.edge_batches == counters.raw_edges == 0


def test_segmented_overlay_order_changing_scope_map_falls_back_after_preflight() -> None:
    anonymous_left = _snapshot("ObjectPropertyAssertion(:left _:a :i)")
    anonymous_right = _snapshot("ObjectPropertyAssertion(:right _:b :j)")
    anonymous = compose_views(anonymous_left, anonymous_right)
    anonymous_lease = _lease(anonymous)
    other = _snapshot("SubClassOf(:Other :Top)")
    inner = compose_views(anonymous, other)
    inner_lease = _composite_lease(
        inner,
        (
            _CompositeMemberFixture(anonymous_lease, b"a" * 32),
            _CompositeMemberFixture(_lease(other), b"b" * 32),
        ),
    )
    current_scopes = sorted(
        _anonymous_scope(anonymous_lease, node_id)
        for node_id in _anonymous_node_ids(anonymous_lease)
    )
    assert len(current_scopes) == 2
    overlay = apply_delta(inner, OntologyDelta())
    lease = _overlay_base_lease(
        overlay,
        inner_lease,
        scope_map=_scope_map(
            (current_scopes[0], b"\xff" * 32),
            (current_scopes[1], b"\x00" * 32),
        ),
    )

    compilation, negotiation, counters = prepare_encoded_subset_compilation(
        overlay,
        ProjectionOptions(backend="native", order="encounter"),
        EncodedNegotiation("encoded-native", lease=lease),
        batch_edges=1,
    )

    assert compilation is None
    assert negotiation.path == "scalar-native"
    assert "scope remap does not preserve canonical order" in (negotiation.reason or "")
    assert counters is not None
    assert counters.referenced_segments == 3
    assert counters.composite_member_segments == 2
    assert counters.scope_map_rows_inspected >= 2
    assert counters.source_roots_inspected == counters.roots_inspected == 3
    assert counters.selected_roots == 3
    assert counters.scalar_fallbacks == 1
    assert counters.edge_batches == counters.raw_edges == 0


def test_segmented_overlay_scope_fallback_does_not_mask_hostile_nested_source() -> None:
    anonymous_left = _snapshot("ObjectPropertyAssertion(:left _:a :i)")
    anonymous_right = _snapshot("ObjectPropertyAssertion(:right _:b :j)")
    anonymous = compose_views(anonymous_left, anonymous_right)
    anonymous_lease = _lease(anonymous)
    other = _snapshot("SubClassOf(:A :Top) SubClassOf(:B :Top)")
    other_lease = _lease(other)
    hostile_buffers = dict(other_lease.buffers)
    root_ids = bytes(hostile_buffers["root_ids"])
    assert len(root_ids) == 8
    hostile_buffers["root_ids"] = memoryview(root_ids[4:] + root_ids[:4])
    frozen_buffers = MappingProxyType(hostile_buffers)
    hostile_encoded = replace(other_lease.encoded_view, buffers=frozen_buffers)
    hostile_other = replace(
        other_lease,
        encoded_view=hostile_encoded,
        buffers=frozen_buffers,
    )
    inner = compose_views(anonymous, other)
    inner_lease = _composite_lease(
        inner,
        (
            _CompositeMemberFixture(anonymous_lease, b"a" * 32),
            _CompositeMemberFixture(hostile_other, b"b" * 32),
        ),
    )
    current_scopes = sorted(
        _anonymous_scope(anonymous_lease, node_id)
        for node_id in _anonymous_node_ids(anonymous_lease)
    )
    assert len(current_scopes) == 2
    overlay = apply_delta(inner, OntologyDelta())
    lease = _overlay_base_lease(
        overlay,
        inner_lease,
        scope_map=_scope_map(
            (current_scopes[0], b"\xff" * 32),
            (current_scopes[1], b"\x00" * 32),
        ),
    )

    with pytest.raises(SnapshotCompatibilityError, match="roots are not canonical"):
        prepare_encoded_subset_compilation(
            overlay,
            ProjectionOptions(backend="native"),
            EncodedNegotiation("encoded-native", lease=lease),
            batch_edges=1,
        )


def test_segmented_overlay_cycle_fails_before_output() -> None:
    left = _snapshot("SubClassOf(:A :Top)")
    right = _snapshot("SubClassOf(:B :Top)")
    inner = compose_views(left, right)
    inner_lease = _composite_lease(
        inner,
        (
            _CompositeMemberFixture(_lease(left), b"a" * 32),
            _CompositeMemberFixture(_lease(right), b"b" * 32),
        ),
    )
    overlay = apply_delta(inner, OntologyDelta())
    overlay_lease = _overlay_base_lease(overlay, inner_lease)
    inner_first = cast(_SegmentFixture, inner_lease.segments[0])
    inner_first.owner = overlay
    inner_first.source = overlay_lease.encoded_view

    with pytest.raises(SnapshotCompatibilityError, match="segment graph is cyclic"):
        prepare_encoded_subset_compilation(
            overlay,
            ProjectionOptions(backend="native"),
            EncodedNegotiation("encoded-native", lease=overlay_lease),
            batch_edges=1,
        )


def test_recursive_composite_cycle_fails_before_output() -> None:
    left = _snapshot("SubClassOf(:A :Top)")
    right = _snapshot("SubClassOf(:B :Top)")
    inner = compose_views(left, right)
    inner_lease = _composite_lease(
        inner,
        (
            _CompositeMemberFixture(_lease(left), b"a" * 32),
            _CompositeMemberFixture(_lease(right), b"b" * 32),
        ),
    )
    third = _snapshot("SubClassOf(:C :Top)")
    outer = compose_views(inner, third)
    outer_lease = _composite_lease(
        outer,
        (
            _CompositeMemberFixture(inner_lease, b"a" * 32),
            _CompositeMemberFixture(_lease(third), b"b" * 32),
        ),
    )
    inner_first = cast(_SegmentFixture, inner_lease.segments[0])
    inner_first.owner = outer
    inner_first.source = outer_lease.encoded_view

    with pytest.raises(SnapshotCompatibilityError, match="segment graph is cyclic"):
        prepare_encoded_subset_compilation(
            outer,
            ProjectionOptions(backend="native"),
            EncodedNegotiation("encoded-native", lease=outer_lease),
            batch_edges=1,
        )


@pytest.mark.parametrize(
    ("corruption", "match"),
    [
        ("role", "roles"),
        ("owner", "member metadata"),
        ("source", "member metadata"),
        ("cycle", "direct cycle"),
        ("mode", "posting mode"),
        ("all-posting", "ALL segment"),
        ("include-empty", "INCLUDE/EXCLUDE"),
        ("posting-range", "source-local"),
        ("posting-partial", "fixed-width layout"),
        ("scope-identity", "identity row"),
        ("scope-unsorted", "sources are not sorted unique"),
        ("token-short", "member metadata"),
        ("token-duplicate", "tokens are not sorted unique"),
        ("token-order", "tokens are not sorted unique"),
        ("local-columns", "nonempty local columns"),
    ],
)
def test_composite_hostile_member_metadata_fails_before_output(
    corruption: str,
    match: str,
) -> None:
    left = _snapshot("SubClassOf(:A :Top)")
    right = _snapshot("SubClassOf(:B :Top)")
    target = compose_views(left, right)
    left_lease = _lease(left)
    right_lease = _lease(right)
    lease = _composite_lease(
        target,
        (
            _CompositeMemberFixture(left_lease, b"a" * 32),
            _CompositeMemberFixture(right_lease, b"b" * 32),
        ),
    )
    first, second = lease.segments
    if corruption == "role":
        first = replace(first, role=3)
    elif corruption == "owner":
        first = replace(first, owner=object())
    elif corruption == "source":
        first = replace(first, source=None)
    elif corruption == "cycle":
        first = replace(first, owner=target, source=lease.encoded_view)
    elif corruption == "mode":
        first = replace(first, posting_mode=9)
    elif corruption == "all-posting":
        first = replace(first, root_ids=_postings(1))
    elif corruption == "include-empty":
        first = replace(first, posting_mode=1)
    elif corruption == "posting-range":
        first = replace(first, posting_mode=1, root_ids=_postings(2))
    elif corruption == "posting-partial":
        first = replace(first, root_ids=memoryview(b"\x01"))
    elif corruption == "scope-identity":
        first = replace(first, anonymous_scope_map=_scope_map((b"a" * 32, b"a" * 32)))
    elif corruption == "scope-unsorted":
        first = replace(
            first,
            anonymous_scope_map=memoryview(b"b" * 32 + b"c" * 32 + b"a" * 32 + b"d" * 32),
        )
    elif corruption == "token-short":
        first = replace(first, member_token=b"short")
    elif corruption == "token-duplicate":
        second = replace(second, member_token=b"a" * 32)
    elif corruption == "token-order":
        first = replace(first, member_token=b"z" * 32)
    hostile = replace(lease, segments=(first, second))
    if corruption == "local-columns":
        hostile = replace(hostile, buffers=left_lease.buffers)

    with pytest.raises(SnapshotCompatibilityError, match=match):
        prepare_encoded_subset_compilation(
            target,
            ProjectionOptions(backend="native"),
            EncodedNegotiation("encoded-native", lease=hostile),
            batch_edges=1,
        )


@pytest.mark.parametrize(
    ("corruption", "match"),
    [
        ("owner", "bridge metadata"),
        ("source", "bridge metadata"),
        ("mode", "bridge metadata"),
        ("posting", "bridge metadata"),
        ("scope-map", "bridge metadata"),
        ("member-token", "bridge metadata"),
        ("empty-local", "bridge metadata"),
    ],
)
def test_composite_hostile_bridge_metadata_fails_before_output(
    corruption: str,
    match: str,
) -> None:
    left = _snapshot("SubClassOf(:A :Top)")
    right = _snapshot("SubClassOf(:B :Top)")
    bridge = _snapshot("SubClassOf(:Bridge :Top)")
    target = compose_views(
        left,
        right,
        delta=OntologyDelta(
            add_axioms=CanonicalSet(tuple(bridge.iter_axioms())),  # type: ignore[attr-defined]
        ),
    )
    left_lease = _lease(left)
    lease = _semantic_composite_lease(
        target,
        (left_lease, _lease(right)),
        bridge=_lease(bridge),
    )
    *members, bridge_segment = lease.segments
    if corruption == "owner":
        bridge_segment = replace(bridge_segment, owner=object())
    elif corruption == "source":
        bridge_segment = replace(
            bridge_segment,
            owner=left,
            source=left_lease.encoded_view,
        )
    elif corruption == "mode":
        bridge_segment = replace(bridge_segment, posting_mode=1)
    elif corruption == "posting":
        bridge_segment = replace(bridge_segment, root_ids=_postings(1))
    elif corruption == "scope-map":
        bridge_segment = replace(
            bridge_segment,
            anonymous_scope_map=_scope_map((b"a" * 32, b"b" * 32)),
        )
    elif corruption == "member-token":
        bridge_segment = replace(bridge_segment, member_token=b"c" * 32)
    hostile = replace(lease, segments=(*members, bridge_segment))
    if corruption == "empty-local":
        hostile = replace(hostile, buffers=_lease(_snapshot("")).buffers)

    with pytest.raises(SnapshotCompatibilityError, match=match):
        prepare_encoded_subset_compilation(
            target,
            ProjectionOptions(backend="native"),
            EncodedNegotiation("encoded-native", lease=hostile),
            batch_edges=1,
        )


def test_overlay_scope_remap_that_reorders_identities_falls_back_before_output() -> None:
    left = _snapshot("ObjectPropertyAssertion(:left _:a :i)")
    right = _snapshot("ObjectPropertyAssertion(:right _:b :j)")
    source = compose_views(left, right)  # type: ignore[arg-type]
    source_lease = _lease(source)
    anonymous_nodes = _anonymous_node_ids(source_lease)
    assert len(anonymous_nodes) == 2
    scopes = tuple(_anonymous_scope(source_lease, node_id) for node_id in anonymous_nodes)
    assert scopes[0] < scopes[1]
    overlay = apply_delta(source, OntologyDelta())
    lease = _overlay_base_lease(
        overlay,
        source_lease,
        scope_map=_scope_map(
            (scopes[0], b"\xff" * 32),
            (scopes[1], b"\x00" * 32),
        ),
    )

    compilation, negotiation, counters = prepare_encoded_subset_compilation(
        overlay,
        ProjectionOptions(backend="native", order="encounter"),
        EncodedNegotiation("encoded-native", lease=lease),
        batch_edges=1,
    )

    assert compilation is None
    assert negotiation.path == "scalar-native"
    assert "scope remap does not preserve canonical order" in (negotiation.reason or "")
    assert counters is not None
    assert counters.referenced_segments == 1
    assert counters.scope_map_rows_inspected == 2
    assert counters.source_roots_inspected == counters.selected_roots == 2
    assert counters.scalar_fallbacks == 1
    assert counters.edge_batches == counters.raw_edges == 0


def test_overlay_delta_order_changing_scope_map_falls_back_after_full_preflight() -> None:
    left = _snapshot("ObjectPropertyAssertion(:left _:a :i)")
    right = _snapshot("ObjectPropertyAssertion(:right _:b :j)")
    source = compose_views(left, right)  # type: ignore[arg-type]
    source_lease = _lease(source)
    anonymous_nodes = _anonymous_node_ids(source_lease)
    assert len(anonymous_nodes) == 2
    scopes = tuple(_anonymous_scope(source_lease, node_id) for node_id in anonymous_nodes)
    assert scopes[0] < scopes[1]
    delta = _snapshot("SubClassOf(:A :B)")
    overlay = apply_delta(
        source,
        OntologyDelta(
            add_axioms=CanonicalSet(tuple(delta.iter_axioms())),  # type: ignore[attr-defined]
        ),
    )
    lease = _overlay_delta_lease(
        overlay,
        source_lease,
        _lease(delta),
        scope_map=_scope_map(
            (scopes[0], b"\xff" * 32),
            (scopes[1], b"\x00" * 32),
        ),
    )

    compilation, negotiation, counters = prepare_encoded_subset_compilation(
        overlay,
        ProjectionOptions(backend="native", order="encounter"),
        EncodedNegotiation("encoded-native", lease=lease),
        batch_edges=1,
    )

    assert compilation is None
    assert negotiation.path == "scalar-native"
    assert "scope remap does not preserve canonical order" in (negotiation.reason or "")
    assert counters is not None
    assert counters.referenced_segments == 1
    assert counters.scope_map_rows_inspected == 2
    assert counters.source_roots_inspected == 2
    assert counters.delta_roots_inspected == 1
    assert counters.selected_roots == 3
    assert counters.scalar_fallbacks == 1
    assert counters.edge_batches == counters.raw_edges == 0


@pytest.mark.parametrize(
    ("corruption", "match"),
    [
        ("posting-partial", "fixed-width layout"),
        ("posting-writable", "readonly memoryview"),
        ("posting-out-of-range", "sorted unique in-range"),
        ("posting-duplicate", "sorted unique in-range"),
        ("scope-identity", "identity row"),
        ("scope-unsorted", "sources are not sorted unique"),
        ("member-token", "member token"),
        ("owner-mismatch", "referenced owner"),
        ("local-columns", "nonempty local columns"),
    ],
)
def test_overlay_base_hostile_metadata_fails_before_output(
    corruption: str,
    match: str,
) -> None:
    base = _snapshot("ObjectPropertyAssertion(:p _:a :i) ObjectPropertyAssertion(:p _:z :j)")
    source_lease = _lease(base)
    axioms = tuple(base.iter_axioms())  # type: ignore[attr-defined]
    overlay = apply_delta(
        base,  # type: ignore[arg-type]
        OntologyDelta(remove_axioms=CanonicalSet((axioms[0],))),
    )
    lease = _overlay_base_lease(
        overlay,
        source_lease,
        posting_mode=2,
        postings=_postings(1),
        local_columns_empty=corruption != "local-columns",
    )
    segment = lease.segments[0]
    if corruption == "posting-partial":
        segment = replace(segment, root_ids=memoryview(b"\x01"))
    elif corruption == "posting-writable":
        segment = replace(segment, root_ids=memoryview(bytearray((1).to_bytes(4, "little"))))
    elif corruption == "posting-out-of-range":
        segment = replace(segment, root_ids=_postings(3))
    elif corruption == "posting-duplicate":
        segment = replace(segment, root_ids=_postings(1, 1))
    elif corruption == "scope-identity":
        scope = _anonymous_scope(source_lease, _anonymous_node_ids(source_lease)[0])
        segment = replace(segment, anonymous_scope_map=memoryview(scope + scope))
    elif corruption == "scope-unsorted":
        segment = replace(
            segment,
            anonymous_scope_map=memoryview(b"b" * 32 + b"c" * 32 + b"a" * 32 + b"d" * 32),
        )
    elif corruption == "member-token":
        segment = replace(segment, member_token=b"m" * 32)
    elif corruption == "owner-mismatch":
        segment = replace(segment, owner=object())
    hostile = replace(lease, segments=(segment,))

    with pytest.raises(SnapshotCompatibilityError, match=match):
        prepare_encoded_subset_compilation(
            overlay,
            ProjectionOptions(backend="native"),
            EncodedNegotiation("encoded-native", lease=hostile),
            batch_edges=1,
        )


@pytest.mark.parametrize(
    ("corruption", "match"),
    [
        ("role", "delta role"),
        ("owner", "delta metadata"),
        ("source", "delta metadata"),
        ("mode", "delta metadata"),
        ("posting", "unexpected postings"),
        ("posting-partial", "fixed-width layout"),
        ("scope-map", "unexpected postings or scope"),
        ("member-token", "delta metadata"),
        ("empty-local", "no local roots"),
    ],
)
def test_overlay_delta_hostile_metadata_fails_before_output(
    corruption: str,
    match: str,
) -> None:
    source = _snapshot("SubClassOf(:A :B)")
    delta = _snapshot("SubClassOf(:B :C)")
    target = _snapshot("SubClassOf(:A :B) SubClassOf(:B :C)")
    source_lease = _lease(source)
    lease = _overlay_delta_lease(target, source_lease, _lease(delta))
    base_segment, delta_segment = lease.segments
    if corruption == "role":
        delta_segment = replace(delta_segment, role=4)
    elif corruption == "owner":
        delta_segment = replace(delta_segment, owner=object())
    elif corruption == "source":
        delta_segment = replace(delta_segment, source=source_lease.encoded_view)
    elif corruption == "mode":
        delta_segment = replace(delta_segment, posting_mode=2)
    elif corruption == "posting":
        delta_segment = replace(delta_segment, root_ids=_postings(1))
    elif corruption == "posting-partial":
        delta_segment = replace(delta_segment, root_ids=memoryview(b"\x01"))
    elif corruption == "scope-map":
        delta_segment = replace(
            delta_segment,
            anonymous_scope_map=memoryview(b"a" * 32 + b"b" * 32),
        )
    elif corruption == "member-token":
        delta_segment = replace(delta_segment, member_token=b"m" * 32)
    hostile = replace(lease, segments=(base_segment, delta_segment))
    if corruption == "empty-local":
        hostile = replace(hostile, buffers=_lease(_snapshot("")).buffers)

    with pytest.raises(SnapshotCompatibilityError, match=match):
        prepare_encoded_subset_compilation(
            target,
            ProjectionOptions(backend="native"),
            EncodedNegotiation("encoded-native", lease=hostile),
            batch_edges=1,
        )


def test_overlay_delta_local_root_order_corruption_fails_before_output() -> None:
    source = _snapshot("SubClassOf(:A :B)")
    delta = _snapshot("SubClassOf(:B :C) SubClassOf(:C :D)")
    target = _snapshot("SubClassOf(:A :B) SubClassOf(:B :C) SubClassOf(:C :D)")
    lease = _overlay_delta_lease(target, _lease(source), _lease(delta))
    buffers = dict(lease.buffers)
    root_ids = bytes(buffers["root_ids"])
    assert len(root_ids) == 8
    buffers["root_ids"] = memoryview(root_ids[4:] + root_ids[:4])
    hostile = replace(lease, buffers=MappingProxyType(buffers))

    with pytest.raises(SnapshotCompatibilityError, match="canonical"):
        prepare_encoded_subset_compilation(
            target,
            ProjectionOptions(backend="native"),
            EncodedNegotiation("encoded-native", lease=hostile),
            batch_edges=1,
        )


def test_overlay_delta_structural_root_order_corruption_fails_before_output() -> None:
    source = _snapshot("SubClassOf(:Base :Top)")
    delta = _snapshot("SubClassOf(:A :Top) SubClassOf(:Z :Top)")
    target = _snapshot("SubClassOf(:A :Top) SubClassOf(:Base :Top) SubClassOf(:Z :Top)")
    lease = _overlay_delta_lease(target, _lease(source), _lease(delta))
    buffers = dict(lease.buffers)
    scalar_bytes = bytearray(buffers["scalar_bytes"])
    left = b"urn:slice#A"
    right = b"urn:slice#Z"
    left_offset = scalar_bytes.find(left)
    right_offset = scalar_bytes.find(right)
    assert left_offset >= 0
    assert right_offset >= 0
    scalar_bytes[left_offset : left_offset + len(left)] = right
    scalar_bytes[right_offset : right_offset + len(right)] = left
    buffers["scalar_bytes"] = memoryview(bytes(scalar_bytes))
    hostile = replace(lease, buffers=MappingProxyType(buffers))

    with pytest.raises(SnapshotCompatibilityError, match="canonical root group"):
        prepare_encoded_subset_compilation(
            target,
            ProjectionOptions(backend="native"),
            EncodedNegotiation("encoded-native", lease=hostile),
            batch_edges=1,
        )


def test_overlay_delta_structural_blank_order_corruption_fails_before_output() -> None:
    source = _snapshot("SubClassOf(:Base :Top)")
    delta = _snapshot("ObjectPropertyAssertion(:p _:a _:z)")
    target = _snapshot("SubClassOf(:Base :Top) ObjectPropertyAssertion(:p _:a _:z)")
    delta_lease = _lease(delta)
    lease = _overlay_delta_lease(target, _lease(source), delta_lease)
    anonymous_nodes = _anonymous_node_ids(delta_lease)
    assert len(anonymous_nodes) == 2
    left_offset, left_length = _anonymous_local_key_span(
        delta_lease,
        anonymous_nodes[0],
    )
    right_offset, right_length = _anonymous_local_key_span(
        delta_lease,
        anonymous_nodes[1],
    )
    assert left_length == right_length
    buffers = dict(lease.buffers)
    original = bytes(buffers["scalar_bytes"])
    scalar_bytes = bytearray(original)
    scalar_bytes[left_offset : left_offset + left_length] = original[
        right_offset : right_offset + right_length
    ]
    scalar_bytes[right_offset : right_offset + right_length] = original[
        left_offset : left_offset + left_length
    ]
    buffers["scalar_bytes"] = memoryview(bytes(scalar_bytes))
    hostile = replace(lease, buffers=MappingProxyType(buffers))

    with pytest.raises(SnapshotCompatibilityError, match="canonical node group"):
        prepare_encoded_subset_compilation(
            target,
            ProjectionOptions(backend="native"),
            EncodedNegotiation("encoded-native", lease=hostile),
            batch_edges=1,
        )


def test_overlay_base_revalidates_referenced_source_columns() -> None:
    base = _snapshot("ObjectPropertyAssertion(:p _:a :i)")
    source_lease = _lease(base)
    buffers = dict(source_lease.buffers)
    tags = buffers["node_tags"]
    assertion_id = next(
        node_id
        for node_id in range(1, tags.nbytes // 2 + 1)
        if int.from_bytes(tags[(node_id - 1) * 2 : node_id * 2], "little") == 113
    )
    offsets = bytearray(buffers["node_field_offsets"])
    end_offset = assertion_id * 8
    end = int.from_bytes(offsets[end_offset : end_offset + 8], "little")
    offsets[end_offset : end_offset + 8] = (end - 1).to_bytes(8, "little")
    buffers["node_field_offsets"] = memoryview(bytes(offsets))
    hostile_source = replace(
        source_lease.encoded_view,
        buffers=MappingProxyType(buffers),
    )
    overlay = apply_delta(base, OntologyDelta())  # type: ignore[arg-type]
    lease = _overlay_base_lease(overlay, source_lease)
    hostile_segment = replace(lease.segments[0], source=hostile_source)
    hostile = replace(lease, segments=(hostile_segment,))

    with pytest.raises(SnapshotCompatibilityError, match=r"offsets|arity"):
        prepare_encoded_subset_compilation(
            overlay,
            ProjectionOptions(backend="native"),
            EncodedNegotiation("encoded-native", lease=hostile),
            batch_edges=1,
        )


def test_selected_class_annotations_match_scalar_order_rendering_and_diagnostics() -> None:
    view = _snapshot(
        "Declaration(Class(:A)) Declaration(Class(:B)) SubClassOf(:A :B) "
        'AnnotationAssertion(<http://www.w3.org/2000/01/rdf-schema#label> :A "plain") '
        "AnnotationAssertion(<http://www.w3.org/2000/01/rdf-schema#comment> :A "
        '"typed"^^<http://www.w3.org/2001/XMLSchema#string>) '
        "AnnotationAssertion(<http://www.w3.org/2004/02/skos/core#prefLabel> "
        ":A <urn:value>) "
        "AnnotationAssertion(<http://www.w3.org/2000/01/rdf-schema#label> :A "
        '"7"^^<http://www.w3.org/2001/XMLSchema#integer>) '
        'AnnotationAssertion(<http://www.w3.org/2000/01/rdf-schema#label> :A "café") '
        'AnnotationAssertion(<http://www.w3.org/2000/01/rdf-schema#label> :A "bonjour"@fr) '
        "AnnotationAssertion(<http://www.w3.org/2000/01/rdf-schema#label> :A "
        '"a\\\\b"^^<urn:datatype>) '
        "AnnotationAssertion(<http://www.w3.org/2000/01/rdf-schema#label> :A _:anon) "
        'AnnotationAssertion(<urn:unsupported> :A "ignored-property") '
        "AnnotationAssertion(<http://www.w3.org/2000/01/rdf-schema#label> "
        '<urn:not-class> "ignored-subject") '
        'AnnotationAssertion(Annotation(<urn:meta> "m") '
        '<http://www.w3.org/2000/01/rdf-schema#label> :A "annotated") '
        'AnnotationAssertion(Annotation(<urn:meta> "duplicate") '
        '<http://www.w3.org/2000/01/rdf-schema#label> :A "plain") '
        "ClassAssertion(:A :i) ObjectPropertyAssertion(:p :i :j)"
    )
    lease = _lease(view)
    cases = (
        ProjectionOptions(backend="python", include_literals=True, order="encounter"),
        ProjectionOptions(
            backend="python",
            include_literals=True,
            duplicates="unique",
            order="canonical",
        ),
        ProjectionOptions(
            backend="python",
            include_literals=True,
            only_taxonomy=True,
            order="encounter",
        ),
        ProjectionOptions(backend="python", include_literals=False, order="encounter"),
    )
    expected: list[tuple[list[Edge], dict[str, object]]] = []
    for options in cases:
        scalar = Projector()
        edges = scalar.project(view, options=options)
        assert scalar.last_report is not None
        expected.append((edges, scalar.last_report.to_dict()))

    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("encoded annotation slice crossed scalar traversal"),
        ),
    ):
        for options, (scalar_edges, scalar_report) in zip(cases, expected, strict=True):
            projector = Projector()
            actual = list(
                projector.iter_edges(
                    view,
                    options=replace(options, backend="native"),
                    buffer_edges=2,
                )
            )

            assert actual == scalar_edges
            assert projector.last_report is not None
            assert projector.last_report.provenance.ingestion.path == "encoded-native"
            assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(
                scalar_report
            )
            counters = projector.last_encoded_counters
            assert counters is not None
            assert counters.roots_inspected == 17
            assert counters.annotation_assertion_axioms == 12
            assert counters.anonymous_individuals == 1
            assert counters.literal_nodes == 11
            assert counters.annotation_nodes == 2
            assert counters.scalar_fallbacks == 0
            if options.include_literals:
                assert counters.edge_batches == 7
                assert counters.raw_edges == 13
                assert len(actual) == (12 if options.duplicates == "unique" else 13)
                assert (
                    Edge(
                        "urn:slice#A",
                        "rdfs:label",
                        '7"^^xsd:intege',
                    )
                    in actual
                )
                assert (
                    Edge(
                        "urn:slice#A",
                        "rdfs:label",
                        "_:genid2147483648",
                    )
                    in actual
                )
                assert Edge("urn:slice#A", "rdfs:label", "café") in actual
                assert Edge("urn:slice#A", "rdfs:label", "bonjour") in actual
                assert (
                    Edge(
                        "urn:slice#A",
                        "rdfs:label",
                        'ab"^^<urn:datatype',
                    )
                    in actual
                )
                if options.order == "encounter":
                    assert actual[0] == Edge("urn:slice#A", SUBCLASS_OF, "urn:slice#B")
                    assert actual[-2:] == [
                        Edge("urn:slice#i", RDF_TYPE, "urn:slice#A"),
                        Edge("urn:slice#i", "urn:slice#p", "urn:slice#j"),
                    ]
            else:
                assert len(actual) == 3
                assert counters.edge_batches == 2
                assert counters.raw_edges == 3
                assert projector.last_report.provenance.counts.ignored_shapes == 0
                assert projector.last_report.provenance.counts.warnings == 0


def test_annotations_on_supported_axioms_match_scalar_edges_blanks_and_reports() -> None:
    view = _snapshot(
        "Declaration(Annotation(<urn:meta> _:metadata) Class(:A)) "
        'SubClassOf(Annotation(<urn:meta> "named") :A :B) '
        'SubClassOf(Annotation(<urn:meta> "restriction") '
        ":B ObjectSomeValuesFrom(:p :C)) "
        'EquivalentClasses(Annotation(<urn:meta> "named-equivalent") :E :F) '
        'EquivalentClasses(Annotation(<urn:meta> "aggregate") '
        ":G ObjectIntersectionOf(:H ObjectSomeValuesFrom(:p :I))) "
        'ClassAssertion(Annotation(<urn:meta> "class") :A :individual) '
        'ObjectPropertyAssertion(Annotation(<urn:meta> "one") :p _:edge :individual) '
        'ObjectPropertyAssertion(Annotation(<urn:meta> "two") :p _:edge :individual) '
        'ObjectPropertyDomain(Annotation(<urn:meta> "domain") :p :D) '
        'ObjectPropertyRange(Annotation(<urn:meta> "range") :p :R)'
    )
    lease = _lease(view)
    cases = (
        ProjectionOptions(backend="python", order="encounter"),
        ProjectionOptions(
            backend="python",
            bidirectional_taxonomy=True,
            duplicates="unique",
            order="canonical",
        ),
        ProjectionOptions(backend="python", only_taxonomy=True, order="encounter"),
    )
    expected: list[tuple[list[Edge], dict[str, object]]] = []
    for options in cases:
        scalar = Projector()
        edges = scalar.project(view, options=options)
        assert scalar.last_report is not None
        expected.append((edges, scalar.last_report.to_dict()))

    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("annotated supported axioms crossed scalar traversal"),
        ),
    ):
        for options, (scalar_edges, scalar_report) in zip(cases, expected, strict=True):
            projector = Projector()
            actual = list(
                projector.iter_edges(
                    view,
                    options=replace(options, backend="native"),
                    buffer_edges=2,
                )
            )

            assert actual == scalar_edges
            assert projector.last_report is not None
            assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(
                scalar_report
            )
            counters = projector.last_encoded_counters
            assert counters is not None
            assert counters.roots_inspected == 10
            assert counters.declaration_axioms == 1
            assert counters.subclass_axioms == 2
            assert counters.restriction_subclass_axioms == 1
            assert counters.equivalent_axioms == 2
            assert counters.aggregate_equivalent_axioms == 1
            assert counters.class_assertion_axioms == 1
            assert counters.object_property_assertion_axioms == 2
            assert counters.object_property_domain_axioms == 1
            assert counters.object_property_range_axioms == 1
            assert counters.annotation_nodes == 10
            assert counters.literal_nodes == 9
            assert counters.anonymous_individuals == 2
            assert counters.scalar_fallbacks == 0
            if options.duplicates == "preserve" and not options.only_taxonomy:
                duplicate = [
                    edge
                    for edge in actual
                    if edge.relation == "urn:slice#p"
                    and edge.destination == "urn:slice#individual"
                    and edge.source.startswith("_:genid")
                ]
                assert len(duplicate) == 2
                assert duplicate[0] == duplicate[1]


def test_annotation_oracle_fixture_matches_scalar_without_structural_traversal() -> None:
    view = pyowl_core.load_snapshot(
        ROOT / "tests" / "fixtures" / "oracle" / "annotations.ofn",
        options=LoadOptions(
            imports=ImportPolicy.IGNORE,
            backend=BackendPreference.PYTHON,
        ),
    )
    lease = _lease(view)
    cases = (
        ProjectionOptions(backend="python", include_literals=True, order="encounter"),
        ProjectionOptions(
            backend="python",
            include_literals=True,
            duplicates="unique",
            order="canonical",
        ),
        ProjectionOptions(backend="python", include_literals=False, order="encounter"),
    )
    expected: list[tuple[list[Edge], dict[str, object]]] = []
    for options in cases:
        scalar = Projector()
        edges = scalar.project(view, options=options)
        assert scalar.last_report is not None
        expected.append((edges, scalar.last_report.to_dict()))

    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("annotation oracle crossed scalar traversal"),
        ),
    ):
        for options, (scalar_edges, scalar_report) in zip(cases, expected, strict=True):
            projector = Projector()
            actual = list(
                projector.iter_edges(
                    view,
                    options=replace(options, backend="native"),
                    buffer_edges=7,
                )
            )

            assert actual == scalar_edges
            assert projector.last_report is not None
            assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(
                scalar_report
            )
            counters = projector.last_encoded_counters
            assert counters is not None
            assert counters.annotation_assertion_axioms == 46
            assert counters.literal_nodes == 44
            assert counters.scalar_fallbacks == 0
            if options.include_literals:
                assert projector.last_report.provenance.counts.ignored_shapes == 2
                assert projector.last_report.provenance.counts.warnings == 3
            else:
                assert actual == []
                assert projector.last_report.provenance.counts.ignored_shapes == 0
                assert projector.last_report.provenance.counts.warnings == 0


def test_imported_annotation_provenance_falls_back_only_when_observable() -> None:
    root = (
        b"Prefix(:=<urn:root#>) Ontology(<urn:root> Import(<urn:leaf>) "
        b"Declaration(Class(:A)) "
        b'AnnotationAssertion(<http://www.w3.org/2000/01/rdf-schema#label> :A "root"))'
    )
    leaf = (
        b"Prefix(:=<urn:leaf#>) Ontology(<urn:leaf> Declaration(Class(:L)) "
        b"SubClassOf(:L <urn:root#A>) "
        b'AnnotationAssertion(<http://www.w3.org/2000/01/rdf-schema#label> :L "leaf"))'
    )
    view = pyowl_core.load_snapshot(
        root,
        options=LoadOptions(
            imports=ImportPolicy.RESOLVE_LOCAL,
            backend=BackendPreference.PYTHON,
        ),
        resolver=pyowl_core.MappingResolver({"urn:leaf": leaf}),
    )
    lease = _lease(view)

    hidden_options = ProjectionOptions(backend="python", include_literals=False, order="encounter")
    hidden_expected = Projector().project(view, options=hidden_options)
    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("unobserved annotations crossed scalar traversal"),
        ),
    ):
        hidden_projector = Projector()
        hidden_actual = hidden_projector.project(
            view,
            options=replace(hidden_options, backend="native"),
        )
    assert hidden_actual == hidden_expected
    assert hidden_projector.last_report is not None
    assert hidden_projector.last_report.provenance.ingestion.path == "encoded-native"

    visible_options = replace(hidden_options, include_literals=True)
    scalar = Projector()
    visible_expected = scalar.project(view, options=visible_options)
    assert scalar.last_report is not None
    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            wraps=scalar_compilation,
        ) as scalar_prepare,
    ):
        visible_projector = Projector()
        visible_actual = visible_projector.project(
            view,
            options=replace(visible_options, backend="native"),
        )

    assert visible_actual == visible_expected
    assert {edge.destination for edge in visible_actual} >= {"urn:root#A", "root"}
    assert "leaf" not in {edge.destination for edge in visible_actual}
    assert scalar_prepare.call_count == 1
    assert visible_projector.last_report is not None
    assert _semantic_report(visible_projector.last_report.to_dict()) == _semantic_report(
        scalar.last_report.to_dict()
    )
    ingestion = visible_projector.last_report.provenance.ingestion
    assert ingestion.path == "scalar-native"
    assert "root-only annotation provenance" in (ingestion.reason or "")
    counters = visible_projector.last_encoded_counters
    assert counters is not None
    assert counters.annotation_assertion_axioms == 2
    assert counters.scalar_fallbacks == 1
    assert counters.edge_batches == 0
    assert counters.raw_edges == 0


def test_domain_range_slice_preserves_scala_instance_role_expansion() -> None:
    role_view = _snapshot("SubObjectPropertyOf(:child :p) InverseObjectProperties(:p :pinv)")
    domain_range_view = _snapshot("ObjectPropertyDomain(:p :D) ObjectPropertyRange(:p :R)")
    options = ProjectionOptions(
        backend="python",
        compatibility_state="scala-instance",
        order="encounter",
    )
    scalar = Projector()
    assert scalar.project(role_view, options=options) == []
    expected = scalar.project(domain_range_view, options=options)
    assert scalar.last_report is not None
    scalar_report = scalar.last_report.to_dict()

    projector = Projector()
    assert projector.project(role_view, options=options) == []
    lease = _lease(domain_range_view)
    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("encoded domain/range crossed scalar traversal"),
        ),
    ):
        actual = projector.project(
            domain_range_view,
            options=replace(options, backend="native"),
        )

    assert actual == expected
    assert actual == [
        Edge("urn:slice#D", "urn:slice#p", "urn:slice#R"),
        Edge("urn:slice#D", "urn:slice#child", "urn:slice#R"),
        Edge("urn:slice#R", "urn:slice#pinv", "urn:slice#D"),
    ]
    assert projector.last_report is not None
    assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(scalar_report)
    counters = projector.last_encoded_counters
    assert counters is not None
    assert counters.object_property_domain_axioms == 1
    assert counters.object_property_range_axioms == 1
    assert counters.raw_edges == 3
    assert counters.scalar_fallbacks == 0


def test_property_chain_role_axioms_match_scalar_without_role_state_mutation() -> None:
    unrelated_roles = " ".join(f"SubObjectPropertyOf(:x{index} :y{index})" for index in range(9))
    view = _snapshot(
        "SubObjectPropertyOf(:c0 :r) SubObjectPropertyOf(:c4 :r) "
        f"{unrelated_roles} "
        "SubObjectPropertyOf(ObjectPropertyChain(:z ObjectInverseOf(:a)) :r) "
        'SubObjectPropertyOf(Annotation(<urn:meta> "chain") '
        "ObjectPropertyChain(ObjectInverseOf(:q) :p :s) :r) "
        "ObjectPropertyDomain(:r :D) ObjectPropertyRange(:r :R)"
    )
    lease = _lease(view)
    cases = (
        ProjectionOptions(backend="python", order="encounter"),
        ProjectionOptions(
            backend="python",
            duplicates="unique",
            order="canonical",
        ),
        ProjectionOptions(
            backend="python",
            compatibility_state="scala-instance",
            order="encounter",
        ),
    )
    expected: list[tuple[list[Edge], dict[str, object]]] = []
    for options in cases:
        scalar = Projector()
        edges = scalar.project(view, options=options)
        assert scalar.last_report is not None
        expected.append((edges, scalar.last_report.to_dict()))

    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("property-chain slice crossed scalar traversal"),
        ),
    ):
        for options, (scalar_edges, scalar_report) in zip(cases, expected, strict=True):
            projector = Projector()
            actual = list(
                projector.iter_edges(
                    view,
                    options=replace(options, backend="native"),
                    buffer_edges=1,
                )
            )

            assert actual == scalar_edges
            assert set(actual) == {
                Edge("urn:slice#D", "urn:slice#r", "urn:slice#R"),
                Edge("urn:slice#D", "urn:slice#c0", "urn:slice#R"),
            }
            assert projector.last_report is not None
            assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(
                scalar_report
            )
            assert projector.last_report.provenance.ingestion.path == "encoded-native"
            assert projector.last_report.provenance.counts.ignored_shapes == 2
            assert projector.last_report.diagnostics == ()
            counters = projector.last_encoded_counters
            assert counters is not None
            assert counters.roots_inspected == 15
            assert counters.sub_object_property_axioms == 13
            assert counters.object_property_domain_axioms == 1
            assert counters.object_property_range_axioms == 1
            assert counters.edge_batches == counters.raw_edges == 2
            assert counters.scalar_fallbacks == 0


def test_equivalent_object_properties_match_scalar_skipped_diagnostics() -> None:
    view = _snapshot(
        "EquivalentObjectProperties(:p ObjectInverseOf(:q) :r) "
        "EquivalentObjectProperties(Annotation(<urn:meta> _:skipped) "
        "ObjectInverseOf(:p) :q) "
        "SubObjectPropertyOf(:child :p) ObjectPropertyDomain(:p :D) "
        "ObjectPropertyRange(:p :R) SubClassOf(:A :B) "
        "ObjectPropertyDomain(ObjectInverseOf(:p) :Ignored) "
        "ObjectPropertyAssertion(:u _:edge :i)"
    )
    lease = _lease(view)
    cases = (
        ProjectionOptions(backend="python", order="encounter"),
        ProjectionOptions(backend="python", duplicates="unique", order="canonical"),
        ProjectionOptions(
            backend="python",
            compatibility_state="scala-instance",
            order="encounter",
        ),
    )
    expected: list[tuple[list[Edge], dict[str, object]]] = []
    for options in cases:
        scalar = Projector()
        edges = scalar.project(view, options=options)
        assert scalar.last_report is not None
        expected.append((edges, scalar.last_report.to_dict()))

    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("equivalent-object-property slice crossed scalar traversal"),
        ),
    ):
        for options, (scalar_edges, scalar_report) in zip(cases, expected, strict=True):
            projector = Projector()
            actual = list(
                projector.iter_edges(
                    view,
                    options=replace(options, backend="native"),
                    buffer_edges=1,
                )
            )

            assert actual == scalar_edges
            assert set(actual) == {
                Edge("urn:slice#A", SUBCLASS_OF, "urn:slice#B"),
                Edge("_:genid2147483648", "urn:slice#u", "urn:slice#i"),
                Edge("urn:slice#D", "urn:slice#p", "urn:slice#R"),
                Edge("urn:slice#D", "urn:slice#child", "urn:slice#R"),
            }
            assert projector.last_report is not None
            assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(
                scalar_report
            )
            assert projector.last_report.provenance.ingestion.path == "encoded-native"
            assert projector.last_report.provenance.counts.skipped_axioms == 2
            assert projector.last_report.provenance.counts.ignored_shapes == 1
            assert projector.last_report.diagnostics == (
                ProjectionDiagnostic(
                    code="MOWL_IGNORED_SHAPE",
                    message="constructor does not emit an edge in the pinned profile",
                    count=1,
                    constructor="ObjectPropertyDomain",
                ),
                ProjectionDiagnostic(
                    code="MOWL_SKIPPED_AXIOM",
                    message="axiom category is not visited by the pinned profile",
                    count=2,
                    constructor="EquivalentObjectProperties",
                ),
            )
            counters = projector.last_encoded_counters
            assert counters is not None
            assert counters.roots_inspected == 8
            assert counters.equivalent_object_property_axioms == 2
            assert counters.sub_object_property_axioms == 1
            assert counters.object_property_assertion_axioms == 1
            assert counters.object_property_domain_axioms == 2
            assert counters.anonymous_individuals == 2
            assert counters.edge_batches == counters.raw_edges == 4
            assert counters.scalar_fallbacks == 0


def test_disjoint_object_properties_match_scalar_skipped_diagnostics() -> None:
    view = _snapshot(
        "DisjointObjectProperties(:p ObjectInverseOf(:q) :r) "
        "DisjointObjectProperties(Annotation(<urn:meta> _:skipped) "
        "ObjectInverseOf(:p) :q) EquivalentObjectProperties(:u :v) "
        "SubObjectPropertyOf(:child :p) ObjectPropertyDomain(:p :D) "
        "ObjectPropertyRange(:p :R) ObjectPropertyAssertion(:u _:edge :i)"
    )
    lease = _lease(view)
    cases = (
        ProjectionOptions(backend="python", order="encounter"),
        ProjectionOptions(backend="python", duplicates="unique", order="canonical"),
        ProjectionOptions(
            backend="python",
            compatibility_state="scala-instance",
            order="encounter",
        ),
    )
    expected: list[tuple[list[Edge], dict[str, object]]] = []
    for options in cases:
        scalar = Projector()
        edges = scalar.project(view, options=options)
        assert scalar.last_report is not None
        expected.append((edges, scalar.last_report.to_dict()))

    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("disjoint-property slice crossed scalar traversal"),
        ),
    ):
        for options, (scalar_edges, scalar_report) in zip(cases, expected, strict=True):
            projector = Projector()
            actual = list(
                projector.iter_edges(
                    view,
                    options=replace(options, backend="native"),
                    buffer_edges=1,
                )
            )

            assert actual == scalar_edges
            assert len(actual) == 3
            assert Edge("urn:slice#D", "urn:slice#p", "urn:slice#R") in actual
            assert Edge("urn:slice#D", "urn:slice#child", "urn:slice#R") in actual
            assertion = next(edge for edge in actual if edge.relation == "urn:slice#u")
            assert assertion.source.startswith("_:genid")
            assert assertion.destination == "urn:slice#i"
            assert projector.last_report is not None
            assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(
                scalar_report
            )
            assert projector.last_report.provenance.ingestion.path == "encoded-native"
            assert projector.last_report.provenance.counts.skipped_axioms == 3
            assert projector.last_report.provenance.counts.ignored_shapes == 0
            assert projector.last_report.diagnostics == (
                ProjectionDiagnostic(
                    code="MOWL_SKIPPED_AXIOM",
                    message="axiom category is not visited by the pinned profile",
                    count=2,
                    constructor="DisjointObjectProperties",
                ),
                ProjectionDiagnostic(
                    code="MOWL_SKIPPED_AXIOM",
                    message="axiom category is not visited by the pinned profile",
                    count=1,
                    constructor="EquivalentObjectProperties",
                ),
            )
            counters = projector.last_encoded_counters
            assert counters is not None
            assert counters.roots_inspected == 7
            assert counters.disjoint_object_property_axioms == 2
            assert counters.equivalent_object_property_axioms == 1
            assert counters.sub_object_property_axioms == 1
            assert counters.object_property_assertion_axioms == 1
            assert counters.anonymous_individuals == 2
            assert counters.edge_batches == counters.raw_edges == 3
            assert counters.scalar_fallbacks == 0


def test_functional_object_properties_match_scalar_skipped_diagnostics() -> None:
    view = _snapshot(
        "FunctionalObjectProperty(:p) "
        "FunctionalObjectProperty(Annotation(<urn:meta> _:skipped) ObjectInverseOf(:p)) "
        "DisjointObjectProperties(:u :v) SubObjectPropertyOf(:child :p) "
        "ObjectPropertyDomain(:p :D) ObjectPropertyRange(:p :R) "
        "ObjectPropertyAssertion(:u _:edge :i)"
    )
    lease = _lease(view)
    cases = (
        ProjectionOptions(backend="python", order="encounter"),
        ProjectionOptions(backend="python", duplicates="unique", order="canonical"),
        ProjectionOptions(
            backend="python",
            compatibility_state="scala-instance",
            order="encounter",
        ),
    )
    expected: list[tuple[list[Edge], dict[str, object]]] = []
    for options in cases:
        scalar = Projector()
        edges = scalar.project(view, options=options)
        assert scalar.last_report is not None
        expected.append((edges, scalar.last_report.to_dict()))

    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("functional-property slice crossed scalar traversal"),
        ),
    ):
        for options, (scalar_edges, scalar_report) in zip(cases, expected, strict=True):
            projector = Projector()
            actual = list(
                projector.iter_edges(
                    view,
                    options=replace(options, backend="native"),
                    buffer_edges=1,
                )
            )

            assert actual == scalar_edges
            assert len(actual) == 3
            assert Edge("urn:slice#D", "urn:slice#p", "urn:slice#R") in actual
            assert Edge("urn:slice#D", "urn:slice#child", "urn:slice#R") in actual
            assertion = next(edge for edge in actual if edge.relation == "urn:slice#u")
            assert assertion.source.startswith("_:genid")
            assert assertion.destination == "urn:slice#i"
            assert projector.last_report is not None
            assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(
                scalar_report
            )
            assert projector.last_report.provenance.ingestion.path == "encoded-native"
            assert projector.last_report.provenance.counts.skipped_axioms == 3
            assert projector.last_report.provenance.counts.ignored_shapes == 0
            assert projector.last_report.diagnostics == (
                ProjectionDiagnostic(
                    code="MOWL_SKIPPED_AXIOM",
                    message="axiom category is not visited by the pinned profile",
                    count=1,
                    constructor="DisjointObjectProperties",
                ),
                ProjectionDiagnostic(
                    code="MOWL_SKIPPED_AXIOM",
                    message="axiom category is not visited by the pinned profile",
                    count=2,
                    constructor="FunctionalObjectProperty",
                ),
            )
            counters = projector.last_encoded_counters
            assert counters is not None
            assert counters.roots_inspected == 7
            assert counters.functional_object_property_axioms == 2
            assert counters.disjoint_object_property_axioms == 1
            assert counters.sub_object_property_axioms == 1
            assert counters.object_property_assertion_axioms == 1
            assert counters.anonymous_individuals == 2
            assert counters.edge_batches == counters.raw_edges == 3
            assert counters.scalar_fallbacks == 0


def test_inverse_functional_properties_match_scalar_skipped_diagnostics() -> None:
    view = _snapshot(
        "InverseFunctionalObjectProperty(:p) "
        "InverseFunctionalObjectProperty(Annotation(<urn:meta> _:skipped) "
        "ObjectInverseOf(:p)) FunctionalObjectProperty(:u) "
        "SubObjectPropertyOf(:child :p) ObjectPropertyDomain(:p :D) "
        "ObjectPropertyRange(:p :R) ObjectPropertyAssertion(:u _:edge :i)"
    )
    lease = _lease(view)
    cases = (
        ProjectionOptions(backend="python", order="encounter"),
        ProjectionOptions(backend="python", duplicates="unique", order="canonical"),
        ProjectionOptions(
            backend="python",
            compatibility_state="scala-instance",
            order="encounter",
        ),
    )
    expected: list[tuple[list[Edge], dict[str, object]]] = []
    for options in cases:
        scalar = Projector()
        edges = scalar.project(view, options=options)
        assert scalar.last_report is not None
        expected.append((edges, scalar.last_report.to_dict()))

    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError(
                "inverse-functional-property slice crossed scalar traversal"
            ),
        ),
    ):
        for options, (scalar_edges, scalar_report) in zip(cases, expected, strict=True):
            projector = Projector()
            actual = list(
                projector.iter_edges(
                    view,
                    options=replace(options, backend="native"),
                    buffer_edges=1,
                )
            )

            assert actual == scalar_edges
            assert len(actual) == 3
            assert Edge("urn:slice#D", "urn:slice#p", "urn:slice#R") in actual
            assert Edge("urn:slice#D", "urn:slice#child", "urn:slice#R") in actual
            assertion = next(edge for edge in actual if edge.relation == "urn:slice#u")
            assert assertion.source.startswith("_:genid")
            assert assertion.destination == "urn:slice#i"
            assert projector.last_report is not None
            assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(
                scalar_report
            )
            assert projector.last_report.provenance.ingestion.path == "encoded-native"
            assert projector.last_report.provenance.counts.skipped_axioms == 3
            assert projector.last_report.provenance.counts.ignored_shapes == 0
            assert projector.last_report.diagnostics == (
                ProjectionDiagnostic(
                    code="MOWL_SKIPPED_AXIOM",
                    message="axiom category is not visited by the pinned profile",
                    count=1,
                    constructor="FunctionalObjectProperty",
                ),
                ProjectionDiagnostic(
                    code="MOWL_SKIPPED_AXIOM",
                    message="axiom category is not visited by the pinned profile",
                    count=2,
                    constructor="InverseFunctionalObjectProperty",
                ),
            )
            counters = projector.last_encoded_counters
            assert counters is not None
            assert counters.roots_inspected == 7
            assert counters.inverse_functional_object_property_axioms == 2
            assert counters.functional_object_property_axioms == 1
            assert counters.sub_object_property_axioms == 1
            assert counters.object_property_assertion_axioms == 1
            assert counters.anonymous_individuals == 2
            assert counters.edge_batches == counters.raw_edges == 3
            assert counters.scalar_fallbacks == 0


def test_reflexive_properties_match_scalar_skipped_diagnostics() -> None:
    view = _snapshot(
        "ReflexiveObjectProperty(:p) "
        "ReflexiveObjectProperty(Annotation(<urn:meta> _:skipped) ObjectInverseOf(:p)) "
        "InverseFunctionalObjectProperty(:u) SubObjectPropertyOf(:child :p) "
        "ObjectPropertyDomain(:p :D) ObjectPropertyRange(:p :R) "
        "ObjectPropertyAssertion(:u _:edge :i)"
    )
    lease = _lease(view)
    cases = (
        ProjectionOptions(backend="python", order="encounter"),
        ProjectionOptions(backend="python", duplicates="unique", order="canonical"),
        ProjectionOptions(
            backend="python",
            compatibility_state="scala-instance",
            order="encounter",
        ),
    )
    expected: list[tuple[list[Edge], dict[str, object]]] = []
    for options in cases:
        scalar = Projector()
        edges = scalar.project(view, options=options)
        assert scalar.last_report is not None
        expected.append((edges, scalar.last_report.to_dict()))

    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("reflexive-property slice crossed scalar traversal"),
        ),
    ):
        for options, (scalar_edges, scalar_report) in zip(cases, expected, strict=True):
            projector = Projector()
            actual = list(
                projector.iter_edges(
                    view,
                    options=replace(options, backend="native"),
                    buffer_edges=1,
                )
            )

            assert actual == scalar_edges
            assert len(actual) == 3
            assert Edge("urn:slice#D", "urn:slice#p", "urn:slice#R") in actual
            assert Edge("urn:slice#D", "urn:slice#child", "urn:slice#R") in actual
            assertion = next(edge for edge in actual if edge.relation == "urn:slice#u")
            assert assertion.source.startswith("_:genid")
            assert assertion.destination == "urn:slice#i"
            assert projector.last_report is not None
            assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(
                scalar_report
            )
            assert projector.last_report.provenance.ingestion.path == "encoded-native"
            assert projector.last_report.provenance.counts.skipped_axioms == 3
            assert projector.last_report.provenance.counts.ignored_shapes == 0
            assert projector.last_report.diagnostics == (
                ProjectionDiagnostic(
                    code="MOWL_SKIPPED_AXIOM",
                    message="axiom category is not visited by the pinned profile",
                    count=1,
                    constructor="InverseFunctionalObjectProperty",
                ),
                ProjectionDiagnostic(
                    code="MOWL_SKIPPED_AXIOM",
                    message="axiom category is not visited by the pinned profile",
                    count=2,
                    constructor="ReflexiveObjectProperty",
                ),
            )
            counters = projector.last_encoded_counters
            assert counters is not None
            assert counters.roots_inspected == 7
            assert counters.reflexive_object_property_axioms == 2
            assert counters.inverse_functional_object_property_axioms == 1
            assert counters.sub_object_property_axioms == 1
            assert counters.object_property_assertion_axioms == 1
            assert counters.anonymous_individuals == 2
            assert counters.edge_batches == counters.raw_edges == 3
            assert counters.scalar_fallbacks == 0


def test_irreflexive_properties_match_scalar_skipped_diagnostics() -> None:
    view = _snapshot(
        "IrreflexiveObjectProperty(:p) "
        "IrreflexiveObjectProperty(Annotation(<urn:meta> _:skipped) ObjectInverseOf(:p)) "
        "ReflexiveObjectProperty(:u) SubObjectPropertyOf(:child :p) "
        "ObjectPropertyDomain(:p :D) ObjectPropertyRange(:p :R) "
        "ObjectPropertyAssertion(:u _:edge :i)"
    )
    lease = _lease(view)
    cases = (
        ProjectionOptions(backend="python", order="encounter"),
        ProjectionOptions(backend="python", duplicates="unique", order="canonical"),
        ProjectionOptions(
            backend="python",
            compatibility_state="scala-instance",
            order="encounter",
        ),
    )
    expected: list[tuple[list[Edge], dict[str, object]]] = []
    for options in cases:
        scalar = Projector()
        edges = scalar.project(view, options=options)
        assert scalar.last_report is not None
        expected.append((edges, scalar.last_report.to_dict()))

    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("irreflexive-property slice crossed scalar traversal"),
        ),
    ):
        for options, (scalar_edges, scalar_report) in zip(cases, expected, strict=True):
            projector = Projector()
            actual = list(
                projector.iter_edges(
                    view,
                    options=replace(options, backend="native"),
                    buffer_edges=1,
                )
            )

            assert actual == scalar_edges
            assert len(actual) == 3
            assert Edge("urn:slice#D", "urn:slice#p", "urn:slice#R") in actual
            assert Edge("urn:slice#D", "urn:slice#child", "urn:slice#R") in actual
            assertion = next(edge for edge in actual if edge.relation == "urn:slice#u")
            assert assertion.source.startswith("_:genid")
            assert assertion.destination == "urn:slice#i"
            assert projector.last_report is not None
            assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(
                scalar_report
            )
            assert projector.last_report.provenance.ingestion.path == "encoded-native"
            assert projector.last_report.provenance.counts.skipped_axioms == 3
            assert projector.last_report.provenance.counts.ignored_shapes == 0
            assert projector.last_report.diagnostics == (
                ProjectionDiagnostic(
                    code="MOWL_SKIPPED_AXIOM",
                    message="axiom category is not visited by the pinned profile",
                    count=2,
                    constructor="IrreflexiveObjectProperty",
                ),
                ProjectionDiagnostic(
                    code="MOWL_SKIPPED_AXIOM",
                    message="axiom category is not visited by the pinned profile",
                    count=1,
                    constructor="ReflexiveObjectProperty",
                ),
            )
            counters = projector.last_encoded_counters
            assert counters is not None
            assert counters.roots_inspected == 7
            assert counters.irreflexive_object_property_axioms == 2
            assert counters.reflexive_object_property_axioms == 1
            assert counters.sub_object_property_axioms == 1
            assert counters.object_property_assertion_axioms == 1
            assert counters.anonymous_individuals == 2
            assert counters.edge_batches == counters.raw_edges == 3
            assert counters.scalar_fallbacks == 0


def test_named_role_axioms_match_scalar_hashset_order_and_same_view_edges() -> None:
    view = _snapshot(
        "SubObjectPropertyOf(:p :r) SubObjectPropertyOf(:q :r) "
        "SubObjectPropertyOf(:p :q) InverseObjectProperties(:r :s) "
        "InverseObjectProperties(:r :t) ObjectPropertyDomain(:r :D) "
        "ObjectPropertyRange(:r :R) ObjectPropertyDomain(:q :QD) "
        "ObjectPropertyRange(:q :QR)"
    )
    lease = _lease(view)
    expected_edges = [
        Edge("urn:slice#QD", "urn:slice#q", "urn:slice#QR"),
        Edge("urn:slice#QD", "urn:slice#p", "urn:slice#QR"),
        Edge("urn:slice#D", "urn:slice#r", "urn:slice#R"),
        Edge("urn:slice#D", "urn:slice#q", "urn:slice#R"),
        Edge("urn:slice#R", "urn:slice#s", "urn:slice#D"),
    ]
    for compatibility_state in ("isolated", "scala-instance"):
        options = ProjectionOptions(
            backend="python",
            compatibility_state=compatibility_state,
            order="encounter",
        )
        scalar = Projector()
        expected = scalar.project(view, options=options)
        assert expected == expected_edges
        assert scalar.last_report is not None
        scalar_report = scalar.last_report.to_dict()

        with (
            _forced_encoded(lease),
            patch.object(
                api_module,
                "prepare_streaming_compilation",
                side_effect=AssertionError("encoded role slice crossed scalar traversal"),
            ),
        ):
            projector = Projector()
            actual = list(
                projector.iter_edges(
                    view,
                    options=replace(options, backend="native"),
                    buffer_edges=2,
                )
            )

        assert actual == expected
        assert projector.last_report is not None
        assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(scalar_report)
        counters = projector.last_encoded_counters
        assert counters is not None
        assert counters.roots_inspected == 9
        assert counters.sub_object_property_axioms == 3
        assert counters.inverse_object_property_axioms == 2
        assert counters.object_property_domain_axioms == 2
        assert counters.object_property_range_axioms == 2
        assert counters.edge_batches == 3
        assert counters.raw_edges == 5
        assert counters.scalar_fallbacks == 0


def test_inverse_role_axioms_match_scalar_hashset_order_and_overwrites() -> None:
    view = _snapshot(
        'SubObjectPropertyOf(Annotation(<urn:meta> "inverse-p-r") '
        "ObjectInverseOf(:p) :r) "
        'SubObjectPropertyOf(Annotation(<urn:meta> "q-inverse-r") '
        ":q ObjectInverseOf(:r)) "
        'SubObjectPropertyOf(Annotation(Annotation(<urn:nested> "ignored") '
        '<urn:meta> "inverse-both") ObjectInverseOf(:p) ObjectInverseOf(:q)) '
        'InverseObjectProperties(Annotation(<urn:meta> "inverse-r-s") '
        "ObjectInverseOf(:r) :s) "
        'InverseObjectProperties(Annotation(<urn:meta> "r-inverse-t") '
        ":r ObjectInverseOf(:t)) "
        "ObjectPropertyDomain(:r :D) ObjectPropertyRange(:r :R) "
        "ObjectPropertyDomain(:q :QD) ObjectPropertyRange(:q :QR)"
    )
    lease = _lease(view)
    expected_edges = [
        Edge("urn:slice#QD", "urn:slice#q", "urn:slice#QR"),
        Edge("urn:slice#QD", "urn:slice#p", "urn:slice#QR"),
        Edge("urn:slice#D", "urn:slice#r", "urn:slice#R"),
        Edge("urn:slice#D", "urn:slice#q", "urn:slice#R"),
        Edge("urn:slice#D", "urn:slice#p", "urn:slice#R"),
        Edge("urn:slice#R", "urn:slice#t", "urn:slice#D"),
    ]
    for compatibility_state in ("isolated", "scala-instance"):
        options = ProjectionOptions(
            backend="python",
            compatibility_state=compatibility_state,
            order="encounter",
        )
        scalar = Projector()
        expected = scalar.project(view, options=options)
        assert expected == expected_edges
        assert scalar.last_report is not None
        scalar_report = scalar.last_report.to_dict()

        with (
            _forced_encoded(lease),
            patch.object(
                api_module,
                "prepare_streaming_compilation",
                side_effect=AssertionError("inverse role axioms crossed scalar traversal"),
            ),
        ):
            projector = Projector()
            actual = list(
                projector.iter_edges(
                    view,
                    options=replace(options, backend="native"),
                    buffer_edges=2,
                )
            )

        assert actual == expected
        assert projector.last_report is not None
        assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(scalar_report)
        counters = projector.last_encoded_counters
        assert counters is not None
        assert counters.roots_inspected == 9
        assert counters.sub_object_property_axioms == 3
        assert counters.inverse_object_property_axioms == 2
        assert counters.annotation_nodes == 6
        assert counters.literal_nodes == 6
        assert counters.raw_edges == 6
        assert counters.scalar_fallbacks == 0


def test_annotated_role_axioms_match_scalar_hashset_order_exactly() -> None:
    view = _snapshot(
        'SubObjectPropertyOf(Annotation(Annotation(<urn:nested> "ignored") '
        '<urn:meta> "0") :a :p) '
        'SubObjectPropertyOf(Annotation(<urn:meta> "4") :c :a) '
        'InverseObjectProperties(Annotation(<urn:meta> "0") :p :x) '
        'InverseObjectProperties(Annotation(<urn:meta> "0") :p :y) '
        'ObjectPropertyDomain(Annotation(<urn:meta> "domain") :p :D) '
        'ObjectPropertyRange(Annotation(<urn:meta> "range") :p :R)'
    )
    lease = _lease(view)
    expected_edges = [
        Edge("urn:slice#D", "urn:slice#p", "urn:slice#R"),
        Edge("urn:slice#D", "urn:slice#a", "urn:slice#R"),
        Edge("urn:slice#R", "urn:slice#y", "urn:slice#D"),
    ]
    for compatibility_state in ("isolated", "scala-instance"):
        options = ProjectionOptions(
            backend="python",
            compatibility_state=compatibility_state,
            order="encounter",
        )
        scalar = Projector()
        expected = scalar.project(view, options=options)
        assert expected == expected_edges
        assert scalar.last_report is not None

        with (
            _forced_encoded(lease),
            patch.object(
                api_module,
                "prepare_streaming_compilation",
                side_effect=AssertionError("annotated role axioms crossed scalar traversal"),
            ),
        ):
            projector = Projector()
            actual = list(
                projector.iter_edges(
                    view,
                    options=replace(options, backend="native"),
                    buffer_edges=2,
                )
            )

        assert actual == expected
        assert Edge("urn:slice#D", "urn:slice#c", "urn:slice#R") not in actual
        assert Edge("urn:slice#R", "urn:slice#x", "urn:slice#D") not in actual
        assert projector.last_report is not None
        assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(
            scalar.last_report.to_dict()
        )
        counters = projector.last_encoded_counters
        assert counters is not None
        assert counters.roots_inspected == 6
        assert counters.sub_object_property_axioms == 2
        assert counters.inverse_object_property_axioms == 2
        assert counters.annotation_nodes == 6
        assert counters.literal_nodes == 5
        assert counters.raw_edges == 3
        assert counters.scalar_fallbacks == 0


def test_encoded_role_annotation_hashes_match_scalar_value_variants() -> None:
    view = _snapshot(
        "SubObjectPropertyOf(Annotation(<urn:meta> <urn:value>) :iri :p) "
        'SubObjectPropertyOf(Annotation(<urn:meta> "typed"^^<urn:datatype>) :typed :p) '
        'SubObjectPropertyOf(Annotation(<urn:a> "first") '
        'Annotation(<urn:b> "second") :multi :p) '
        'InverseObjectProperties(Annotation(Annotation(<urn:nested> "ignored") '
        '<urn:meta> "plain") :p :inverse) '
        'SubObjectPropertyOf(Annotation(<urn:meta> "inverse-sub") '
        "ObjectInverseOf(:inverseSub) :inverseSuper) "
        'InverseObjectProperties(Annotation(<urn:meta> "inverse-pair") '
        "ObjectInverseOf(:inverseFirst) :inverseSecond)"
    )
    compilation, negotiation, counters = prepare_encoded_subset_compilation(
        view,
        ProjectionOptions(backend="native"),
        EncodedNegotiation("encoded-native", lease=_lease(view)),
        batch_edges=1,
    )
    assert compilation is not None
    assert negotiation.path == "encoded-native"
    assert counters is not None
    encoded_hashes = {(row.first, row.second): row.owlapi_hash for row in compilation._role_axioms}
    scalar_hashes: dict[tuple[str, str], int] = {}

    def underlying_iri(expression: object) -> str:
        value = getattr(expression, "property", expression)
        return cast(Any, value).iri.value

    for axiom in view.iter_axioms():  # type: ignore[attr-defined]
        if type(axiom).__name__ == "SubObjectPropertyOf":
            typed = cast(Any, axiom)
            first = underlying_iri(typed.sub_property)
            second = underlying_iri(typed.super_property)
        elif type(axiom).__name__ == "InverseObjectProperties":
            typed = cast(Any, axiom)
            first = underlying_iri(typed.first)
            second = underlying_iri(typed.second)
        else:  # pragma: no cover - fixture contains only role axioms
            continue
        scalar_hashes[(first, second)] = _owlapi_hash(axiom)

    assert encoded_hashes == scalar_hashes
    assert counters.sub_object_property_axioms == 4
    assert counters.inverse_object_property_axioms == 2
    assert counters.scalar_fallbacks == 0


def test_unhashable_annotated_role_value_selects_scalar_before_output() -> None:
    view = _snapshot(
        "SubObjectPropertyOf(Annotation(<urn:meta> _:annotation) :child :p) "
        "ObjectPropertyDomain(:p :D) ObjectPropertyRange(:p :R)"
    )
    lease = _lease(view)
    compilation, negotiation, counters = prepare_encoded_subset_compilation(
        view,
        ProjectionOptions(backend="native", order="encounter"),
        EncodedNegotiation("encoded-native", lease=lease),
        batch_edges=1,
    )

    assert compilation is None
    assert negotiation.path == "scalar-native"
    assert "cannot reproduce scalar hashing" in (negotiation.reason or "")
    assert counters is not None
    assert counters.roots_inspected == 3
    assert counters.sub_object_property_axioms == 1
    assert counters.annotation_nodes == counters.anonymous_individuals == 1
    assert counters.scalar_fallbacks == 1
    assert counters.edge_batches == counters.raw_edges == 0

    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            wraps=scalar_compilation,
        ) as scalar_prepare,
    ):
        projector = Projector()
        with pytest.raises(UnicodeEncodeError):
            projector.project(
                view,
                options=ProjectionOptions(backend="native", order="encounter"),
            )

    assert scalar_prepare.call_count == 1


def test_inverse_encoded_role_state_is_reused_by_a_later_scala_instance_call() -> None:
    role_view = _snapshot(
        "SubObjectPropertyOf(ObjectInverseOf(:child) :p) "
        "InverseObjectProperties(ObjectInverseOf(:p) :pinv)"
    )
    domain_range_view = _snapshot("ObjectPropertyDomain(:p :D) ObjectPropertyRange(:p :R)")
    options = ProjectionOptions(
        backend="python",
        compatibility_state="scala-instance",
        order="encounter",
    )
    scalar = Projector()
    assert scalar.project(role_view, options=options) == []
    expected = scalar.project(domain_range_view, options=options)

    projector = Projector()
    with (
        _forced_encoded(_lease(role_view)),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("encoded role state crossed scalar traversal"),
        ),
    ):
        assert projector.project(role_view, options=replace(options, backend="native")) == []
    first_counters = projector.last_encoded_counters
    assert first_counters is not None
    assert first_counters.sub_object_property_axioms == 1
    assert first_counters.inverse_object_property_axioms == 1

    with (
        _forced_encoded(_lease(domain_range_view)),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("encoded lifecycle crossed scalar traversal"),
        ),
    ):
        actual = projector.project(
            domain_range_view,
            options=replace(options, backend="native"),
        )

    assert actual == expected
    assert actual == [
        Edge("urn:slice#D", "urn:slice#p", "urn:slice#R"),
        Edge("urn:slice#D", "urn:slice#child", "urn:slice#R"),
        Edge("urn:slice#R", "urn:slice#pinv", "urn:slice#D"),
    ]


def test_property_chain_does_not_leak_role_state_across_scala_instance_calls() -> None:
    role_view = _snapshot(
        'SubObjectPropertyOf(Annotation(<urn:meta> "ignored") '
        "ObjectPropertyChain(:first ObjectInverseOf(:second)) :shared)"
    )
    domain_range_view = _snapshot(
        "ObjectPropertyDomain(:shared :D) ObjectPropertyRange(:shared :R)"
    )
    options = ProjectionOptions(
        backend="python",
        compatibility_state="scala-instance",
        order="encounter",
    )
    scalar = Projector()
    assert scalar.project(role_view, options=options) == []
    assert scalar.last_report is not None
    first_scalar_report = scalar.last_report.to_dict()
    expected = scalar.project(domain_range_view, options=options)
    assert scalar.last_report is not None
    second_scalar_report = scalar.last_report.to_dict()

    projector = Projector()
    with (
        _forced_encoded(_lease(role_view)),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("encoded chain lifecycle crossed scalar traversal"),
        ),
    ):
        assert projector.project(role_view, options=replace(options, backend="native")) == []

    assert projector.last_report is not None
    assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(
        first_scalar_report
    )
    assert projector.last_report.provenance.counts.ignored_shapes == 1
    assert projector.last_report.provenance.invocation_count == 1
    assert projector.last_report.diagnostics == ()
    first_counters = projector.last_encoded_counters
    assert first_counters is not None
    assert first_counters.sub_object_property_axioms == 1
    assert first_counters.scalar_fallbacks == 0

    with (
        _forced_encoded(_lease(domain_range_view)),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("encoded chain follow-on crossed scalar traversal"),
        ),
    ):
        actual = projector.project(
            domain_range_view,
            options=replace(options, backend="native"),
        )

    assert actual == expected == [Edge("urn:slice#D", "urn:slice#shared", "urn:slice#R")]
    assert projector.last_report is not None
    assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(
        second_scalar_report
    )
    assert projector.last_report.provenance.invocation_count == 2
    second_counters = projector.last_encoded_counters
    assert second_counters is not None
    assert second_counters.object_property_domain_axioms == 1
    assert second_counters.object_property_range_axioms == 1
    assert second_counters.scalar_fallbacks == 0


def test_skipped_equivalent_properties_do_not_leak_scala_instance_state() -> None:
    skipped_view = _snapshot(
        'EquivalentObjectProperties(Annotation(<urn:meta> "skipped") '
        "ObjectInverseOf(:first) :shared)"
    )
    domain_range_view = _snapshot(
        "ObjectPropertyDomain(:shared :D) ObjectPropertyRange(:shared :R)"
    )
    options = ProjectionOptions(
        backend="python",
        compatibility_state="scala-instance",
        order="encounter",
    )
    scalar = Projector()
    assert scalar.project(skipped_view, options=options) == []
    assert scalar.last_report is not None
    first_scalar_report = scalar.last_report.to_dict()
    expected = scalar.project(domain_range_view, options=options)
    assert scalar.last_report is not None
    second_scalar_report = scalar.last_report.to_dict()

    projector = Projector()
    with (
        _forced_encoded(_lease(skipped_view)),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("encoded skipped lifecycle crossed scalar traversal"),
        ),
    ):
        assert projector.project(skipped_view, options=replace(options, backend="native")) == []

    assert projector.last_report is not None
    assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(
        first_scalar_report
    )
    assert projector.last_report.provenance.counts.skipped_axioms == 1
    assert projector.last_report.provenance.invocation_count == 1
    first_counters = projector.last_encoded_counters
    assert first_counters is not None
    assert first_counters.equivalent_object_property_axioms == 1
    assert first_counters.scalar_fallbacks == 0

    with (
        _forced_encoded(_lease(domain_range_view)),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("encoded skipped follow-on crossed scalar traversal"),
        ),
    ):
        actual = projector.project(
            domain_range_view,
            options=replace(options, backend="native"),
        )

    assert actual == expected == [Edge("urn:slice#D", "urn:slice#shared", "urn:slice#R")]
    assert projector.last_report is not None
    assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(
        second_scalar_report
    )
    assert projector.last_report.provenance.invocation_count == 2
    second_counters = projector.last_encoded_counters
    assert second_counters is not None
    assert second_counters.object_property_domain_axioms == 1
    assert second_counters.object_property_range_axioms == 1
    assert second_counters.scalar_fallbacks == 0


def test_skipped_disjoint_properties_do_not_leak_scala_instance_state() -> None:
    skipped_view = _snapshot(
        'DisjointObjectProperties(Annotation(<urn:meta> "skipped") ObjectInverseOf(:first) :shared)'
    )
    domain_range_view = _snapshot(
        "ObjectPropertyDomain(:shared :D) ObjectPropertyRange(:shared :R)"
    )
    options = ProjectionOptions(
        backend="python",
        compatibility_state="scala-instance",
        order="encounter",
    )
    scalar = Projector()
    assert scalar.project(skipped_view, options=options) == []
    assert scalar.last_report is not None
    first_scalar_report = scalar.last_report.to_dict()
    expected = scalar.project(domain_range_view, options=options)
    assert scalar.last_report is not None
    second_scalar_report = scalar.last_report.to_dict()

    projector = Projector()
    with (
        _forced_encoded(_lease(skipped_view)),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("encoded disjoint lifecycle crossed scalar traversal"),
        ),
    ):
        assert projector.project(skipped_view, options=replace(options, backend="native")) == []

    assert projector.last_report is not None
    assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(
        first_scalar_report
    )
    assert projector.last_report.provenance.counts.skipped_axioms == 1
    assert projector.last_report.provenance.invocation_count == 1
    first_counters = projector.last_encoded_counters
    assert first_counters is not None
    assert first_counters.disjoint_object_property_axioms == 1
    assert first_counters.scalar_fallbacks == 0

    with (
        _forced_encoded(_lease(domain_range_view)),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("encoded disjoint follow-on crossed scalar traversal"),
        ),
    ):
        actual = projector.project(
            domain_range_view,
            options=replace(options, backend="native"),
        )

    assert actual == expected == [Edge("urn:slice#D", "urn:slice#shared", "urn:slice#R")]
    assert projector.last_report is not None
    assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(
        second_scalar_report
    )
    assert projector.last_report.provenance.invocation_count == 2
    second_counters = projector.last_encoded_counters
    assert second_counters is not None
    assert second_counters.object_property_domain_axioms == 1
    assert second_counters.object_property_range_axioms == 1
    assert second_counters.scalar_fallbacks == 0


def test_skipped_functional_property_does_not_leak_scala_instance_state() -> None:
    skipped_view = _snapshot(
        'FunctionalObjectProperty(Annotation(<urn:meta> "skipped") ObjectInverseOf(:shared))'
    )
    domain_range_view = _snapshot(
        "ObjectPropertyDomain(:shared :D) ObjectPropertyRange(:shared :R)"
    )
    options = ProjectionOptions(
        backend="python",
        compatibility_state="scala-instance",
        order="encounter",
    )
    scalar = Projector()
    assert scalar.project(skipped_view, options=options) == []
    assert scalar.last_report is not None
    first_scalar_report = scalar.last_report.to_dict()
    expected = scalar.project(domain_range_view, options=options)
    assert scalar.last_report is not None
    second_scalar_report = scalar.last_report.to_dict()

    projector = Projector()
    with (
        _forced_encoded(_lease(skipped_view)),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("encoded functional lifecycle crossed scalar traversal"),
        ),
    ):
        assert projector.project(skipped_view, options=replace(options, backend="native")) == []

    assert projector.last_report is not None
    assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(
        first_scalar_report
    )
    assert projector.last_report.provenance.counts.skipped_axioms == 1
    assert projector.last_report.provenance.invocation_count == 1
    first_counters = projector.last_encoded_counters
    assert first_counters is not None
    assert first_counters.functional_object_property_axioms == 1
    assert first_counters.scalar_fallbacks == 0

    with (
        _forced_encoded(_lease(domain_range_view)),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("encoded functional follow-on crossed scalar traversal"),
        ),
    ):
        actual = projector.project(
            domain_range_view,
            options=replace(options, backend="native"),
        )

    assert actual == expected == [Edge("urn:slice#D", "urn:slice#shared", "urn:slice#R")]
    assert projector.last_report is not None
    assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(
        second_scalar_report
    )
    assert projector.last_report.provenance.invocation_count == 2
    second_counters = projector.last_encoded_counters
    assert second_counters is not None
    assert second_counters.object_property_domain_axioms == 1
    assert second_counters.object_property_range_axioms == 1
    assert second_counters.scalar_fallbacks == 0


def test_skipped_inverse_functional_property_does_not_leak_scala_instance_state() -> None:
    skipped_view = _snapshot(
        'InverseFunctionalObjectProperty(Annotation(<urn:meta> "skipped") ObjectInverseOf(:shared))'
    )
    domain_range_view = _snapshot(
        "ObjectPropertyDomain(:shared :D) ObjectPropertyRange(:shared :R)"
    )
    options = ProjectionOptions(
        backend="python",
        compatibility_state="scala-instance",
        order="encounter",
    )
    scalar = Projector()
    assert scalar.project(skipped_view, options=options) == []
    assert scalar.last_report is not None
    first_scalar_report = scalar.last_report.to_dict()
    expected = scalar.project(domain_range_view, options=options)
    assert scalar.last_report is not None
    second_scalar_report = scalar.last_report.to_dict()

    projector = Projector()
    with (
        _forced_encoded(_lease(skipped_view)),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError(
                "encoded inverse-functional lifecycle crossed scalar traversal"
            ),
        ),
    ):
        assert projector.project(skipped_view, options=replace(options, backend="native")) == []

    assert projector.last_report is not None
    assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(
        first_scalar_report
    )
    assert projector.last_report.provenance.counts.skipped_axioms == 1
    assert projector.last_report.provenance.invocation_count == 1
    first_counters = projector.last_encoded_counters
    assert first_counters is not None
    assert first_counters.inverse_functional_object_property_axioms == 1
    assert first_counters.scalar_fallbacks == 0

    with (
        _forced_encoded(_lease(domain_range_view)),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError(
                "encoded inverse-functional follow-on crossed scalar traversal"
            ),
        ),
    ):
        actual = projector.project(
            domain_range_view,
            options=replace(options, backend="native"),
        )

    assert actual == expected == [Edge("urn:slice#D", "urn:slice#shared", "urn:slice#R")]
    assert projector.last_report is not None
    assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(
        second_scalar_report
    )
    assert projector.last_report.provenance.invocation_count == 2
    second_counters = projector.last_encoded_counters
    assert second_counters is not None
    assert second_counters.object_property_domain_axioms == 1
    assert second_counters.object_property_range_axioms == 1
    assert second_counters.scalar_fallbacks == 0


def test_skipped_reflexive_property_does_not_leak_scala_instance_state() -> None:
    skipped_view = _snapshot(
        'ReflexiveObjectProperty(Annotation(<urn:meta> "skipped") ObjectInverseOf(:shared))'
    )
    domain_range_view = _snapshot(
        "ObjectPropertyDomain(:shared :D) ObjectPropertyRange(:shared :R)"
    )
    options = ProjectionOptions(
        backend="python",
        compatibility_state="scala-instance",
        order="encounter",
    )
    scalar = Projector()
    assert scalar.project(skipped_view, options=options) == []
    assert scalar.last_report is not None
    first_scalar_report = scalar.last_report.to_dict()
    expected = scalar.project(domain_range_view, options=options)
    assert scalar.last_report is not None
    second_scalar_report = scalar.last_report.to_dict()

    projector = Projector()
    with (
        _forced_encoded(_lease(skipped_view)),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("encoded reflexive lifecycle crossed scalar traversal"),
        ),
    ):
        assert projector.project(skipped_view, options=replace(options, backend="native")) == []

    assert projector.last_report is not None
    assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(
        first_scalar_report
    )
    assert projector.last_report.provenance.counts.skipped_axioms == 1
    assert projector.last_report.provenance.invocation_count == 1
    first_counters = projector.last_encoded_counters
    assert first_counters is not None
    assert first_counters.reflexive_object_property_axioms == 1
    assert first_counters.scalar_fallbacks == 0

    with (
        _forced_encoded(_lease(domain_range_view)),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("encoded reflexive follow-on crossed scalar traversal"),
        ),
    ):
        actual = projector.project(
            domain_range_view,
            options=replace(options, backend="native"),
        )

    assert actual == expected == [Edge("urn:slice#D", "urn:slice#shared", "urn:slice#R")]
    assert projector.last_report is not None
    assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(
        second_scalar_report
    )
    assert projector.last_report.provenance.invocation_count == 2
    second_counters = projector.last_encoded_counters
    assert second_counters is not None
    assert second_counters.object_property_domain_axioms == 1
    assert second_counters.object_property_range_axioms == 1
    assert second_counters.scalar_fallbacks == 0


def test_skipped_irreflexive_property_does_not_leak_scala_instance_state() -> None:
    skipped_view = _snapshot(
        'IrreflexiveObjectProperty(Annotation(<urn:meta> "skipped") ObjectInverseOf(:shared))'
    )
    domain_range_view = _snapshot(
        "ObjectPropertyDomain(:shared :D) ObjectPropertyRange(:shared :R)"
    )
    options = ProjectionOptions(
        backend="python",
        compatibility_state="scala-instance",
        order="encounter",
    )
    scalar = Projector()
    assert scalar.project(skipped_view, options=options) == []
    assert scalar.last_report is not None
    first_scalar_report = scalar.last_report.to_dict()
    expected = scalar.project(domain_range_view, options=options)
    assert scalar.last_report is not None
    second_scalar_report = scalar.last_report.to_dict()

    projector = Projector()
    with (
        _forced_encoded(_lease(skipped_view)),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("encoded irreflexive lifecycle crossed scalar traversal"),
        ),
    ):
        assert projector.project(skipped_view, options=replace(options, backend="native")) == []

    assert projector.last_report is not None
    assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(
        first_scalar_report
    )
    assert projector.last_report.provenance.counts.skipped_axioms == 1
    assert projector.last_report.provenance.invocation_count == 1
    first_counters = projector.last_encoded_counters
    assert first_counters is not None
    assert first_counters.irreflexive_object_property_axioms == 1
    assert first_counters.scalar_fallbacks == 0

    with (
        _forced_encoded(_lease(domain_range_view)),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("encoded irreflexive follow-on crossed scalar traversal"),
        ),
    ):
        actual = projector.project(
            domain_range_view,
            options=replace(options, backend="native"),
        )

    assert actual == expected == [Edge("urn:slice#D", "urn:slice#shared", "urn:slice#R")]
    assert projector.last_report is not None
    assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(
        second_scalar_report
    )
    assert projector.last_report.provenance.invocation_count == 2
    second_counters = projector.last_encoded_counters
    assert second_counters is not None
    assert second_counters.object_property_domain_axioms == 1
    assert second_counters.object_property_range_axioms == 1
    assert second_counters.scalar_fallbacks == 0


def test_named_subclass_restrictions_match_scalar_options_and_role_expansion() -> None:
    view = _snapshot(
        "SubObjectPropertyOf(:child :p) InverseObjectProperties(:p :pinv) "
        "SubClassOf(:A ObjectSomeValuesFrom(:p :B)) "
        "SubClassOf(ObjectAllValuesFrom(:p :C) :D) "
        "SubClassOf(:E ObjectMinCardinality(2 :p :F)) "
        "SubClassOf(ObjectMaxCardinality(3 :p :G) :H) "
        "SubClassOf(:I ObjectMinCardinality(1 :q)) "
        "SubClassOf(ObjectMaxCardinality(2 :q) :J)"
    )
    lease = _lease(view)
    cases = (
        ProjectionOptions(backend="python", order="encounter"),
        ProjectionOptions(
            backend="python",
            bidirectional_taxonomy=True,
            duplicates="unique",
            order="canonical",
        ),
        ProjectionOptions(
            backend="python",
            only_taxonomy=True,
            order="encounter",
        ),
    )
    expected: list[tuple[list[Edge], dict[str, object]]] = []
    for options in cases:
        scalar = Projector()
        edges = scalar.project(view, options=options)
        assert scalar.last_report is not None
        expected.append((edges, scalar.last_report.to_dict()))

    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("encoded restriction slice crossed scalar traversal"),
        ),
    ):
        for options, (scalar_edges, scalar_report) in zip(cases, expected, strict=True):
            projector = Projector()
            actual = list(
                projector.iter_edges(
                    view,
                    options=replace(options, backend="native"),
                    buffer_edges=3,
                )
            )

            assert actual == scalar_edges
            assert projector.last_report is not None
            assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(
                scalar_report
            )
            counters = projector.last_encoded_counters
            assert counters is not None
            assert counters.roots_inspected == 8
            assert counters.subclass_axioms == 6
            assert counters.restriction_subclass_axioms == 6
            assert counters.sub_object_property_axioms == 1
            assert counters.inverse_object_property_axioms == 1
            assert counters.raw_edges == len(actual)
            assert counters.edge_batches == (len(actual) + 2) // 3
            assert counters.scalar_fallbacks == 0
            if options.only_taxonomy:
                assert actual == []
                assert projector.last_report.provenance.counts.ignored_shapes == 6
            else:
                assert len(actual) == 14
                assert Edge("urn:slice#D", "urn:slice#p", "urn:slice#C") in actual
                assert Edge("urn:slice#C", "urn:slice#pinv", "urn:slice#D") in actual
                assert (
                    Edge(
                        "urn:slice#I",
                        "urn:slice#q",
                        "http://www.w3.org/2002/07/owl#Thing",
                    )
                    in actual
                )


def test_inverse_property_restrictions_and_domain_range_match_scalar_exactly() -> None:
    view = _snapshot(
        "SubObjectPropertyOf(:child :p) InverseObjectProperties(:p :pinv) "
        'SubClassOf(Annotation(<urn:meta> "some-inverse") '
        ":SomeSubject ObjectSomeValuesFrom(ObjectInverseOf(:p) :SomeTarget)) "
        "SubClassOf(ObjectAllValuesFrom(ObjectInverseOf(:p) :AllTarget) :AllSubject) "
        "SubClassOf(:MinSubject ObjectMinCardinality(2 ObjectInverseOf(:p) :MinTarget)) "
        "SubClassOf(ObjectMaxCardinality(3 ObjectInverseOf(:p) :MaxTarget) :MaxSubject) "
        "EquivalentClasses(:Aggregate ObjectIntersectionOf(:Named "
        "ObjectSomeValuesFrom(:z :NamedZ) "
        "ObjectSomeValuesFrom(ObjectInverseOf(:a) :InverseA) "
        "ObjectSomeValuesFrom(:a :NamedA) "
        "ObjectSomeValuesFrom(ObjectInverseOf(:z) :InverseZ) "
        "ObjectAllValuesFrom(ObjectInverseOf(:p) :AggregateAll) "
        "ObjectMinCardinality(4 ObjectInverseOf(:p) :AggregateMin) "
        "ObjectMaxCardinality(5 ObjectInverseOf(:p) :AggregateMax))) "
        'EquivalentClasses(Annotation(<urn:meta> "ignored-inverse") '
        ":IgnoredEquivalent ObjectSomeValuesFrom(ObjectInverseOf(:p) :IgnoredTarget)) "
        'ClassAssertion(Annotation(<urn:meta> "inverse-class") '
        "ObjectSomeValuesFrom(ObjectInverseOf(:p) :ClassTarget) :complexIndividual) "
        'ObjectPropertyDomain(Annotation(<urn:meta> "inverse-domain") '
        "ObjectInverseOf(:p) :IgnoredDomain) "
        'ObjectPropertyRange(Annotation(<urn:meta> "inverse-range") '
        "ObjectInverseOf(:p) :IgnoredRange) "
        "ObjectPropertyDomain(ObjectInverseOf(:q) ObjectUnionOf(:ComplexDomainA "
        ":ComplexDomainB)) "
        "ObjectPropertyRange(ObjectInverseOf(:q) "
        "ObjectAllValuesFrom(ObjectInverseOf(:p) :ComplexRange)) "
        "ObjectPropertyDomain(:p :D) ObjectPropertyRange(:p :R)"
    )
    lease = _lease(view)
    cases = (
        ProjectionOptions(backend="python", order="encounter"),
        ProjectionOptions(
            backend="python",
            bidirectional_taxonomy=True,
            duplicates="unique",
            order="canonical",
        ),
        ProjectionOptions(backend="python", only_taxonomy=True, order="encounter"),
    )
    expected: list[tuple[list[Edge], dict[str, object]]] = []
    for options in cases:
        scalar = Projector()
        edges = scalar.project(view, options=options)
        assert scalar.last_report is not None
        expected.append((edges, scalar.last_report.to_dict()))

    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("inverse-property slice crossed scalar traversal"),
        ),
    ):
        for options, (scalar_edges, scalar_report) in zip(cases, expected, strict=True):
            projector = Projector()
            actual = list(
                projector.iter_edges(
                    view,
                    options=replace(options, backend="native"),
                    buffer_edges=3,
                )
            )

            assert actual == scalar_edges
            assert projector.last_report is not None
            assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(
                scalar_report
            )
            assert projector.last_report.provenance.ingestion.path == "encoded-native"
            ignored = {
                item.constructor: item.count
                for item in projector.last_report.diagnostics
                if item.code == "MOWL_IGNORED_SHAPE"
            }
            assert ignored == {
                "ClassAssertion": 1,
                "EquivalentClasses": 1,
                "ObjectPropertyDomain": 2,
                "ObjectPropertyRange": 2,
                **({"SubClassOf": 4} if options.only_taxonomy else {}),
            }
            counters = projector.last_encoded_counters
            assert counters is not None
            assert counters.scalar_fallbacks == 0
            assert counters.raw_edges == len(scalar_edges)
            if not options.only_taxonomy:
                assert (
                    Edge("urn:slice#SomeSubject", "urn:slice#p", "urn:slice#SomeTarget") in actual
                )
                assert (
                    Edge("urn:slice#SomeTarget", "urn:slice#pinv", "urn:slice#SomeSubject")
                    in actual
                )
                assert Edge("urn:slice#Aggregate", "urn:slice#a", "urn:slice#InverseA") in actual
                assert Edge("urn:slice#Aggregate", "urn:slice#z", "urn:slice#InverseZ") in actual


def test_named_aggregate_equivalence_matches_scalar_operand_order_and_roles() -> None:
    view = _snapshot(
        "SubObjectPropertyOf(:child :p) InverseObjectProperties(:p :pinv) "
        "EquivalentClasses(:A ObjectIntersectionOf(:C :B "
        "ObjectSomeValuesFrom(:p :D) ObjectAllValuesFrom(:p :E) "
        "ObjectMinCardinality(2 :p :F) ObjectMaxCardinality(3 :p :G))) "
        "EquivalentClasses(:Z ObjectUnionOf(:Y :X ObjectSomeValuesFrom(:p :W)))"
    )
    lease = _lease(view)
    cases = (
        ProjectionOptions(backend="python", order="encounter"),
        ProjectionOptions(
            backend="python",
            bidirectional_taxonomy=True,
            duplicates="unique",
            order="canonical",
        ),
        ProjectionOptions(
            backend="python",
            only_taxonomy=True,
            order="encounter",
        ),
    )
    expected: list[tuple[list[Edge], dict[str, object]]] = []
    for options in cases:
        scalar = Projector()
        edges = scalar.project(view, options=options)
        assert scalar.last_report is not None
        expected.append((edges, scalar.last_report.to_dict()))

    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("encoded aggregate slice crossed scalar traversal"),
        ),
    ):
        for options, (scalar_edges, scalar_report) in zip(cases, expected, strict=True):
            projector = Projector()
            actual = list(
                projector.iter_edges(
                    view,
                    options=replace(options, backend="native"),
                    buffer_edges=4,
                )
            )

            assert actual == scalar_edges
            assert projector.last_report is not None
            assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(
                scalar_report
            )
            counters = projector.last_encoded_counters
            assert counters is not None
            assert counters.roots_inspected == 4
            assert counters.equivalent_axioms == 2
            assert counters.aggregate_equivalent_axioms == 2
            assert counters.sub_object_property_axioms == 1
            assert counters.inverse_object_property_axioms == 1
            assert counters.raw_edges == len(actual)
            assert counters.edge_batches == (len(actual) + 3) // 4
            assert counters.scalar_fallbacks == 0
            if options.only_taxonomy:
                assert len(actual) == 4
            elif options.bidirectional_taxonomy:
                assert len(actual) == 23
            else:
                assert len(actual) == 19
                assert actual[:2] == [
                    Edge("urn:slice#A", SUBCLASS_OF, "urn:slice#B"),
                    Edge("urn:slice#A", SUBCLASS_OF, "urn:slice#C"),
                ]
                assert Edge("urn:slice#D", "urn:slice#pinv", "urn:slice#A") in actual


@pytest.mark.parametrize(
    ("root_name", "template", "constructor"),
    _IGNORED_COMPLEX_ROOTS,
    ids=[row[0] for row in _IGNORED_COMPLEX_ROOTS],
)
@pytest.mark.parametrize(
    ("expression_name", "expression"),
    _VALIDATED_COMPLEX_EXPRESSIONS,
    ids=[row[0] for row in _VALIDATED_COMPLEX_EXPRESSIONS],
)
def test_validated_complex_root_shapes_match_scalar_ignored_diagnostics(
    root_name: str,
    template: str,
    constructor: str,
    expression_name: str,
    expression: str,
) -> None:
    del root_name, expression_name
    view = _snapshot(template.format(expression=expression))
    lease = _lease(view)
    python_options = ProjectionOptions(backend="python", order="encounter")
    scalar = Projector()
    expected = scalar.project(view, options=python_options)
    assert scalar.last_report is not None
    scalar_report = scalar.last_report.to_dict()

    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("validated ignored shape crossed scalar traversal"),
        ),
    ):
        projector = Projector()
        actual = projector.project(
            view,
            options=replace(python_options, backend="native"),
        )

    assert actual == expected == []
    assert projector.last_report is not None
    assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(scalar_report)
    assert projector.last_report.provenance.ingestion.path == "encoded-native"
    ignored = tuple(
        (item.constructor, item.count)
        for item in projector.last_report.diagnostics
        if item.code == "MOWL_IGNORED_SHAPE"
    )
    assert ignored == ((constructor, 1),)
    counters = projector.last_encoded_counters
    assert counters is not None
    assert counters.scalar_fallbacks == 0
    assert counters.raw_edges == counters.edge_batches == 0


def test_ignored_shapes_and_mixed_nary_equivalence_match_all_scalar_options() -> None:
    view = _snapshot(
        'SubClassOf(Annotation(<urn:meta> "both-complex") '
        "ObjectSomeValuesFrom(:p :A) ObjectAllValuesFrom(:q :B)) "
        "SubClassOf(:AggregateSubject ObjectIntersectionOf(:C :D)) "
        'EquivalentClasses(Annotation(<urn:meta> "restriction") '
        ":IgnoredEquivalent ObjectMinCardinality(2 :p :E)) "
        "EquivalentClasses(:MixA :MixB ObjectIntersectionOf(:LaterA :LaterB)) "
        "EquivalentClasses(:Lead ObjectUnionOf(:OpB :OpA) "
        "ObjectSomeValuesFrom(:p :Trailing)) "
        'ClassAssertion(Annotation(<urn:meta> "complex-class") '
        "ObjectMaxCardinality(3 :p :F) :complexIndividual) "
        "ClassAssertion(:NamedClass _:anonymous) "
        'ObjectPropertyDomain(Annotation(<urn:meta> "complex-domain") '
        ":p ObjectUnionOf(:DomainA :DomainB)) "
        'ObjectPropertyRange(Annotation(<urn:meta> "complex-range") '
        ":p ObjectAllValuesFrom(:q :Range)) "
        "SubClassOf(:RoleSubject ObjectSomeValuesFrom(:p :RoleTarget)) "
        "SubClassOf(:TaxA :TaxB) ClassAssertion(:TaxA :namedIndividual)"
    )
    lease = _lease(view)
    cases = (
        ProjectionOptions(backend="python", order="encounter"),
        ProjectionOptions(
            backend="python",
            bidirectional_taxonomy=True,
            duplicates="unique",
            order="canonical",
        ),
        ProjectionOptions(
            backend="python",
            only_taxonomy=True,
            order="encounter",
        ),
    )
    expected: list[tuple[list[Edge], dict[str, object]]] = []
    for options in cases:
        scalar = Projector()
        edges = scalar.project(view, options=options)
        assert scalar.last_report is not None
        expected.append((edges, scalar.last_report.to_dict()))

    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("ignored-shape tranche crossed scalar traversal"),
        ),
    ):
        for options, (scalar_edges, scalar_report) in zip(cases, expected, strict=True):
            projector = Projector()
            actual = list(
                projector.iter_edges(
                    view,
                    options=replace(options, backend="native"),
                    buffer_edges=2,
                )
            )

            assert actual == scalar_edges
            assert Edge("urn:slice#MixA", SUBCLASS_OF, "urn:slice#MixB") in actual
            assert not any("Later" in edge.source or "Later" in edge.destination for edge in actual)
            assert Edge("urn:slice#Lead", SUBCLASS_OF, "urn:slice#OpA") in actual
            assert Edge("urn:slice#Lead", SUBCLASS_OF, "urn:slice#OpB") in actual
            assert not any(edge.destination == "urn:slice#Trailing" for edge in actual)
            assert projector.last_report is not None
            assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(
                scalar_report
            )
            ignored = {
                item.constructor: item.count
                for item in projector.last_report.diagnostics
                if item.code == "MOWL_IGNORED_SHAPE"
            }
            assert ignored == {
                "ClassAssertion": 2,
                "EquivalentClasses": 1,
                "ObjectPropertyDomain": 1,
                "ObjectPropertyRange": 1,
                "SubClassOf": 3 if options.only_taxonomy else 2,
            }
            counters = projector.last_encoded_counters
            assert counters is not None
            assert counters.scalar_fallbacks == 0
            assert counters.raw_edges >= 1


def test_segmented_ignored_shapes_preserve_differential_diagnostics_and_leases() -> None:
    source_body = (
        "SubClassOf(ObjectSomeValuesFrom(:p :A) ObjectAllValuesFrom(:q :B)) "
        "EquivalentClasses(:MixA :MixB ObjectIntersectionOf(:LaterA :LaterB))"
    )
    delta_body = (
        "ClassAssertion(ObjectMinCardinality(2 :p :C) :i) "
        "ObjectPropertyDomain(:p ObjectUnionOf(:D :E))"
    )
    source = _snapshot(source_body)
    delta = _snapshot(delta_body)
    overlay = _snapshot(f"{source_body} {delta_body}")
    composite = compose_views(source, delta)
    rows = (
        (
            overlay,
            _overlay_delta_lease(overlay, _lease(source), _lease(delta)),
            {id(source)},
        ),
        (
            composite,
            _semantic_composite_lease(composite, (_lease(source), _lease(delta))),
            {id(source), id(delta)},
        ),
    )
    options = ProjectionOptions(backend="python", duplicates="unique", order="canonical")

    for view, lease, retained_owner_ids in rows:
        scalar = Projector()
        expected = scalar.project(view, options=options)
        assert scalar.last_report is not None
        scalar_report = scalar.last_report.to_dict()
        prepared, negotiation, initial = prepare_encoded_subset_compilation(
            view,
            replace(options, backend="native"),
            EncodedNegotiation("encoded-native", lease=lease),
            batch_edges=1,
        )
        assert prepared is not None
        assert negotiation.path == "encoded-native"
        assert initial is not None
        assert {id(item.owner) for item in prepared._retained_leases} == retained_owner_ids

        with (
            _forced_encoded(lease),
            patch.object(
                api_module,
                "prepare_streaming_compilation",
                side_effect=AssertionError("segmented ignored shapes crossed scalar traversal"),
            ),
        ):
            projector = Projector()
            actual = projector.project(view, options=replace(options, backend="native"))

        assert actual == expected == [Edge("urn:slice#MixA", SUBCLASS_OF, "urn:slice#MixB")]
        assert projector.last_report is not None
        assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(scalar_report)
        counters = projector.last_encoded_counters
        assert counters is not None
        assert counters.roots_inspected == counters.selected_roots == 4
        assert counters.scalar_fallbacks == 0
        assert counters.referenced_segments in {1, 2}


def test_segmented_property_chains_preserve_unrelated_edges_reports_and_leases() -> None:
    source_body = (
        "SubObjectPropertyOf(ObjectPropertyChain(:p :q) :r) "
        "ObjectPropertyDomain(:r :D) ObjectPropertyAssertion(:u :i :j)"
    )
    delta_body = (
        'SubObjectPropertyOf(Annotation(<urn:meta> "chain") '
        "ObjectPropertyChain(ObjectInverseOf(:z) :a) :r) "
        "ObjectPropertyRange(:r :R) SubObjectPropertyOf(:child :r)"
    )
    source = _snapshot(source_body)
    delta = _snapshot(delta_body)
    overlay = _snapshot(f"{source_body} {delta_body}")
    composite = compose_views(source, delta)
    rows = (
        (
            overlay,
            _overlay_delta_lease(overlay, _lease(source), _lease(delta)),
            {id(source)},
        ),
        (
            composite,
            _semantic_composite_lease(composite, (_lease(source), _lease(delta))),
            {id(source), id(delta)},
        ),
    )
    options = ProjectionOptions(backend="python", duplicates="unique", order="canonical")

    for view, lease, retained_owner_ids in rows:
        scalar = Projector()
        expected = scalar.project(view, options=options)
        assert scalar.last_report is not None
        scalar_report = scalar.last_report.to_dict()
        prepared, negotiation, initial = prepare_encoded_subset_compilation(
            view,
            replace(options, backend="native"),
            EncodedNegotiation("encoded-native", lease=lease),
            batch_edges=1,
        )
        assert prepared is not None
        assert negotiation.path == "encoded-native"
        assert initial is not None
        assert prepared.statistics.ignored_shapes == 2
        assert prepared.diagnostics == ()
        assert len(prepared._role_axioms) == 1
        assert {id(item.owner) for item in prepared._retained_leases} == retained_owner_ids

        with (
            _forced_encoded(lease),
            patch.object(
                api_module,
                "prepare_streaming_compilation",
                side_effect=AssertionError("segmented chains crossed scalar traversal"),
            ),
        ):
            projector = Projector()
            actual = projector.project(view, options=replace(options, backend="native"))

        assert actual == expected
        assert set(actual) == {
            Edge("urn:slice#i", "urn:slice#u", "urn:slice#j"),
            Edge("urn:slice#D", "urn:slice#r", "urn:slice#R"),
            Edge("urn:slice#D", "urn:slice#child", "urn:slice#R"),
        }
        assert projector.last_report is not None
        assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(scalar_report)
        assert projector.last_report.provenance.counts.ignored_shapes == 2
        assert projector.last_report.diagnostics == ()
        counters = projector.last_encoded_counters
        assert counters is not None
        assert counters.roots_inspected == counters.selected_roots == 6
        assert counters.sub_object_property_axioms == 3
        assert counters.scalar_fallbacks == 0
        assert counters.referenced_segments in {1, 2}


def test_segmented_equivalent_properties_preserve_skips_edges_and_leases() -> None:
    source_body = (
        "EquivalentObjectProperties(:p ObjectInverseOf(:q) :r) "
        "ObjectPropertyDomain(:p :D) ObjectPropertyAssertion(:u :i :j)"
    )
    delta_body = (
        'EquivalentObjectProperties(Annotation(<urn:meta> "skipped") '
        "ObjectInverseOf(:z) :a) "
        "ObjectPropertyRange(:p :R) SubObjectPropertyOf(:child :p)"
    )
    source = _snapshot(source_body)
    delta = _snapshot(delta_body)
    overlay = _snapshot(f"{source_body} {delta_body}")
    composite = compose_views(source, delta)
    rows = (
        (
            overlay,
            _overlay_delta_lease(overlay, _lease(source), _lease(delta)),
            {id(source)},
        ),
        (
            composite,
            _semantic_composite_lease(composite, (_lease(source), _lease(delta))),
            {id(source), id(delta)},
        ),
    )
    options = ProjectionOptions(backend="python", duplicates="unique", order="canonical")

    for view, lease, retained_owner_ids in rows:
        scalar = Projector()
        expected = scalar.project(view, options=options)
        assert scalar.last_report is not None
        scalar_report = scalar.last_report.to_dict()
        prepared, negotiation, initial = prepare_encoded_subset_compilation(
            view,
            replace(options, backend="native"),
            EncodedNegotiation("encoded-native", lease=lease),
            batch_edges=1,
        )
        assert prepared is not None
        assert negotiation.path == "encoded-native"
        assert initial is not None
        assert prepared.statistics.skipped_axioms == 2
        assert len(prepared._role_axioms) == 1
        assert {id(item.owner) for item in prepared._retained_leases} == retained_owner_ids

        with (
            _forced_encoded(lease),
            patch.object(
                api_module,
                "prepare_streaming_compilation",
                side_effect=AssertionError(
                    "segmented equivalent properties crossed scalar traversal"
                ),
            ),
        ):
            projector = Projector()
            actual = projector.project(view, options=replace(options, backend="native"))

        assert actual == expected
        assert set(actual) == {
            Edge("urn:slice#i", "urn:slice#u", "urn:slice#j"),
            Edge("urn:slice#D", "urn:slice#p", "urn:slice#R"),
            Edge("urn:slice#D", "urn:slice#child", "urn:slice#R"),
        }
        assert projector.last_report is not None
        assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(scalar_report)
        assert projector.last_report.provenance.counts.skipped_axioms == 2
        assert projector.last_report.provenance.counts.ignored_shapes == 0
        assert projector.last_report.diagnostics == (
            ProjectionDiagnostic(
                code="MOWL_SKIPPED_AXIOM",
                message="axiom category is not visited by the pinned profile",
                count=2,
                constructor="EquivalentObjectProperties",
            ),
        )
        counters = projector.last_encoded_counters
        assert counters is not None
        assert counters.roots_inspected == counters.selected_roots == 6
        assert counters.equivalent_object_property_axioms == 2
        assert counters.scalar_fallbacks == 0
        assert counters.referenced_segments in {1, 2}


def test_segmented_disjoint_properties_preserve_skips_edges_and_leases() -> None:
    source_body = (
        "DisjointObjectProperties(:p ObjectInverseOf(:q) :r) "
        "ObjectPropertyDomain(:p :D) ObjectPropertyAssertion(:u :i :j)"
    )
    delta_body = (
        'DisjointObjectProperties(Annotation(<urn:meta> "skipped") '
        "ObjectInverseOf(:z) :a) "
        "ObjectPropertyRange(:p :R) SubObjectPropertyOf(:child :p)"
    )
    source = _snapshot(source_body)
    delta = _snapshot(delta_body)
    overlay = _snapshot(f"{source_body} {delta_body}")
    composite = compose_views(source, delta)
    rows = (
        (
            overlay,
            _overlay_delta_lease(overlay, _lease(source), _lease(delta)),
            {id(source)},
        ),
        (
            composite,
            _semantic_composite_lease(composite, (_lease(source), _lease(delta))),
            {id(source), id(delta)},
        ),
    )
    options = ProjectionOptions(backend="python", duplicates="unique", order="canonical")

    for view, lease, retained_owner_ids in rows:
        scalar = Projector()
        expected = scalar.project(view, options=options)
        assert scalar.last_report is not None
        scalar_report = scalar.last_report.to_dict()
        prepared, negotiation, initial = prepare_encoded_subset_compilation(
            view,
            replace(options, backend="native"),
            EncodedNegotiation("encoded-native", lease=lease),
            batch_edges=1,
        )
        assert prepared is not None
        assert negotiation.path == "encoded-native"
        assert initial is not None
        assert prepared.statistics.skipped_axioms == 2
        assert len(prepared._role_axioms) == 1
        assert {id(item.owner) for item in prepared._retained_leases} == retained_owner_ids

        with (
            _forced_encoded(lease),
            patch.object(
                api_module,
                "prepare_streaming_compilation",
                side_effect=AssertionError(
                    "segmented disjoint properties crossed scalar traversal"
                ),
            ),
        ):
            projector = Projector()
            actual = projector.project(view, options=replace(options, backend="native"))

        assert actual == expected
        assert set(actual) == {
            Edge("urn:slice#i", "urn:slice#u", "urn:slice#j"),
            Edge("urn:slice#D", "urn:slice#p", "urn:slice#R"),
            Edge("urn:slice#D", "urn:slice#child", "urn:slice#R"),
        }
        assert projector.last_report is not None
        assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(scalar_report)
        assert projector.last_report.provenance.counts.skipped_axioms == 2
        assert projector.last_report.provenance.counts.ignored_shapes == 0
        assert projector.last_report.diagnostics == (
            ProjectionDiagnostic(
                code="MOWL_SKIPPED_AXIOM",
                message="axiom category is not visited by the pinned profile",
                count=2,
                constructor="DisjointObjectProperties",
            ),
        )
        counters = projector.last_encoded_counters
        assert counters is not None
        assert counters.roots_inspected == counters.selected_roots == 6
        assert counters.disjoint_object_property_axioms == 2
        assert counters.scalar_fallbacks == 0
        assert counters.referenced_segments in {1, 2}


def test_segmented_functional_properties_preserve_skips_edges_and_leases() -> None:
    source_body = (
        "FunctionalObjectProperty(ObjectInverseOf(:p)) "
        "ObjectPropertyDomain(:p :D) ObjectPropertyAssertion(:u :i :j)"
    )
    delta_body = (
        'FunctionalObjectProperty(Annotation(<urn:meta> "skipped") :p) '
        "ObjectPropertyRange(:p :R) SubObjectPropertyOf(:child :p)"
    )
    source = _snapshot(source_body)
    delta = _snapshot(delta_body)
    overlay = _snapshot(f"{source_body} {delta_body}")
    composite = compose_views(source, delta)
    rows = (
        (
            overlay,
            _overlay_delta_lease(overlay, _lease(source), _lease(delta)),
            {id(source)},
        ),
        (
            composite,
            _semantic_composite_lease(composite, (_lease(source), _lease(delta))),
            {id(source), id(delta)},
        ),
    )
    options = ProjectionOptions(backend="python", duplicates="unique", order="canonical")

    for view, lease, retained_owner_ids in rows:
        scalar = Projector()
        expected = scalar.project(view, options=options)
        assert scalar.last_report is not None
        scalar_report = scalar.last_report.to_dict()
        prepared, negotiation, initial = prepare_encoded_subset_compilation(
            view,
            replace(options, backend="native"),
            EncodedNegotiation("encoded-native", lease=lease),
            batch_edges=1,
        )
        assert prepared is not None
        assert negotiation.path == "encoded-native"
        assert initial is not None
        assert prepared.statistics.skipped_axioms == 2
        assert len(prepared._role_axioms) == 1
        assert {id(item.owner) for item in prepared._retained_leases} == retained_owner_ids

        with (
            _forced_encoded(lease),
            patch.object(
                api_module,
                "prepare_streaming_compilation",
                side_effect=AssertionError(
                    "segmented functional properties crossed scalar traversal"
                ),
            ),
        ):
            projector = Projector()
            actual = projector.project(view, options=replace(options, backend="native"))

        assert actual == expected
        assert set(actual) == {
            Edge("urn:slice#i", "urn:slice#u", "urn:slice#j"),
            Edge("urn:slice#D", "urn:slice#p", "urn:slice#R"),
            Edge("urn:slice#D", "urn:slice#child", "urn:slice#R"),
        }
        assert projector.last_report is not None
        assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(scalar_report)
        assert projector.last_report.provenance.counts.skipped_axioms == 2
        assert projector.last_report.provenance.counts.ignored_shapes == 0
        assert projector.last_report.diagnostics == (
            ProjectionDiagnostic(
                code="MOWL_SKIPPED_AXIOM",
                message="axiom category is not visited by the pinned profile",
                count=2,
                constructor="FunctionalObjectProperty",
            ),
        )
        counters = projector.last_encoded_counters
        assert counters is not None
        assert counters.roots_inspected == counters.selected_roots == 6
        assert counters.functional_object_property_axioms == 2
        assert counters.scalar_fallbacks == 0
        assert counters.referenced_segments in {1, 2}


def test_segmented_inverse_functional_properties_preserve_skips_and_leases() -> None:
    source_body = (
        "InverseFunctionalObjectProperty(ObjectInverseOf(:p)) "
        "ObjectPropertyDomain(:p :D) ObjectPropertyAssertion(:u :i :j)"
    )
    delta_body = (
        'InverseFunctionalObjectProperty(Annotation(<urn:meta> "skipped") :p) '
        "ObjectPropertyRange(:p :R) SubObjectPropertyOf(:child :p)"
    )
    source = _snapshot(source_body)
    delta = _snapshot(delta_body)
    overlay = _snapshot(f"{source_body} {delta_body}")
    composite = compose_views(source, delta)
    rows = (
        (
            overlay,
            _overlay_delta_lease(overlay, _lease(source), _lease(delta)),
            {id(source)},
        ),
        (
            composite,
            _semantic_composite_lease(composite, (_lease(source), _lease(delta))),
            {id(source), id(delta)},
        ),
    )
    options = ProjectionOptions(backend="python", duplicates="unique", order="canonical")

    for view, lease, retained_owner_ids in rows:
        scalar = Projector()
        expected = scalar.project(view, options=options)
        assert scalar.last_report is not None
        scalar_report = scalar.last_report.to_dict()
        prepared, negotiation, initial = prepare_encoded_subset_compilation(
            view,
            replace(options, backend="native"),
            EncodedNegotiation("encoded-native", lease=lease),
            batch_edges=1,
        )
        assert prepared is not None
        assert negotiation.path == "encoded-native"
        assert initial is not None
        assert prepared.statistics.skipped_axioms == 2
        assert len(prepared._role_axioms) == 1
        assert {id(item.owner) for item in prepared._retained_leases} == retained_owner_ids

        with (
            _forced_encoded(lease),
            patch.object(
                api_module,
                "prepare_streaming_compilation",
                side_effect=AssertionError(
                    "segmented inverse-functional properties crossed scalar traversal"
                ),
            ),
        ):
            projector = Projector()
            actual = projector.project(view, options=replace(options, backend="native"))

        assert actual == expected
        assert set(actual) == {
            Edge("urn:slice#i", "urn:slice#u", "urn:slice#j"),
            Edge("urn:slice#D", "urn:slice#p", "urn:slice#R"),
            Edge("urn:slice#D", "urn:slice#child", "urn:slice#R"),
        }
        assert projector.last_report is not None
        assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(scalar_report)
        assert projector.last_report.provenance.counts.skipped_axioms == 2
        assert projector.last_report.provenance.counts.ignored_shapes == 0
        assert projector.last_report.diagnostics == (
            ProjectionDiagnostic(
                code="MOWL_SKIPPED_AXIOM",
                message="axiom category is not visited by the pinned profile",
                count=2,
                constructor="InverseFunctionalObjectProperty",
            ),
        )
        counters = projector.last_encoded_counters
        assert counters is not None
        assert counters.roots_inspected == counters.selected_roots == 6
        assert counters.inverse_functional_object_property_axioms == 2
        assert counters.scalar_fallbacks == 0
        assert counters.referenced_segments in {1, 2}


def test_segmented_reflexive_properties_preserve_skips_edges_and_leases() -> None:
    source_body = (
        "ReflexiveObjectProperty(ObjectInverseOf(:p)) "
        "ObjectPropertyDomain(:p :D) ObjectPropertyAssertion(:u :i :j)"
    )
    delta_body = (
        'ReflexiveObjectProperty(Annotation(<urn:meta> "skipped") :p) '
        "ObjectPropertyRange(:p :R) SubObjectPropertyOf(:child :p)"
    )
    source = _snapshot(source_body)
    delta = _snapshot(delta_body)
    overlay = _snapshot(f"{source_body} {delta_body}")
    composite = compose_views(source, delta)
    rows = (
        (
            overlay,
            _overlay_delta_lease(overlay, _lease(source), _lease(delta)),
            {id(source)},
        ),
        (
            composite,
            _semantic_composite_lease(composite, (_lease(source), _lease(delta))),
            {id(source), id(delta)},
        ),
    )
    options = ProjectionOptions(backend="python", duplicates="unique", order="canonical")

    for view, lease, retained_owner_ids in rows:
        scalar = Projector()
        expected = scalar.project(view, options=options)
        assert scalar.last_report is not None
        scalar_report = scalar.last_report.to_dict()
        prepared, negotiation, initial = prepare_encoded_subset_compilation(
            view,
            replace(options, backend="native"),
            EncodedNegotiation("encoded-native", lease=lease),
            batch_edges=1,
        )
        assert prepared is not None
        assert negotiation.path == "encoded-native"
        assert initial is not None
        assert prepared.statistics.skipped_axioms == 2
        assert len(prepared._role_axioms) == 1
        assert {id(item.owner) for item in prepared._retained_leases} == retained_owner_ids

        with (
            _forced_encoded(lease),
            patch.object(
                api_module,
                "prepare_streaming_compilation",
                side_effect=AssertionError(
                    "segmented reflexive properties crossed scalar traversal"
                ),
            ),
        ):
            projector = Projector()
            actual = projector.project(view, options=replace(options, backend="native"))

        assert actual == expected
        assert set(actual) == {
            Edge("urn:slice#i", "urn:slice#u", "urn:slice#j"),
            Edge("urn:slice#D", "urn:slice#p", "urn:slice#R"),
            Edge("urn:slice#D", "urn:slice#child", "urn:slice#R"),
        }
        assert projector.last_report is not None
        assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(scalar_report)
        assert projector.last_report.provenance.counts.skipped_axioms == 2
        assert projector.last_report.provenance.counts.ignored_shapes == 0
        assert projector.last_report.diagnostics == (
            ProjectionDiagnostic(
                code="MOWL_SKIPPED_AXIOM",
                message="axiom category is not visited by the pinned profile",
                count=2,
                constructor="ReflexiveObjectProperty",
            ),
        )
        counters = projector.last_encoded_counters
        assert counters is not None
        assert counters.roots_inspected == counters.selected_roots == 6
        assert counters.reflexive_object_property_axioms == 2
        assert counters.scalar_fallbacks == 0
        assert counters.referenced_segments in {1, 2}


def test_segmented_irreflexive_properties_preserve_skips_edges_and_leases() -> None:
    source_body = (
        "IrreflexiveObjectProperty(ObjectInverseOf(:p)) "
        "ObjectPropertyDomain(:p :D) ObjectPropertyAssertion(:u :i :j)"
    )
    delta_body = (
        'IrreflexiveObjectProperty(Annotation(<urn:meta> "skipped") :p) '
        "ObjectPropertyRange(:p :R) SubObjectPropertyOf(:child :p)"
    )
    source = _snapshot(source_body)
    delta = _snapshot(delta_body)
    overlay = _snapshot(f"{source_body} {delta_body}")
    composite = compose_views(source, delta)
    rows = (
        (
            overlay,
            _overlay_delta_lease(overlay, _lease(source), _lease(delta)),
            {id(source)},
        ),
        (
            composite,
            _semantic_composite_lease(composite, (_lease(source), _lease(delta))),
            {id(source), id(delta)},
        ),
    )
    options = ProjectionOptions(backend="python", duplicates="unique", order="canonical")

    for view, lease, retained_owner_ids in rows:
        scalar = Projector()
        expected = scalar.project(view, options=options)
        assert scalar.last_report is not None
        scalar_report = scalar.last_report.to_dict()
        prepared, negotiation, initial = prepare_encoded_subset_compilation(
            view,
            replace(options, backend="native"),
            EncodedNegotiation("encoded-native", lease=lease),
            batch_edges=1,
        )
        assert prepared is not None
        assert negotiation.path == "encoded-native"
        assert initial is not None
        assert prepared.statistics.skipped_axioms == 2
        assert len(prepared._role_axioms) == 1
        assert {id(item.owner) for item in prepared._retained_leases} == retained_owner_ids

        with (
            _forced_encoded(lease),
            patch.object(
                api_module,
                "prepare_streaming_compilation",
                side_effect=AssertionError(
                    "segmented irreflexive properties crossed scalar traversal"
                ),
            ),
        ):
            projector = Projector()
            actual = projector.project(view, options=replace(options, backend="native"))

        assert actual == expected
        assert set(actual) == {
            Edge("urn:slice#i", "urn:slice#u", "urn:slice#j"),
            Edge("urn:slice#D", "urn:slice#p", "urn:slice#R"),
            Edge("urn:slice#D", "urn:slice#child", "urn:slice#R"),
        }
        assert projector.last_report is not None
        assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(scalar_report)
        assert projector.last_report.provenance.counts.skipped_axioms == 2
        assert projector.last_report.provenance.counts.ignored_shapes == 0
        assert projector.last_report.diagnostics == (
            ProjectionDiagnostic(
                code="MOWL_SKIPPED_AXIOM",
                message="axiom category is not visited by the pinned profile",
                count=2,
                constructor="IrreflexiveObjectProperty",
            ),
        )
        counters = projector.last_encoded_counters
        assert counters is not None
        assert counters.roots_inspected == counters.selected_roots == 6
        assert counters.irreflexive_object_property_axioms == 2
        assert counters.scalar_fallbacks == 0
        assert counters.referenced_segments in {1, 2}


def test_segmented_inverse_properties_preserve_order_diagnostics_and_leases() -> None:
    source_body = (
        'SubClassOf(Annotation(<urn:meta> "inverse-source") '
        ":A ObjectSomeValuesFrom(ObjectInverseOf(:p) :B)) "
        "ObjectPropertyDomain(ObjectInverseOf(:p) :IgnoredDomain) "
        "SubObjectPropertyOf(ObjectInverseOf(:child) :p)"
    )
    delta_body = (
        "EquivalentClasses(:C ObjectIntersectionOf(:D "
        "ObjectAllValuesFrom(ObjectInverseOf(:p) :E))) "
        'ObjectPropertyRange(Annotation(<urn:meta> "inverse-delta") '
        "ObjectInverseOf(:p) :IgnoredRange) "
        "InverseObjectProperties(ObjectInverseOf(:p) :pinv) "
        "ObjectPropertyDomain(:p :ProjectedDomain) "
        "ObjectPropertyRange(:p :ProjectedRange)"
    )
    source = _snapshot(source_body)
    delta = _snapshot(delta_body)
    overlay = _snapshot(f"{source_body} {delta_body}")
    composite = compose_views(source, delta)
    rows = (
        (
            overlay,
            _overlay_delta_lease(overlay, _lease(source), _lease(delta)),
            {id(source)},
        ),
        (
            composite,
            _semantic_composite_lease(composite, (_lease(source), _lease(delta))),
            {id(source), id(delta)},
        ),
    )
    options = ProjectionOptions(backend="python", duplicates="unique", order="canonical")

    for view, lease, retained_owner_ids in rows:
        scalar = Projector()
        expected = scalar.project(view, options=options)
        assert scalar.last_report is not None
        scalar_report = scalar.last_report.to_dict()
        prepared, negotiation, initial = prepare_encoded_subset_compilation(
            view,
            replace(options, backend="native"),
            EncodedNegotiation("encoded-native", lease=lease),
            batch_edges=1,
        )
        assert prepared is not None
        assert negotiation.path == "encoded-native"
        assert initial is not None
        assert {id(item.owner) for item in prepared._retained_leases} == retained_owner_ids

        with (
            _forced_encoded(lease),
            patch.object(
                api_module,
                "prepare_streaming_compilation",
                side_effect=AssertionError("segmented inverse properties crossed scalar traversal"),
            ),
        ):
            projector = Projector()
            actual = projector.project(view, options=replace(options, backend="native"))

        assert (
            actual
            == expected
            == [
                Edge("urn:slice#A", "urn:slice#child", "urn:slice#B"),
                Edge("urn:slice#A", "urn:slice#p", "urn:slice#B"),
                Edge("urn:slice#B", "urn:slice#pinv", "urn:slice#A"),
                Edge("urn:slice#C", SUBCLASS_OF, "urn:slice#D"),
                Edge("urn:slice#C", "urn:slice#child", "urn:slice#E"),
                Edge("urn:slice#C", "urn:slice#p", "urn:slice#E"),
                Edge("urn:slice#E", "urn:slice#pinv", "urn:slice#C"),
                Edge(
                    "urn:slice#ProjectedDomain",
                    "urn:slice#child",
                    "urn:slice#ProjectedRange",
                ),
                Edge("urn:slice#ProjectedDomain", "urn:slice#p", "urn:slice#ProjectedRange"),
                Edge(
                    "urn:slice#ProjectedRange",
                    "urn:slice#pinv",
                    "urn:slice#ProjectedDomain",
                ),
            ]
        )
        assert projector.last_report is not None
        assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(scalar_report)
        ignored = {
            item.constructor: item.count
            for item in projector.last_report.diagnostics
            if item.code == "MOWL_IGNORED_SHAPE"
        }
        assert ignored == {"ObjectPropertyDomain": 1, "ObjectPropertyRange": 1}
        counters = projector.last_encoded_counters
        assert counters is not None
        assert counters.roots_inspected == counters.selected_roots == 8
        assert counters.sub_object_property_axioms == 1
        assert counters.inverse_object_property_axioms == 1
        assert counters.scalar_fallbacks == 0
        assert counters.referenced_segments in {1, 2}


def test_unsupported_constructor_selects_one_whole_operation_scalar_fallback() -> None:
    view = _snapshot("SubClassOf(:A ObjectSomeValuesFrom(:p ObjectIntersectionOf(:B :C)))")
    lease = _lease(view)
    python_options = ProjectionOptions(backend="python", order="encounter")
    scalar = Projector()
    expected = scalar.project(view, options=python_options)
    assert scalar.last_report is not None
    scalar_report = scalar.last_report.to_dict()
    real_scalar = scalar_compilation

    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            wraps=real_scalar,
        ) as scalar_prepare,
    ):
        projector = Projector()
        actual = projector.project(
            view,
            options=replace(python_options, backend="native"),
        )

    assert actual == expected
    assert scalar_prepare.call_count == 1
    assert projector.last_report is not None
    assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(scalar_report)
    ingestion = projector.last_report.provenance.ingestion
    assert ingestion.path == "scalar-native"
    assert "whole-operation scalar compiler" in (ingestion.reason or "")
    assert projector.last_encoded_counters is not None
    assert projector.last_encoded_counters.scalar_fallbacks == 1
    assert projector.last_encoded_counters.edge_batches == 0
    assert projector.last_encoded_counters.raw_edges == 0


@pytest.mark.parametrize(
    "body",
    [
        "EquivalentClasses(:A ObjectIntersectionOf(:B ObjectComplementOf(:C)))",
    ],
)
def test_new_slice_unsupported_shapes_fallback_once_before_output(body: str) -> None:
    view = _snapshot(body)
    lease = _lease(view)
    python_options = ProjectionOptions(backend="python", order="encounter")
    scalar = Projector()
    expected = scalar.project(view, options=python_options)
    assert scalar.last_report is not None
    scalar_report = scalar.last_report.to_dict()

    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            wraps=scalar_compilation,
        ) as scalar_prepare,
    ):
        projector = Projector()
        actual = projector.project(
            view,
            options=replace(python_options, backend="native"),
        )

    assert actual == expected
    assert scalar_prepare.call_count == 1
    assert projector.last_report is not None
    assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(scalar_report)
    assert projector.last_report.provenance.ingestion.path == "scalar-native"
    assert projector.last_encoded_counters is not None
    assert projector.last_encoded_counters.scalar_fallbacks == 1
    assert projector.last_encoded_counters.edge_batches == 0
    assert projector.last_encoded_counters.raw_edges == 0


def test_inverse_object_property_assertion_falls_back_to_scalar_error() -> None:
    view = _snapshot("ObjectPropertyAssertion(ObjectInverseOf(:p) :i :j)")
    lease = _lease(view)
    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            wraps=scalar_compilation,
        ) as scalar_prepare,
    ):
        projector = Projector()
        with pytest.raises(UnsupportedAxiomShapeError, match="inverse object-property"):
            projector.project(
                view,
                options=ProjectionOptions(backend="native", order="encounter"),
            )

    assert scalar_prepare.call_count == 1
    counters = projector.last_encoded_counters
    assert counters is not None
    assert counters.scalar_fallbacks == 1
    assert counters.edge_batches == 0
    assert counters.raw_edges == 0


def test_asserted_taxonomy_api_uses_the_same_bounded_slice() -> None:
    view = _snapshot(
        "Declaration(Class(:A)) Declaration(Class(:B)) Declaration(Class(:C)) "
        "SubClassOf(:A :B) SubClassOf(:B :C)"
    )
    lease = _lease(view)
    expected = Projector().project_taxonomy(
        view,
        bidirectional=True,
        duplicates="unique",
        order="canonical",
        backend="python",
        buffer_edges=2,
    )

    with _forced_encoded(lease):
        projector = Projector()
        actual = projector.project_taxonomy(
            view,
            bidirectional=True,
            duplicates="unique",
            order="canonical",
            backend="native",
            buffer_edges=2,
        )

    assert actual == expected
    assert projector.last_encoded_counters is not None
    assert projector.last_encoded_counters.edge_batches == 2
    assert projector.last_encoded_counters.raw_edges == 4


def test_asserted_taxonomy_skips_other_supported_axiom_edges() -> None:
    view = _snapshot(
        "SubClassOf(:A :B) EquivalentClasses(:A :C :D) ClassAssertion(:A :i) "
        "ObjectPropertyAssertion(:p :i :j) ObjectPropertyAssertion(:p _:anon :i) "
        "ObjectPropertyDomain(:p :C) "
        "ObjectPropertyRange(:p :D) SubObjectPropertyOf(:q :p) "
        "SubObjectPropertyOf(ObjectPropertyChain(:p :q) :r) "
        "EquivalentObjectProperties(:p ObjectInverseOf(:q)) "
        "DisjointObjectProperties(:p ObjectInverseOf(:q)) "
        "FunctionalObjectProperty(ObjectInverseOf(:p)) "
        "InverseFunctionalObjectProperty(ObjectInverseOf(:p)) "
        "ReflexiveObjectProperty(ObjectInverseOf(:p)) "
        "IrreflexiveObjectProperty(ObjectInverseOf(:p)) "
        "InverseObjectProperties(:p :pinv) SubClassOf(:C ObjectSomeValuesFrom(:p :D)) "
        "SubClassOf(ObjectSomeValuesFrom(:p :D) ObjectAllValuesFrom(:q :E)) "
        "EquivalentClasses(:E ObjectIntersectionOf(:F ObjectSomeValuesFrom(:p :D))) "
        "ClassAssertion(ObjectMinCardinality(2 :p :D) :complexIndividual) "
        "ObjectPropertyDomain(:p ObjectUnionOf(:C :D)) "
        "ObjectPropertyRange(:p ObjectMaxCardinality(3 :q :E)) "
        'AnnotationAssertion(<http://www.w3.org/2000/01/rdf-schema#label> :A "label")'
    )
    lease = _lease(view)
    expected = Projector().project_taxonomy(
        view,
        bidirectional=True,
        duplicates="preserve",
        order="encounter",
        backend="python",
        buffer_edges=1,
    )

    with (
        _forced_encoded(lease),
        patch.object(
            api_module,
            "iter_asserted_taxonomy",
            side_effect=AssertionError("encoded taxonomy crossed scalar axiom traversal"),
        ),
    ):
        projector = Projector()
        actual = projector.project_taxonomy(
            view,
            bidirectional=True,
            duplicates="preserve",
            order="encounter",
            backend="native",
            buffer_edges=1,
        )

    assert actual == expected
    assert len(actual) == 2
    counters = projector.last_encoded_counters
    assert counters is not None
    assert counters.subclass_axioms == 3
    assert counters.restriction_subclass_axioms == 2
    assert counters.equivalent_axioms == 2
    assert counters.aggregate_equivalent_axioms == 1
    assert counters.class_assertion_axioms == 2
    assert counters.sub_object_property_axioms == 2
    assert counters.equivalent_object_property_axioms == 1
    assert counters.disjoint_object_property_axioms == 1
    assert counters.inverse_object_property_axioms == 1
    assert counters.object_property_assertion_axioms == 2
    assert counters.object_property_domain_axioms == 2
    assert counters.object_property_range_axioms == 2
    assert counters.functional_object_property_axioms == 1
    assert counters.inverse_functional_object_property_axioms == 1
    assert counters.reflexive_object_property_axioms == 1
    assert counters.irreflexive_object_property_axioms == 1
    assert counters.annotation_assertion_axioms == 1
    assert counters.anonymous_individuals == 1
    assert counters.literal_nodes == 1
    assert counters.edge_batches == 2
    assert counters.raw_edges == 2
    assert counters.scalar_fallbacks == 0


@pytest.mark.parametrize(
    "corruption",
    ["arity", "entity-kind", "unknown-tag", "root-order"],
)
def test_supported_tag_corruption_fails_before_edge_output(corruption: str) -> None:
    view = _snapshot("Declaration(Class(:A)) SubClassOf(:A :B)")
    lease = _lease(view)
    buffers = dict(lease.buffers)
    if corruption == "arity":
        tags = buffers["node_tags"]
        subclass_id = next(
            index
            for index in range(1, tags.nbytes // 2 + 1)
            if int.from_bytes(tags[(index - 1) * 2 : index * 2], "little") == 61
        )
        offsets = bytearray(buffers["node_field_offsets"])
        end_offset = subclass_id * 8
        end = int.from_bytes(offsets[end_offset : end_offset + 8], "little")
        offsets[end_offset : end_offset + 8] = (end - 1).to_bytes(8, "little")
        buffers["node_field_offsets"] = memoryview(bytes(offsets))
    elif corruption == "entity-kind":
        scalars = bytes(buffers["scalar_bytes"])
        assert b"class" in scalars
        buffers["scalar_bytes"] = memoryview(scalars.replace(b"class", b"xxxxx", 1))
    elif corruption == "unknown-tag":
        tag_bytes = bytearray(buffers["node_tags"])
        tag_bytes[:2] = (999).to_bytes(2, "little")
        buffers["node_tags"] = memoryview(bytes(tag_bytes))
    else:
        root_ids = bytes(buffers["root_ids"])
        assert len(root_ids) == 8
        buffers["root_ids"] = memoryview(root_ids[4:] + root_ids[:4])
    hostile = replace(lease, buffers=MappingProxyType(buffers))

    with pytest.raises(
        SnapshotCompatibilityError,
        match=r"arity|entity kind|frozen schema|canonical",
    ):
        prepare_encoded_subset_compilation(
            view,
            ProjectionOptions(backend="native"),
            EncodedNegotiation("encoded-native", lease=hostile),
            batch_edges=1,
        )


@pytest.mark.parametrize("corruption", ["item-kind", "item-length", "item-order", "set-size"])
def test_equivalent_class_set_corruption_fails_before_edge_output(corruption: str) -> None:
    view = _snapshot("EquivalentClasses(:Z :AA :B)")
    lease = _lease(view)
    buffers = dict(lease.buffers)
    if corruption == "item-kind":
        kinds = bytearray(buffers["item_kinds"])
        kinds[0] = 5
        buffers["item_kinds"] = memoryview(bytes(kinds))
    elif corruption == "item-length":
        lengths = bytearray(buffers["item_lengths"])
        lengths[:8] = (1).to_bytes(8, "little")
        buffers["item_lengths"] = memoryview(bytes(lengths))
    elif corruption == "item-order":
        values = bytearray(buffers["item_values"])
        first = bytes(values[:8])
        second = bytes(values[8:16])
        values[:8] = second
        values[8:16] = first
        buffers["item_values"] = memoryview(bytes(values))
    else:
        tags = buffers["node_tags"]
        equivalent_id = next(
            index
            for index in range(1, tags.nbytes // 2 + 1)
            if int.from_bytes(tags[(index - 1) * 2 : index * 2], "little") == 62
        )
        field_offsets = buffers["node_field_offsets"]
        field_start = int.from_bytes(
            field_offsets[(equivalent_id - 1) * 8 : equivalent_id * 8], "little"
        )
        lengths = bytearray(buffers["field_lengths"])
        offset = field_start * 8
        lengths[offset : offset + 8] = (1).to_bytes(8, "little")
        buffers["field_lengths"] = memoryview(bytes(lengths))
    hostile = replace(lease, buffers=MappingProxyType(buffers))

    with pytest.raises(
        SnapshotCompatibilityError,
        match=r"node reference|sorted and unique|too few items",
    ):
        prepare_encoded_subset_compilation(
            view,
            ProjectionOptions(backend="native"),
            EncodedNegotiation("encoded-native", lease=hostile),
            batch_edges=1,
        )


@pytest.mark.parametrize(
    ("body", "tag", "arity"),
    [
        (
            "SubClassOf(ObjectSomeValuesFrom(:p :A) ObjectAllValuesFrom(:q :B))",
            61,
            3,
        ),
        ("EquivalentClasses(:A ObjectSomeValuesFrom(:p :B))", 62, 2),
        ("ClassAssertion(ObjectSomeValuesFrom(:p :B) :i)", 112, 3),
        ("ObjectPropertyDomain(:p ObjectIntersectionOf(:A :B))", 74, 3),
        ("ObjectPropertyRange(:p ObjectMaxCardinality(2 :q :B))", 75, 3),
    ],
    ids=["subclass", "equivalent", "class-assertion", "domain", "range"],
)
def test_validated_ignored_root_arity_corruption_fails_before_output(
    body: str,
    tag: int,
    arity: int,
) -> None:
    view = _snapshot(body)
    lease = _lease(view)
    buffers = dict(lease.buffers)
    tags = buffers["node_tags"]
    node_id = next(
        index
        for index in range(1, tags.nbytes // 2 + 1)
        if int.from_bytes(tags[(index - 1) * 2 : index * 2], "little") == tag
    )
    offsets = bytearray(buffers["node_field_offsets"])
    end_offset = node_id * 8
    end = int.from_bytes(offsets[end_offset : end_offset + 8], "little")
    offsets[end_offset : end_offset + 8] = (end - 1).to_bytes(8, "little")
    buffers["node_field_offsets"] = memoryview(bytes(offsets))
    hostile = replace(lease, buffers=MappingProxyType(buffers))

    with pytest.raises(SnapshotCompatibilityError, match="arity") as raised:
        prepare_encoded_subset_compilation(
            view,
            ProjectionOptions(backend="native"),
            EncodedNegotiation("encoded-native", lease=hostile),
            batch_edges=1,
        )
    assert raised.value.details["expected_arity"] == arity


@pytest.mark.parametrize(
    ("body", "root_tag", "field_delta"),
    [
        (
            "SubClassOf(:A ObjectIntersectionOf(:B :C)) Declaration(ObjectProperty(:wrong))",
            61,
            1,
        ),
        (
            "ClassAssertion(ObjectSomeValuesFrom(:p :B) :i) Declaration(ObjectProperty(:wrong))",
            112,
            0,
        ),
        (
            "ObjectPropertyDomain(:p ObjectUnionOf(:A :B)) Declaration(ObjectProperty(:wrong))",
            74,
            1,
        ),
        (
            "ObjectPropertyRange(:p ObjectMaxCardinality(2 :q :B)) "
            "Declaration(ObjectProperty(:wrong))",
            75,
            1,
        ),
    ],
    ids=["subclass", "class-assertion", "domain", "range"],
)
def test_hostile_wrong_kind_complex_root_reference_selects_scalar_before_output(
    body: str,
    root_tag: int,
    field_delta: int,
) -> None:
    view = _snapshot(body)
    lease = _lease(view)
    columns = _EncodedColumns(lease)
    wrong_id = next(
        node_id
        for node_id in range(1, columns.node_count + 1)
        if columns._named_object_property_iri(node_id) == "urn:slice#wrong"
    )
    buffers = dict(lease.buffers)
    tags = buffers["node_tags"]
    root_id = next(
        node_id
        for node_id in range(1, tags.nbytes // 2 + 1)
        if int.from_bytes(tags[(node_id - 1) * 2 : node_id * 2], "little") == root_tag
    )
    offsets = buffers["node_field_offsets"]
    field_start = int.from_bytes(offsets[(root_id - 1) * 8 : root_id * 8], "little")
    values = bytearray(buffers["field_values"])
    field_offset = (field_start + field_delta) * 8
    values[field_offset : field_offset + 8] = wrong_id.to_bytes(8, "little")
    buffers["field_values"] = memoryview(bytes(values))
    hostile = replace(lease, buffers=MappingProxyType(buffers))

    compilation, negotiation, counters = prepare_encoded_subset_compilation(
        view,
        ProjectionOptions(backend="native"),
        EncodedNegotiation("encoded-native", lease=hostile),
        batch_edges=1,
    )

    assert compilation is None
    assert negotiation.path == "scalar-native"
    assert "whole-operation scalar compiler" in (negotiation.reason or "")
    assert counters is not None
    assert counters.scalar_fallbacks == 1
    assert counters.raw_edges == counters.edge_batches == 0


@pytest.mark.parametrize("nested", [False, True], ids=["equivalent-root", "aggregate-operand"])
def test_hostile_wrong_kind_equivalent_set_reference_selects_scalar_before_output(
    nested: bool,
) -> None:
    body = (
        "EquivalentClasses(:A ObjectIntersectionOf(:B :C)) Declaration(ObjectProperty(:wrong))"
        if nested
        else "EquivalentClasses(:A ObjectSomeValuesFrom(:p :B)) Declaration(ObjectProperty(:wrong))"
    )
    view = _snapshot(body)
    lease = _lease(view)
    columns = _EncodedColumns(lease)
    wrong_id = next(
        node_id
        for node_id in range(1, columns.node_count + 1)
        if columns._named_object_property_iri(node_id) == "urn:slice#wrong"
    )
    target_tag = 30 if nested else 62
    buffers = dict(lease.buffers)
    tags = buffers["node_tags"]
    target_id = next(
        node_id
        for node_id in range(1, tags.nbytes // 2 + 1)
        if int.from_bytes(tags[(node_id - 1) * 2 : node_id * 2], "little") == target_tag
    )
    offsets = buffers["node_field_offsets"]
    field_start = int.from_bytes(offsets[(target_id - 1) * 8 : target_id * 8], "little")
    field_values = buffers["field_values"]
    item_start = int.from_bytes(
        field_values[field_start * 8 : (field_start + 1) * 8],
        "little",
    )
    item_length = int.from_bytes(
        buffers["field_lengths"][field_start * 8 : (field_start + 1) * 8],
        "little",
    )
    item_values = bytearray(buffers["item_values"])
    item_ids = [
        int.from_bytes(item_values[index * 8 : (index + 1) * 8], "little")
        for index in range(item_start, item_start + item_length)
    ]
    item_ids[-1] = wrong_id
    item_ids.sort()
    assert len(set(item_ids)) == item_length
    for index, node_id in enumerate(item_ids, item_start):
        item_values[index * 8 : (index + 1) * 8] = node_id.to_bytes(8, "little")
    buffers["item_values"] = memoryview(bytes(item_values))
    hostile = replace(lease, buffers=MappingProxyType(buffers))

    compilation, negotiation, counters = prepare_encoded_subset_compilation(
        view,
        ProjectionOptions(backend="native"),
        EncodedNegotiation("encoded-native", lease=hostile),
        batch_edges=1,
    )

    assert compilation is None
    assert negotiation.path == "scalar-native"
    assert counters is not None
    assert counters.scalar_fallbacks == 1
    assert counters.raw_edges == counters.edge_batches == 0


@pytest.mark.parametrize(
    ("tag", "arity"),
    [(34, 2), (35, 2), (38, 3), (39, 3)],
)
def test_restriction_arity_corruption_fails_before_edge_output(
    tag: int,
    arity: int,
) -> None:
    view = _snapshot(
        "SubClassOf(:A ObjectSomeValuesFrom(:p :B)) "
        "SubClassOf(:C ObjectAllValuesFrom(:p :D)) "
        "SubClassOf(:E ObjectMinCardinality(2 :p :F)) "
        "SubClassOf(:G ObjectMaxCardinality(3 :p :H))"
    )
    lease = _lease(view)
    buffers = dict(lease.buffers)
    tags = buffers["node_tags"]
    node_id = next(
        index
        for index in range(1, tags.nbytes // 2 + 1)
        if int.from_bytes(tags[(index - 1) * 2 : index * 2], "little") == tag
    )
    offsets = bytearray(buffers["node_field_offsets"])
    end_offset = node_id * 8
    end = int.from_bytes(offsets[end_offset : end_offset + 8], "little")
    offsets[end_offset : end_offset + 8] = (end - 1).to_bytes(8, "little")
    buffers["node_field_offsets"] = memoryview(bytes(offsets))
    hostile = replace(lease, buffers=MappingProxyType(buffers))

    with pytest.raises(SnapshotCompatibilityError, match="arity") as raised:
        prepare_encoded_subset_compilation(
            view,
            ProjectionOptions(backend="native"),
            EncodedNegotiation("encoded-native", lease=hostile),
            batch_edges=1,
        )
    assert raised.value.details["expected_arity"] == arity


@pytest.mark.parametrize(
    "corruption",
    [
        "arity",
        "field-kind",
        "sequence-size",
        "sequence-range",
        "item-kind",
        "item-length",
        "item-range",
        "wrong-item",
        "nested-chain",
    ],
)
def test_property_chain_corruption_fails_before_edge_output(corruption: str) -> None:
    view = _snapshot(
        "SubObjectPropertyOf(ObjectPropertyChain(:z ObjectInverseOf(:a) :m) :r) "
        "SubClassOf(:A :B) Declaration(Class(:Wrong))"
    )
    lease = _lease(view)
    columns = _EncodedColumns(lease)
    chain_id = next(
        node_id for node_id in range(1, columns.node_count + 1) if columns.node_tag(node_id) == 11
    )
    wrong_id = next(
        node_id
        for node_id in range(1, columns.node_count + 1)
        if columns._named_class_iri(node_id) == "urn:slice#Wrong"
    )
    buffers = dict(lease.buffers)
    offsets = buffers["node_field_offsets"]
    field_index = int.from_bytes(
        offsets[(chain_id - 1) * 8 : chain_id * 8],
        "little",
    )
    item_start = int.from_bytes(
        buffers["field_values"][field_index * 8 : (field_index + 1) * 8],
        "little",
    )
    if corruption == "arity":
        changed_offsets = bytearray(offsets)
        end_offset = chain_id * 8
        end = int.from_bytes(changed_offsets[end_offset : end_offset + 8], "little")
        changed_offsets[end_offset : end_offset + 8] = (end - 1).to_bytes(8, "little")
        buffers["node_field_offsets"] = memoryview(bytes(changed_offsets))
    elif corruption == "field-kind":
        kinds = bytearray(buffers["field_kinds"])
        kinds[field_index] = 6
        buffers["field_kinds"] = memoryview(bytes(kinds))
    elif corruption == "sequence-size":
        lengths = bytearray(buffers["field_lengths"])
        lengths[field_index * 8 : (field_index + 1) * 8] = (1).to_bytes(8, "little")
        buffers["field_lengths"] = memoryview(bytes(lengths))
    elif corruption == "sequence-range":
        values = bytearray(buffers["field_values"])
        values[field_index * 8 : (field_index + 1) * 8] = (columns.item_count + 1).to_bytes(
            8,
            "little",
        )
        buffers["field_values"] = memoryview(bytes(values))
    elif corruption == "item-kind":
        kinds = bytearray(buffers["item_kinds"])
        kinds[item_start] = 5
        buffers["item_kinds"] = memoryview(bytes(kinds))
    elif corruption == "item-length":
        lengths = bytearray(buffers["item_lengths"])
        lengths[item_start * 8 : (item_start + 1) * 8] = (1).to_bytes(8, "little")
        buffers["item_lengths"] = memoryview(bytes(lengths))
    else:
        values = bytearray(buffers["item_values"])
        replacement = {
            "item-range": columns.node_count + 1,
            "wrong-item": wrong_id,
            "nested-chain": chain_id,
        }[corruption]
        values[item_start * 8 : (item_start + 1) * 8] = replacement.to_bytes(8, "little")
        buffers["item_values"] = memoryview(bytes(values))
    hostile = replace(lease, buffers=MappingProxyType(buffers))

    with pytest.raises(
        SnapshotCompatibilityError,
        match=(
            r"arity|ordered sequence|too few items|out of bounds|node reference|"
            r"node id is out of range|sorted and unique|ObjectPropertyChain item"
        ),
    ):
        prepare_encoded_subset_compilation(
            view,
            ProjectionOptions(backend="native"),
            EncodedNegotiation("encoded-native", lease=hostile),
            batch_edges=1,
        )


@pytest.mark.parametrize(
    "corruption",
    [
        "arity",
        "field-kind",
        "set-size",
        "set-range",
        "item-kind",
        "item-length",
        "item-order",
        "item-range",
        "wrong-item",
        "property-chain-item",
    ],
)
@pytest.mark.parametrize(
    ("constructor", "tag"),
    [("EquivalentObjectProperties", 71), ("DisjointObjectProperties", 72)],
    ids=["equivalent", "disjoint"],
)
def test_skipped_object_property_set_corruption_fails_before_output(
    corruption: str,
    constructor: str,
    tag: int,
) -> None:
    view = _snapshot(
        f"{constructor}(:p ObjectInverseOf(:q) :r) "
        "SubObjectPropertyOf(ObjectPropertyChain(:p :q) :s) "
        "SubClassOf(:A :B) Declaration(Class(:Wrong))"
    )
    lease = _lease(view)
    columns = _EncodedColumns(lease)
    axiom_id = next(
        node_id for node_id in range(1, columns.node_count + 1) if columns.node_tag(node_id) == tag
    )
    chain_id = next(
        node_id for node_id in range(1, columns.node_count + 1) if columns.node_tag(node_id) == 11
    )
    wrong_id = next(
        node_id
        for node_id in range(1, columns.node_count + 1)
        if columns._named_class_iri(node_id) == "urn:slice#Wrong"
    )
    buffers = dict(lease.buffers)
    offsets = buffers["node_field_offsets"]
    field_index = int.from_bytes(
        offsets[(axiom_id - 1) * 8 : axiom_id * 8],
        "little",
    )
    item_start = int.from_bytes(
        buffers["field_values"][field_index * 8 : (field_index + 1) * 8],
        "little",
    )
    item_length = int.from_bytes(
        buffers["field_lengths"][field_index * 8 : (field_index + 1) * 8],
        "little",
    )
    assert item_length == 3
    if corruption == "arity":
        changed_offsets = bytearray(offsets)
        end_offset = axiom_id * 8
        end = int.from_bytes(changed_offsets[end_offset : end_offset + 8], "little")
        changed_offsets[end_offset : end_offset + 8] = (end - 1).to_bytes(8, "little")
        buffers["node_field_offsets"] = memoryview(bytes(changed_offsets))
    elif corruption == "field-kind":
        kinds = bytearray(buffers["field_kinds"])
        kinds[field_index] = 7
        buffers["field_kinds"] = memoryview(bytes(kinds))
    elif corruption == "set-size":
        lengths = bytearray(buffers["field_lengths"])
        lengths[field_index * 8 : (field_index + 1) * 8] = (1).to_bytes(8, "little")
        buffers["field_lengths"] = memoryview(bytes(lengths))
    elif corruption == "set-range":
        values = bytearray(buffers["field_values"])
        values[field_index * 8 : (field_index + 1) * 8] = (columns.item_count + 1).to_bytes(
            8,
            "little",
        )
        buffers["field_values"] = memoryview(bytes(values))
    elif corruption == "item-kind":
        kinds = bytearray(buffers["item_kinds"])
        kinds[item_start] = 5
        buffers["item_kinds"] = memoryview(bytes(kinds))
    elif corruption == "item-length":
        lengths = bytearray(buffers["item_lengths"])
        lengths[item_start * 8 : (item_start + 1) * 8] = (1).to_bytes(8, "little")
        buffers["item_lengths"] = memoryview(bytes(lengths))
    else:
        values = bytearray(buffers["item_values"])
        if corruption == "item-order":
            first = bytes(values[item_start * 8 : (item_start + 1) * 8])
            second = bytes(values[(item_start + 1) * 8 : (item_start + 2) * 8])
            values[item_start * 8 : (item_start + 1) * 8] = second
            values[(item_start + 1) * 8 : (item_start + 2) * 8] = first
        else:
            replacement = {
                "item-range": columns.node_count + 1,
                "wrong-item": wrong_id,
                "property-chain-item": chain_id,
            }[corruption]
            values[item_start * 8 : (item_start + 1) * 8] = replacement.to_bytes(8, "little")
        buffers["item_values"] = memoryview(bytes(values))
    hostile = replace(lease, buffers=MappingProxyType(buffers))

    with pytest.raises(
        SnapshotCompatibilityError,
        match=(
            r"arity|canonical set|too few items|out of bounds|node reference|"
            r"node id is out of range|sorted and unique|ObjectProperties item"
        ),
    ):
        prepare_encoded_subset_compilation(
            view,
            ProjectionOptions(backend="native"),
            EncodedNegotiation("encoded-native", lease=hostile),
            batch_edges=1,
        )


@pytest.mark.parametrize(
    "corruption",
    [
        "arity",
        "property-kind",
        "property-length",
        "property-range",
        "wrong-property",
        "property-chain",
        "annotation-kind",
        "annotation-item",
    ],
)
@pytest.mark.parametrize(
    ("constructor", "tag"),
    [
        ("FunctionalObjectProperty", 76),
        ("InverseFunctionalObjectProperty", 77),
        ("ReflexiveObjectProperty", 78),
        ("IrreflexiveObjectProperty", 79),
    ],
    ids=["functional", "inverse-functional", "reflexive", "irreflexive"],
)
def test_unary_object_property_characteristic_corruption_fails_before_output(
    corruption: str,
    constructor: str,
    tag: int,
) -> None:
    view = _snapshot(
        f'{constructor}(Annotation(<urn:a> "x") ObjectInverseOf(:p)) '
        "SubObjectPropertyOf(ObjectPropertyChain(:p :q) :s) "
        "SubClassOf(:A :B) Declaration(Class(:Wrong))"
    )
    lease = _lease(view)
    columns = _EncodedColumns(lease)
    axiom_id = next(
        node_id for node_id in range(1, columns.node_count + 1) if columns.node_tag(node_id) == tag
    )
    chain_id = next(
        node_id for node_id in range(1, columns.node_count + 1) if columns.node_tag(node_id) == 11
    )
    wrong_id = next(
        node_id
        for node_id in range(1, columns.node_count + 1)
        if columns._named_class_iri(node_id) == "urn:slice#Wrong"
    )
    buffers = dict(lease.buffers)
    offsets = buffers["node_field_offsets"]
    field_index = int.from_bytes(
        offsets[(axiom_id - 1) * 8 : axiom_id * 8],
        "little",
    )
    annotation_index = field_index + 1
    annotation_item = int.from_bytes(
        buffers["field_values"][annotation_index * 8 : (annotation_index + 1) * 8],
        "little",
    )
    if corruption == "arity":
        changed_offsets = bytearray(offsets)
        end_offset = axiom_id * 8
        end = int.from_bytes(changed_offsets[end_offset : end_offset + 8], "little")
        changed_offsets[end_offset : end_offset + 8] = (end - 1).to_bytes(8, "little")
        buffers["node_field_offsets"] = memoryview(bytes(changed_offsets))
    elif corruption == "property-kind":
        kinds = bytearray(buffers["field_kinds"])
        kinds[field_index] = 2
        buffers["field_kinds"] = memoryview(bytes(kinds))
    elif corruption == "property-length":
        lengths = bytearray(buffers["field_lengths"])
        lengths[field_index * 8 : (field_index + 1) * 8] = (1).to_bytes(8, "little")
        buffers["field_lengths"] = memoryview(bytes(lengths))
    elif corruption == "annotation-kind":
        kinds = bytearray(buffers["field_kinds"])
        kinds[annotation_index] = 7
        buffers["field_kinds"] = memoryview(bytes(kinds))
    elif corruption == "annotation-item":
        values = bytearray(buffers["item_values"])
        values[annotation_item * 8 : (annotation_item + 1) * 8] = wrong_id.to_bytes(8, "little")
        buffers["item_values"] = memoryview(bytes(values))
    else:
        values = bytearray(buffers["field_values"])
        replacement = {
            "property-range": columns.node_count + 1,
            "wrong-property": wrong_id,
            "property-chain": chain_id,
        }[corruption]
        values[field_index * 8 : (field_index + 1) * 8] = replacement.to_bytes(8, "little")
        buffers["field_values"] = memoryview(bytes(values))
    hostile = replace(lease, buffers=MappingProxyType(buffers))

    with pytest.raises(
        SnapshotCompatibilityError,
        match=(
            r"arity|node reference|out of range|canonical set|annotation set|"
            r"ObjectProperty property"
        ),
    ):
        prepare_encoded_subset_compilation(
            view,
            ProjectionOptions(backend="native"),
            EncodedNegotiation("encoded-native", lease=hostile),
            batch_edges=1,
        )


@pytest.mark.parametrize(
    "corruption",
    [
        "arity",
        "field-kind",
        "field-length",
        "out-of-range",
        "wrong-entity",
        "nested-inverse",
    ],
)
def test_inverse_property_corruption_fails_before_edge_output(corruption: str) -> None:
    view = _snapshot(
        "SubClassOf(:A ObjectSomeValuesFrom(ObjectInverseOf(:p) :B)) "
        "ObjectPropertyDomain(ObjectInverseOf(:q) :D)"
    )
    lease = _lease(view)
    buffers = dict(lease.buffers)
    tags = buffers["node_tags"]
    inverse_ids = tuple(
        node_id
        for node_id in range(1, tags.nbytes // 2 + 1)
        if int.from_bytes(tags[(node_id - 1) * 2 : node_id * 2], "little") == 10
    )
    assert len(inverse_ids) == 2
    node_id = inverse_ids[0]
    offsets = buffers["node_field_offsets"]
    field_index = int.from_bytes(offsets[(node_id - 1) * 8 : node_id * 8], "little")
    if corruption == "arity":
        changed_offsets = bytearray(offsets)
        end_offset = node_id * 8
        end = int.from_bytes(changed_offsets[end_offset : end_offset + 8], "little")
        changed_offsets[end_offset : end_offset + 8] = (end - 1).to_bytes(8, "little")
        buffers["node_field_offsets"] = memoryview(bytes(changed_offsets))
    elif corruption == "field-kind":
        kinds = bytearray(buffers["field_kinds"])
        kinds[field_index] = 2
        buffers["field_kinds"] = memoryview(bytes(kinds))
    elif corruption == "field-length":
        lengths = bytearray(buffers["field_lengths"])
        lengths[field_index * 8 : (field_index + 1) * 8] = (1).to_bytes(8, "little")
        buffers["field_lengths"] = memoryview(bytes(lengths))
    else:
        values = bytearray(buffers["field_values"])
        if corruption == "out-of-range":
            replacement = tags.nbytes // 2 + 1
        elif corruption == "nested-inverse":
            replacement = inverse_ids[1]
        else:
            columns = _EncodedColumns(lease)
            replacement = next(
                candidate
                for candidate in range(1, columns.node_count + 1)
                if columns._named_class_iri(candidate) == "urn:slice#A"
            )
        values[field_index * 8 : (field_index + 1) * 8] = replacement.to_bytes(8, "little")
        buffers["field_values"] = memoryview(bytes(values))
    hostile = replace(lease, buffers=MappingProxyType(buffers))

    with pytest.raises(
        SnapshotCompatibilityError,
        match=r"arity|node reference|out of range|ObjectInverseOf property",
    ) as raised:
        prepare_encoded_subset_compilation(
            view,
            ProjectionOptions(backend="native"),
            EncodedNegotiation("encoded-native", lease=hostile),
            batch_edges=1,
        )
    if corruption == "arity":
        assert raised.value.details["expected_arity"] == 1


@pytest.mark.parametrize("tag", [30, 31])
def test_aggregate_expression_arity_corruption_fails_before_edge_output(tag: int) -> None:
    view = _snapshot(
        "EquivalentClasses(:A ObjectIntersectionOf(:B :C)) "
        "EquivalentClasses(:D ObjectUnionOf(:E :F))"
    )
    lease = _lease(view)
    buffers = dict(lease.buffers)
    tags = buffers["node_tags"]
    node_id = next(
        index
        for index in range(1, tags.nbytes // 2 + 1)
        if int.from_bytes(tags[(index - 1) * 2 : index * 2], "little") == tag
    )
    offsets = bytearray(buffers["node_field_offsets"])
    end_offset = node_id * 8
    end = int.from_bytes(offsets[end_offset : end_offset + 8], "little")
    offsets[end_offset : end_offset + 8] = (end - 1).to_bytes(8, "little")
    buffers["node_field_offsets"] = memoryview(bytes(offsets))
    hostile = replace(lease, buffers=MappingProxyType(buffers))

    with pytest.raises(SnapshotCompatibilityError, match="arity") as raised:
        prepare_encoded_subset_compilation(
            view,
            ProjectionOptions(backend="native"),
            EncodedNegotiation("encoded-native", lease=hostile),
            batch_edges=1,
        )
    assert raised.value.details["expected_arity"] == 1


@pytest.mark.parametrize("corruption", ["integer-kind", "integer-minimal"])
def test_restriction_integer_corruption_fails_before_edge_output(corruption: str) -> None:
    view = _snapshot("SubClassOf(:A ObjectMinCardinality(256 :p :B))")
    lease = _lease(view)
    buffers = dict(lease.buffers)
    tags = buffers["node_tags"]
    node_id = next(
        index
        for index in range(1, tags.nbytes // 2 + 1)
        if int.from_bytes(tags[(index - 1) * 2 : index * 2], "little") == 38
    )
    offsets = buffers["node_field_offsets"]
    field_index = int.from_bytes(offsets[(node_id - 1) * 8 : node_id * 8], "little")
    if corruption == "integer-kind":
        kinds = bytearray(buffers["field_kinds"])
        kinds[field_index] = 2
        buffers["field_kinds"] = memoryview(bytes(kinds))
    else:
        values = buffers["field_values"]
        scalar_offset = int.from_bytes(values[field_index * 8 : (field_index + 1) * 8], "little")
        scalars = bytearray(buffers["scalar_bytes"])
        scalars[scalar_offset + 1] = 0
        buffers["scalar_bytes"] = memoryview(bytes(scalars))
    hostile = replace(lease, buffers=MappingProxyType(buffers))

    with pytest.raises(
        SnapshotCompatibilityError,
        match=r"scalar field kind|minimally encoded",
    ):
        prepare_encoded_subset_compilation(
            view,
            ProjectionOptions(backend="native"),
            EncodedNegotiation("encoded-native", lease=hostile),
            batch_edges=1,
        )


@pytest.mark.parametrize(
    ("tag", "arity"),
    [(70, 3), (73, 3), (74, 3), (75, 3), (113, 4)],
)
def test_named_property_axiom_corruption_fails_before_edge_output(
    tag: int,
    arity: int,
) -> None:
    view = _snapshot(
        "SubObjectPropertyOf(:q :p) InverseObjectProperties(:p :pinv) "
        "ObjectPropertyDomain(:p :D) ObjectPropertyRange(:p :R) "
        "ObjectPropertyAssertion(:p :i :j)"
    )
    lease = _lease(view)
    buffers = dict(lease.buffers)
    tags = buffers["node_tags"]
    node_id = next(
        index
        for index in range(1, tags.nbytes // 2 + 1)
        if int.from_bytes(tags[(index - 1) * 2 : index * 2], "little") == tag
    )
    offsets = bytearray(buffers["node_field_offsets"])
    end_offset = node_id * 8
    end = int.from_bytes(offsets[end_offset : end_offset + 8], "little")
    offsets[end_offset : end_offset + 8] = (end - 1).to_bytes(8, "little")
    buffers["node_field_offsets"] = memoryview(bytes(offsets))
    hostile = replace(lease, buffers=MappingProxyType(buffers))

    with pytest.raises(SnapshotCompatibilityError, match="arity") as raised:
        prepare_encoded_subset_compilation(
            view,
            ProjectionOptions(backend="native"),
            EncodedNegotiation("encoded-native", lease=hostile),
            batch_edges=1,
        )
    assert raised.value.details["expected_arity"] == arity


@pytest.mark.parametrize(
    "corruption",
    ["arity", "scope-kind", "scope-length", "local-empty"],
)
def test_anonymous_individual_corruption_fails_before_edge_output(corruption: str) -> None:
    view = _snapshot("ObjectPropertyAssertion(:p _:anon :i)")
    lease = _lease(view)
    buffers = dict(lease.buffers)
    tags = buffers["node_tags"]
    node_id = next(
        index
        for index in range(1, tags.nbytes // 2 + 1)
        if int.from_bytes(tags[(index - 1) * 2 : index * 2], "little") == 3
    )
    offsets = buffers["node_field_offsets"]
    field_index = int.from_bytes(offsets[(node_id - 1) * 8 : node_id * 8], "little")
    if corruption == "arity":
        changed_offsets = bytearray(offsets)
        end_offset = node_id * 8
        end = int.from_bytes(changed_offsets[end_offset : end_offset + 8], "little")
        changed_offsets[end_offset : end_offset + 8] = (end - 1).to_bytes(8, "little")
        buffers["node_field_offsets"] = memoryview(bytes(changed_offsets))
    elif corruption == "scope-kind":
        kinds = bytearray(buffers["field_kinds"])
        kinds[field_index] = 2
        buffers["field_kinds"] = memoryview(bytes(kinds))
    else:
        lengths = bytearray(buffers["field_lengths"])
        target_index = field_index if corruption == "scope-length" else field_index + 1
        replacement = 31 if corruption == "scope-length" else 0
        offset = target_index * 8
        lengths[offset : offset + 8] = replacement.to_bytes(8, "little")
        buffers["field_lengths"] = memoryview(bytes(lengths))
    hostile = replace(lease, buffers=MappingProxyType(buffers))

    with pytest.raises(
        SnapshotCompatibilityError,
        match=r"arity|scalar field kind|bytes32|local key",
    ):
        prepare_encoded_subset_compilation(
            view,
            ProjectionOptions(backend="native"),
            EncodedNegotiation("encoded-native", lease=hostile),
            batch_edges=1,
        )


@pytest.mark.parametrize(
    ("corruption", "match"),
    [
        ("root-set-kind", "node reference"),
        ("root-set-item", "annotation set"),
        ("annotation-arity", "arity"),
        ("annotation-property", "property"),
        ("annotation-value", "value"),
        ("nested-set-item", "annotation set"),
    ],
)
def test_annotated_logical_axiom_corruption_fails_before_output(
    corruption: str,
    match: str,
) -> None:
    view = _snapshot(
        'SubClassOf(Annotation(Annotation(<urn:nested> "inner") <urn:meta> "outer") :A :B)'
    )
    lease = _lease(view)
    buffers = dict(lease.buffers)
    tags = buffers["node_tags"]

    def tagged_nodes(tag: int) -> tuple[int, ...]:
        return tuple(
            index
            for index in range(1, tags.nbytes // 2 + 1)
            if int.from_bytes(tags[(index - 1) * 2 : index * 2], "little") == tag
        )

    offsets = buffers["node_field_offsets"]
    values = buffers["field_values"]
    lengths = buffers["field_lengths"]

    def field_start(node_id: int) -> int:
        return int.from_bytes(offsets[(node_id - 1) * 8 : node_id * 8], "little")

    axiom_id = tagged_nodes(61)[0]
    axiom_start = field_start(axiom_id)
    annotation_ids = tagged_nodes(5)
    outer_annotation_id = next(
        node_id
        for node_id in annotation_ids
        if int.from_bytes(
            lengths[(field_start(node_id) + 2) * 8 : (field_start(node_id) + 3) * 8],
            "little",
        )
        == 1
    )
    outer_start = field_start(outer_annotation_id)
    literal_id = tagged_nodes(4)[0]
    class_entity_id = int.from_bytes(
        values[axiom_start * 8 : (axiom_start + 1) * 8],
        "little",
    )
    if corruption == "root-set-kind":
        kinds = bytearray(buffers["field_kinds"])
        kinds[axiom_start + 2] = 1
        buffers["field_kinds"] = memoryview(bytes(kinds))
    elif corruption in {"root-set-item", "nested-set-item"}:
        set_field = axiom_start + 2 if corruption == "root-set-item" else outer_start + 2
        item_start = int.from_bytes(values[set_field * 8 : (set_field + 1) * 8], "little")
        item_values = bytearray(buffers["item_values"])
        item_values[item_start * 8 : (item_start + 1) * 8] = literal_id.to_bytes(8, "little")
        buffers["item_values"] = memoryview(bytes(item_values))
    elif corruption == "annotation-arity":
        changed_offsets = bytearray(offsets)
        end_offset = outer_annotation_id * 8
        end = int.from_bytes(changed_offsets[end_offset : end_offset + 8], "little")
        changed_offsets[end_offset : end_offset + 8] = (end - 1).to_bytes(8, "little")
        buffers["node_field_offsets"] = memoryview(bytes(changed_offsets))
    else:
        field_delta = 0 if corruption == "annotation-property" else 1
        changed_values = bytearray(values)
        field_offset = (outer_start + field_delta) * 8
        changed_values[field_offset : field_offset + 8] = class_entity_id.to_bytes(8, "little")
        buffers["field_values"] = memoryview(bytes(changed_values))
    hostile = replace(lease, buffers=MappingProxyType(buffers))

    with pytest.raises(SnapshotCompatibilityError, match=match):
        prepare_encoded_subset_compilation(
            view,
            ProjectionOptions(backend="native"),
            EncodedNegotiation("encoded-native", lease=hostile),
            batch_edges=1,
        )


@pytest.mark.parametrize(
    "corruption",
    ["arity", "property-node", "subject-node", "value-node", "annotation-item"],
)
def test_annotation_assertion_corruption_fails_before_edge_output(corruption: str) -> None:
    view = _snapshot(
        "Declaration(Class(:A)) "
        'AnnotationAssertion(Annotation(<urn:meta> "m") '
        '<http://www.w3.org/2000/01/rdf-schema#label> :A "text")'
    )
    lease = _lease(view)
    buffers = dict(lease.buffers)
    tags = buffers["node_tags"]

    def tagged_node(tag: int) -> int:
        return next(
            index
            for index in range(1, tags.nbytes // 2 + 1)
            if int.from_bytes(tags[(index - 1) * 2 : index * 2], "little") == tag
        )

    axiom_id = tagged_node(120)
    declaration_id = tagged_node(60)
    offsets = buffers["node_field_offsets"]
    axiom_start = int.from_bytes(offsets[(axiom_id - 1) * 8 : axiom_id * 8], "little")
    declaration_start = int.from_bytes(
        offsets[(declaration_id - 1) * 8 : declaration_id * 8], "little"
    )
    values = buffers["field_values"]
    class_entity_id = int.from_bytes(
        values[declaration_start * 8 : (declaration_start + 1) * 8],
        "little",
    )
    if corruption == "arity":
        changed_offsets = bytearray(offsets)
        end_offset = axiom_id * 8
        end = int.from_bytes(changed_offsets[end_offset : end_offset + 8], "little")
        changed_offsets[end_offset : end_offset + 8] = (end - 1).to_bytes(8, "little")
        buffers["node_field_offsets"] = memoryview(bytes(changed_offsets))
    elif corruption == "annotation-item":
        annotation_item_start = int.from_bytes(
            values[(axiom_start + 3) * 8 : (axiom_start + 4) * 8],
            "little",
        )
        item_values = bytearray(buffers["item_values"])
        item_offset = annotation_item_start * 8
        item_values[item_offset : item_offset + 8] = tagged_node(4).to_bytes(8, "little")
        buffers["item_values"] = memoryview(bytes(item_values))
    else:
        changed_values = bytearray(values)
        field_delta = {"property-node": 0, "subject-node": 1, "value-node": 2}[corruption]
        field_offset = (axiom_start + field_delta) * 8
        changed_values[field_offset : field_offset + 8] = class_entity_id.to_bytes(8, "little")
        buffers["field_values"] = memoryview(bytes(changed_values))
    hostile = replace(lease, buffers=MappingProxyType(buffers))

    with pytest.raises(
        SnapshotCompatibilityError,
        match=r"arity|property|subject|value|annotation set",
    ):
        prepare_encoded_subset_compilation(
            view,
            ProjectionOptions(backend="native", include_literals=True),
            EncodedNegotiation("encoded-native", lease=hostile),
            batch_edges=1,
        )


@pytest.mark.parametrize(
    "corruption",
    ["arity", "lexical-kind", "lexical-utf8", "datatype-node", "language-kind"],
)
def test_literal_corruption_fails_before_edge_output(corruption: str) -> None:
    view = _snapshot(
        "Declaration(Class(:A)) "
        'AnnotationAssertion(<http://www.w3.org/2000/01/rdf-schema#label> :A "text")'
    )
    lease = _lease(view)
    buffers = dict(lease.buffers)
    tags = buffers["node_tags"]

    def tagged_node(tag: int) -> int:
        return next(
            index
            for index in range(1, tags.nbytes // 2 + 1)
            if int.from_bytes(tags[(index - 1) * 2 : index * 2], "little") == tag
        )

    literal_id = tagged_node(4)
    declaration_id = tagged_node(60)
    offsets = buffers["node_field_offsets"]
    literal_start = int.from_bytes(offsets[(literal_id - 1) * 8 : literal_id * 8], "little")
    declaration_start = int.from_bytes(
        offsets[(declaration_id - 1) * 8 : declaration_id * 8], "little"
    )
    values = buffers["field_values"]
    class_entity_id = int.from_bytes(
        values[declaration_start * 8 : (declaration_start + 1) * 8],
        "little",
    )
    if corruption == "arity":
        changed_offsets = bytearray(offsets)
        end_offset = literal_id * 8
        end = int.from_bytes(changed_offsets[end_offset : end_offset + 8], "little")
        changed_offsets[end_offset : end_offset + 8] = (end - 1).to_bytes(8, "little")
        buffers["node_field_offsets"] = memoryview(bytes(changed_offsets))
    elif corruption in {"lexical-kind", "language-kind"}:
        kinds = bytearray(buffers["field_kinds"])
        field_delta = 0 if corruption == "lexical-kind" else 2
        kinds[literal_start + field_delta] = 3
        buffers["field_kinds"] = memoryview(bytes(kinds))
    elif corruption == "lexical-utf8":
        scalar_offset = int.from_bytes(
            values[literal_start * 8 : (literal_start + 1) * 8],
            "little",
        )
        scalars = bytearray(buffers["scalar_bytes"])
        scalars[scalar_offset] = 0xFF
        buffers["scalar_bytes"] = memoryview(bytes(scalars))
    else:
        changed_values = bytearray(values)
        datatype_offset = (literal_start + 1) * 8
        changed_values[datatype_offset : datatype_offset + 8] = class_entity_id.to_bytes(
            8,
            "little",
        )
        buffers["field_values"] = memoryview(bytes(changed_values))
    hostile = replace(lease, buffers=MappingProxyType(buffers))

    with pytest.raises(
        SnapshotCompatibilityError,
        match=r"arity|scalar field kind|UTF-8|datatype|language field kind",
    ):
        prepare_encoded_subset_compilation(
            view,
            ProjectionOptions(backend="native", include_literals=True),
            EncodedNegotiation("encoded-native", lease=hostile),
            batch_edges=1,
        )


def test_incomplete_slice_is_not_advertised_by_the_native_feature_ledger() -> None:
    native_source = (ROOT / "native" / "src" / "lib.rs").read_text("utf-8")
    assert ENCODED_NATIVE_FEATURE not in native_source
