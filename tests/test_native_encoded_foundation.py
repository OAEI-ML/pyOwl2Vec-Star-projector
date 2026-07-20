from __future__ import annotations

import gc
import sys
import time
import weakref
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import MappingProxyType, SimpleNamespace
from typing import Any, cast

import pyowl_core
import pytest

from pyowl2vec_star_projector import (
    Edge,
    ProjectionOptions,
    ProjectionResourceError,
    Projector,
    probe_native_backend,
)
from pyowl2vec_star_projector.compiler import (
    RDF_TYPE,
    SUBCLASS_OF,
    SUPERCLASS_OF,
    iter_asserted_taxonomy,
)
from pyowl2vec_star_projector.encoded import (
    ENCODED_NATIVE_FEATURE,
    EncodedStructuralLease,
    _validate_encoded_view,
)
from pyowl2vec_star_projector.errors import (
    SnapshotCompatibilityError,
    UnsupportedAxiomShapeError,
)
from pyowl2vec_star_projector.native import (
    ENCODED_DIRECT_BUFFER_ORDER,
    NativeEncodedDirectCancelled,
    NativeEncodedDirectCompiler,
    NativeEncodedDirectUnsupported,
    load_native_module,
    prepare_native_encoded_direct,
)

NATIVE_AVAILABLE = probe_native_backend().available

pytestmark = pytest.mark.skipif(
    not NATIVE_AVAILABLE,
    reason="optional native extension is not installed",
)


def _snapshot(body: str) -> object:
    source = f"Prefix(:=<urn:native-direct#>) Ontology(<urn:native-direct> {body})".encode()
    return pyowl_core.load_snapshot(
        source,
        options=pyowl_core.LoadOptions(
            imports=pyowl_core.ImportPolicy.IGNORE,
            backend=pyowl_core.BackendPreference.PYTHON,
        ),
    )


def _annotation_snapshot() -> object:
    return _snapshot(
        "Declaration(Class(:A)) Declaration(Class(:B)) SubClassOf(:A :B) "
        "AnnotationAssertion(<http://www.w3.org/2000/01/rdf-schema#label> "
        ":A <urn:value>) "
        "AnnotationAssertion(<http://www.w3.org/2000/01/rdf-schema#comment> "
        ':A "bonjour"@fr) '
        "AnnotationAssertion(<http://www.w3.org/2004/02/skos/core#prefLabel> "
        ':A "text") '
        "AnnotationAssertion(<http://www.w3.org/2000/01/rdf-schema#label> :A "
        '"7"^^<http://www.w3.org/2001/XMLSchema#integer>) '
        "AnnotationAssertion(<http://www.w3.org/2000/01/rdf-schema#label> :A "
        '"a\\\\b"^^<urn:datatype>) '
        'AnnotationAssertion(<urn:unsupported> :A "ignored") '
        "AnnotationAssertion(<http://www.w3.org/2000/01/rdf-schema#label> "
        '<urn:not-class> "ignored") '
        "AnnotationAssertion(<http://www.w3.org/2000/01/rdf-schema#label> "
        ':A "duplicate") '
        'AnnotationAssertion(Annotation(<urn:meta> "one") '
        "<http://www.w3.org/2000/01/rdf-schema#label> :A "
        '"duplicate") '
        "AnnotationAssertion(Annotation(Annotation(<urn:nested> <urn:nested-value>) "
        '<urn:meta> "two") <http://www.w3.org/2000/01/rdf-schema#label> '
        ':A "duplicate") '
        "ClassAssertion(:A :i) ObjectPropertyAssertion(:p :i :j)"
    )


def _skipped_logical_snapshot() -> object:
    return _snapshot(
        'HasKey(Annotation(<urn:key-meta> "key") '
        "ObjectIntersectionOf(:Keyed ObjectSomeValuesFrom(:aux :F)) "
        "(:op ObjectInverseOf(:inv)) (:dp :dq)) "
        "SameIndividual(Annotation(<urn:same-meta> <urn:value>) :same _:z _:a) "
        'DifferentIndividuals(Annotation(<urn:different-meta> "different") '
        ":different _:z _:a) SubObjectPropertyOf(:child :p) "
        "SubClassOf(:TaxA :TaxB) "
        "SubClassOf(:Source ObjectSomeValuesFrom(:p :Target)) "
        "ObjectPropertyDomain(:p :D) ObjectPropertyRange(:p :R)"
    )


def _property_chain_snapshot() -> object:
    unrelated = " ".join(f"SubObjectPropertyOf(:x{index} :y{index})" for index in range(9))
    return _snapshot(
        "SubObjectPropertyOf(:c0 :r) SubObjectPropertyOf(:c71 :r) "
        f"{unrelated} "
        "SubObjectPropertyOf(ObjectPropertyChain(:z ObjectInverseOf(:a) :m) :r) "
        "SubObjectPropertyOf(ObjectPropertyChain(:m ObjectInverseOf(:a) :z) :r) "
        "ObjectPropertyDomain(:r :D) ObjectPropertyRange(:r :R)"
    )


def _lease(view: object) -> EncodedStructuralLease:
    encoded = cast(Any, view).view(
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


def _replace_buffers(
    lease: EncodedStructuralLease,
    replacements: dict[str, memoryview],
) -> EncodedStructuralLease:
    buffers = dict(lease.buffers)
    buffers.update(replacements)
    frozen = MappingProxyType(buffers)
    encoded = replace(cast(Any, lease.encoded_view), buffers=frozen)
    return replace(lease, encoded_view=encoded, buffers=frozen)


@pytest.fixture(autouse=True)
def _require_current_kernel() -> None:
    if NATIVE_AVAILABLE and not hasattr(load_native_module(), "EncodedDirectCompiler"):
        pytest.skip("installed native extension predates the private P7 foundation")


def test_direct_named_subclass_batch_matches_python_and_reports_real_work() -> None:
    view = _snapshot(
        "Declaration(Class(:A)) Declaration(Class(:B)) Declaration(Class(:C)) "
        "SubClassOf(:A :B) SubClassOf(:B :C)"
    )
    lease = _lease(view)
    expected = list(
        iter_asserted_taxonomy(
            view,
            bidirectional=True,
            duplicates="preserve",
            order="encounter",
        )
    )

    compiler = prepare_native_encoded_direct(lease)
    actual, statistics = compiler.compile_batch(
        bidirectional=True,
        max_edges=4,
        max_iri_bytes=1024 * 1024,
    )

    assert actual == expected
    assert statistics.roots == 5
    assert statistics.declarations == 3
    assert statistics.subclasses == 2
    assert statistics.restriction_subclasses == 0
    assert statistics.equivalents == 0
    assert statistics.aggregate_equivalents == 0
    assert statistics.disjoint_classes == 0
    assert statistics.disjoint_unions == 0
    assert statistics.has_keys == 0
    assert statistics.same_individuals == 0
    assert statistics.different_individuals == 0
    assert statistics.class_assertions == 0
    assert statistics.object_property_assertions == 0
    assert statistics.negative_object_property_assertions == 0
    assert statistics.sub_object_properties == 0
    assert statistics.object_property_chains == 0
    assert statistics.equivalent_object_properties == 0
    assert statistics.disjoint_object_properties == 0
    assert statistics.inverse_object_properties == 0
    assert statistics.functional_object_properties == 0
    assert statistics.inverse_functional_object_properties == 0
    assert statistics.reflexive_object_properties == 0
    assert statistics.irreflexive_object_properties == 0
    assert statistics.symmetric_object_properties == 0
    assert statistics.asymmetric_object_properties == 0
    assert statistics.transitive_object_properties == 0
    assert statistics.sub_data_properties == 0
    assert statistics.equivalent_data_properties == 0
    assert statistics.disjoint_data_properties == 0
    assert statistics.data_property_domains == 0
    assert statistics.data_property_ranges == 0
    assert statistics.functional_data_properties == 0
    assert statistics.datatype_definitions == 0
    assert statistics.data_property_assertions == 0
    assert statistics.negative_data_property_assertions == 0
    assert statistics.annotation_assertions == 0
    assert statistics.annotation_edges == 0
    assert statistics.non_string_literal_renderings == 0
    assert statistics.skipped_axioms == 0
    assert statistics.object_property_domains == 0
    assert statistics.object_property_ranges == 0
    assert statistics.domain_range_edges == 0
    assert statistics.role_expansion_edges == 0
    assert statistics.edges == 4
    assert statistics.nodes > statistics.roots
    assert statistics.buffer_bytes == sum(value.nbytes for value in lease.buffers.values())
    assert dict(statistics.ingestion_counters) == {
        "encoded_buffer_bytes": statistics.buffer_bytes,
        "encoded_buffer_count": 11,
        "encoded_compiler_gil_released": True,
        "encoded_detached_buffer_count": 11,
        "encoded_indexed_buffer_count": 0,
        "encoded_staging_copy_bytes": 0,
        "encoded_zero_copy_buffers": 11,
        "native_boundary_calls": 1,
        "per_row_ffi_calls": 0,
        "structural_copy_bytes": 0,
    }
    assert compiler.retained_buffer_count == len(ENCODED_DIRECT_BUFFER_ORDER) == 11
    assert compiler.state == "finished"


def test_many_axioms_cross_one_bounded_call_and_limit_failure_publishes_nothing() -> None:
    axioms = " ".join(f"SubClassOf(:C{index} :Top)" for index in range(250))
    lease = _lease(_snapshot(axioms))
    compiler = prepare_native_encoded_direct(lease)
    edges, statistics = compiler.compile_batch(
        bidirectional=False,
        max_edges=250,
        max_iri_bytes=1024 * 1024,
    )
    assert len(edges) == statistics.subclasses == statistics.edges == 250

    limited = prepare_native_encoded_direct(lease)
    with pytest.raises(ProjectionResourceError, match="configured edge resources"):
        limited.compile_batch(
            bidirectional=False,
            max_edges=249,
            max_iri_bytes=1024 * 1024,
        )
    assert limited.state == "failed"


@pytest.mark.parametrize("bidirectional", [False, True])
def test_named_equivalence_and_class_assertion_match_python_oracle(
    bidirectional: bool,
) -> None:
    view = _snapshot(
        "Declaration(Class(:Z)) Declaration(Class(:AA)) Declaration(Class(:B)) "
        "Declaration(Class(:Top)) Declaration(NamedIndividual(:i)) "
        "SubClassOf(:Z :Top) EquivalentClasses(:Z :AA :B) ClassAssertion(:Z :i)"
    )
    lease = _lease(view)
    expected = Projector().project(
        view,
        options=ProjectionOptions(
            backend="python",
            bidirectional_taxonomy=bidirectional,
            duplicates="preserve",
            order="encounter",
        ),
    )

    compiler = prepare_native_encoded_direct(lease)
    actual, statistics = compiler.compile_batch(
        bidirectional=bidirectional,
        max_edges=len(expected),
        max_iri_bytes=1024 * 1024,
    )

    assert actual == expected
    assert actual[0] == Edge("urn:native-direct#Z", SUBCLASS_OF, "urn:native-direct#Top")
    equivalent_index = 2 if bidirectional else 1
    assert actual[equivalent_index] == Edge(
        "urn:native-direct#AA",
        SUBCLASS_OF,
        "urn:native-direct#B",
    )
    if bidirectional:
        assert actual[equivalent_index + 1] == Edge(
            "urn:native-direct#B",
            SUPERCLASS_OF,
            "urn:native-direct#AA",
        )
    assert actual[-1] == Edge("urn:native-direct#i", RDF_TYPE, "urn:native-direct#Z")
    assert statistics.roots == 8
    assert statistics.declarations == 5
    assert statistics.subclasses == 1
    assert statistics.equivalents == 1
    assert statistics.class_assertions == 1
    assert statistics.edges == len(expected)
    assert statistics.ingestion_counters["native_boundary_calls"] == 1


@pytest.mark.parametrize(
    ("bidirectional", "only_taxonomy"),
    [(False, False), (True, False), (False, True)],
    ids=["forward", "bidirectional", "only-taxonomy"],
)
def test_named_aggregate_equivalents_match_operand_order_roles_and_duplicates(
    bidirectional: bool,
    only_taxonomy: bool,
) -> None:
    view = _snapshot(
        "SubObjectPropertyOf(:child :p) InverseObjectProperties(:p :pinv) "
        "EquivalentClasses(:A ObjectIntersectionOf(:C :B "
        "ObjectSomeValuesFrom(:p :D) ObjectAllValuesFrom(:p :E) "
        "ObjectMinCardinality(2 :p :F) ObjectMinCardinality(7 :p :F) "
        "ObjectMaxCardinality(3 :p :G))) "
        "EquivalentClasses(:Z ObjectUnionOf(:Y :X ObjectSomeValuesFrom(:p :W))) "
        "EquivalentClasses(:MixA :MixB ObjectIntersectionOf(:LaterA :LaterB)) "
        "EquivalentClasses(:Lead ObjectIntersectionOf(:IB :IA) ObjectUnionOf(:UB :UA))"
    )
    expected = Projector().project(
        view,
        options=ProjectionOptions(
            backend="python",
            bidirectional_taxonomy=bidirectional,
            only_taxonomy=only_taxonomy,
            duplicates="preserve",
            order="encounter",
        ),
    )
    compiler = prepare_native_encoded_direct(_lease(view))
    actual, statistics = compiler.compile_batch(
        bidirectional=bidirectional,
        max_edges=len(expected),
        max_iri_bytes=1024 * 1024,
        only_taxonomy=only_taxonomy,
    )

    assert actual == expected
    assert statistics.roots == 6
    assert statistics.equivalents == 4
    assert statistics.aggregate_equivalents == 3
    assert statistics.role_expansion_edges == (0 if only_taxonomy else 12)
    assert statistics.edges == len(expected)
    assert len(actual) == (7 if only_taxonomy else 32 if bidirectional else 25)
    assert actual.count(
        Edge("urn:native-direct#A", "urn:native-direct#p", "urn:native-direct#F")
    ) == (0 if only_taxonomy else 2)
    assert (
        Edge(
            "urn:native-direct#MixA",
            SUBCLASS_OF,
            "urn:native-direct#MixB",
        )
        in actual
    )
    assert not any("Later" in edge.source or "Later" in edge.destination for edge in actual)
    assert (
        Edge(
            "urn:native-direct#Lead",
            SUBCLASS_OF,
            "urn:native-direct#IA",
        )
        in actual
    )
    assert not any("#U" in edge.destination for edge in actual)


def test_asserted_taxonomy_preflights_and_suppresses_aggregate_equivalence() -> None:
    view = _snapshot(
        "SubClassOf(:TaxA :TaxB) EquivalentClasses(:A ObjectIntersectionOf("
        ":C :B ObjectSomeValuesFrom(:p :D)))"
    )
    expected = list(
        iter_asserted_taxonomy(
            view,
            bidirectional=True,
            duplicates="preserve",
            order="encounter",
        )
    )
    compiler = prepare_native_encoded_direct(_lease(view))
    actual, statistics = compiler.compile_batch(
        bidirectional=True,
        max_edges=len(expected),
        max_iri_bytes=1024 * 1024,
        asserted_taxonomy_only=True,
    )

    assert actual == expected
    assert statistics.equivalents == statistics.aggregate_equivalents == 1
    assert statistics.role_expansion_edges == 0


@pytest.mark.parametrize("only_taxonomy", [False, True])
def test_disjoint_class_families_are_aggregate_aware_state_neutral_skips(
    only_taxonomy: bool,
) -> None:
    view = _snapshot(
        "SubObjectPropertyOf(:child :p) "
        "SubClassOf(:Source ObjectSomeValuesFrom(:p :Target)) "
        "DisjointClasses(:A :B ObjectIntersectionOf("
        ":C ObjectSomeValuesFrom(:p :D))) "
        "DisjointUnion(:Defined :E ObjectUnionOf(:F :G))"
    )
    expected = Projector().project(
        view,
        options=ProjectionOptions(
            backend="python",
            only_taxonomy=only_taxonomy,
            duplicates="preserve",
            order="encounter",
        ),
    )
    compiler = prepare_native_encoded_direct(_lease(view))
    actual, statistics = compiler.compile_batch(
        bidirectional=False,
        max_edges=max(1, len(expected)),
        max_iri_bytes=1024 * 1024,
        only_taxonomy=only_taxonomy,
    )

    assert actual == expected
    assert statistics.roots == 4
    assert statistics.disjoint_classes == 1
    assert statistics.disjoint_unions == 1
    assert statistics.skipped_axioms == 2
    assert statistics.role_expansion_edges == (0 if only_taxonomy else 1)


def test_asserted_taxonomy_preflights_disjoint_class_families_without_skips() -> None:
    view = _snapshot(
        "SubClassOf(:TaxA :TaxB) DisjointClasses("
        ":A ObjectIntersectionOf(:B :C)) DisjointUnion("
        ":Defined :D ObjectUnionOf(:E :F))"
    )
    expected = list(
        iter_asserted_taxonomy(
            view,
            bidirectional=True,
            duplicates="preserve",
            order="encounter",
        )
    )
    compiler = prepare_native_encoded_direct(_lease(view))
    actual, statistics = compiler.compile_batch(
        bidirectional=True,
        max_edges=len(expected),
        max_iri_bytes=1024 * 1024,
        asserted_taxonomy_only=True,
    )

    assert actual == expected
    assert statistics.disjoint_classes == statistics.disjoint_unions == 1
    assert statistics.skipped_axioms == 0


def test_asserted_taxonomy_mode_preflights_and_suppresses_adjacent_axioms() -> None:
    view = _snapshot("SubClassOf(:A :B) EquivalentClasses(:A :C :D) ClassAssertion(:A :i)")
    lease = _lease(view)
    expected = list(
        iter_asserted_taxonomy(
            view,
            bidirectional=True,
            duplicates="preserve",
            order="encounter",
        )
    )
    compiler = prepare_native_encoded_direct(lease)
    actual, statistics = compiler.compile_batch(
        bidirectional=True,
        max_edges=len(expected),
        max_iri_bytes=1024 * 1024,
        asserted_taxonomy_only=True,
    )

    assert actual == expected
    assert statistics.subclasses == 1
    assert statistics.equivalents == 1
    assert statistics.class_assertions == 1
    assert statistics.edges == 2


@pytest.mark.parametrize(
    ("bidirectional", "only_taxonomy"),
    [(False, False), (True, False), (False, True)],
)
def test_named_restrictions_and_domain_range_products_match_python_oracle(
    bidirectional: bool,
    only_taxonomy: bool,
) -> None:
    view = _snapshot(
        "SubClassOf(:TaxA :TaxB) "
        "SubClassOf(:A ObjectSomeValuesFrom(:p :B)) "
        "SubClassOf(ObjectAllValuesFrom(:p :C) :D) "
        "SubClassOf(:E ObjectMinCardinality(256 :p :F)) "
        "SubClassOf(ObjectMaxCardinality(3 :p :G) :H) "
        "EquivalentClasses(:Y :Z) ClassAssertion(:Y :i) "
        "ObjectPropertyDomain(:p :D2) ObjectPropertyDomain(:p :D1) "
        "ObjectPropertyRange(:p :R2) ObjectPropertyRange(:p :R1) "
        "ObjectPropertyDomain(:q :QD) ObjectPropertyRange(:q :QR)"
    )
    lease = _lease(view)
    expected = Projector().project(
        view,
        options=ProjectionOptions(
            backend="python",
            bidirectional_taxonomy=bidirectional,
            only_taxonomy=only_taxonomy,
            duplicates="preserve",
            order="encounter",
        ),
    )

    compiler = prepare_native_encoded_direct(lease)
    actual, statistics = compiler.compile_batch(
        bidirectional=bidirectional,
        max_edges=len(expected),
        max_iri_bytes=1024 * 1024,
        only_taxonomy=only_taxonomy,
    )

    assert actual == expected
    assert statistics.roots == 13
    assert statistics.subclasses == 5
    assert statistics.restriction_subclasses == 4
    assert statistics.equivalents == 1
    assert statistics.class_assertions == 1
    assert statistics.object_property_domains == 3
    assert statistics.object_property_ranges == 3
    assert statistics.domain_range_edges == 5
    assert statistics.edges == len(expected)
    assert actual[-5:] == [
        Edge("urn:native-direct#D1", "urn:native-direct#p", "urn:native-direct#R1"),
        Edge("urn:native-direct#D1", "urn:native-direct#p", "urn:native-direct#R2"),
        Edge("urn:native-direct#D2", "urn:native-direct#p", "urn:native-direct#R1"),
        Edge("urn:native-direct#D2", "urn:native-direct#p", "urn:native-direct#R2"),
        Edge("urn:native-direct#QD", "urn:native-direct#q", "urn:native-direct#QR"),
    ]
    restriction_edges = [
        edge
        for edge in actual
        if edge.relation == "urn:native-direct#p"
        and edge.source in {"urn:native-direct#A", "urn:native-direct#D"}
    ]
    assert len(restriction_edges) == (0 if only_taxonomy else 2)
    assert statistics.ingestion_counters["native_boundary_calls"] == 1


def test_asserted_taxonomy_mode_suppresses_preflighted_role_family() -> None:
    view = _snapshot(
        "SubClassOf(:A :B) SubClassOf(:A ObjectSomeValuesFrom(:p :C)) "
        "SubObjectPropertyOf(ObjectInverseOf(:child) :p) "
        "InverseObjectProperties(:p ObjectInverseOf(:pinv)) "
        "EquivalentObjectProperties(:p ObjectInverseOf(:equivalent)) "
        "FunctionalObjectProperty(ObjectInverseOf(:p)) "
        "ObjectPropertyDomain(:p :D) ObjectPropertyRange(:p :R)"
    )
    lease = _lease(view)
    expected = list(
        iter_asserted_taxonomy(
            view,
            bidirectional=True,
            duplicates="preserve",
            order="encounter",
        )
    )
    compiler = prepare_native_encoded_direct(lease)
    actual, statistics = compiler.compile_batch(
        bidirectional=True,
        max_edges=len(expected),
        max_iri_bytes=1024 * 1024,
        asserted_taxonomy_only=True,
    )

    assert actual == expected
    assert statistics.subclasses == 2
    assert statistics.restriction_subclasses == 1
    assert statistics.object_property_domains == 1
    assert statistics.object_property_ranges == 1
    assert statistics.domain_range_edges == 0
    assert statistics.sub_object_properties == 1
    assert statistics.inverse_object_properties == 1
    assert statistics.equivalent_object_properties == 1
    assert statistics.functional_object_properties == 1
    assert statistics.skipped_axioms == 0
    assert statistics.role_expansion_edges == 0


@pytest.mark.parametrize("only_taxonomy", [False, True])
def test_named_role_hashset_order_expands_restrictions_and_domains_but_not_assertions(
    only_taxonomy: bool,
) -> None:
    view = _snapshot(
        "SubObjectPropertyOf(:p :r) SubObjectPropertyOf(:q :r) "
        "SubObjectPropertyOf(:p :q) InverseObjectProperties(:r :s) "
        "InverseObjectProperties(:r :t) SubClassOf(:A ObjectSomeValuesFrom(:r :B)) "
        "ObjectPropertyAssertion(:r :i :j) ObjectPropertyDomain(:r :D) "
        "ObjectPropertyRange(:r :R)"
    )
    lease = _lease(view)
    expected = Projector().project(
        view,
        options=ProjectionOptions(
            backend="python",
            only_taxonomy=only_taxonomy,
            duplicates="preserve",
            order="encounter",
        ),
    )
    actual, statistics = prepare_native_encoded_direct(lease).compile_batch(
        bidirectional=False,
        max_edges=len(expected),
        max_iri_bytes=1024 * 1024,
        only_taxonomy=only_taxonomy,
    )

    assert actual == expected
    assert statistics.sub_object_properties == 3
    assert statistics.inverse_object_properties == 2
    assert statistics.role_expansion_edges == (2 if only_taxonomy else 4)
    direct_assertion = Edge(
        "urn:native-direct#i",
        "urn:native-direct#r",
        "urn:native-direct#j",
    )
    assert actual.count(direct_assertion) == 1
    assert not any(
        edge.source in {"urn:native-direct#i", "urn:native-direct#j"}
        and edge.relation in {"urn:native-direct#p", "urn:native-direct#s"}
        for edge in actual
    )


def test_inverse_role_operands_and_skipped_object_property_families_match_python() -> None:
    view = _snapshot(
        "SubObjectPropertyOf(ObjectInverseOf(:child) ObjectInverseOf(:p)) "
        "InverseObjectProperties(ObjectInverseOf(:p) ObjectInverseOf(:pinv)) "
        "EquivalentObjectProperties(:u ObjectInverseOf(:v) :w) "
        "DisjointObjectProperties(:x ObjectInverseOf(:y)) FunctionalObjectProperty(:u) "
        "InverseFunctionalObjectProperty(ObjectInverseOf(:u)) ReflexiveObjectProperty(:u) "
        "IrreflexiveObjectProperty(ObjectInverseOf(:u)) SymmetricObjectProperty(:u) "
        "AsymmetricObjectProperty(ObjectInverseOf(:u)) TransitiveObjectProperty(:u) "
        "SubClassOf(:A ObjectSomeValuesFrom(:p :B)) "
        "ObjectPropertyDomain(:p :D) ObjectPropertyRange(:p :R)"
    )
    lease = _lease(view)
    expected = Projector().project(
        view,
        options=ProjectionOptions(
            backend="python",
            duplicates="preserve",
            order="encounter",
        ),
    )
    actual, statistics = prepare_native_encoded_direct(lease).compile_batch(
        bidirectional=False,
        max_edges=len(expected),
        max_iri_bytes=1024 * 1024,
    )

    assert actual == expected
    assert statistics.sub_object_properties == 1
    assert statistics.inverse_object_properties == 1
    assert statistics.equivalent_object_properties == 1
    assert statistics.disjoint_object_properties == 1
    assert statistics.functional_object_properties == 1
    assert statistics.inverse_functional_object_properties == 1
    assert statistics.reflexive_object_properties == 1
    assert statistics.irreflexive_object_properties == 1
    assert statistics.symmetric_object_properties == 1
    assert statistics.asymmetric_object_properties == 1
    assert statistics.transitive_object_properties == 1
    assert statistics.skipped_axioms == 9
    assert statistics.domain_range_edges == 1
    assert statistics.role_expansion_edges == 4


@pytest.mark.parametrize("only_taxonomy", [False, True])
def test_named_object_assertions_and_negative_inverse_skips_match_python_oracle(
    only_taxonomy: bool,
) -> None:
    view = _snapshot(
        "SubClassOf(:A :B) SubClassOf(:A ObjectSomeValuesFrom(:r :C)) "
        "ClassAssertion(:A :i) ObjectPropertyAssertion(:p :i :j) "
        "ObjectPropertyAssertion(:q :j :i) NegativeObjectPropertyAssertion(:p :j :i) "
        "NegativeObjectPropertyAssertion(ObjectInverseOf(:q) :i :j) "
        "ObjectPropertyDomain(:r :D) ObjectPropertyRange(:r :R)"
    )
    lease = _lease(view)
    expected = Projector().project(
        view,
        options=ProjectionOptions(
            backend="python",
            only_taxonomy=only_taxonomy,
            duplicates="preserve",
            order="encounter",
        ),
    )
    compiler = prepare_native_encoded_direct(lease)
    actual, statistics = compiler.compile_batch(
        bidirectional=False,
        max_edges=len(expected),
        max_iri_bytes=1024 * 1024,
        only_taxonomy=only_taxonomy,
    )

    assert actual == expected
    assert statistics.roots == 9
    assert statistics.object_property_assertions == 2
    assert statistics.negative_object_property_assertions == 2
    assert statistics.skipped_axioms == 2
    assert statistics.edges == len(expected)
    assertion_edges = [
        Edge("urn:native-direct#i", "urn:native-direct#p", "urn:native-direct#j"),
        Edge("urn:native-direct#j", "urn:native-direct#q", "urn:native-direct#i"),
    ]
    assertion_start = 3 if not only_taxonomy else 2
    assert actual[assertion_start : assertion_start + 2] == assertion_edges
    assert actual[-1] == Edge(
        "urn:native-direct#D",
        "urn:native-direct#r",
        "urn:native-direct#R",
    )


def test_asserted_taxonomy_suppresses_preflighted_object_assertions_and_skips() -> None:
    view = _snapshot(
        "SubClassOf(:A :B) ObjectPropertyAssertion(:p :i :j) "
        "NegativeObjectPropertyAssertion(ObjectInverseOf(:p) :j :i)"
    )
    lease = _lease(view)
    expected = list(
        iter_asserted_taxonomy(
            view,
            bidirectional=True,
            duplicates="preserve",
            order="encounter",
        )
    )
    compiler = prepare_native_encoded_direct(lease)
    actual, statistics = compiler.compile_batch(
        bidirectional=True,
        max_edges=len(expected),
        max_iri_bytes=1024 * 1024,
        asserted_taxonomy_only=True,
    )

    assert actual == expected
    assert statistics.object_property_assertions == 1
    assert statistics.negative_object_property_assertions == 1
    assert statistics.skipped_axioms == 0


def test_positive_inverse_object_assertion_preserves_reference_failure() -> None:
    compiler = prepare_native_encoded_direct(
        _lease(_snapshot("ObjectPropertyAssertion(ObjectInverseOf(:p) :i :j)"))
    )
    with pytest.raises(UnsupportedAxiomShapeError, match="inverse object-property") as raised:
        compiler.compile_batch(
            bidirectional=False,
            max_edges=1,
            max_iri_bytes=1024 * 1024,
        )
    assert raised.value.details == {
        "constructor": "ObjectInverseOf",
        "reference_error": "java.lang.ClassCastException",
    }
    assert compiler.state == "failed"


@pytest.mark.parametrize("only_taxonomy", [False, True])
def test_named_data_property_families_and_literal_forms_match_python_oracle(
    only_taxonomy: bool,
) -> None:
    view = _snapshot(
        "SubClassOf(:A :B) SubDataPropertyOf(:dp :dq) "
        "EquivalentDataProperties(:dp :dq :dr) DisjointDataProperties(:dp :dq) "
        "DataPropertyDomain(:dp :A) "
        "DataPropertyRange(:dp <http://www.w3.org/2001/XMLSchema#string>) "
        "FunctionalDataProperty(:dp) "
        "DatatypeDefinition(:custom <http://www.w3.org/2001/XMLSchema#string>) "
        'DataPropertyAssertion(:dp :i "plain") '
        'DataPropertyAssertion(:dq :i "7"^^'
        "<http://www.w3.org/2001/XMLSchema#integer>) "
        'DataPropertyAssertion(:dr :i "bonjour"@fr) '
        'NegativeDataPropertyAssertion(:dp :i "blocked")'
    )
    expected = Projector().project(
        view,
        options=ProjectionOptions(
            backend="python",
            only_taxonomy=only_taxonomy,
            duplicates="preserve",
            order="encounter",
        ),
    )
    compiler = prepare_native_encoded_direct(_lease(view))
    actual, statistics = compiler.compile_batch(
        bidirectional=False,
        max_edges=len(expected),
        max_iri_bytes=1024 * 1024,
        only_taxonomy=only_taxonomy,
    )

    assert actual == expected == [Edge("urn:native-direct#A", SUBCLASS_OF, "urn:native-direct#B")]
    assert statistics.roots == 12
    assert statistics.sub_data_properties == 1
    assert statistics.equivalent_data_properties == 1
    assert statistics.disjoint_data_properties == 1
    assert statistics.data_property_domains == 1
    assert statistics.data_property_ranges == 1
    assert statistics.functional_data_properties == 1
    assert statistics.datatype_definitions == 1
    assert statistics.data_property_assertions == 3
    assert statistics.negative_data_property_assertions == 1
    assert statistics.skipped_axioms == 11
    assert statistics.edges == 1
    assert compiler.state == "finished"


def test_asserted_taxonomy_preflights_data_state_but_suppresses_its_skip_count() -> None:
    view = _snapshot(
        "SubClassOf(:A :B) DataPropertyDomain(:dp :A) "
        "DataPropertyRange(:dp <http://www.w3.org/2001/XMLSchema#string>) "
        'DataPropertyAssertion(:dp :i "value") '
        'NegativeDataPropertyAssertion(:dp :i "blocked")'
    )
    expected = list(
        iter_asserted_taxonomy(
            view,
            bidirectional=True,
            duplicates="preserve",
            order="encounter",
        )
    )
    compiler = prepare_native_encoded_direct(_lease(view))
    actual, statistics = compiler.compile_batch(
        bidirectional=True,
        max_edges=len(expected),
        max_iri_bytes=1024 * 1024,
        asserted_taxonomy_only=True,
    )

    assert actual == expected
    assert statistics.data_property_domains == 1
    assert statistics.data_property_ranges == 1
    assert statistics.data_property_assertions == 1
    assert statistics.negative_data_property_assertions == 1
    assert statistics.skipped_axioms == 0


def test_many_data_assertions_cross_one_zero_output_bounded_call() -> None:
    assertions = " ".join(
        f'DataPropertyAssertion(:score :i{index:03d} "{index}")' for index in range(250)
    )
    compiler = prepare_native_encoded_direct(_lease(_snapshot(assertions)))
    edges, statistics = compiler.compile_batch(
        bidirectional=False,
        max_edges=1,
        max_iri_bytes=1024 * 1024,
    )

    assert edges == []
    assert statistics.roots == statistics.data_property_assertions == 250
    assert statistics.skipped_axioms == 250
    assert statistics.edges == 0
    assert statistics.ingestion_counters["native_boundary_calls"] == 1


@pytest.mark.parametrize(
    "body",
    [
        'SubDataPropertyOf(Annotation(<urn:meta> "unsupported") :dp :dq)',
        'EquivalentDataProperties(Annotation(<urn:meta> "unsupported") :dp :dq)',
        'DisjointDataProperties(Annotation(<urn:meta> "unsupported") :dp :dq)',
        'DataPropertyDomain(Annotation(<urn:meta> "unsupported") :dp :A)',
        'DataPropertyRange(Annotation(<urn:meta> "unsupported") :dp '
        "<http://www.w3.org/2001/XMLSchema#string>)",
        'FunctionalDataProperty(Annotation(<urn:meta> "unsupported") :dp)',
        'DatatypeDefinition(Annotation(<urn:meta> "unsupported") :custom '
        "<http://www.w3.org/2001/XMLSchema#string>)",
        'DataPropertyAssertion(Annotation(<urn:meta> "unsupported") :dp :i "value")',
        'NegativeDataPropertyAssertion(Annotation(<urn:meta> "unsupported") :dp :i "value")',
    ],
    ids=[
        "subproperty",
        "equivalent",
        "disjoint",
        "domain",
        "range",
        "functional",
        "datatype-definition",
        "positive-assertion",
        "negative-assertion",
    ],
)
def test_annotated_data_property_families_fail_before_output(body: str) -> None:
    compiler = prepare_native_encoded_direct(_lease(_snapshot(body)))
    with pytest.raises(NativeEncodedDirectUnsupported, match=r"annotations|schema tag 5"):
        compiler.compile_batch(
            bidirectional=False,
            max_edges=1,
            max_iri_bytes=1024 * 1024,
        )
    assert compiler.state == "failed"


@pytest.mark.parametrize(
    "body",
    [
        "DataPropertyDomain(:dp ObjectIntersectionOf(:A :B))",
        "DataPropertyRange(:dp DataUnionOf("
        "<http://www.w3.org/2001/XMLSchema#string> "
        "<http://www.w3.org/2001/XMLSchema#integer>))",
        "DatatypeDefinition(:custom DataComplementOf(<http://www.w3.org/2001/XMLSchema#string>))",
        'DataPropertyAssertion(:dp _:anonymous "value")',
        'NegativeDataPropertyAssertion(:dp _:anonymous "value")',
    ],
    ids=[
        "complex-domain",
        "complex-range",
        "complex-definition",
        "anonymous-positive",
        "anonymous-negative",
    ],
)
def test_out_of_slice_data_shapes_are_transactionally_unsupported(body: str) -> None:
    compiler = prepare_native_encoded_direct(_lease(_snapshot(body)))
    with pytest.raises(NativeEncodedDirectUnsupported):
        compiler.compile_batch(
            bidirectional=False,
            max_edges=1,
            max_iri_bytes=1024 * 1024,
        )
    assert compiler.state == "failed"


def test_hostile_data_set_and_literal_language_fail_closed() -> None:
    set_lease = _lease(_snapshot("EquivalentDataProperties(:dp :dq :dr)"))
    item_values = bytearray(set_lease.buffers["item_values"])
    first = bytes(item_values[:8])
    item_values[:8] = item_values[8:16]
    item_values[8:16] = first
    hostile_set = _replace_buffers(
        set_lease,
        {"item_values": memoryview(bytes(item_values))},
    )
    malformed_set = prepare_native_encoded_direct(hostile_set)
    with pytest.raises(SnapshotCompatibilityError, match="sorted and unique"):
        malformed_set.compile_batch(
            bidirectional=False,
            max_edges=1,
            max_iri_bytes=1024 * 1024,
        )
    assert malformed_set.state == "failed"

    literal_lease = _lease(_snapshot('DataPropertyAssertion(:dp :i "bonjour"@fr)'))
    tags = literal_lease.buffers["node_tags"]
    literal_id = next(
        node_id
        for node_id in range(1, tags.nbytes // 2 + 1)
        if int.from_bytes(tags[(node_id - 1) * 2 : node_id * 2], "little") == 4
    )
    offsets = literal_lease.buffers["node_field_offsets"]
    field_start = int.from_bytes(
        offsets[(literal_id - 1) * 8 : literal_id * 8],
        "little",
    )
    field_values = literal_lease.buffers["field_values"]
    language_offset = int.from_bytes(
        field_values[(field_start + 2) * 8 : (field_start + 3) * 8],
        "little",
    )
    scalar = bytearray(literal_lease.buffers["scalar_bytes"])
    assert scalar[language_offset : language_offset + 2] == b"fr"
    scalar[language_offset : language_offset + 2] = b"FR"
    hostile_literal = _replace_buffers(
        literal_lease,
        {"scalar_bytes": memoryview(bytes(scalar))},
    )
    malformed_literal = prepare_native_encoded_direct(hostile_literal)
    with pytest.raises(SnapshotCompatibilityError, match="language is not canonical"):
        malformed_literal.compile_batch(
            bidirectional=False,
            max_edges=1,
            max_iri_bytes=1024 * 1024,
        )
    assert malformed_literal.state == "failed"


def test_data_literal_datatype_iri_limit_fails_before_taxonomy_publication() -> None:
    compiler = prepare_native_encoded_direct(
        _lease(
            _snapshot(
                "SubClassOf(:A :B) DataPropertyAssertion(:dp :i "
                '"value"^^<urn:datatype-with-a-deliberately-long-name>)'
            )
        )
    )
    with pytest.raises(ProjectionResourceError, match="configured edge resources"):
        compiler.compile_batch(
            bidirectional=False,
            max_edges=10,
            max_iri_bytes=25,
        )
    assert compiler.state == "failed"


def test_annotation_assertions_match_scalar_rendering_order_and_annotated_duplicates() -> None:
    view = _annotation_snapshot()
    expected = Projector().project(
        view,
        options=ProjectionOptions(
            backend="python",
            include_literals=True,
            duplicates="preserve",
            order="encounter",
        ),
    )
    compiler = prepare_native_encoded_direct(_lease(view))
    actual, statistics = compiler.compile_batch(
        bidirectional=False,
        max_edges=len(expected),
        max_iri_bytes=1024 * 1024,
        include_literals=True,
    )

    assert actual == expected
    assert statistics.roots == 15
    assert statistics.annotation_assertions == 10
    assert statistics.annotation_edges == 8
    assert statistics.non_string_literal_renderings == 2
    assert statistics.skipped_axioms == 0
    assert actual[1] == Edge("urn:native-direct#A", "rdfs:label", "urn:value")
    assert actual[2:4] == [
        Edge("urn:native-direct#A", "rdfs:label", 'ab"^^<urn:datatype'),
        Edge("urn:native-direct#A", "rdfs:label", '7"^^xsd:intege'),
    ]
    duplicate = Edge("urn:native-direct#A", "rdfs:label", "duplicate")
    assert actual.count(duplicate) == 3
    assert actual[-2:] == [
        Edge("urn:native-direct#i", RDF_TYPE, "urn:native-direct#A"),
        Edge("urn:native-direct#i", "urn:native-direct#p", "urn:native-direct#j"),
    ]


def test_annotation_options_pin_historical_only_taxonomy_and_asserted_suppression() -> None:
    view = _annotation_snapshot()
    cases = (
        ProjectionOptions(
            backend="python",
            include_literals=False,
            duplicates="preserve",
            order="encounter",
        ),
        ProjectionOptions(
            backend="python",
            include_literals=True,
            only_taxonomy=True,
            duplicates="preserve",
            order="encounter",
        ),
    )
    for options in cases:
        expected = Projector().project(view, options=options)
        actual, statistics = prepare_native_encoded_direct(_lease(view)).compile_batch(
            bidirectional=False,
            max_edges=len(expected),
            max_iri_bytes=1024 * 1024,
            only_taxonomy=options.only_taxonomy,
            include_literals=options.include_literals,
        )
        assert actual == expected
        assert statistics.annotation_assertions == 10
        assert statistics.annotation_edges == (8 if options.include_literals else 0)
        assert statistics.non_string_literal_renderings == (2 if options.include_literals else 0)

    historical = Projector().project(view, options=cases[1])
    assert Edge("urn:native-direct#A", "rdfs:label", "urn:value") in historical

    expected_taxonomy = list(
        iter_asserted_taxonomy(
            view,
            bidirectional=True,
            duplicates="preserve",
            order="encounter",
        )
    )
    actual, statistics = prepare_native_encoded_direct(_lease(view)).compile_batch(
        bidirectional=True,
        max_edges=len(expected_taxonomy),
        max_iri_bytes=1024 * 1024,
        asserted_taxonomy_only=True,
        only_taxonomy=True,
        include_literals=True,
    )
    assert actual == expected_taxonomy
    assert all(edge.relation in {SUBCLASS_OF, SUPERCLASS_OF} for edge in actual)
    assert statistics.annotation_assertions == 10
    assert statistics.annotation_edges == 0
    assert statistics.non_string_literal_renderings == 0

    invalid = prepare_native_encoded_direct(_lease(view))
    with pytest.raises(TypeError, match="include_literals must be bool"):
        invalid.compile_batch(
            bidirectional=False,
            max_edges=1,
            max_iri_bytes=1024 * 1024,
            include_literals=1,  # type: ignore[arg-type]
        )
    assert invalid.state == "idle"


def test_annotation_edge_limit_and_nonrenderable_values_fail_before_publication() -> None:
    assertions = " ".join(
        f'AnnotationAssertion(<http://www.w3.org/2000/01/rdf-schema#label> :A "value-{index:03d}")'
        for index in range(250)
    )
    view = _snapshot(f"Declaration(Class(:A)) {assertions}")
    limited = prepare_native_encoded_direct(_lease(view))
    with pytest.raises(ProjectionResourceError, match="configured edge resources"):
        limited.compile_batch(
            bidirectional=False,
            max_edges=249,
            max_iri_bytes=1024 * 1024,
            include_literals=True,
        )
    assert limited.state == "failed"

    for body in (
        "Declaration(Class(:A)) AnnotationAssertion("
        "<http://www.w3.org/2000/01/rdf-schema#label> :A _:anonymous)",
        "Declaration(Class(:A)) AnnotationAssertion(Annotation("
        "<urn:meta> _:anonymous) "
        '<http://www.w3.org/2000/01/rdf-schema#label> :A "value")',
    ):
        unsupported = prepare_native_encoded_direct(_lease(_snapshot(body)))
        with pytest.raises(
            NativeEncodedDirectUnsupported,
            match=r"schema tag 3|IRI or literal",
        ):
            unsupported.compile_batch(
                bidirectional=False,
                max_edges=1,
                max_iri_bytes=1024 * 1024,
                include_literals=True,
            )
        assert unsupported.state == "failed"


@pytest.mark.parametrize(
    ("corruption", "match"),
    [
        ("property", "annotation-property"),
        ("subject", "IRI"),
        ("value", "IRI or literal"),
        ("annotation-item", "annotation set item"),
    ],
)
def test_hostile_annotation_assertion_rows_fail_before_output(
    corruption: str,
    match: str,
) -> None:
    lease = _lease(
        _snapshot(
            "SubClassOf(:Before :After) Declaration(Class(:A)) "
            'AnnotationAssertion(Annotation(<urn:meta> "m") '
            "<http://www.w3.org/2000/01/rdf-schema#label> :A "
            '"value")'
        )
    )
    tags = lease.buffers["node_tags"]

    def tagged_node(tag: int) -> int:
        return next(
            node_id
            for node_id in range(1, tags.nbytes // 2 + 1)
            if int.from_bytes(tags[(node_id - 1) * 2 : node_id * 2], "little") == tag
        )

    assertion_id = tagged_node(120)
    offsets = lease.buffers["node_field_offsets"]
    field_start = int.from_bytes(
        offsets[(assertion_id - 1) * 8 : assertion_id * 8],
        "little",
    )
    values = bytearray(lease.buffers["field_values"])
    if corruption == "annotation-item":
        item_start = int.from_bytes(
            values[(field_start + 3) * 8 : (field_start + 4) * 8],
            "little",
        )
        item_values = bytearray(lease.buffers["item_values"])
        item_values[item_start * 8 : (item_start + 1) * 8] = tagged_node(4).to_bytes(
            8,
            "little",
        )
        hostile = _replace_buffers(
            lease,
            {"item_values": memoryview(bytes(item_values))},
        )
    else:
        field_delta = {"property": 0, "subject": 1, "value": 2}[corruption]
        replacement = tagged_node(2)
        values[(field_start + field_delta) * 8 : (field_start + field_delta + 1) * 8] = (
            replacement.to_bytes(8, "little")
        )
        hostile = _replace_buffers(
            lease,
            {"field_values": memoryview(bytes(values))},
        )
    compiler = prepare_native_encoded_direct(hostile)
    with pytest.raises(
        (SnapshotCompatibilityError, NativeEncodedDirectUnsupported),
        match=match,
    ):
        compiler.compile_batch(
            bidirectional=False,
            max_edges=10,
            max_iri_bytes=1024 * 1024,
            include_literals=True,
        )
    assert compiler.state == "failed"


@pytest.mark.parametrize("only_taxonomy", [False, True])
def test_key_and_individual_set_roots_match_scalar_state_neutral_skips(
    only_taxonomy: bool,
) -> None:
    view = _skipped_logical_snapshot()
    expected = Projector().project(
        view,
        options=ProjectionOptions(
            backend="python",
            only_taxonomy=only_taxonomy,
            duplicates="preserve",
            order="encounter",
        ),
    )
    actual, statistics = prepare_native_encoded_direct(_lease(view)).compile_batch(
        bidirectional=False,
        max_edges=len(expected),
        max_iri_bytes=1024 * 1024,
        only_taxonomy=only_taxonomy,
    )

    assert actual == expected
    assert statistics.roots == 8
    assert statistics.has_keys == 1
    assert statistics.same_individuals == 1
    assert statistics.different_individuals == 1
    assert statistics.skipped_axioms == 3
    assert statistics.role_expansion_edges == (1 if only_taxonomy else 2)
    assert len(actual) == (3 if only_taxonomy else 5)


def test_asserted_taxonomy_preflights_key_and_individual_sets_without_leakage() -> None:
    view = _skipped_logical_snapshot()
    expected = list(
        iter_asserted_taxonomy(
            view,
            bidirectional=True,
            duplicates="preserve",
            order="encounter",
        )
    )
    actual, statistics = prepare_native_encoded_direct(_lease(view)).compile_batch(
        bidirectional=True,
        max_edges=len(expected),
        max_iri_bytes=1024 * 1024,
        asserted_taxonomy_only=True,
    )

    assert actual == expected
    assert all(edge.relation in {SUBCLASS_OF, SUPERCLASS_OF} for edge in actual)
    assert statistics.has_keys == 1
    assert statistics.same_individuals == 1
    assert statistics.different_individuals == 1
    assert statistics.skipped_axioms == 0
    assert statistics.role_expansion_edges == 0


def test_many_individual_set_roots_cross_one_zero_output_bounded_call() -> None:
    axioms = " ".join(
        (
            f"SameIndividual(:left{index:03d} _:anonymous{index:03d})"
            if index % 2 == 0
            else f"DifferentIndividuals(:left{index:03d} _:anonymous{index:03d})"
        )
        for index in range(250)
    )
    compiler = prepare_native_encoded_direct(_lease(_snapshot(axioms)))
    edges, statistics = compiler.compile_batch(
        bidirectional=False,
        max_edges=1,
        max_iri_bytes=1024 * 1024,
    )

    assert edges == []
    assert statistics.roots == 250
    assert statistics.same_individuals == 125
    assert statistics.different_individuals == 125
    assert statistics.skipped_axioms == 250
    assert statistics.ingestion_counters["native_boundary_calls"] == 1


@pytest.mark.parametrize(
    ("body", "target_tag", "field_delta", "replacement_kind", "match"),
    [
        (
            "HasKey(:Keyed (:p) ())",
            101,
            1,
            "declared-class",
            "object-property",
        ),
        (
            "SameIndividual(:i :j)",
            110,
            0,
            "declared-class",
            "individual",
        ),
        (
            'DifferentIndividuals(Annotation(<urn:meta> "m") :i :j)',
            111,
            1,
            "literal",
            "annotation set item",
        ),
    ],
    ids=["has-key-property", "same-individual-member", "different-annotation"],
)
def test_hostile_key_and_individual_set_rows_fail_before_output(
    body: str,
    target_tag: int,
    field_delta: int,
    replacement_kind: str,
    match: str,
) -> None:
    lease = _lease(_snapshot(f"SubClassOf(:Before :After) {body} Declaration(Class(:Wrong))"))
    tags = lease.buffers["node_tags"]

    def tagged_nodes(tag: int) -> list[int]:
        return [
            node_id
            for node_id in range(1, tags.nbytes // 2 + 1)
            if int.from_bytes(tags[(node_id - 1) * 2 : node_id * 2], "little") == tag
        ]

    offsets = lease.buffers["node_field_offsets"]

    def field_start(node_id: int) -> int:
        return int.from_bytes(
            offsets[(node_id - 1) * 8 : node_id * 8],
            "little",
        )

    field_values = lease.buffers["field_values"]
    target_field = field_start(tagged_nodes(target_tag)[0]) + field_delta
    collection_start = int.from_bytes(
        field_values[target_field * 8 : (target_field + 1) * 8],
        "little",
    )
    field_lengths = lease.buffers["field_lengths"]
    collection_length = int.from_bytes(
        field_lengths[target_field * 8 : (target_field + 1) * 8],
        "little",
    )
    item_values = bytearray(lease.buffers["item_values"])
    if replacement_kind == "declared-class":
        declaration_field = field_start(tagged_nodes(60)[0])
        replacement = int.from_bytes(
            field_values[declaration_field * 8 : (declaration_field + 1) * 8],
            "little",
        )
    else:
        assert replacement_kind == "literal"
        replacement = tagged_nodes(4)[0]
    collection = [
        int.from_bytes(item_values[index * 8 : (index + 1) * 8], "little")
        for index in range(collection_start, collection_start + collection_length)
    ]
    collection[0] = replacement
    collection.sort()
    for index, node_id in enumerate(collection, start=collection_start):
        item_values[index * 8 : (index + 1) * 8] = node_id.to_bytes(8, "little")
    hostile = _replace_buffers(
        lease,
        {"item_values": memoryview(bytes(item_values))},
    )
    compiler = prepare_native_encoded_direct(hostile)
    with pytest.raises(SnapshotCompatibilityError, match=match):
        compiler.compile_batch(
            bidirectional=False,
            max_edges=10,
            max_iri_bytes=1024 * 1024,
        )
    assert compiler.state == "failed"


def test_hostile_anonymous_individual_shape_fails_before_output() -> None:
    lease = _lease(_snapshot("SubClassOf(:Before :After) SameIndividual(:i _:anonymous)"))
    tags = lease.buffers["node_tags"]
    anonymous_id = next(
        node_id
        for node_id in range(1, tags.nbytes // 2 + 1)
        if int.from_bytes(tags[(node_id - 1) * 2 : node_id * 2], "little") == 3
    )
    offsets = lease.buffers["node_field_offsets"]
    field_start = int.from_bytes(
        offsets[(anonymous_id - 1) * 8 : anonymous_id * 8],
        "little",
    )
    kinds = bytearray(lease.buffers["field_kinds"])
    kinds[field_start] = 2
    hostile = _replace_buffers(
        lease,
        {"field_kinds": memoryview(bytes(kinds))},
    )
    compiler = prepare_native_encoded_direct(hostile)
    with pytest.raises(SnapshotCompatibilityError, match="scalar field"):
        compiler.compile_batch(
            bidirectional=False,
            max_edges=10,
            max_iri_bytes=1024 * 1024,
        )
    assert compiler.state == "failed"


def test_unsupported_constructor_and_exporters_are_rejected_before_output() -> None:
    constructor_lease = _lease(_snapshot("SubAnnotationPropertyOf(:a :b)"))
    compiler = prepare_native_encoded_direct(constructor_lease)
    with pytest.raises(NativeEncodedDirectUnsupported, match="schema tag 121"):
        compiler.compile_batch(
            bidirectional=False,
            max_edges=10,
            max_iri_bytes=1024 * 1024,
        )
    assert compiler.state == "failed"

    direct = _lease(_snapshot("SubClassOf(:A :B)"))
    root_kinds = bytes(direct.buffers["root_kinds"])
    sliced_owner = b"x" + root_kinds
    sliced = _replace_buffers(direct, {"root_kinds": memoryview(sliced_owner)[1:]})
    with pytest.raises(
        NativeEncodedDirectUnsupported,
        match="does not cover its complete bytes exporter",
    ):
        prepare_native_encoded_direct(sliced)

    readonly_bytearray = memoryview(bytearray(root_kinds)).toreadonly()
    non_bytes = _replace_buffers(direct, {"root_kinds": readonly_bytearray})
    with pytest.raises(
        NativeEncodedDirectUnsupported,
        match="not backed by exact immutable bytes",
    ):
        prepare_native_encoded_direct(non_bytes)


@pytest.mark.parametrize(
    "body",
    [
        "EquivalentClasses(:A ObjectIntersectionOf(:B ObjectExactCardinality(1 :p :C)))",
        "ClassAssertion(:A _:anonymous)",
        'EquivalentClasses(Annotation(<urn:meta> "unsupported") :A ObjectIntersectionOf(:B :C))',
    ],
    ids=["complex-equivalent", "anonymous-individual", "annotated-equivalent"],
)
def test_valid_but_out_of_slice_class_axioms_are_transactionally_unsupported(body: str) -> None:
    compiler = prepare_native_encoded_direct(_lease(_snapshot(body)))
    with pytest.raises(NativeEncodedDirectUnsupported):
        compiler.compile_batch(
            bidirectional=False,
            max_edges=10,
            max_iri_bytes=1024 * 1024,
        )
    assert compiler.state == "failed"


def test_equivalent_set_corruption_and_mixed_edge_limit_fail_before_publication() -> None:
    lease = _lease(_snapshot("EquivalentClasses(:Z :AA :B)"))
    values = bytearray(lease.buffers["item_values"])
    first = bytes(values[:8])
    values[:8] = values[8:16]
    values[8:16] = first
    hostile = _replace_buffers(lease, {"item_values": memoryview(bytes(values))})
    malformed = prepare_native_encoded_direct(hostile)
    with pytest.raises(SnapshotCompatibilityError, match="sorted and unique"):
        malformed.compile_batch(
            bidirectional=False,
            max_edges=10,
            max_iri_bytes=1024 * 1024,
        )
    assert malformed.state == "failed"

    mixed = _lease(_snapshot("SubClassOf(:A :B) EquivalentClasses(:A :C) ClassAssertion(:A :i)"))
    limited = prepare_native_encoded_direct(mixed)
    with pytest.raises(ProjectionResourceError, match="configured edge resources"):
        limited.compile_batch(
            bidirectional=True,
            max_edges=4,
            max_iri_bytes=1024 * 1024,
        )
    assert limited.state == "failed"


def test_hostile_aggregate_arity_and_wrong_kind_operand_fail_before_output() -> None:
    arity_lease = _lease(_snapshot("EquivalentClasses(:A ObjectIntersectionOf(:B :C))"))
    tags = arity_lease.buffers["node_tags"]
    aggregate_id = next(
        node_id
        for node_id in range(1, tags.nbytes // 2 + 1)
        if int.from_bytes(tags[(node_id - 1) * 2 : node_id * 2], "little") == 30
    )
    offsets = bytearray(arity_lease.buffers["node_field_offsets"])
    end_offset = aggregate_id * 8
    end = int.from_bytes(offsets[end_offset : end_offset + 8], "little")
    offsets[end_offset : end_offset + 8] = (end - 1).to_bytes(8, "little")
    hostile_arity = _replace_buffers(
        arity_lease,
        {"node_field_offsets": memoryview(bytes(offsets))},
    )
    malformed_arity = prepare_native_encoded_direct(hostile_arity)
    with pytest.raises(SnapshotCompatibilityError, match="arity"):
        malformed_arity.compile_batch(
            bidirectional=False,
            max_edges=10,
            max_iri_bytes=1024 * 1024,
        )
    assert malformed_arity.state == "failed"

    kind_lease = _lease(
        _snapshot(
            "EquivalentClasses(:A ObjectIntersectionOf(:B :C)) Declaration(ObjectProperty(:wrong))"
        )
    )
    tags = kind_lease.buffers["node_tags"]
    offsets = kind_lease.buffers["node_field_offsets"]
    field_values = kind_lease.buffers["field_values"]
    declaration_id = next(
        node_id
        for node_id in range(1, tags.nbytes // 2 + 1)
        if int.from_bytes(tags[(node_id - 1) * 2 : node_id * 2], "little") == 60
    )
    declaration_start = int.from_bytes(
        offsets[(declaration_id - 1) * 8 : declaration_id * 8],
        "little",
    )
    wrong_id = int.from_bytes(
        field_values[declaration_start * 8 : (declaration_start + 1) * 8],
        "little",
    )
    aggregate_id = next(
        node_id
        for node_id in range(1, tags.nbytes // 2 + 1)
        if int.from_bytes(tags[(node_id - 1) * 2 : node_id * 2], "little") == 30
    )
    aggregate_start = int.from_bytes(
        offsets[(aggregate_id - 1) * 8 : aggregate_id * 8],
        "little",
    )
    item_start = int.from_bytes(
        field_values[aggregate_start * 8 : (aggregate_start + 1) * 8],
        "little",
    )
    item_length = int.from_bytes(
        kind_lease.buffers["field_lengths"][aggregate_start * 8 : (aggregate_start + 1) * 8],
        "little",
    )
    item_values = bytearray(kind_lease.buffers["item_values"])
    operands = [
        int.from_bytes(item_values[index * 8 : (index + 1) * 8], "little")
        for index in range(item_start, item_start + item_length)
    ]
    operands[-1] = wrong_id
    operands.sort()
    assert len(set(operands)) == item_length
    for index, node_id in enumerate(operands, item_start):
        item_values[index * 8 : (index + 1) * 8] = node_id.to_bytes(8, "little")
    hostile_kind = _replace_buffers(
        kind_lease,
        {"item_values": memoryview(bytes(item_values))},
    )
    malformed_kind = prepare_native_encoded_direct(hostile_kind)
    with pytest.raises(SnapshotCompatibilityError, match="not a class"):
        malformed_kind.compile_batch(
            bidirectional=False,
            max_edges=10,
            max_iri_bytes=1024 * 1024,
        )
    assert malformed_kind.state == "failed"


def test_aggregate_edge_limit_fails_before_prior_taxonomy_publication() -> None:
    operands = " ".join(f":C{index:03d}" for index in range(250))
    compiler = prepare_native_encoded_direct(
        _lease(
            _snapshot(
                f"SubClassOf(:TaxA :TaxB) EquivalentClasses(:Lead ObjectIntersectionOf({operands}))"
            )
        )
    )
    with pytest.raises(ProjectionResourceError, match="configured edge resources"):
        compiler.compile_batch(
            bidirectional=False,
            max_edges=250,
            max_iri_bytes=1024 * 1024,
        )
    assert compiler.state == "failed"


def test_disjoint_class_set_and_defined_class_corruption_fail_closed() -> None:
    set_lease = _lease(_snapshot("DisjointClasses(:A :B :C)"))
    item_values = bytearray(set_lease.buffers["item_values"])
    first = bytes(item_values[:8])
    item_values[:8] = item_values[8:16]
    item_values[8:16] = first
    hostile_set = _replace_buffers(
        set_lease,
        {"item_values": memoryview(bytes(item_values))},
    )
    malformed_set = prepare_native_encoded_direct(hostile_set)
    with pytest.raises(SnapshotCompatibilityError, match="sorted and unique"):
        malformed_set.compile_batch(
            bidirectional=False,
            max_edges=1,
            max_iri_bytes=1024 * 1024,
        )
    assert malformed_set.state == "failed"

    union_lease = _lease(
        _snapshot("DisjointUnion(:Defined :A :B) Declaration(ObjectProperty(:wrong))")
    )
    tags = union_lease.buffers["node_tags"]
    offsets = union_lease.buffers["node_field_offsets"]
    values = bytearray(union_lease.buffers["field_values"])
    declaration_id = next(
        node_id
        for node_id in range(1, tags.nbytes // 2 + 1)
        if int.from_bytes(tags[(node_id - 1) * 2 : node_id * 2], "little") == 60
    )
    declaration_start = int.from_bytes(
        offsets[(declaration_id - 1) * 8 : declaration_id * 8],
        "little",
    )
    wrong_id = bytes(values[declaration_start * 8 : (declaration_start + 1) * 8])
    union_id = next(
        node_id
        for node_id in range(1, tags.nbytes // 2 + 1)
        if int.from_bytes(tags[(node_id - 1) * 2 : node_id * 2], "little") == 64
    )
    union_start = int.from_bytes(
        offsets[(union_id - 1) * 8 : union_id * 8],
        "little",
    )
    values[union_start * 8 : (union_start + 1) * 8] = wrong_id
    hostile_union = _replace_buffers(
        union_lease,
        {"field_values": memoryview(bytes(values))},
    )
    malformed_union = prepare_native_encoded_direct(hostile_union)
    with pytest.raises(SnapshotCompatibilityError, match="not a class"):
        malformed_union.compile_batch(
            bidirectional=False,
            max_edges=1,
            max_iri_bytes=1024 * 1024,
        )
    assert malformed_union.state == "failed"


@pytest.mark.parametrize(
    "body",
    [
        'DisjointClasses(Annotation(<urn:meta> "unsupported") :A :B)',
        'DisjointUnion(Annotation(<urn:meta> "unsupported") :Defined :A :B)',
    ],
    ids=["disjoint-classes", "disjoint-union"],
)
def test_annotated_disjoint_class_families_fail_before_output(body: str) -> None:
    compiler = prepare_native_encoded_direct(_lease(_snapshot(body)))
    with pytest.raises(NativeEncodedDirectUnsupported, match=r"annotations|schema tag 5"):
        compiler.compile_batch(
            bidirectional=False,
            max_edges=1,
            max_iri_bytes=1024 * 1024,
        )
    assert compiler.state == "failed"


def test_many_disjoint_class_roots_cross_one_zero_output_bounded_call() -> None:
    axioms = " ".join(f"DisjointClasses(:A{index:03d} :B{index:03d})" for index in range(250))
    compiler = prepare_native_encoded_direct(_lease(_snapshot(axioms)))
    edges, statistics = compiler.compile_batch(
        bidirectional=False,
        max_edges=1,
        max_iri_bytes=1024 * 1024,
    )

    assert edges == []
    assert statistics.roots == statistics.disjoint_classes == 250
    assert statistics.disjoint_unions == 0
    assert statistics.skipped_axioms == 250
    assert statistics.ingestion_counters["native_boundary_calls"] == 1


def test_nonminimal_cardinality_and_domain_range_limit_fail_before_publication() -> None:
    lease = _lease(_snapshot("SubClassOf(:A ObjectMinCardinality(256 :p :B))"))
    scalar = bytearray(lease.buffers["scalar_bytes"])
    offset = scalar.index(b"\x00\x01")
    scalar[offset + 1] = 0
    hostile = _replace_buffers(lease, {"scalar_bytes": memoryview(bytes(scalar))})
    malformed = prepare_native_encoded_direct(hostile)
    with pytest.raises(SnapshotCompatibilityError, match="minimally encoded"):
        malformed.compile_batch(
            bidirectional=False,
            max_edges=10,
            max_iri_bytes=1024 * 1024,
        )
    assert malformed.state == "failed"

    domains = " ".join(f"ObjectPropertyDomain(:p :D{index:02d})" for index in range(20))
    ranges = " ".join(f"ObjectPropertyRange(:p :R{index:02d})" for index in range(20))
    product = prepare_native_encoded_direct(_lease(_snapshot(f"{domains} {ranges}")))
    with pytest.raises(ProjectionResourceError, match="configured edge resources"):
        product.compile_batch(
            bidirectional=False,
            max_edges=399,
            max_iri_bytes=1024 * 1024,
        )
    assert product.state == "failed"


@pytest.mark.parametrize("only_taxonomy", [False, True])
def test_ordered_inverse_property_chains_match_scalar_capacity_and_state_neutrality(
    only_taxonomy: bool,
) -> None:
    view = _property_chain_snapshot()
    expected = Projector().project(
        view,
        options=ProjectionOptions(
            backend="python",
            only_taxonomy=only_taxonomy,
            duplicates="preserve",
            order="encounter",
        ),
    )
    actual, statistics = prepare_native_encoded_direct(_lease(view)).compile_batch(
        bidirectional=False,
        max_edges=len(expected),
        max_iri_bytes=1024 * 1024,
        only_taxonomy=only_taxonomy,
    )

    assert actual == expected
    assert actual == [
        Edge("urn:native-direct#D", "urn:native-direct#r", "urn:native-direct#R"),
        Edge("urn:native-direct#D", "urn:native-direct#c71", "urn:native-direct#R"),
    ]
    assert statistics.roots == 15
    assert statistics.sub_object_properties == 13
    assert statistics.object_property_chains == 2
    assert statistics.skipped_axioms == 0
    assert statistics.domain_range_edges == 1
    assert statistics.role_expansion_edges == 1


def test_asserted_taxonomy_preflights_property_chains_without_role_leakage() -> None:
    view = _property_chain_snapshot()
    actual, statistics = prepare_native_encoded_direct(_lease(view)).compile_batch(
        bidirectional=True,
        max_edges=1,
        max_iri_bytes=1024 * 1024,
        asserted_taxonomy_only=True,
    )

    assert actual == []
    assert statistics.sub_object_properties == 13
    assert statistics.object_property_chains == 2
    assert statistics.skipped_axioms == 0
    assert statistics.domain_range_edges == 0
    assert statistics.role_expansion_edges == 0


def test_many_property_chains_cross_one_zero_output_bounded_call() -> None:
    axioms = " ".join(
        "SubObjectPropertyOf("
        f"ObjectPropertyChain(:left{index:03d} ObjectInverseOf(:right{index:03d})) "
        f":super{index:03d})"
        for index in range(250)
    )
    compiler = prepare_native_encoded_direct(_lease(_snapshot(axioms)))
    edges, statistics = compiler.compile_batch(
        bidirectional=False,
        max_edges=1,
        max_iri_bytes=1024 * 1024,
    )

    assert edges == []
    assert statistics.roots == 250
    assert statistics.sub_object_properties == 250
    assert statistics.object_property_chains == 250
    assert statistics.skipped_axioms == 0
    assert statistics.ingestion_counters["native_boundary_calls"] == 1


@pytest.mark.parametrize(
    "corruption",
    ["sequence-kind", "item-kind", "wrong-item", "nested-chain"],
)
def test_hostile_property_chain_rows_fail_before_output(corruption: str) -> None:
    lease = _lease(
        _snapshot(
            "SubClassOf(:Before :After) "
            "SubObjectPropertyOf(ObjectPropertyChain(:p ObjectInverseOf(:q)) :r) "
            "Declaration(Class(:Wrong))"
        )
    )
    buffers = lease.buffers
    tags = buffers["node_tags"]

    def tagged_nodes(tag: int) -> list[int]:
        return [
            node_id
            for node_id in range(1, tags.nbytes // 2 + 1)
            if int.from_bytes(tags[(node_id - 1) * 2 : node_id * 2], "little") == tag
        ]

    offsets = buffers["node_field_offsets"]

    def field_start(node_id: int) -> int:
        return int.from_bytes(
            offsets[(node_id - 1) * 8 : node_id * 8],
            "little",
        )

    chain_id = tagged_nodes(11)[0]
    chain_field = field_start(chain_id)
    item_start = int.from_bytes(
        buffers["field_values"][chain_field * 8 : (chain_field + 1) * 8],
        "little",
    )
    replacements: dict[str, memoryview]
    if corruption == "sequence-kind":
        field_kinds = bytearray(buffers["field_kinds"])
        field_kinds[chain_field] = 6
        replacements = {"field_kinds": memoryview(bytes(field_kinds))}
    elif corruption == "item-kind":
        item_kinds = bytearray(buffers["item_kinds"])
        item_kinds[item_start] = 2
        replacements = {"item_kinds": memoryview(bytes(item_kinds))}
    else:
        item_values = bytearray(buffers["item_values"])
        if corruption == "wrong-item":
            declaration_field = field_start(tagged_nodes(60)[0])
            replacement = int.from_bytes(
                buffers["field_values"][
                    declaration_field * 8 : (declaration_field + 1) * 8
                ],
                "little",
            )
        else:
            assert corruption == "nested-chain"
            replacement = chain_id
        item_values[item_start * 8 : (item_start + 1) * 8] = replacement.to_bytes(
            8,
            "little",
        )
        replacements = {"item_values": memoryview(bytes(item_values))}
    compiler = prepare_native_encoded_direct(_replace_buffers(lease, replacements))
    with pytest.raises(
        (SnapshotCompatibilityError, NativeEncodedDirectUnsupported),
        match=(
            r"ordered sequence|sorted and unique|collection item|component kind|"
            r"scalar offset|object-property|named or inverse"
        ),
    ):
        compiler.compile_batch(
            bidirectional=False,
            max_edges=10,
            max_iri_bytes=1024 * 1024,
        )
    assert compiler.state == "failed"


@pytest.mark.parametrize(
    "body",
    [
        "SubClassOf(:A ObjectSomeValuesFrom(ObjectInverseOf(:p) :B))",
        "SubClassOf(:A ObjectSomeValuesFrom(:p ObjectIntersectionOf(:B :C)))",
        "SubClassOf(:A ObjectExactCardinality(1 :p :B))",
        "SubClassOf(ObjectSomeValuesFrom(:p :A) ObjectAllValuesFrom(:q :B))",
        "ObjectPropertyDomain(:p ObjectIntersectionOf(:A :B))",
        'ObjectPropertyRange(Annotation(<urn:meta> "unsupported") :p :R)',
        'SubObjectPropertyOf(Annotation(<urn:meta> "unsupported") '
        "ObjectPropertyChain(:p :q) :r)",
        'SubObjectPropertyOf(Annotation(<urn:meta> "unsupported") :p :q)',
        'InverseObjectProperties(Annotation(<urn:meta> "unsupported") :p :q)',
        'EquivalentObjectProperties(Annotation(<urn:meta> "unsupported") :p :q)',
        'FunctionalObjectProperty(Annotation(<urn:meta> "unsupported") :p)',
    ],
    ids=[
        "inverse-property",
        "complex-filler",
        "exact-cardinality",
        "restriction-pair",
        "complex-domain",
        "annotated-range",
        "annotated-property-chain",
        "annotated-subproperty",
        "annotated-inverse",
        "annotated-equivalent",
        "annotated-characteristic",
    ],
)
def test_valid_but_out_of_slice_role_shapes_are_transactionally_unsupported(body: str) -> None:
    compiler = prepare_native_encoded_direct(_lease(_snapshot(body)))
    with pytest.raises(NativeEncodedDirectUnsupported):
        compiler.compile_batch(
            bidirectional=False,
            max_edges=10,
            max_iri_bytes=1024 * 1024,
        )
    assert compiler.state == "failed"


@pytest.mark.parametrize(
    "body",
    [
        "ObjectPropertyAssertion(:p _:anonymous :i)",
        "NegativeObjectPropertyAssertion(:p :i _:anonymous)",
        'ObjectPropertyAssertion(Annotation(<urn:meta> "unsupported") :p :i :j)',
        'NegativeObjectPropertyAssertion(Annotation(<urn:meta> "unsupported") :p :i :j)',
    ],
    ids=[
        "anonymous-positive",
        "anonymous-negative",
        "annotated-positive",
        "annotated-negative",
    ],
)
def test_out_of_slice_object_assertion_boundaries_are_transactionally_unsupported(
    body: str,
) -> None:
    compiler = prepare_native_encoded_direct(_lease(_snapshot(body)))
    with pytest.raises(NativeEncodedDirectUnsupported):
        compiler.compile_batch(
            bidirectional=False,
            max_edges=10,
            max_iri_bytes=1024 * 1024,
        )
    assert compiler.state == "failed"


def test_hostile_object_assertion_individual_and_edge_limit_fail_before_publication() -> None:
    lease = _lease(_snapshot("ObjectPropertyAssertion(:p :i :j)"))
    tags = lease.buffers["node_tags"]
    assertion_id = next(
        node_id
        for node_id in range(1, tags.nbytes // 2 + 1)
        if int.from_bytes(tags[(node_id - 1) * 2 : node_id * 2], "little") == 113
    )
    offsets = lease.buffers["node_field_offsets"]
    field_start = int.from_bytes(
        offsets[(assertion_id - 1) * 8 : assertion_id * 8],
        "little",
    )
    values = bytearray(lease.buffers["field_values"])
    property_id = bytes(values[field_start * 8 : field_start * 8 + 8])
    source_offset = (field_start + 1) * 8
    values[source_offset : source_offset + 8] = property_id
    hostile = _replace_buffers(lease, {"field_values": memoryview(bytes(values))})
    malformed = prepare_native_encoded_direct(hostile)
    with pytest.raises(SnapshotCompatibilityError, match="named individual"):
        malformed.compile_batch(
            bidirectional=False,
            max_edges=10,
            max_iri_bytes=1024 * 1024,
        )
    assert malformed.state == "failed"

    assertions = " ".join(
        f"ObjectPropertyAssertion(:p :i{index:03d} :j{index:03d})" for index in range(250)
    )
    limited = prepare_native_encoded_direct(_lease(_snapshot(assertions)))
    with pytest.raises(ProjectionResourceError, match="configured edge resources"):
        limited.compile_batch(
            bidirectional=False,
            max_edges=249,
            max_iri_bytes=1024 * 1024,
        )
    assert limited.state == "failed"


def test_role_set_corruption_and_expanded_edge_limit_fail_before_publication() -> None:
    lease = _lease(_snapshot("EquivalentObjectProperties(:p :q :r)"))
    values = bytearray(lease.buffers["item_values"])
    first = bytes(values[:8])
    values[:8] = values[8:16]
    values[8:16] = first
    hostile = _replace_buffers(lease, {"item_values": memoryview(bytes(values))})
    malformed = prepare_native_encoded_direct(hostile)
    with pytest.raises(SnapshotCompatibilityError, match="sorted and unique"):
        malformed.compile_batch(
            bidirectional=False,
            max_edges=10,
            max_iri_bytes=1024 * 1024,
        )
    assert malformed.state == "failed"

    restrictions = " ".join(
        f"SubClassOf(:A{index:03d} ObjectSomeValuesFrom(:p :B{index:03d}))" for index in range(250)
    )
    expanded = prepare_native_encoded_direct(
        _lease(
            _snapshot(
                f"SubObjectPropertyOf(:child :p) InverseObjectProperties(:p :pinv) {restrictions}"
            )
        )
    )
    with pytest.raises(ProjectionResourceError, match="configured edge resources"):
        expanded.compile_batch(
            bidirectional=False,
            max_edges=749,
            max_iri_bytes=1024 * 1024,
        )
    assert expanded.state == "failed"


def test_descriptor_binding_and_hostile_supported_rows_fail_closed() -> None:
    lease = _lease(_snapshot("SubClassOf(:A :B)"))
    mismatched = replace(lease, descriptor_sha256="00" * 32)
    with pytest.raises(SnapshotCompatibilityError, match="descriptor digest differs"):
        prepare_native_encoded_direct(mismatched)

    root_ids = bytearray(lease.buffers["root_ids"])
    root_ids[0:4] = (2**32 - 1).to_bytes(4, "little")
    hostile = _replace_buffers(lease, {"root_ids": memoryview(bytes(root_ids))})
    compiler = prepare_native_encoded_direct(hostile)
    with pytest.raises(SnapshotCompatibilityError, match="node reference is out of range"):
        compiler.compile_batch(
            bidirectional=False,
            max_edges=10,
            max_iri_bytes=1024 * 1024,
        )
    assert compiler.state == "failed"


def test_native_owner_and_exact_bytes_exporters_live_until_handle_drop() -> None:
    view = _snapshot(
        "SubClassOf(:A :B) SubClassOf(:A ObjectSomeValuesFrom(:p :B)) "
        "EquivalentClasses(:A :C) ClassAssertion(:A :individual) "
        "ObjectPropertyAssertion(:p :individual :other) "
        "NegativeObjectPropertyAssertion(ObjectInverseOf(:p) :other :individual) "
        "ObjectPropertyDomain(:p :A) ObjectPropertyRange(:p :B)"
    )
    lease = _lease(view)
    exporter = cast(bytes, lease.buffers["scalar_bytes"].obj)
    before = sys.getrefcount(exporter)
    compiler = prepare_native_encoded_direct(lease)
    assert sys.getrefcount(exporter) >= before + 1
    del compiler
    gc.collect()
    assert sys.getrefcount(exporter) == before

    class Owner:
        pass

    def create() -> tuple[NativeEncodedDirectCompiler, weakref.ReferenceType[object]]:
        view = _snapshot(
            "SubClassOf(:A :B) SubClassOf(:A ObjectSomeValuesFrom(:p :B)) "
            "EquivalentClasses(:A :C) ClassAssertion(:A :individual) "
            "ObjectPropertyAssertion(:p :individual :other) "
            "NegativeObjectPropertyAssertion(ObjectInverseOf(:p) :other :individual) "
            "ObjectPropertyDomain(:p :A) ObjectPropertyRange(:p :B)"
        )
        lease = _lease(view)
        owner = Owner()
        segment = SimpleNamespace(
            role=1,
            owner=owner,
            source=None,
            posting_mode=0,
            root_ids=memoryview(b""),
            anonymous_scope_map=memoryview(b""),
            member_token=None,
        )
        encoded = SimpleNamespace(
            schema_name=lease.schema_name,
            schema_version=lease.schema_version,
            model_schema=lease.model_schema,
            owner=owner,
            descriptor=cast(Any, lease.encoded_view).descriptor,
            buffers=lease.buffers,
            segments=(segment,),
        )
        retained = replace(
            lease,
            encoded_view=encoded,
            owner=owner,
            segments=(segment,),
        )
        return prepare_native_encoded_direct(retained), weakref.ref(owner)

    compiler, owner_ref = create()
    gc.collect()
    assert owner_ref() is not None
    del compiler
    gc.collect()
    assert owner_ref() is None


def test_detached_work_releases_the_gil_and_accepts_concurrent_cancel() -> None:
    lease = _lease(
        _snapshot(
            "SubClassOf(:A :B) SubClassOf(:A ObjectSomeValuesFrom(:p :B)) "
            "EquivalentClasses(:A :C) ClassAssertion(:A :individual) "
            "ObjectPropertyAssertion(:p :individual :other) "
            "NegativeObjectPropertyAssertion(ObjectInverseOf(:p) :other :individual) "
            "ObjectPropertyDomain(:p :A) ObjectPropertyRange(:p :B)"
        )
    )
    compiler = prepare_native_encoded_direct(lease)
    module = load_native_module()
    kernel = compiler._kernel

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(kernel.test_wait_for_cancel, 100_000_000)
        deadline = time.monotonic() + 5
        while compiler.state != "running" and time.monotonic() < deadline:
            time.sleep(0)
        assert compiler.state == "running"
        assert compiler.cancel() is True
        with pytest.raises(module.EncodedDirectCancelledError):
            future.result(timeout=5)
    assert compiler.state == "cancelled"
    with pytest.raises(NativeEncodedDirectCancelled):
        compiler.compile_batch(
            bidirectional=False,
            max_edges=6,
            max_iri_bytes=1024 * 1024,
        )


def test_encoded_capability_remains_unadvertised() -> None:
    features = tuple(load_native_module().FEATURES)
    assert features == ("abi3-py310", "bounded-batches")
    assert ENCODED_NATIVE_FEATURE not in frozenset(features)
