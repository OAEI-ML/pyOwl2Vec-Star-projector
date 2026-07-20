from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
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
)
from pyowl2vec_star_projector.compiler import (
    prepare_streaming_compilation as scalar_compilation,
)
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
    return replace(
        top,
        owner=view,
        encoded_view=empty.encoded_view if not hasattr(view, "view") else top.encoded_view,
        buffers=empty.buffers if local_columns_empty else top.buffers,
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
    return replace(
        top,
        owner=view,
        buffers=delta.buffers,
        segments=(base_segment, delta_segment),
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
    view = _snapshot("SubObjectPropertyOf(ObjectPropertyChain(:p :q :r) :super)")
    lease = _lease(view)
    columns = _EncodedColumns(lease)
    assert 7 in bytes(lease.buffers["field_kinds"])
    assert columns.inspect().fallback_reason is not None
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


def test_encoded_role_state_is_reused_by_a_later_scala_instance_call() -> None:
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
        "EquivalentClasses(:A ObjectSomeValuesFrom(:p :B))",
        "ClassAssertion(ObjectSomeValuesFrom(:p :B) :i)",
        "ClassAssertion(:A _:anon)",
        'EquivalentClasses(Annotation(<urn:p> "x") :A :B)',
        'ClassAssertion(Annotation(<urn:p> "x") :A :i)',
        "ObjectPropertyDomain(:p ObjectIntersectionOf(:A :B))",
        'ObjectPropertyRange(Annotation(<urn:a> "x") :p :R)',
        'ObjectPropertyAssertion(Annotation(<urn:a> "x") :p :i :j)',
        "EquivalentObjectProperties(:p :q)",
        "SubObjectPropertyOf(ObjectPropertyChain(:p :q) :r)",
        'SubObjectPropertyOf(Annotation(<urn:a> "x") :p :q)',
        'InverseObjectProperties(Annotation(<urn:a> "x") :p :q)',
        "SubClassOf(ObjectSomeValuesFrom(:p :A) ObjectAllValuesFrom(:q :B))",
        "SubClassOf(:A ObjectSomeValuesFrom(ObjectInverseOf(:p) :B))",
        'SubClassOf(Annotation(<urn:a> "x") :A ObjectSomeValuesFrom(:p :B))',
        "EquivalentClasses(:A :B ObjectIntersectionOf(:C :D))",
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
        "InverseObjectProperties(:p :pinv) SubClassOf(:C ObjectSomeValuesFrom(:p :D)) "
        "EquivalentClasses(:E ObjectIntersectionOf(:F ObjectSomeValuesFrom(:p :D))) "
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
    assert counters.subclass_axioms == 2
    assert counters.restriction_subclass_axioms == 1
    assert counters.equivalent_axioms == 2
    assert counters.aggregate_equivalent_axioms == 1
    assert counters.class_assertion_axioms == 1
    assert counters.sub_object_property_axioms == 1
    assert counters.inverse_object_property_axioms == 1
    assert counters.object_property_assertion_axioms == 2
    assert counters.object_property_domain_axioms == 1
    assert counters.object_property_range_axioms == 1
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
