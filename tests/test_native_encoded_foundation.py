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
from pyowl_core.backends.python import PythonParser

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
    RoleState,
    iter_asserted_taxonomy,
)
from pyowl2vec_star_projector.encoded import (
    ENCODED_NATIVE_FEATURE,
    EncodedNegotiation,
    EncodedStructuralLease,
    _validate_encoded_view,
)
from pyowl2vec_star_projector.encoded_compiler import prepare_encoded_subset_compilation
from pyowl2vec_star_projector.errors import (
    ProjectionError,
    SnapshotCompatibilityError,
    UnsupportedAxiomShapeError,
)
from pyowl2vec_star_projector.native import (
    ENCODED_DIRECT_BUFFER_ORDER,
    NativeEncodedDirectBatchIterator,
    NativeEncodedDirectCancelled,
    NativeEncodedDirectCompiler,
    NativeEncodedDirectRoleState,
    NativeEncodedDirectStatistics,
    NativeEncodedDirectUnsupported,
    load_native_module,
    prepare_native_encoded_direct,
    prepare_native_encoded_role_state,
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


def _swrl_snapshot(body: str) -> object:
    source = f"Prefix(:=<urn:native-direct#>) Ontology(<urn:native-direct> {body})".encode()
    options = pyowl_core.LoadOptions(
        imports=pyowl_core.ImportPolicy.IGNORE,
        backend=pyowl_core.BackendPreference.PYTHON,
    )
    document = PythonParser().parse(source, options=options, allow_swrl=True)
    return pyowl_core.load_snapshot(document, options=options)


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
        "SubObjectPropertyOf(Annotation(<urn:chain-meta> <urn:chain-value>) "
        "ObjectPropertyChain(:z ObjectInverseOf(:a) :m) :r) "
        "SubObjectPropertyOf(Annotation(Annotation(<urn:nested> _:ignored) "
        '<urn:chain-meta> "typed"^^<urn:datatype>) '
        "ObjectPropertyChain(:m ObjectInverseOf(:a) :z) :r) "
        "ObjectPropertyDomain(:r :D) ObjectPropertyRange(:r :R)"
    )


def _nonprojecting_class_snapshot() -> object:
    return _snapshot(
        "SubClassOf(:A ObjectOneOf(:member _:one)) "
        "SubClassOf(ObjectHasValue(ObjectInverseOf(:p) _:value) :B) "
        "SubClassOf(:C ObjectHasSelf(ObjectInverseOf(:q))) "
        "ClassAssertion(ObjectOneOf(:member _:assertion) :i) "
        "ClassAssertion(:Named _:classAnonymous) ClassAssertion(:Type :named) "
        "SubClassOf(:ExactA ObjectExactCardinality(2 ObjectInverseOf(:exact) :ExactB)) "
        "SubClassOf(ObjectComplementOf(ObjectHasSelf(ObjectInverseOf(:complement))) "
        ":Complement) "
        "ClassAssertion(ObjectComplementOf(ObjectOneOf(:member _:nested)) :complemented) "
        "SubClassOf(:TaxA :TaxB) SubObjectPropertyOf(:child :r) "
        "ObjectPropertyDomain(:r :D) ObjectPropertyRange(:r :R)"
    )


def _data_class_expression_snapshot() -> object:
    return _snapshot(
        "SubClassOf(:Some DataSomeValuesFrom(:dp :dq DataIntersectionOf("
        "<http://www.w3.org/2001/XMLSchema#string> "
        'DataComplementOf(DataOneOf("one" "two"@en)) '
        "DatatypeRestriction(<http://www.w3.org/2001/XMLSchema#integer> "
        '<http://www.w3.org/2001/XMLSchema#minInclusive> "1"^^'
        "<http://www.w3.org/2001/XMLSchema#integer>)))) "
        "SubClassOf(DataAllValuesFrom(:dp "
        "<http://www.w3.org/2001/XMLSchema#string>) :All) "
        'SubClassOf(:Has DataHasValue(:dp "value"^^'
        "<http://www.w3.org/2001/XMLSchema#string>)) "
        "SubClassOf(:Min DataMinCardinality(2 :dp "
        "DatatypeRestriction(<http://www.w3.org/2001/XMLSchema#integer> "
        '<http://www.w3.org/2001/XMLSchema#maxInclusive> "9"^^'
        "<http://www.w3.org/2001/XMLSchema#integer>))) "
        'SubClassOf(DataMaxCardinality(3 :dp DataUnionOf(DataOneOf("x" "y"@fr) '
        "<http://www.w3.org/2001/XMLSchema#string>)) :Max) "
        "SubClassOf(:Exact DataExactCardinality(4 :dp "
        "DataComplementOf(<http://www.w3.org/2001/XMLSchema#integer>))) "
        "SubClassOf(:Wrapped ObjectComplementOf(DataSomeValuesFrom(:dp "
        "<http://www.w3.org/2001/XMLSchema#string>))) "
        'ClassAssertion(DataHasValue(:dp "asserted") :i) '
        "ClassAssertion(ObjectComplementOf(DataExactCardinality(1 :dp "
        "<http://www.w3.org/2001/XMLSchema#string>)) :j) "
        "SubClassOf(:TaxA :TaxB) ClassAssertion(:Type :named) "
        "SubObjectPropertyOf(:child :r) "
        "ObjectPropertyDomain(:r :D) ObjectPropertyRange(:r :R)"
    )


def _expanded_expression_axiom_snapshot() -> object:
    return _snapshot(
        'EquivalentClasses(:Eq ObjectIntersectionOf(:Named DataHasValue(:dp "eq") '
        "ObjectOneOf(:one))) "
        "EquivalentClasses(:Ignored DataExactCardinality(2 :dp "
        "<http://www.w3.org/2001/XMLSchema#string>)) "
        "SubClassOf(:AggregateSub ObjectIntersectionOf(:B "
        "DataSomeValuesFrom(:dp <http://www.w3.org/2001/XMLSchema#string>))) "
        'ClassAssertion(ObjectUnionOf(:C DataHasValue(:dp "assert")) '
        ":aggregateIndividual) "
        "ClassAssertion(ObjectSomeValuesFrom(:op :F) :restrictionIndividual) "
        'DisjointClasses(ObjectHasSelf(:op) DataHasValue(:dp "disjoint") '
        "ObjectIntersectionOf(:D ObjectOneOf(:one))) "
        "DisjointUnion(:Defined ObjectOneOf(:one) "
        "DataExactCardinality(1 :dp <http://www.w3.org/2001/XMLSchema#string>)) "
        "HasKey(DataSomeValuesFrom(:dp "
        "<http://www.w3.org/2001/XMLSchema#string>) () (:dp)) "
        "DataPropertyDomain(:dp "
        "ObjectComplementOf(ObjectSomeValuesFrom(:op :F))) "
        "DataPropertyRange(:dp DataUnionOf("
        "<http://www.w3.org/2001/XMLSchema#string> DataOneOf(\"range\"))) "
        'DatatypeDefinition(:dt DataComplementOf(DataOneOf("definition"))) '
        "SubClassOf(:TaxA :TaxB) ClassAssertion(:Type :named) "
        "SubObjectPropertyOf(:child :r) "
        "ObjectPropertyDomain(:r :Domain) ObjectPropertyRange(:r :Range)"
    )


def _inverse_restriction_domain_snapshot() -> object:
    return _snapshot(
        "SubClassOf(:A ObjectSomeValuesFrom(ObjectInverseOf(:p) :B)) "
        "SubClassOf(ObjectAllValuesFrom(ObjectInverseOf(:p) :C) :D) "
        "SubClassOf(:Min ObjectMinCardinality(1 ObjectInverseOf(:p) :MinF)) "
        "SubClassOf(ObjectMaxCardinality(2 ObjectInverseOf(:p) :MaxF) :Max) "
        "EquivalentClasses(:Eq ObjectIntersectionOf(:Named "
        "ObjectSomeValuesFrom(ObjectInverseOf(:p) :EqF))) "
        "SubObjectPropertyOf(:child :p) InverseObjectProperties(:p :pinv) "
        "ObjectPropertyDomain(ObjectInverseOf(:p) :IgnoredDomain) "
        "ObjectPropertyRange(ObjectInverseOf(:p) :IgnoredRange) "
        "ObjectPropertyDomain(:p ObjectIntersectionOf(:ComplexDomain "
        'DataHasValue(:dp "value"))) '
        "ObjectPropertyRange(:p ObjectComplementOf("
        "ObjectSomeValuesFrom(ObjectInverseOf(:p) :ComplexRange))) "
        "ObjectPropertyDomain(:p :Domain) ObjectPropertyRange(:p :Range) "
        "SubClassOf(:TaxA :TaxB)"
    )


def _annotation_metadata_root_snapshot() -> object:
    return _snapshot(
        "Annotation(Annotation(<urn:nested> _:ontologyNested) "
        "<urn:ontology-meta> _:ontology) "
        "SubAnnotationPropertyOf(Annotation(<urn:meta> _:subMeta) "
        ":childAnnotation :parentAnnotation) "
        "AnnotationPropertyDomain(Annotation(<urn:meta> _:domainMeta) "
        ":childAnnotation <urn:annotation-domain>) "
        "AnnotationPropertyRange(Annotation(<urn:meta> _:rangeMeta) "
        ":parentAnnotation <urn:annotation-range>) "
        "SubClassOf(:A :B) "
        "AnnotationAssertion(Annotation(<urn:meta> _:assertionMeta) "
        "<http://www.w3.org/2000/01/rdf-schema#label> :A \"label\") "
        "SubObjectPropertyOf(:child :p) "
        "ObjectPropertyDomain(:p :D) ObjectPropertyRange(:p :R)"
    )


def _annotated_non_role_axiom_snapshot() -> object:
    metadata = "Annotation(Annotation(<urn:nested> _:nestedMeta) <urn:meta> _:axiomMeta)"
    xsd_string = "<http://www.w3.org/2001/XMLSchema#string>"
    axioms = [
        f"Declaration({metadata} Class(:A))",
        f"SubClassOf({metadata} :A :B)",
        f"SubClassOf({metadata} :B ObjectSomeValuesFrom(:p :C))",
        f"EquivalentClasses({metadata} :E :F)",
        f"EquivalentClasses({metadata} :G "
        "ObjectIntersectionOf(:H ObjectSomeValuesFrom(:p :I)))",
        f"DisjointClasses({metadata} :J :K)",
        f"DisjointUnion({metadata} :Defined :L :M)",
        f"EquivalentObjectProperties({metadata} :q :r)",
        f"DisjointObjectProperties({metadata} :q :r)",
        f"FunctionalObjectProperty({metadata} :q)",
        f"InverseFunctionalObjectProperty({metadata} :q)",
        f"ReflexiveObjectProperty({metadata} :q)",
        f"IrreflexiveObjectProperty({metadata} :q)",
        f"SymmetricObjectProperty({metadata} :q)",
        f"AsymmetricObjectProperty({metadata} :q)",
        f"TransitiveObjectProperty({metadata} :q)",
        f"ObjectPropertyDomain({metadata} :p :D)",
        f"ObjectPropertyRange({metadata} :p :R)",
        f"ClassAssertion({metadata} :A :individual)",
        f"ObjectPropertyAssertion({metadata} :u :source :target)",
        f"NegativeObjectPropertyAssertion({metadata} :q :source :target)",
        f"SubDataPropertyOf({metadata} :dp :dq)",
        f"EquivalentDataProperties({metadata} :dp :dq)",
        f"DisjointDataProperties({metadata} :dp :dq)",
        f"DataPropertyDomain({metadata} :dp :A)",
        f"DataPropertyRange({metadata} :dp {xsd_string})",
        f"FunctionalDataProperty({metadata} :dp)",
        f"DatatypeDefinition({metadata} :custom {xsd_string})",
        f'DataPropertyAssertion({metadata} :dp :individual "value")',
        f'NegativeDataPropertyAssertion({metadata} :dp :individual "blocked")',
        "SubObjectPropertyOf(:child :p)",
    ]
    return _snapshot(" ".join(axioms))


def _annotated_role_axiom_snapshot() -> object:
    return _snapshot(
        'SubObjectPropertyOf(Annotation(Annotation(<urn:nested> "ignored") '
        '<urn:meta> "0") :a :p) '
        'SubObjectPropertyOf(Annotation(<urn:meta> "4") :c :a) '
        'InverseObjectProperties(Annotation(<urn:meta> "0") :p :x) '
        'InverseObjectProperties(Annotation(<urn:meta> "0") :p :y) '
        "SubObjectPropertyOf(Annotation(<urn:chain-meta> <urn:chain-value>) "
        "ObjectPropertyChain(:left ObjectInverseOf(:right)) :p) "
        'SubClassOf(Annotation(<urn:subclass-meta> "ignored") '
        ":Source ObjectSomeValuesFrom(:p :Target)) "
        'ObjectPropertyDomain(Annotation(<urn:meta> "domain") :p :D) '
        'ObjectPropertyRange(Annotation(<urn:meta> "range") :p :R)'
    )


def _annotated_role_value_snapshot() -> object:
    return _snapshot(
        "InverseObjectProperties(Annotation(<urn:meta> <urn:value>) :p :iriValue) "
        'InverseObjectProperties(Annotation(<urn:meta> "typed"^^<urn:datatype>) '
        ":p :typedValue) "
        'InverseObjectProperties(Annotation(<urn:meta> "bonjour"@fr) :p :langValue) '
        'InverseObjectProperties(Annotation(<urn:a> "first") '
        'Annotation(<urn:b> "second") :p :multiValue) '
        'InverseObjectProperties(Annotation(Annotation(<urn:nested> "ignored") '
        '<urn:meta> "plain") :p :nestedValue) '
        'InverseObjectProperties(Annotation(<urn:meta> "inverse-expression") '
        "ObjectInverseOf(:p) :inverseExpression) "
        "ObjectPropertyDomain(:p :D) ObjectPropertyRange(:p :R)"
    )


def _swrl_extension_snapshot() -> object:
    return _swrl_snapshot(
        "SWRLRule(Annotation(<urn:meta> _:ruleMetadata) "
        "(ClassAtom(ObjectIntersectionOf(:RuleOnly :RuleSupport) Variable(:x)) "
        "DataRangeAtom(DataUnionOf(<http://www.w3.org/2001/XMLSchema#string> "
        'DataOneOf("one")) Variable(:d)) '
        "ObjectPropertyAtom(ObjectInverseOf(:p) Variable(:x) _:ruleIndividual) "
        'DataPropertyAtom(:dp :named "v") '
        'BuiltInAtom(<urn:builtin> Variable(:d) "n") '
        "BuiltInAtom(<urn:zero>) SameIndividualAtom(Variable(:x) :named) "
        "DifferentIndividualsAtom(_:ruleIndividual Variable(:x))) "
        "(ClassAtom(ObjectSomeValuesFrom(:p :RuleOnly) Variable(:x)))) "
        "SWRLRule(() ()) SubClassOf(:TaxA :TaxB) "
        "ObjectPropertyAssertion(:u :named :other) "
        "ObjectPropertyDomain(:p :D) ObjectPropertyRange(:p :R) "
        "AnnotationAssertion(<http://www.w3.org/2000/01/rdf-schema#label> "
        ':RuleOnly "rule class")'
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


def _root_lease(view: object) -> EncodedStructuralLease:
    encoded = cast(Any, view).view(
        pyowl_core.EncodedStructuralView,
        schema_version=1,
        scope=pyowl_core.AxiomScope.ROOT,
    )
    return _validate_encoded_view(
        view,
        encoded,
        pyowl_core.EncodedStructuralView,
        pyowl_core.AxiomScope.ROOT,
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


def _packed_lease(
    lease: EncodedStructuralLease,
    *,
    prefix: bytes = b"",
    mutable: bool = False,
) -> EncodedStructuralLease:
    payload = prefix + b"".join(bytes(lease.buffers[name]) for name in ENCODED_DIRECT_BUFFER_ORDER)
    owner: bytes | bytearray = bytearray(payload) if mutable else payload
    packed = memoryview(owner).toreadonly()
    start = len(prefix)
    replacements: dict[str, memoryview] = {}
    for name in ENCODED_DIRECT_BUFFER_ORDER:
        end = start + lease.buffers[name].nbytes
        replacements[name] = packed[start:end]
        start = end
    return _replace_buffers(lease, replacements)


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
    assert statistics.ontology_annotations == 0
    assert statistics.declarations == 3
    assert statistics.subclasses == 2
    assert statistics.restriction_subclasses == 0
    assert statistics.ignored_subclasses == 0
    assert statistics.equivalents == 0
    assert statistics.aggregate_equivalents == 0
    assert statistics.equivalent_base_edges == 0
    assert statistics.ignored_equivalents == 0
    assert statistics.disjoint_classes == 0
    assert statistics.disjoint_unions == 0
    assert statistics.has_keys == 0
    assert statistics.same_individuals == 0
    assert statistics.different_individuals == 0
    assert statistics.class_assertions == 0
    assert statistics.ignored_class_assertions == 0
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
    assert statistics.sub_annotation_properties == 0
    assert statistics.annotation_property_domains == 0
    assert statistics.annotation_property_ranges == 0
    assert statistics.annotation_edges == 0
    assert statistics.non_string_literal_renderings == 0
    assert statistics.skipped_axioms == 0
    assert statistics.object_property_domains == 0
    assert statistics.object_property_ranges == 0
    assert statistics.ignored_object_property_domains == 0
    assert statistics.ignored_object_property_ranges == 0
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
    assert statistics.equivalent_base_edges == (
        7 if only_taxonomy else 20 if bidirectional else 13
    )
    assert statistics.ignored_equivalents == 0
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


@pytest.mark.parametrize("annotated", [False, True], ids=["plain", "annotated"])
@pytest.mark.parametrize("mode", ["normal", "only-taxonomy", "asserted-taxonomy"])
@pytest.mark.parametrize(
    "nested_operands",
    [
        "ObjectUnionOf(:C :D)",
        "ObjectComplementOf(ObjectIntersectionOf(:C ObjectComplementOf(:D))) "
        "ObjectSomeValuesFrom(:ignored ObjectUnionOf(:X :Y))",
    ],
    ids=["aggregate", "complement-and-restriction-filler"],
)
def test_nested_aggregate_equivalence_emits_supported_siblings_in_rust(
    annotated: bool,
    mode: str,
    nested_operands: str,
) -> None:
    metadata = 'Annotation(<urn:meta> "nested") ' if annotated else ""
    view = _snapshot(
        "SubObjectPropertyOf(:child :p) InverseObjectProperties(:p :pinv) "
        "SubClassOf(:Before :After) "
        f"EquivalentClasses({metadata}:A ObjectIntersectionOf("
        f":B {nested_operands} ObjectSomeValuesFrom(:p :E)))"
    )
    if mode == "asserted-taxonomy":
        scalar = list(
            iter_asserted_taxonomy(
                view,
                bidirectional=False,
                duplicates="preserve",
                order="encounter",
            )
        )
    else:
        scalar = Projector().project(
            view,
            options=ProjectionOptions(
                backend="python",
                only_taxonomy=mode == "only-taxonomy",
                duplicates="preserve",
                order="encounter",
            ),
        )
    expected = [
        Edge("urn:native-direct#Before", SUBCLASS_OF, "urn:native-direct#After")
    ]
    if mode != "asserted-taxonomy":
        expected.append(
            Edge("urn:native-direct#A", SUBCLASS_OF, "urn:native-direct#B")
        )
    if mode == "normal":
        expected.extend(
            [
                Edge("urn:native-direct#A", "urn:native-direct#p", "urn:native-direct#E"),
                Edge(
                    "urn:native-direct#A",
                    "urn:native-direct#child",
                    "urn:native-direct#E",
                ),
                Edge(
                    "urn:native-direct#E",
                    "urn:native-direct#pinv",
                    "urn:native-direct#A",
                ),
            ]
        )
    actual, statistics = prepare_native_encoded_direct(_lease(view)).compile_batch(
        bidirectional=False,
        max_edges=len(expected),
        max_iri_bytes=1024 * 1024,
        only_taxonomy=mode == "only-taxonomy",
        asserted_taxonomy_only=mode == "asserted-taxonomy",
    )

    assert actual == scalar == expected
    assert statistics.equivalents == statistics.aggregate_equivalents == 1
    assert statistics.role_expansion_edges == (2 if mode == "normal" else 0)
    assert statistics.ingestion_counters["native_boundary_calls"] == 1


def test_deep_nested_aggregate_equivalence_uses_one_bounded_output_call() -> None:
    expression = ":Leaf"
    for index in range(200):
        constructor = "ObjectUnionOf" if index % 2 == 0 else "ObjectIntersectionOf"
        expression = f"{constructor}(:Side{index:03d} {expression})"
    view = _snapshot(f"EquivalentClasses(:Root {expression})")
    expected = Projector().project(
        view,
        options=ProjectionOptions(backend="python", order="encounter"),
    )
    actual, statistics = prepare_native_encoded_direct(_lease(view)).compile_batch(
        bidirectional=False,
        max_edges=1,
        max_iri_bytes=1024 * 1024,
    )

    assert actual == expected == [
        Edge("urn:native-direct#Root", SUBCLASS_OF, "urn:native-direct#Side199")
    ]
    assert statistics.aggregate_equivalents == 1
    assert statistics.ingestion_counters["native_boundary_calls"] == 1


def test_deep_complement_restriction_recursion_uses_one_bounded_output_call() -> None:
    recursive = ":Leaf"
    for index in range(200):
        if index % 2 == 0:
            recursive = f"ObjectComplementOf({recursive})"
        else:
            recursive = f"ObjectSomeValuesFrom(:ignored{index:03d} {recursive})"
    view = _snapshot(
        "EquivalentClasses(:Root "
        f"ObjectIntersectionOf(:Direct {recursive}))"
    )
    expected = Projector().project(
        view,
        options=ProjectionOptions(backend="python", order="encounter"),
    )
    actual, statistics = prepare_native_encoded_direct(_lease(view)).compile_batch(
        bidirectional=False,
        max_edges=1,
        max_iri_bytes=1024 * 1024,
    )

    assert actual == expected == [
        Edge("urn:native-direct#Root", SUBCLASS_OF, "urn:native-direct#Direct")
    ]
    assert statistics.aggregate_equivalents == 1
    assert statistics.role_expansion_edges == 0
    assert statistics.ingestion_counters["native_boundary_calls"] == 1


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
    assert statistics.ignored_object_property_domains == 0
    assert statistics.ignored_object_property_ranges == 0
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


@pytest.mark.parametrize("only_taxonomy", [False, True])
def test_annotated_role_hashes_match_scalar_overwrites_and_chain_state(
    only_taxonomy: bool,
) -> None:
    view = _annotated_role_axiom_snapshot()
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
    exact = (
        [
            Edge("urn:native-direct#D", "urn:native-direct#p", "urn:native-direct#R"),
            Edge("urn:native-direct#D", "urn:native-direct#a", "urn:native-direct#R"),
            Edge("urn:native-direct#D", "urn:native-direct#c", "urn:native-direct#R"),
            Edge("urn:native-direct#R", "urn:native-direct#x", "urn:native-direct#D"),
        ]
        if only_taxonomy
        else [
            Edge(
                "urn:native-direct#Source",
                "urn:native-direct#p",
                "urn:native-direct#Target",
            ),
            Edge(
                "urn:native-direct#Source",
                "urn:native-direct#a",
                "urn:native-direct#Target",
            ),
            Edge(
                "urn:native-direct#Source",
                "urn:native-direct#c",
                "urn:native-direct#Target",
            ),
            Edge(
                "urn:native-direct#Target",
                "urn:native-direct#x",
                "urn:native-direct#Source",
            ),
            Edge("urn:native-direct#D", "urn:native-direct#p", "urn:native-direct#R"),
            Edge("urn:native-direct#D", "urn:native-direct#a", "urn:native-direct#R"),
            Edge("urn:native-direct#D", "urn:native-direct#c", "urn:native-direct#R"),
            Edge("urn:native-direct#R", "urn:native-direct#x", "urn:native-direct#D"),
        ]
    )
    assert actual == exact
    assert not any(edge.relation == "urn:native-direct#y" for edge in actual)
    assert statistics.roots == 8
    assert statistics.sub_object_properties == 3
    assert statistics.object_property_chains == 1
    assert statistics.inverse_object_properties == 2
    assert statistics.role_expansion_edges == (3 if only_taxonomy else 6)
    assert statistics.ingestion_counters["native_boundary_calls"] == 1


def test_asserted_taxonomy_preflights_annotated_roles_without_state_leakage() -> None:
    view = _annotated_role_axiom_snapshot()
    actual, statistics = prepare_native_encoded_direct(_lease(view)).compile_batch(
        bidirectional=True,
        max_edges=1,
        max_iri_bytes=1024 * 1024,
        asserted_taxonomy_only=True,
    )

    assert actual == []
    assert statistics.sub_object_properties == 3
    assert statistics.object_property_chains == 1
    assert statistics.inverse_object_properties == 2
    assert statistics.domain_range_edges == 0
    assert statistics.role_expansion_edges == 0


def test_role_annotation_value_hash_variants_match_scalar_overwrite_order() -> None:
    view = _annotated_role_value_snapshot()
    expected = Projector().project(
        view,
        options=ProjectionOptions(
            backend="python",
            duplicates="preserve",
            order="encounter",
        ),
    )
    actual, statistics = prepare_native_encoded_direct(_lease(view)).compile_batch(
        bidirectional=False,
        max_edges=len(expected),
        max_iri_bytes=1024 * 1024,
    )

    assert (
        actual
        == expected
        == [
            Edge("urn:native-direct#D", "urn:native-direct#p", "urn:native-direct#R"),
            Edge(
                "urn:native-direct#R",
                "urn:native-direct#langValue",
                "urn:native-direct#D",
            ),
        ]
    )
    assert statistics.inverse_object_properties == 6
    assert statistics.role_expansion_edges == 1
    assert statistics.ingestion_counters["native_boundary_calls"] == 1


@pytest.mark.parametrize("include_literals", [False, True])
def test_swrl_extensions_match_scalar_silent_semantics(include_literals: bool) -> None:
    view = _swrl_extension_snapshot()
    expected = Projector().project(
        view,
        options=ProjectionOptions(
            backend="python",
            include_literals=include_literals,
            duplicates="preserve",
            order="encounter",
        ),
    )
    actual, statistics = prepare_native_encoded_direct(_lease(view)).compile_batch(
        bidirectional=False,
        max_edges=len(expected),
        max_iri_bytes=1024 * 1024,
        include_literals=include_literals,
    )

    exact = [
        Edge(
            "urn:native-direct#TaxA",
            SUBCLASS_OF,
            "urn:native-direct#TaxB",
        )
    ]
    if include_literals:
        exact.append(
            Edge(
                "urn:native-direct#RuleOnly",
                "rdfs:label",
                "rule class",
            )
        )
    exact.extend(
        [
            Edge(
                "urn:native-direct#named",
                "urn:native-direct#u",
                "urn:native-direct#other",
            ),
            Edge("urn:native-direct#D", "urn:native-direct#p", "urn:native-direct#R"),
        ]
    )
    assert actual == expected == exact
    assert statistics.roots == 7
    assert statistics.swrl_rules == 2
    assert statistics.skipped_axioms == 0
    assert statistics.role_expansion_edges == 0
    assert statistics.ingestion_counters["native_boundary_calls"] == 1


def test_asserted_taxonomy_preflights_swrl_extensions_without_leakage() -> None:
    view = _swrl_extension_snapshot()
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
        include_literals=True,
    )

    assert actual == expected == [
        Edge("urn:native-direct#TaxA", SUBCLASS_OF, "urn:native-direct#TaxB"),
        Edge("urn:native-direct#TaxB", SUPERCLASS_OF, "urn:native-direct#TaxA"),
    ]
    assert statistics.swrl_rules == 2
    assert statistics.annotation_edges == 0
    assert statistics.domain_range_edges == 0
    assert statistics.role_expansion_edges == 0


def test_many_swrl_extensions_cross_one_zero_output_bounded_call() -> None:
    rules = " ".join(
        f'SWRLRule(Annotation(<urn:rule-meta-{index:03d}> "{index}") () ())'
        for index in range(250)
    )
    compiler = prepare_native_encoded_direct(_lease(_swrl_snapshot(rules)))
    edges, statistics = compiler.compile_batch(
        bidirectional=False,
        max_edges=1,
        max_iri_bytes=1024 * 1024,
    )

    assert edges == []
    assert statistics.roots == statistics.swrl_rules == 250
    assert statistics.skipped_axioms == 0
    assert statistics.ingestion_counters["native_boundary_calls"] == 1


def test_recursive_swrl_data_range_predicate_is_validated_and_silent() -> None:
    view = _swrl_snapshot(
        "SWRLRule((DataRangeAtom(DataComplementOf(DataUnionOf("
        "<http://www.w3.org/2001/XMLSchema#string> "
        "DataComplementOf(<http://www.w3.org/2001/XMLSchema#integer>))) "
        "Variable(:value))) ()) SubClassOf(:Before :After)"
    )
    expected = Projector().project(
        view,
        options=ProjectionOptions(backend="python", order="encounter"),
    )
    actual, statistics = prepare_native_encoded_direct(_lease(view)).compile_batch(
        bidirectional=False,
        max_edges=1,
        max_iri_bytes=1024 * 1024,
    )

    assert actual == expected == [
        Edge("urn:native-direct#Before", SUBCLASS_OF, "urn:native-direct#After")
    ]
    assert statistics.swrl_rules == 1
    assert statistics.skipped_axioms == 0
    assert statistics.role_expansion_edges == 0


def test_recursive_swrl_class_predicate_is_validated_and_silent() -> None:
    view = _swrl_snapshot(
        "SubClassOf(:Before :After) "
        "SWRLRule((ClassAtom(ObjectExactCardinality(1 :p "
        "ObjectComplementOf(ObjectIntersectionOf(:A ObjectComplementOf(:B)))) "
        "Variable(:x))) ())"
    )
    expected = Projector().project(
        view,
        options=ProjectionOptions(
            backend="python",
            bidirectional_taxonomy=True,
            order="encounter",
        ),
    )
    actual, statistics = prepare_native_encoded_direct(_lease(view)).compile_batch(
        bidirectional=True,
        max_edges=2,
        max_iri_bytes=1024 * 1024,
    )

    assert actual == expected == [
        Edge("urn:native-direct#Before", SUBCLASS_OF, "urn:native-direct#After"),
        Edge("urn:native-direct#After", SUPERCLASS_OF, "urn:native-direct#Before"),
    ]
    assert statistics.swrl_rules == 1
    assert statistics.skipped_axioms == 0
    assert statistics.role_expansion_edges == 0


@pytest.mark.parametrize("tag", range(140, 149))
def test_swrl_constructor_arity_corruption_fails_before_output(tag: int) -> None:
    lease = _lease(_swrl_extension_snapshot())
    buffers = lease.buffers
    tags = buffers["node_tags"]
    node_id = next(
        candidate
        for candidate in range(1, tags.nbytes // 2 + 1)
        if int.from_bytes(tags[(candidate - 1) * 2 : candidate * 2], "little") == tag
    )
    offsets = bytearray(buffers["node_field_offsets"])
    end_offset = node_id * 8
    end = int.from_bytes(offsets[end_offset : end_offset + 8], "little")
    offsets[end_offset : end_offset + 8] = (end - 1).to_bytes(8, "little")
    compiler = prepare_native_encoded_direct(
        _replace_buffers(
            lease,
            {"node_field_offsets": memoryview(bytes(offsets))},
        )
    )

    with pytest.raises(SnapshotCompatibilityError, match="arity"):
        compiler.compile_batch(
            bidirectional=True,
            max_edges=20,
            max_iri_bytes=1024 * 1024,
        )
    assert compiler.state == "failed"


@pytest.mark.parametrize("corruption", ["root-kind", "body-kind", "variable-target"])
def test_hostile_swrl_structure_fails_before_output(corruption: str) -> None:
    lease = _lease(_swrl_extension_snapshot())
    buffers = lease.buffers
    tags = buffers["node_tags"]

    def tagged_node(tag: int) -> int:
        return next(
            node_id
            for node_id in range(1, tags.nbytes // 2 + 1)
            if int.from_bytes(tags[(node_id - 1) * 2 : node_id * 2], "little") == tag
        )

    offsets = buffers["node_field_offsets"]

    def field_start(node_id: int) -> int:
        return int.from_bytes(
            offsets[(node_id - 1) * 8 : node_id * 8],
            "little",
        )

    if corruption == "root-kind":
        root_kinds = bytearray(buffers["root_kinds"])
        extension_index = root_kinds.index(3)
        root_kinds[extension_index] = 2
        replacements = {"root_kinds": memoryview(bytes(root_kinds))}
    elif corruption == "body-kind":
        field_kinds = bytearray(buffers["field_kinds"])
        field_kinds[field_start(tagged_node(148))] = 7
        replacements = {"field_kinds": memoryview(bytes(field_kinds))}
    else:
        field_values = bytearray(buffers["field_values"])
        variable_field = field_start(tagged_node(140))
        class_atom_field = field_start(tagged_node(141))
        class_id = int.from_bytes(
            field_values[class_atom_field * 8 : (class_atom_field + 1) * 8],
            "little",
        )
        field_values[variable_field * 8 : (variable_field + 1) * 8] = class_id.to_bytes(
            8,
            "little",
        )
        replacements = {"field_values": memoryview(bytes(field_values))}
    compiler = prepare_native_encoded_direct(_replace_buffers(lease, replacements))

    with pytest.raises(SnapshotCompatibilityError):
        compiler.compile_batch(
            bidirectional=True,
            max_edges=20,
            max_iri_bytes=1024 * 1024,
        )
    assert compiler.state == "failed"


@pytest.mark.parametrize("only_taxonomy", [False, True])
def test_inverse_restrictions_project_and_complex_domain_range_roots_are_ignored(
    only_taxonomy: bool,
) -> None:
    view = _inverse_restriction_domain_snapshot()
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
    assert len(actual) == (5 if only_taxonomy else 20)
    assert statistics.subclasses == 5
    assert statistics.restriction_subclasses == 4
    assert statistics.equivalents == statistics.aggregate_equivalents == 1
    assert statistics.object_property_domains == 3
    assert statistics.object_property_ranges == 3
    assert statistics.ignored_object_property_domains == 2
    assert statistics.ignored_object_property_ranges == 2
    assert statistics.domain_range_edges == 1
    assert statistics.role_expansion_edges == (2 if only_taxonomy else 12)
    assert not any(
        edge.source
        in {
            "urn:native-direct#IgnoredDomain",
            "urn:native-direct#ComplexDomain",
        }
        or edge.destination
        in {
            "urn:native-direct#IgnoredRange",
            "urn:native-direct#ComplexRange",
        }
        for edge in actual
    )


def test_asserted_taxonomy_preflights_inverse_restrictions_and_ignored_domains() -> None:
    view = _inverse_restriction_domain_snapshot()
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
    assert len(actual) == 2
    assert statistics.restriction_subclasses == 4
    assert statistics.object_property_domains == 3
    assert statistics.object_property_ranges == 3
    assert statistics.domain_range_edges == 0
    assert statistics.role_expansion_edges == 0


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
def test_annotated_data_property_families_are_state_neutral_skips(body: str) -> None:
    view = _snapshot(body)
    expected = Projector().project(
        view,
        options=ProjectionOptions(backend="python", order="encounter"),
    )
    compiler = prepare_native_encoded_direct(_lease(view))
    actual, statistics = compiler.compile_batch(
        bidirectional=False,
        max_edges=1,
        max_iri_bytes=1024 * 1024,
    )

    assert actual == expected == []
    assert statistics.skipped_axioms == 1
    assert compiler.state == "finished"


@pytest.mark.parametrize(
    ("body", "counter"),
    [
        (
            "DataPropertyRange(:dp DataComplementOf(DataComplementOf("
            "<http://www.w3.org/2001/XMLSchema#string>)))",
            "data_property_ranges",
        ),
        (
            "DatatypeDefinition(:custom DataIntersectionOf("
            "<http://www.w3.org/2001/XMLSchema#string> "
            "DataUnionOf(<http://www.w3.org/2001/XMLSchema#integer> "
            "<http://www.w3.org/2001/XMLSchema#decimal>)))",
            "datatype_definitions",
        ),
    ],
    ids=["range", "datatype-definition"],
)
def test_recursive_data_range_skipped_families_match_scalar(
    body: str,
    counter: str,
) -> None:
    view = _snapshot(body)
    expected = Projector().project(
        view,
        options=ProjectionOptions(backend="python", order="encounter"),
    )
    actual, statistics = prepare_native_encoded_direct(_lease(view)).compile_batch(
        bidirectional=False,
        max_edges=1,
        max_iri_bytes=1024 * 1024,
    )

    assert actual == expected == []
    assert getattr(statistics, counter) == 1
    assert statistics.skipped_axioms == 1
    assert statistics.role_expansion_edges == 0


@pytest.mark.parametrize(
    ("body", "counter"),
    [
        ('DataPropertyAssertion(:dp _:anonymous "value")', "data_property_assertions"),
        (
            'NegativeDataPropertyAssertion(:dp _:anonymous "value")',
            "negative_data_property_assertions",
        ),
    ],
    ids=["positive", "negative"],
)
def test_anonymous_data_assertions_are_state_neutral_skips(
    body: str,
    counter: str,
) -> None:
    view = _snapshot(body)
    expected = Projector().project(
        view,
        options=ProjectionOptions(backend="python", order="encounter"),
    )
    actual, statistics = prepare_native_encoded_direct(_lease(view)).compile_batch(
        bidirectional=False,
        max_edges=1,
        max_iri_bytes=1024 * 1024,
    )

    assert actual == expected == []
    assert getattr(statistics, counter) == 1
    assert statistics.anonymous_individuals == 1
    assert statistics.skipped_axioms == 1


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


@pytest.mark.parametrize("only_taxonomy", [False, True])
def test_annotation_metadata_roots_match_scalar_state_neutral_skips(
    only_taxonomy: bool,
) -> None:
    view = _annotation_metadata_root_snapshot()
    options = ProjectionOptions(
        backend="python",
        include_literals=True,
        only_taxonomy=only_taxonomy,
        duplicates="preserve",
        order="encounter",
    )
    expected = Projector().project(view, options=options)
    actual, statistics = prepare_native_encoded_direct(_lease(view)).compile_batch(
        bidirectional=False,
        max_edges=len(expected),
        max_iri_bytes=1024 * 1024,
        only_taxonomy=only_taxonomy,
        include_literals=True,
    )

    assert actual == expected
    assert actual == [
        Edge("urn:native-direct#A", SUBCLASS_OF, "urn:native-direct#B"),
        Edge("urn:native-direct#A", "rdfs:label", "label"),
        Edge("urn:native-direct#D", "urn:native-direct#p", "urn:native-direct#R"),
        Edge("urn:native-direct#D", "urn:native-direct#child", "urn:native-direct#R"),
    ]
    assert statistics.roots == 9
    assert statistics.ontology_annotations == 1
    assert statistics.sub_annotation_properties == 1
    assert statistics.annotation_property_domains == 1
    assert statistics.annotation_property_ranges == 1
    assert statistics.annotation_assertions == 1
    assert statistics.annotation_edges == 1
    assert statistics.skipped_axioms == 3
    assert statistics.domain_range_edges == 1
    assert statistics.role_expansion_edges == 1


def test_asserted_taxonomy_preflights_annotation_metadata_roots_without_leakage() -> None:
    view = _annotation_metadata_root_snapshot()
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
        include_literals=True,
    )

    assert actual == expected
    assert len(actual) == 2
    assert statistics.ontology_annotations == 1
    assert statistics.sub_annotation_properties == 1
    assert statistics.annotation_property_domains == 1
    assert statistics.annotation_property_ranges == 1
    assert statistics.annotation_edges == 0
    assert statistics.skipped_axioms == 0
    assert statistics.role_expansion_edges == 0


@pytest.mark.parametrize("only_taxonomy", [False, True])
def test_annotated_non_role_axiom_families_match_scalar(
    only_taxonomy: bool,
) -> None:
    view = _annotated_non_role_axiom_snapshot()
    options = ProjectionOptions(
        backend="python",
        only_taxonomy=only_taxonomy,
        duplicates="preserve",
        order="encounter",
    )
    expected = Projector().project(view, options=options)
    actual, statistics = prepare_native_encoded_direct(_lease(view)).compile_batch(
        bidirectional=False,
        max_edges=len(expected),
        max_iri_bytes=1024 * 1024,
        only_taxonomy=only_taxonomy,
    )

    assert actual == expected
    assert len(actual) == (7 if only_taxonomy else 11)
    assert statistics.roots == 31
    assert statistics.declarations == 1
    assert statistics.subclasses == 2
    assert statistics.restriction_subclasses == 1
    assert statistics.equivalents == 2
    assert statistics.aggregate_equivalents == 1
    assert statistics.disjoint_classes == 1
    assert statistics.disjoint_unions == 1
    assert statistics.class_assertions == 1
    assert statistics.object_property_assertions == 1
    assert statistics.negative_object_property_assertions == 1
    assert statistics.sub_object_properties == 1
    assert statistics.equivalent_object_properties == 1
    assert statistics.disjoint_object_properties == 1
    assert statistics.functional_object_properties == 1
    assert statistics.inverse_functional_object_properties == 1
    assert statistics.reflexive_object_properties == 1
    assert statistics.irreflexive_object_properties == 1
    assert statistics.symmetric_object_properties == 1
    assert statistics.asymmetric_object_properties == 1
    assert statistics.transitive_object_properties == 1
    assert statistics.sub_data_properties == 1
    assert statistics.equivalent_data_properties == 1
    assert statistics.disjoint_data_properties == 1
    assert statistics.data_property_domains == 1
    assert statistics.data_property_ranges == 1
    assert statistics.functional_data_properties == 1
    assert statistics.datatype_definitions == 1
    assert statistics.data_property_assertions == 1
    assert statistics.negative_data_property_assertions == 1
    assert statistics.skipped_axioms == 21
    assert statistics.domain_range_edges == 1
    assert statistics.role_expansion_edges == (1 if only_taxonomy else 3)


def test_asserted_taxonomy_preflights_annotated_non_role_axioms() -> None:
    view = _annotated_non_role_axiom_snapshot()
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
    assert len(actual) == 2
    assert statistics.roots == 31
    assert statistics.skipped_axioms == 0
    assert statistics.role_expansion_edges == 0


def test_many_annotated_non_role_roots_cross_one_zero_output_bounded_call() -> None:
    axioms = " ".join(
        "DataPropertyAssertion("
        f"Annotation(<urn:meta> _:metadata{index:03d}) "
        f':dp :individual{index:03d} "{index}")'
        for index in range(250)
    )
    compiler = prepare_native_encoded_direct(_lease(_snapshot(axioms)))
    edges, statistics = compiler.compile_batch(
        bidirectional=False,
        max_edges=1,
        max_iri_bytes=1024 * 1024,
    )

    assert edges == []
    assert statistics.roots == statistics.data_property_assertions == 250
    assert statistics.skipped_axioms == 250
    assert statistics.ingestion_counters["native_boundary_calls"] == 1


@pytest.mark.parametrize(
    ("target_tag", "annotation_delta"),
    [(61, 2), (75, 2), (76, 1), (115, 3)],
    ids=["subclass", "object-range", "object-characteristic", "data-assertion"],
)
def test_hostile_non_role_axiom_annotation_sets_fail_before_output(
    target_tag: int,
    annotation_delta: int,
) -> None:
    lease = _lease(_annotated_non_role_axiom_snapshot())
    buffers = lease.buffers
    tags = buffers["node_tags"]

    def tagged_node(tag: int) -> int:
        return next(
            node_id
            for node_id in range(1, tags.nbytes // 2 + 1)
            if int.from_bytes(tags[(node_id - 1) * 2 : node_id * 2], "little") == tag
        )

    offsets = buffers["node_field_offsets"]

    def field_start(node_id: int) -> int:
        return int.from_bytes(
            offsets[(node_id - 1) * 8 : node_id * 8],
            "little",
        )

    subclass_start = field_start(tagged_node(61))
    class_id = int.from_bytes(
        buffers["field_values"][subclass_start * 8 : (subclass_start + 1) * 8],
        "little",
    )
    annotation_field = field_start(tagged_node(target_tag)) + annotation_delta
    item_start = int.from_bytes(
        buffers["field_values"][annotation_field * 8 : (annotation_field + 1) * 8],
        "little",
    )
    item_values = bytearray(buffers["item_values"])
    item_values[item_start * 8 : (item_start + 1) * 8] = class_id.to_bytes(8, "little")
    compiler = prepare_native_encoded_direct(
        _replace_buffers(lease, {"item_values": memoryview(bytes(item_values))})
    )
    with pytest.raises(SnapshotCompatibilityError, match="annotation set item"):
        compiler.compile_batch(
            bidirectional=False,
            max_edges=20,
            max_iri_bytes=1024 * 1024,
        )
    assert compiler.state == "failed"


def test_many_annotation_metadata_roots_cross_one_zero_output_bounded_call() -> None:
    annotations = " ".join(
        f"Annotation(<urn:ontology-meta-{index:03d}> _:value{index:03d})"
        for index in range(63)
    )
    subproperties = " ".join(
        "SubAnnotationPropertyOf("
        f"Annotation(<urn:meta> _:subMeta{index:03d}) "
        f":sub{index:03d} :super{index:03d})"
        for index in range(63)
    )
    domains = " ".join(
        f"AnnotationPropertyDomain(:domain{index:03d} <urn:domain-{index:03d}>)"
        for index in range(62)
    )
    ranges = " ".join(
        f"AnnotationPropertyRange(:range{index:03d} <urn:range-{index:03d}>)"
        for index in range(62)
    )
    compiler = prepare_native_encoded_direct(
        _lease(_snapshot(f"{annotations} {subproperties} {domains} {ranges}"))
    )
    edges, statistics = compiler.compile_batch(
        bidirectional=False,
        max_edges=1,
        max_iri_bytes=1024 * 1024,
    )

    assert edges == []
    assert statistics.roots == 250
    assert statistics.ontology_annotations == 63
    assert statistics.sub_annotation_properties == 63
    assert statistics.annotation_property_domains == 62
    assert statistics.annotation_property_ranges == 62
    assert statistics.skipped_axioms == 187
    assert statistics.ingestion_counters["native_boundary_calls"] == 1


@pytest.mark.parametrize("corrupt_table", ["closure", "root-provenance"])
def test_cyclic_annotation_metadata_graph_fails_before_output(corrupt_table: str) -> None:
    view = _snapshot(
        "SubClassOf(:Before :After) "
        "AnnotationAssertion(Annotation(Annotation(<urn:inner> <urn:value>) "
        '<urn:outer> "metadata") '
        '<http://www.w3.org/2000/01/rdf-schema#label> :Before "label")'
    )
    closure = _lease(view)
    target = closure if corrupt_table == "closure" else _root_lease(view)
    tags = target.buffers["node_tags"]
    offsets = target.buffers["node_field_offsets"]
    field_values = target.buffers["field_values"]
    field_lengths = target.buffers["field_lengths"]

    cyclic_annotation = None
    cyclic_item_start = None
    for node_id in range(1, tags.nbytes // 2 + 1):
        tag = int.from_bytes(tags[(node_id - 1) * 2 : node_id * 2], "little")
        if tag != 5:
            continue
        field_start = int.from_bytes(
            offsets[(node_id - 1) * 8 : node_id * 8],
            "little",
        )
        annotation_set_field = field_start + 2
        length = int.from_bytes(
            field_lengths[
                annotation_set_field * 8 : (annotation_set_field + 1) * 8
            ],
            "little",
        )
        if length:
            cyclic_annotation = node_id
            cyclic_item_start = int.from_bytes(
                field_values[
                    annotation_set_field * 8 : (annotation_set_field + 1) * 8
                ],
                "little",
            )
            break
    assert cyclic_annotation is not None
    assert cyclic_item_start is not None

    item_values = bytearray(target.buffers["item_values"])
    item_values[cyclic_item_start * 8 : (cyclic_item_start + 1) * 8] = (
        cyclic_annotation.to_bytes(8, "little")
    )
    hostile = _replace_buffers(
        target,
        {"item_values": memoryview(bytes(item_values))},
    )
    compiler = prepare_native_encoded_direct(
        hostile if corrupt_table == "closure" else closure,
        root_annotation_lease=(hostile if corrupt_table == "root-provenance" else None),
    )

    with pytest.raises(
        SnapshotCompatibilityError,
        match="annotation metadata graph is cyclic",
    ):
        compiler.compile_batch(
            bidirectional=False,
            max_edges=2,
            max_iri_bytes=1024 * 1024,
        )
    assert compiler.state == "failed"


def test_annotation_edge_limit_and_anonymous_values_match_scalar_ids() -> None:
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

    anonymous_view = _snapshot(
        "Declaration(Class(:A)) AnnotationAssertion("
        "<http://www.w3.org/2000/01/rdf-schema#label> :A _:anonymous)"
    )
    expected = Projector().project(
        anonymous_view,
        options=ProjectionOptions(
            backend="python",
            include_literals=True,
            order="encounter",
        ),
    )
    actual, statistics = prepare_native_encoded_direct(
        _lease(anonymous_view)
    ).compile_batch(
        bidirectional=False,
        max_edges=1,
        max_iri_bytes=1024 * 1024,
        include_literals=True,
    )

    assert actual == expected == [
        Edge("urn:native-direct#A", "rdfs:label", "_:genid2147483648")
    ]
    assert statistics.anonymous_individuals == 1
    assert statistics.annotation_edges == 1


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


@pytest.mark.parametrize(
    ("target_tag", "field_delta", "replacement", "match"),
    [
        (121, 0, "class", "annotation-property"),
        (121, 1, "class", "annotation-property"),
        (122, 0, "class", "annotation-property"),
        (122, 1, "annotation-property", "IRI"),
        (123, 0, "class", "annotation-property"),
        (123, 1, "annotation-property", "IRI"),
        (121, 2, "annotation-item", "annotation set item"),
    ],
    ids=[
        "sub-property",
        "super-property",
        "domain-property",
        "domain-iri",
        "range-property",
        "range-iri",
        "annotation-item",
    ],
)
def test_hostile_annotation_metadata_axiom_fields_fail_before_output(
    target_tag: int,
    field_delta: int,
    replacement: str,
    match: str,
) -> None:
    lease = _lease(
        _snapshot(
            'Annotation(<urn:ontology-meta> "ontology") '
            'SubAnnotationPropertyOf(Annotation(<urn:meta> "sub") :sub :super) '
            'AnnotationPropertyDomain(Annotation(<urn:meta> "domain") '
            ':domain <urn:domain>) '
            'AnnotationPropertyRange(Annotation(<urn:meta> "range") '
            ':range <urn:range>) '
            "SubClassOf(:Before :After)"
        )
    )
    buffers = lease.buffers
    tags = buffers["node_tags"]

    def tagged_node(tag: int) -> int:
        return next(
            node_id
            for node_id in range(1, tags.nbytes // 2 + 1)
            if int.from_bytes(tags[(node_id - 1) * 2 : node_id * 2], "little") == tag
        )

    offsets = buffers["node_field_offsets"]

    def field_start(node_id: int) -> int:
        return int.from_bytes(
            offsets[(node_id - 1) * 8 : node_id * 8],
            "little",
        )

    subclass_start = field_start(tagged_node(61))
    class_id = int.from_bytes(
        buffers["field_values"][subclass_start * 8 : (subclass_start + 1) * 8],
        "little",
    )
    subproperty_start = field_start(tagged_node(121))
    annotation_property_id = int.from_bytes(
        buffers["field_values"][subproperty_start * 8 : (subproperty_start + 1) * 8],
        "little",
    )
    target_field = field_start(tagged_node(target_tag)) + field_delta
    if replacement == "annotation-item":
        item_start = int.from_bytes(
            buffers["field_values"][target_field * 8 : (target_field + 1) * 8],
            "little",
        )
        item_values = bytearray(buffers["item_values"])
        item_values[item_start * 8 : (item_start + 1) * 8] = tagged_node(4).to_bytes(
            8,
            "little",
        )
        replacements = {"item_values": memoryview(bytes(item_values))}
    else:
        replacement_id = class_id if replacement == "class" else annotation_property_id
        field_values = bytearray(buffers["field_values"])
        field_values[target_field * 8 : (target_field + 1) * 8] = replacement_id.to_bytes(
            8,
            "little",
        )
        replacements = {"field_values": memoryview(bytes(field_values))}
    compiler = prepare_native_encoded_direct(_replace_buffers(lease, replacements))
    with pytest.raises(SnapshotCompatibilityError, match=match):
        compiler.compile_batch(
            bidirectional=False,
            max_edges=10,
            max_iri_bytes=1024 * 1024,
        )
    assert compiler.state == "failed"


def test_hostile_ontology_annotation_root_kind_fails_before_output() -> None:
    lease = _lease(_annotation_metadata_root_snapshot())
    root_kinds = bytearray(lease.buffers["root_kinds"])
    ontology_index = root_kinds.index(1)
    root_kinds[ontology_index] = 2
    compiler = prepare_native_encoded_direct(
        _replace_buffers(lease, {"root_kinds": memoryview(bytes(root_kinds))})
    )
    with pytest.raises(SnapshotCompatibilityError, match="root kind"):
        compiler.compile_batch(
            bidirectional=False,
            max_edges=10,
            max_iri_bytes=1024 * 1024,
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


def test_unsupported_exporters_are_rejected_before_output() -> None:
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


def test_canonical_packed_bytes_exporter_is_retained_without_copy() -> None:
    view = _snapshot(
        "Declaration(Class(:A)) Declaration(Class(:B)) SubClassOf(:A :B) ClassAssertion(:A :i)"
    )
    direct = _lease(view)
    packed = _packed_lease(direct)
    packed_owner = next(iter(packed.buffers.values())).obj
    assert isinstance(packed_owner, bytes)
    assert len({id(buffer.obj) for buffer in packed.buffers.values()}) == 1
    assert sum(buffer.nbytes for buffer in packed.buffers.values()) == len(packed_owner)
    compiler = prepare_native_encoded_direct(packed)

    actual, statistics = compiler.compile_batch(
        bidirectional=True,
        max_edges=3,
        max_iri_bytes=1024 * 1024,
    )

    assert actual == [
        Edge("urn:native-direct#A", SUBCLASS_OF, "urn:native-direct#B"),
        Edge("urn:native-direct#B", SUPERCLASS_OF, "urn:native-direct#A"),
        Edge("urn:native-direct#i", RDF_TYPE, "urn:native-direct#A"),
    ]
    assert statistics.buffer_bytes == sum(value.nbytes for value in packed.buffers.values())
    assert statistics.ingestion_counters["encoded_staging_copy_bytes"] == 0
    assert statistics.ingestion_counters["encoded_zero_copy_buffers"] == 11


def test_noncanonical_packed_bytes_layouts_are_rejected_before_output() -> None:
    direct = _lease(_snapshot("SubClassOf(:A :B)"))

    with pytest.raises(
        NativeEncodedDirectUnsupported,
        match="do not exactly cover their shared bytes exporter",
    ):
        prepare_native_encoded_direct(_packed_lease(direct, prefix=b"gap"))

    reordered = _packed_lease(direct)
    first, second = ENCODED_DIRECT_BUFFER_ORDER[:2]
    hostile = _replace_buffers(
        reordered,
        {
            first: reordered.buffers[second],
            second: reordered.buffers[first],
        },
    )
    with pytest.raises(
        NativeEncodedDirectUnsupported,
        match="does not match the canonical packed bytes layout",
    ):
        prepare_native_encoded_direct(hostile)

    overlapping = _packed_lease(direct)
    shared_owner = next(iter(overlapping.buffers.values())).obj
    canonical_start = 0
    overlap_replacement: tuple[str, memoryview] | None = None
    for name in ENCODED_DIRECT_BUFFER_ORDER:
        canonical = overlapping.buffers[name]
        if canonical_start and canonical.nbytes:
            candidate = memoryview(shared_owner)[
                canonical_start - 1 : canonical_start - 1 + canonical.nbytes
            ]
            if bytes(candidate) != bytes(canonical):
                overlap_replacement = name, candidate
                break
        canonical_start += canonical.nbytes
    assert overlap_replacement is not None
    overlap_name, overlap_buffer = overlap_replacement
    with pytest.raises(
        NativeEncodedDirectUnsupported,
        match=f"encoded buffer {overlap_name} does not match the canonical packed bytes layout",
    ):
        prepare_native_encoded_direct(
            _replace_buffers(overlapping, {overlap_name: overlap_buffer})
        )

    with pytest.raises(
        NativeEncodedDirectUnsupported,
        match="not backed by exact immutable bytes",
    ):
        prepare_native_encoded_direct(_packed_lease(direct, mutable=True))


@pytest.mark.parametrize(
    "body",
    [
        "ClassAssertion(ObjectComplementOf(ObjectComplementOf(:A)) :i)",
    ],
    ids=["recursive-class"],
)
def test_recursive_complement_class_assertion_matches_scalar(body: str) -> None:
    view = _snapshot(f"SubClassOf(:Before :After) {body}")
    expected = Projector().project(
        view,
        options=ProjectionOptions(backend="python", order="encounter"),
    )
    actual, statistics = prepare_native_encoded_direct(_lease(view)).compile_batch(
        bidirectional=False,
        max_edges=1,
        max_iri_bytes=1024 * 1024,
    )

    assert actual == expected == [
        Edge("urn:native-direct#Before", SUBCLASS_OF, "urn:native-direct#After")
    ]
    assert statistics.ignored_class_assertions == 1
    assert statistics.role_expansion_edges == 0


@pytest.mark.parametrize(
    "body",
    [
        "ClassAssertion(ObjectComplementOf(ObjectIntersectionOf(:A :B)) :i)",
        "DisjointClasses(:A ObjectComplementOf(ObjectIntersectionOf(:B :C)))",
    ],
    ids=["class-assertion", "disjoint"],
)
def test_complement_wrapped_aggregate_axioms_match_scalar(body: str) -> None:
    view = _snapshot(f"SubClassOf(:Before :After) {body}")
    expected = Projector().project(
        view,
        options=ProjectionOptions(backend="python", order="encounter"),
    )
    actual, statistics = prepare_native_encoded_direct(_lease(view)).compile_batch(
        bidirectional=False,
        max_edges=1,
        max_iri_bytes=1024 * 1024,
    )

    assert actual == expected == [
        Edge("urn:native-direct#Before", SUBCLASS_OF, "urn:native-direct#After")
    ]
    assert statistics.role_expansion_edges == 0


@pytest.mark.parametrize(
    ("body", "counter"),
    [
        (
            "SubClassOf(:A ObjectIntersectionOf(:B ObjectUnionOf(:C :D)))",
            "ignored_subclasses",
        ),
        (
            "HasKey(ObjectIntersectionOf(:A ObjectUnionOf(:B :C)) () (:dp))",
            "has_keys",
        ),
        (
            "DataPropertyDomain(:dp ObjectIntersectionOf(:A ObjectUnionOf(:B :C)))",
            "data_property_domains",
        ),
        (
            "ObjectPropertyDomain(:p ObjectIntersectionOf(:A ObjectUnionOf(:B :C)))",
            "object_property_domains",
        ),
    ],
    ids=["subclass", "has-key", "data-domain", "object-domain"],
)
def test_nested_aggregate_nonprojecting_consumers_match_scalar(
    body: str,
    counter: str,
) -> None:
    view = _snapshot(f"SubClassOf(:Before :After) {body}")
    expected = Projector().project(
        view,
        options=ProjectionOptions(backend="python", order="encounter"),
    )
    actual, statistics = prepare_native_encoded_direct(_lease(view)).compile_batch(
        bidirectional=False,
        max_edges=1,
        max_iri_bytes=1024 * 1024,
    )

    assert actual == expected == [
        Edge("urn:native-direct#Before", SUBCLASS_OF, "urn:native-direct#After")
    ]
    assert getattr(statistics, counter) == 1
    assert statistics.role_expansion_edges == 0


def test_cyclic_recursive_class_expression_fails_before_output() -> None:
    lease = _lease(
        _snapshot(
            "SubClassOf(:Before :After) EquivalentClasses(:Root "
            "ObjectIntersectionOf(:Direct ObjectUnionOf(:InnerA :InnerB)))"
        )
    )
    tags = lease.buffers["node_tags"]
    aggregates = {
        int.from_bytes(tags[(node_id - 1) * 2 : node_id * 2], "little"): node_id
        for node_id in range(1, tags.nbytes // 2 + 1)
        if int.from_bytes(tags[(node_id - 1) * 2 : node_id * 2], "little") in {30, 31}
    }
    outer = aggregates[30]
    inner = aggregates[31]
    offsets = lease.buffers["node_field_offsets"]
    inner_field = int.from_bytes(offsets[(inner - 1) * 8 : inner * 8], "little")
    item_start = int.from_bytes(
        lease.buffers["field_values"][inner_field * 8 : (inner_field + 1) * 8],
        "little",
    )
    item_length = int.from_bytes(
        lease.buffers["field_lengths"][inner_field * 8 : (inner_field + 1) * 8],
        "little",
    )
    values = bytearray(lease.buffers["item_values"])
    members = [
        int.from_bytes(values[index * 8 : (index + 1) * 8], "little")
        for index in range(item_start, item_start + item_length)
    ]
    members[0] = outer
    members.sort()
    for index, node_id in enumerate(members, start=item_start):
        values[index * 8 : (index + 1) * 8] = node_id.to_bytes(8, "little")
    compiler = prepare_native_encoded_direct(
        _replace_buffers(lease, {"item_values": memoryview(bytes(values))})
    )

    with pytest.raises(
        SnapshotCompatibilityError,
        match="recursive class-expression graph is cyclic",
    ):
        compiler.compile_batch(
            bidirectional=False,
            max_edges=2,
            max_iri_bytes=1024 * 1024,
        )
    assert compiler.state == "failed"

    unflattened_tags = bytearray(tags)
    unflattened_tags[(inner - 1) * 2 : inner * 2] = (30).to_bytes(2, "little")
    unflattened = prepare_native_encoded_direct(
        _replace_buffers(
            lease,
            {"node_tags": memoryview(bytes(unflattened_tags))},
        )
    )
    with pytest.raises(SnapshotCompatibilityError, match="operands are not flattened"):
        unflattened.compile_batch(
            bidirectional=False,
            max_edges=2,
            max_iri_bytes=1024 * 1024,
        )
    assert unflattened.state == "failed"

    recursive_lease = _lease(
        _snapshot("ClassAssertion(ObjectComplementOf(ObjectComplementOf(:A)) :i)")
    )
    recursive_tags = recursive_lease.buffers["node_tags"]
    complement_ids = [
        node_id
        for node_id in range(1, recursive_tags.nbytes // 2 + 1)
        if int.from_bytes(
            recursive_tags[(node_id - 1) * 2 : node_id * 2],
            "little",
        )
        == 32
    ]
    assert len(complement_ids) == 2
    recursive_offsets = recursive_lease.buffers["node_field_offsets"]
    recursive_values = bytearray(recursive_lease.buffers["field_values"])

    def complement_field(node_id: int) -> int:
        return int.from_bytes(
            recursive_offsets[(node_id - 1) * 8 : node_id * 8],
            "little",
        )

    references = {
        node_id: int.from_bytes(
            recursive_values[
                complement_field(node_id) * 8 : (complement_field(node_id) + 1) * 8
            ],
            "little",
        )
        for node_id in complement_ids
    }
    outer_complement = next(
        node_id for node_id, child in references.items() if child in complement_ids
    )
    inner_complement = references[outer_complement]
    inner_field = complement_field(inner_complement)
    recursive_values[inner_field * 8 : (inner_field + 1) * 8] = outer_complement.to_bytes(
        8,
        "little",
    )
    recursive = prepare_native_encoded_direct(
        _replace_buffers(
            recursive_lease,
            {"field_values": memoryview(bytes(recursive_values))},
        )
    )
    with pytest.raises(
        SnapshotCompatibilityError,
        match="recursive class-expression graph is cyclic",
    ):
        recursive.compile_batch(
            bidirectional=False,
            max_edges=1,
            max_iri_bytes=1024 * 1024,
        )
    assert recursive.state == "failed"


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
def test_annotated_disjoint_class_families_are_state_neutral_skips(body: str) -> None:
    view = _snapshot(body)
    expected = Projector().project(
        view,
        options=ProjectionOptions(backend="python", order="encounter"),
    )
    compiler = prepare_native_encoded_direct(_lease(view))
    actual, statistics = compiler.compile_batch(
        bidirectional=False,
        max_edges=1,
        max_iri_bytes=1024 * 1024,
    )

    assert actual == expected == []
    assert statistics.skipped_axioms == 1
    assert compiler.state == "finished"


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
        f'Annotation(<urn:chain-meta{index:03d}> "value{index:03d}") '
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


@pytest.mark.parametrize("target_tag", [70, 73], ids=["subproperty", "inverse"])
def test_hostile_role_annotation_sets_fail_before_output(target_tag: int) -> None:
    lease = _lease(_annotated_role_axiom_snapshot())
    buffers = lease.buffers
    tags = buffers["node_tags"]

    def tagged_node(tag: int) -> int:
        return next(
            node_id
            for node_id in range(1, tags.nbytes // 2 + 1)
            if int.from_bytes(tags[(node_id - 1) * 2 : node_id * 2], "little") == tag
        )

    offsets = buffers["node_field_offsets"]

    def field_start(node_id: int) -> int:
        return int.from_bytes(
            offsets[(node_id - 1) * 8 : node_id * 8],
            "little",
        )

    subclass_start = field_start(tagged_node(61))
    class_id = int.from_bytes(
        buffers["field_values"][subclass_start * 8 : (subclass_start + 1) * 8],
        "little",
    )
    annotation_field = field_start(tagged_node(target_tag)) + 2
    item_start = int.from_bytes(
        buffers["field_values"][annotation_field * 8 : (annotation_field + 1) * 8],
        "little",
    )
    item_values = bytearray(buffers["item_values"])
    item_values[item_start * 8 : (item_start + 1) * 8] = class_id.to_bytes(8, "little")
    compiler = prepare_native_encoded_direct(
        _replace_buffers(lease, {"item_values": memoryview(bytes(item_values))})
    )

    with pytest.raises(SnapshotCompatibilityError, match="annotation set item"):
        compiler.compile_batch(
            bidirectional=False,
            max_edges=20,
            max_iri_bytes=1024 * 1024,
        )
    assert compiler.state == "failed"


@pytest.mark.parametrize(
    "body",
    [
        "SubObjectPropertyOf(Annotation(<urn:meta> _:anonymous) :p :q)",
        "SubObjectPropertyOf(Annotation(<urn:meta> _:anonymous) ObjectPropertyChain(:p :q) :r)",
        "InverseObjectProperties(Annotation(<urn:meta> _:anonymous) :p :q)",
    ],
    ids=["subproperty", "property-chain", "inverse"],
)
def test_unhashable_anonymous_role_annotations_fallback_before_output(body: str) -> None:
    compiler = prepare_native_encoded_direct(
        _lease(_snapshot(f"SubClassOf(:Before :After) {body}"))
    )

    with pytest.raises(
        NativeEncodedDirectUnsupported,
        match="cannot reproduce scalar hashing",
    ):
        compiler.compile_batch(
            bidirectional=True,
            max_edges=20,
            max_iri_bytes=1024 * 1024,
        )
    assert compiler.state == "failed"


def test_valid_utf8_anonymous_role_hashes_match_encoded_scalar_order() -> None:
    view = _snapshot(
        "InverseObjectProperties(Annotation(<urn:meta> _:one) :p :one) "
        "InverseObjectProperties(Annotation(<urn:meta> _:two) :p :two) "
        "InverseObjectProperties(Annotation(<urn:meta> _:three) :p :three) "
        "ObjectPropertyDomain(:p :D) ObjectPropertyRange(:p :R)"
    )
    lease = _lease(view)
    buffers = lease.buffers
    tags = buffers["node_tags"]
    offsets = buffers["node_field_offsets"]
    anonymous_ids = [
        node_id
        for node_id in range(1, tags.nbytes // 2 + 1)
        if int.from_bytes(tags[(node_id - 1) * 2 : node_id * 2], "little") == 3
    ]
    replacements = [b"a" * 32, b"z" * 32, "🙂".encode() * 8]
    scalar_bytes = bytearray(buffers["scalar_bytes"])
    for node_id, replacement in zip(anonymous_ids, replacements, strict=True):
        field_start = int.from_bytes(
            offsets[(node_id - 1) * 8 : node_id * 8],
            "little",
        )
        local_field = field_start + 1
        scalar_start = int.from_bytes(
            buffers["field_values"][local_field * 8 : (local_field + 1) * 8],
            "little",
        )
        scalar_length = int.from_bytes(
            buffers["field_lengths"][local_field * 8 : (local_field + 1) * 8],
            "little",
        )
        assert scalar_length == len(replacement)
        scalar_bytes[scalar_start : scalar_start + scalar_length] = replacement
    mutated = _replace_buffers(
        lease,
        {"scalar_bytes": memoryview(bytes(scalar_bytes))},
    )
    options = ProjectionOptions(
        backend="native",
        duplicates="preserve",
        order="encounter",
    )
    reference, negotiation, counters = prepare_encoded_subset_compilation(
        view,
        options,
        EncodedNegotiation("encoded-native", lease=mutated),
        batch_edges=10,
    )

    assert reference is not None
    assert negotiation.path == "encoded-native"
    assert counters is not None
    expected = list(reference.iter_raw_edges())
    actual, statistics = prepare_native_encoded_direct(mutated).compile_batch(
        bidirectional=False,
        max_edges=len(expected),
        max_iri_bytes=1024 * 1024,
    )

    assert (
        actual
        == expected
        == [
            Edge("urn:native-direct#D", "urn:native-direct#p", "urn:native-direct#R"),
            Edge("urn:native-direct#R", "urn:native-direct#one", "urn:native-direct#D"),
        ]
    )
    assert statistics.inverse_object_properties == 3
    assert statistics.role_expansion_edges == 1


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


@pytest.mark.parametrize("only_taxonomy", [False, True])
def test_nonprojecting_object_expressions_match_scalar_state_neutral_ignores(
    only_taxonomy: bool,
) -> None:
    view = _nonprojecting_class_snapshot()
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
    assert len(actual) == 4
    assert not any(
        edge.relation in {"urn:native-direct#p", "urn:native-direct#q"} for edge in actual
    )
    assert statistics.roots == 13
    assert statistics.subclasses == 6
    assert statistics.restriction_subclasses == 0
    assert statistics.ignored_subclasses == 5
    assert statistics.class_assertions == 4
    assert statistics.ignored_class_assertions == 3
    assert statistics.skipped_axioms == 0
    assert statistics.role_expansion_edges == 1


def test_asserted_taxonomy_preflights_nonprojecting_expressions_without_leakage() -> None:
    view = _nonprojecting_class_snapshot()
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
    assert len(actual) == 2
    assert statistics.ignored_subclasses == 5
    assert statistics.ignored_class_assertions == 3
    assert statistics.role_expansion_edges == 0


@pytest.mark.parametrize("only_taxonomy", [False, True])
def test_bounded_data_class_expressions_match_scalar_state_neutral_ignores(
    only_taxonomy: bool,
) -> None:
    view = _data_class_expression_snapshot()
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
    assert len(actual) == 4
    assert not any(edge.relation == "urn:native-direct#dp" for edge in actual)
    assert statistics.roots == 14
    assert statistics.subclasses == 8
    assert statistics.restriction_subclasses == 0
    assert statistics.ignored_subclasses == 7
    assert statistics.class_assertions == 3
    assert statistics.ignored_class_assertions == 2
    assert statistics.skipped_axioms == 0
    assert statistics.role_expansion_edges == 1


def test_asserted_taxonomy_preflights_bounded_data_class_expressions() -> None:
    view = _data_class_expression_snapshot()
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
    assert len(actual) == 2
    assert statistics.ignored_subclasses == 7
    assert statistics.ignored_class_assertions == 2
    assert statistics.role_expansion_edges == 0


@pytest.mark.parametrize("only_taxonomy", [False, True])
def test_bounded_expressions_extend_ignored_and_skipped_axiom_families(
    only_taxonomy: bool,
) -> None:
    view = _expanded_expression_axiom_snapshot()
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
    assert len(actual) == 5
    assert statistics.roots == 16
    assert statistics.subclasses == 2
    assert statistics.ignored_subclasses == 1
    assert statistics.equivalents == 2
    assert statistics.aggregate_equivalents == 1
    assert statistics.class_assertions == 3
    assert statistics.ignored_class_assertions == 2
    assert statistics.disjoint_classes == 1
    assert statistics.disjoint_unions == 1
    assert statistics.has_keys == 1
    assert statistics.data_property_domains == 1
    assert statistics.data_property_ranges == 1
    assert statistics.datatype_definitions == 1
    assert statistics.skipped_axioms == 6
    assert statistics.role_expansion_edges == 1


def test_asserted_taxonomy_preflights_expanded_expression_axiom_families() -> None:
    view = _expanded_expression_axiom_snapshot()
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
    assert len(actual) == 2
    assert statistics.aggregate_equivalents == 1
    assert statistics.ignored_subclasses == 1
    assert statistics.ignored_class_assertions == 2
    assert statistics.skipped_axioms == 0
    assert statistics.role_expansion_edges == 0


def test_many_nonprojecting_roots_cross_one_zero_output_bounded_call() -> None:
    axioms = " ".join(
        (
            f"SubClassOf(:A{index:03d} ObjectExactCardinality("
            f"{index} ObjectInverseOf(:p{index:03d}) :B{index:03d}))"
            if index % 2 == 0
            else "ClassAssertion("
            f"ObjectComplementOf(ObjectOneOf(:member{index:03d} "
            f"_:anonymous{index:03d})) :i{index:03d})"
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
    assert statistics.subclasses == statistics.ignored_subclasses == 125
    assert statistics.class_assertions == statistics.ignored_class_assertions == 125
    assert statistics.skipped_axioms == 0
    assert statistics.ingestion_counters["native_boundary_calls"] == 1


def test_many_data_class_expression_roots_cross_one_zero_output_bounded_call() -> None:
    axioms = " ".join(
        (
            f"SubClassOf(:A{index:03d} DataExactCardinality("
            f"{index} :p{index:03d} DataOneOf(\"{index}\")))"
            if index % 2 == 0
            else "ClassAssertion(ObjectComplementOf("
            f"DataHasValue(:p{index:03d} \"value{index:03d}\"@en)) :i{index:03d})"
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
    assert statistics.subclasses == statistics.ignored_subclasses == 125
    assert statistics.class_assertions == statistics.ignored_class_assertions == 125
    assert statistics.skipped_axioms == 0
    assert statistics.ingestion_counters["native_boundary_calls"] == 1


def test_many_expanded_expression_axiom_roots_cross_one_zero_output_bounded_call() -> None:
    xsd_string = "<http://www.w3.org/2001/XMLSchema#string>"
    axioms: list[str] = []
    for index in range(250):
        family = index % 5
        if family == 0:
            axioms.append(
                f'DisjointClasses(DataHasValue(:dp{index:03d} "{index}") '
                f"ObjectOneOf(:i{index:03d}))"
            )
        elif family == 1:
            axioms.append(
                f"HasKey(DataSomeValuesFrom(:dp{index:03d} {xsd_string}) "
                f"() (:dp{index:03d}))"
            )
        elif family == 2:
            axioms.append(
                f"DataPropertyDomain(:dp{index:03d} ObjectComplementOf("
                f"ObjectSomeValuesFrom(:op{index:03d} :F{index:03d})))"
            )
        elif family == 3:
            axioms.append(
                f"DataPropertyRange(:dp{index:03d} DataUnionOf({xsd_string} "
                f'DataOneOf("{index}")))'
            )
        else:
            axioms.append(
                f"DatatypeDefinition(:dt{index:03d} "
                f'DataComplementOf(DataOneOf("{index}")))'
            )
    compiler = prepare_native_encoded_direct(_lease(_snapshot(" ".join(axioms))))
    edges, statistics = compiler.compile_batch(
        bidirectional=False,
        max_edges=1,
        max_iri_bytes=1024 * 1024,
    )

    assert edges == []
    assert statistics.roots == 250
    assert statistics.disjoint_classes == 50
    assert statistics.has_keys == 50
    assert statistics.data_property_domains == 50
    assert statistics.data_property_ranges == 50
    assert statistics.datatype_definitions == 50
    assert statistics.skipped_axioms == 250
    assert statistics.role_expansion_edges == 0
    assert statistics.ingestion_counters["native_boundary_calls"] == 1


@pytest.mark.parametrize(
    ("target_tag", "field_delta", "collection", "match"),
    [
        (33, 0, True, "individual"),
        (36, 0, False, "object-property"),
        (36, 1, False, "individual"),
        (37, 0, False, "object-property"),
        (40, 1, False, "object-property"),
    ],
    ids=[
        "one-of-member",
        "has-value-property",
        "has-value-member",
        "has-self-property",
        "exact-property",
    ],
)
def test_hostile_nonprojecting_expression_rows_fail_before_output(
    target_tag: int,
    field_delta: int,
    collection: bool,
    match: str,
) -> None:
    lease = _lease(
        _snapshot(
            "SubClassOf(:Before :After) SubClassOf(:A ObjectOneOf(:member _:one)) "
            "SubClassOf(:B ObjectHasValue(ObjectInverseOf(:p) _:value)) "
            "SubClassOf(:C ObjectExactCardinality(2 ObjectInverseOf(:exact) :F)) "
            "ClassAssertion(ObjectHasSelf(ObjectInverseOf(:q)) :i) "
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

    declaration_field = field_start(tagged_nodes(60)[0])
    wrong_class = int.from_bytes(
        buffers["field_values"][declaration_field * 8 : (declaration_field + 1) * 8],
        "little",
    )
    target_field = field_start(tagged_nodes(target_tag)[0]) + field_delta
    if collection:
        item_start = int.from_bytes(
            buffers["field_values"][target_field * 8 : (target_field + 1) * 8],
            "little",
        )
        item_length = int.from_bytes(
            buffers["field_lengths"][target_field * 8 : (target_field + 1) * 8],
            "little",
        )
        item_values = bytearray(buffers["item_values"])
        values = [
            int.from_bytes(item_values[index * 8 : (index + 1) * 8], "little")
            for index in range(item_start, item_start + item_length)
        ]
        values[0] = wrong_class
        values.sort()
        for index, value in enumerate(values, start=item_start):
            item_values[index * 8 : (index + 1) * 8] = value.to_bytes(8, "little")
        replacements = {"item_values": memoryview(bytes(item_values))}
    else:
        field_values = bytearray(buffers["field_values"])
        field_values[target_field * 8 : (target_field + 1) * 8] = wrong_class.to_bytes(
            8,
            "little",
        )
        replacements = {"field_values": memoryview(bytes(field_values))}
    compiler = prepare_native_encoded_direct(_replace_buffers(lease, replacements))
    with pytest.raises(SnapshotCompatibilityError, match=match):
        compiler.compile_batch(
            bidirectional=False,
            max_edges=10,
            max_iri_bytes=1024 * 1024,
        )
    assert compiler.state == "failed"


@pytest.mark.parametrize(
    ("target_tag", "field_delta", "collection", "match"),
    [
        (41, 0, True, "data-property"),
        (43, 0, False, "data-property"),
        (43, 1, False, "Literal"),
        (46, 2, False, "datatype"),
        (20, 0, False, "IRI"),
        (25, 1, True, "facet-restriction"),
    ],
    ids=[
        "quantifier-property-sequence",
        "has-value-property",
        "has-value-literal",
        "cardinality-filler",
        "facet-iri",
        "datatype-restriction-facet",
    ],
)
def test_hostile_data_class_expression_rows_fail_before_output(
    target_tag: int,
    field_delta: int,
    collection: bool,
    match: str,
) -> None:
    lease = _lease(
        _snapshot(
            "SubClassOf(:Before :After) "
            "SubClassOf(:A DataSomeValuesFrom(:dp :dq "
            "<http://www.w3.org/2001/XMLSchema#string>)) "
            'SubClassOf(:B DataHasValue(:dp "value")) '
            "SubClassOf(:C DataExactCardinality(2 :dp "
            "<http://www.w3.org/2001/XMLSchema#string>)) "
            "SubClassOf(:D DataMinCardinality(1 :dp "
            "DatatypeRestriction(<http://www.w3.org/2001/XMLSchema#integer> "
            '<http://www.w3.org/2001/XMLSchema#minInclusive> "0"^^'
            "<http://www.w3.org/2001/XMLSchema#integer>))) "
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

    declaration_field = field_start(tagged_nodes(60)[0])
    wrong_class = int.from_bytes(
        buffers["field_values"][declaration_field * 8 : (declaration_field + 1) * 8],
        "little",
    )
    target_field = field_start(tagged_nodes(target_tag)[0]) + field_delta
    if collection:
        item_start = int.from_bytes(
            buffers["field_values"][target_field * 8 : (target_field + 1) * 8],
            "little",
        )
        item_values = bytearray(buffers["item_values"])
        item_values[item_start * 8 : (item_start + 1) * 8] = wrong_class.to_bytes(
            8,
            "little",
        )
        replacements = {"item_values": memoryview(bytes(item_values))}
    else:
        field_values = bytearray(buffers["field_values"])
        field_values[target_field * 8 : (target_field + 1) * 8] = wrong_class.to_bytes(
            8,
            "little",
        )
        replacements = {"field_values": memoryview(bytes(field_values))}
    compiler = prepare_native_encoded_direct(_replace_buffers(lease, replacements))
    with pytest.raises(SnapshotCompatibilityError, match=match):
        compiler.compile_batch(
            bidirectional=False,
            max_edges=10,
            max_iri_bytes=1024 * 1024,
        )
    assert compiler.state == "failed"


@pytest.mark.parametrize(
    ("target_tag", "field_delta", "replacement", "match"),
    [
        (93, 1, "data-property", "class"),
        (94, 1, "class", "datatype"),
        (100, 1, "class", "datatype"),
        (101, 0, "data-property", "class"),
    ],
    ids=["data-domain", "data-range", "datatype-definition", "has-key-class"],
)
def test_hostile_expanded_expression_axiom_fields_fail_before_output(
    target_tag: int,
    field_delta: int,
    replacement: str,
    match: str,
) -> None:
    lease = _lease(
        _snapshot(
            "SubClassOf(:Before :After) DataPropertyDomain(:dp :A) "
            "DataPropertyRange(:dp <http://www.w3.org/2001/XMLSchema#string>) "
            "DatatypeDefinition(:dt <http://www.w3.org/2001/XMLSchema#string>) "
            "HasKey(:A () (:dp))"
        )
    )
    buffers = lease.buffers
    tags = buffers["node_tags"]

    def tagged_node(tag: int) -> int:
        return next(
            node_id
            for node_id in range(1, tags.nbytes // 2 + 1)
            if int.from_bytes(tags[(node_id - 1) * 2 : node_id * 2], "little") == tag
        )

    offsets = buffers["node_field_offsets"]

    def field_start(node_id: int) -> int:
        return int.from_bytes(
            offsets[(node_id - 1) * 8 : node_id * 8],
            "little",
        )

    domain_start = field_start(tagged_node(93))
    replacement_delta = 0 if replacement == "data-property" else 1
    replacement_id = int.from_bytes(
        buffers["field_values"][
            (domain_start + replacement_delta) * 8 : (domain_start + replacement_delta + 1) * 8
        ],
        "little",
    )
    target_field = field_start(tagged_node(target_tag)) + field_delta
    field_values = bytearray(buffers["field_values"])
    field_values[target_field * 8 : (target_field + 1) * 8] = replacement_id.to_bytes(
        8,
        "little",
    )
    compiler = prepare_native_encoded_direct(
        _replace_buffers(lease, {"field_values": memoryview(bytes(field_values))})
    )
    with pytest.raises(SnapshotCompatibilityError, match=match):
        compiler.compile_batch(
            bidirectional=False,
            max_edges=10,
            max_iri_bytes=1024 * 1024,
        )
    assert compiler.state == "failed"


@pytest.mark.parametrize(
    ("target_tag", "field_delta", "replacement", "match"),
    [
        (10, 0, "class", "object-property"),
        (74, 1, "property", "class"),
        (75, 0, "class", "object-property"),
        (75, 1, "property", "class"),
    ],
    ids=["inverse-inner", "domain-class", "range-property", "range-class"],
)
def test_hostile_inverse_restriction_domain_range_fields_fail_before_output(
    target_tag: int,
    field_delta: int,
    replacement: str,
    match: str,
) -> None:
    lease = _lease(
        _snapshot(
            "SubClassOf(:Before :After) "
            "ObjectPropertyDomain(ObjectInverseOf(:p) :D) "
            "ObjectPropertyRange(:p ObjectIntersectionOf(:R "
            'DataHasValue(:dp "value")))'
        )
    )
    buffers = lease.buffers
    tags = buffers["node_tags"]

    def tagged_node(tag: int) -> int:
        return next(
            node_id
            for node_id in range(1, tags.nbytes // 2 + 1)
            if int.from_bytes(tags[(node_id - 1) * 2 : node_id * 2], "little") == tag
        )

    offsets = buffers["node_field_offsets"]

    def field_start(node_id: int) -> int:
        return int.from_bytes(
            offsets[(node_id - 1) * 8 : node_id * 8],
            "little",
        )

    inverse_start = field_start(tagged_node(10))
    domain_start = field_start(tagged_node(74))
    replacement_field = inverse_start if replacement == "property" else domain_start + 1
    replacement_id = int.from_bytes(
        buffers["field_values"][replacement_field * 8 : (replacement_field + 1) * 8],
        "little",
    )
    target_field = field_start(tagged_node(target_tag)) + field_delta
    field_values = bytearray(buffers["field_values"])
    field_values[target_field * 8 : (target_field + 1) * 8] = replacement_id.to_bytes(
        8,
        "little",
    )
    compiler = prepare_native_encoded_direct(
        _replace_buffers(lease, {"field_values": memoryview(bytes(field_values))})
    )
    with pytest.raises(SnapshotCompatibilityError, match=match):
        compiler.compile_batch(
            bidirectional=False,
            max_edges=10,
            max_iri_bytes=1024 * 1024,
        )
    assert compiler.state == "failed"


def test_hostile_complement_operand_fails_before_output() -> None:
    lease = _lease(
        _snapshot(
            "SubClassOf(:Before :After) "
            "ClassAssertion(ObjectComplementOf(ObjectOneOf(:member _:one)) :i)"
        )
    )
    tags = lease.buffers["node_tags"]

    def tagged_node(tag: int) -> int:
        return next(
            node_id
            for node_id in range(1, tags.nbytes // 2 + 1)
            if int.from_bytes(tags[(node_id - 1) * 2 : node_id * 2], "little") == tag
        )

    offsets = lease.buffers["node_field_offsets"]

    def field_start(node_id: int) -> int:
        return int.from_bytes(
            offsets[(node_id - 1) * 8 : node_id * 8],
            "little",
        )

    assertion_start = field_start(tagged_node(112))
    individual_id = int.from_bytes(
        lease.buffers["field_values"][(assertion_start + 1) * 8 : (assertion_start + 2) * 8],
        "little",
    )
    complement_start = field_start(tagged_node(32))
    field_values = bytearray(lease.buffers["field_values"])
    field_values[complement_start * 8 : (complement_start + 1) * 8] = individual_id.to_bytes(
        8,
        "little",
    )
    compiler = prepare_native_encoded_direct(
        _replace_buffers(lease, {"field_values": memoryview(bytes(field_values))})
    )
    with pytest.raises(SnapshotCompatibilityError, match="class expression"):
        compiler.compile_batch(
            bidirectional=False,
            max_edges=10,
            max_iri_bytes=1024 * 1024,
        )
    assert compiler.state == "failed"


@pytest.mark.parametrize(
    "body",
    [
        "SubClassOf(:A ObjectExactCardinality(256 ObjectInverseOf(:p) :B))",
        "SubClassOf(:A DataExactCardinality(256 :p "
        "<http://www.w3.org/2001/XMLSchema#integer>))",
    ],
    ids=["object", "data"],
)
def test_nonminimal_exact_cardinality_fails_before_output(body: str) -> None:
    lease = _lease(_snapshot(body))
    scalar = bytearray(lease.buffers["scalar_bytes"])
    offset = scalar.index(b"\x00\x01")
    scalar[offset + 1] = 0
    compiler = prepare_native_encoded_direct(
        _replace_buffers(lease, {"scalar_bytes": memoryview(bytes(scalar))})
    )
    with pytest.raises(SnapshotCompatibilityError, match="minimally encoded"):
        compiler.compile_batch(
            bidirectional=False,
            max_edges=10,
            max_iri_bytes=1024 * 1024,
        )
    assert compiler.state == "failed"


@pytest.mark.parametrize(
    "body",
    [
        "SubClassOf(:A ObjectComplementOf(ObjectComplementOf(:B)))",
        "SubClassOf(:A ObjectExactCardinality(1 :p ObjectComplementOf(:B)))",
    ],
    ids=["nested-complement", "complex-exact-filler"],
)
def test_recursive_or_exact_nonprojecting_variants_match_scalar(body: str) -> None:
    view = _snapshot(f"SubClassOf(:Before :After) {body}")
    expected = Projector().project(
        view,
        options=ProjectionOptions(backend="python", order="encounter"),
    )
    actual, statistics = prepare_native_encoded_direct(_lease(view)).compile_batch(
        bidirectional=False,
        max_edges=1,
        max_iri_bytes=1024 * 1024,
    )

    assert actual == expected == [
        Edge("urn:native-direct#Before", SUBCLASS_OF, "urn:native-direct#After")
    ]
    assert statistics.subclasses == 2
    assert statistics.ignored_subclasses == 1
    assert statistics.role_expansion_edges == 0


@pytest.mark.parametrize(
    "body",
    [
        "SubClassOf(:A DataSomeValuesFrom(:dp "
        "DataComplementOf(DataComplementOf("
        "<http://www.w3.org/2001/XMLSchema#string>))))",
        "SubClassOf(:A DataMinCardinality(1 :dp "
        "DataIntersectionOf(<http://www.w3.org/2001/XMLSchema#string> "
        "DataUnionOf(<http://www.w3.org/2001/XMLSchema#integer> "
        "<http://www.w3.org/2001/XMLSchema#decimal>))))",
    ],
    ids=["nested-data-complement", "nested-data-aggregate"],
)
@pytest.mark.parametrize("mode", ["normal", "only-taxonomy", "asserted-taxonomy"])
def test_recursive_data_range_variants_match_scalar_state_neutrality(
    body: str,
    mode: str,
) -> None:
    view = _snapshot(f"SubClassOf(:Before :After) {body}")
    if mode == "asserted-taxonomy":
        expected = list(
            iter_asserted_taxonomy(
                view,
                bidirectional=False,
                duplicates="preserve",
                order="encounter",
            )
        )
    else:
        expected = Projector().project(
            view,
            options=ProjectionOptions(
                backend="python",
                only_taxonomy=mode == "only-taxonomy",
                order="encounter",
            ),
        )
    actual, statistics = prepare_native_encoded_direct(_lease(view)).compile_batch(
        bidirectional=False,
        max_edges=1,
        max_iri_bytes=1024 * 1024,
        only_taxonomy=mode == "only-taxonomy",
        asserted_taxonomy_only=mode == "asserted-taxonomy",
    )

    assert actual == expected == [
        Edge("urn:native-direct#Before", SUBCLASS_OF, "urn:native-direct#After")
    ]
    assert statistics.subclasses == 2
    assert statistics.ignored_subclasses == 1
    assert statistics.role_expansion_edges == 0


def test_deep_recursive_data_range_uses_one_bounded_zero_output_call() -> None:
    data_range = "<http://www.w3.org/2001/XMLSchema#string>"
    for _index in range(200):
        data_range = f"DataComplementOf({data_range})"
    compiler = prepare_native_encoded_direct(
        _lease(_snapshot(f"DataPropertyRange(:dp {data_range})"))
    )
    edges, statistics = compiler.compile_batch(
        bidirectional=False,
        max_edges=1,
        max_iri_bytes=1024 * 1024,
    )

    assert edges == []
    assert statistics.data_property_ranges == 1
    assert statistics.skipped_axioms == 1
    assert statistics.ingestion_counters["native_boundary_calls"] == 1


def test_cyclic_recursive_data_range_fails_before_output() -> None:
    lease = _lease(
        _snapshot(
            "SubClassOf(:Before :After) DataPropertyRange(:dp "
            "DataComplementOf(DataComplementOf("
            "<http://www.w3.org/2001/XMLSchema#string>)))"
        )
    )
    tags = lease.buffers["node_tags"]
    complement_ids = [
        node_id
        for node_id in range(1, tags.nbytes // 2 + 1)
        if int.from_bytes(tags[(node_id - 1) * 2 : node_id * 2], "little") == 23
    ]
    assert len(complement_ids) == 2
    offsets = lease.buffers["node_field_offsets"]
    values = bytearray(lease.buffers["field_values"])

    def field_start(node_id: int) -> int:
        return int.from_bytes(
            offsets[(node_id - 1) * 8 : node_id * 8],
            "little",
        )

    references = {
        node_id: int.from_bytes(
            values[field_start(node_id) * 8 : (field_start(node_id) + 1) * 8],
            "little",
        )
        for node_id in complement_ids
    }
    outer = next(node_id for node_id, child in references.items() if child in complement_ids)
    inner = references[outer]
    inner_field = field_start(inner)
    values[inner_field * 8 : (inner_field + 1) * 8] = outer.to_bytes(8, "little")
    compiler = prepare_native_encoded_direct(
        _replace_buffers(
            lease,
            {"field_values": memoryview(bytes(values))},
        )
    )

    with pytest.raises(SnapshotCompatibilityError, match="data-range graph is cyclic"):
        compiler.compile_batch(
            bidirectional=False,
            max_edges=2,
            max_iri_bytes=1024 * 1024,
        )
    assert compiler.state == "failed"


@pytest.mark.parametrize(
    "body",
    [
        "SubClassOf(:A ObjectSomeValuesFrom(ObjectInverseOf(:p) "
        "ObjectComplementOf(:B)))",
        "SubClassOf(:A ObjectSomeValuesFrom(:p ObjectIntersectionOf(:B :C)))",
        "SubClassOf(ObjectSomeValuesFrom(:p :A) ObjectAllValuesFrom(:q :B))",
    ],
    ids=[
        "inverse-complex-filler",
        "complex-filler",
        "restriction-pair",
    ],
)
def test_complex_restriction_shapes_match_scalar_state_neutrality(body: str) -> None:
    view = _snapshot(f"SubClassOf(:Before :After) {body}")
    expected = Projector().project(
        view,
        options=ProjectionOptions(backend="python", order="encounter"),
    )
    actual, statistics = prepare_native_encoded_direct(_lease(view)).compile_batch(
        bidirectional=False,
        max_edges=1,
        max_iri_bytes=1024 * 1024,
    )

    assert actual == expected == [
        Edge("urn:native-direct#Before", SUBCLASS_OF, "urn:native-direct#After")
    ]
    assert statistics.subclasses == 2
    assert statistics.ignored_subclasses == 1
    assert statistics.role_expansion_edges == 0


@pytest.mark.parametrize(
    ("body", "projected"),
    [
        ("ObjectPropertyAssertion(:p _:anonymous :i)", True),
        ("ObjectPropertyAssertion(:p :i _:anonymous)", True),
        ("NegativeObjectPropertyAssertion(:p _:anonymous :i)", False),
        ("NegativeObjectPropertyAssertion(:p :i _:anonymous)", False),
    ],
    ids=[
        "positive-source",
        "positive-target",
        "negative-source",
        "negative-target",
    ],
)
def test_anonymous_object_assertion_boundaries_match_scalar(
    body: str,
    projected: bool,
) -> None:
    view = _snapshot(body)
    expected = Projector().project(
        view,
        options=ProjectionOptions(backend="python", order="encounter"),
    )
    actual, statistics = prepare_native_encoded_direct(_lease(view)).compile_batch(
        bidirectional=False,
        max_edges=1,
        max_iri_bytes=1024 * 1024,
    )

    assert actual == expected
    assert len(actual) == int(projected)
    if projected:
        assert "_:genid2147483648" in {actual[0].source, actual[0].destination}
    assert statistics.anonymous_individuals == 1
    assert statistics.skipped_axioms == int(not projected)


def test_anonymous_object_assertions_match_exact_scalar_blank_id_order() -> None:
    view = _snapshot(
        "ObjectPropertyAssertion(:p _:z :i) "
        "ObjectPropertyAssertion(:p :i _:a) "
        "ObjectPropertyAssertion(:p _:z _:a)"
    )
    expected = Projector().project(
        view,
        options=ProjectionOptions(backend="python", order="encounter"),
    )
    actual, statistics = prepare_native_encoded_direct(_lease(view)).compile_batch(
        bidirectional=False,
        max_edges=3,
        max_iri_bytes=1024 * 1024,
    )

    assert actual == expected
    assert set(actual) == {
        Edge("_:genid2147483649", "urn:native-direct#p", "_:genid2147483648"),
        Edge("_:genid2147483649", "urn:native-direct#p", "urn:native-direct#i"),
        Edge("urn:native-direct#i", "urn:native-direct#p", "_:genid2147483648"),
    }
    assert statistics.anonymous_individuals == 2
    assert statistics.object_property_assertions == 3


def test_non_axiom_anonymous_individuals_do_not_shift_scalar_blank_ids() -> None:
    view = _swrl_snapshot(
        "Annotation(<urn:ontology-meta> _:ontologyOnly) "
        "SWRLRule((SameIndividualAtom(_:ruleOnly :named)) ()) "
        "ObjectPropertyAssertion(:p _:axiomOnly :named)"
    )
    expected = Projector().project(
        view,
        options=ProjectionOptions(backend="python", order="encounter"),
    )
    actual, statistics = prepare_native_encoded_direct(_lease(view)).compile_batch(
        bidirectional=False,
        max_edges=1,
        max_iri_bytes=1024 * 1024,
    )

    assert actual == expected == [
        Edge("_:genid2147483648", "urn:native-direct#p", "urn:native-direct#named")
    ]
    assert statistics.anonymous_individuals == 1
    assert statistics.ontology_annotations == 1
    assert statistics.swrl_rules == 1


@pytest.mark.parametrize("only_taxonomy", [False, True])
def test_all_axiom_reachable_anonymous_individuals_share_one_scalar_id_space(
    only_taxonomy: bool,
) -> None:
    view = _snapshot(
        "Declaration(Class(:A)) "
        "AnnotationAssertion("
        "<http://www.w3.org/2000/01/rdf-schema#label> _:silentSubject \"ignored\") "
        "ObjectPropertyAssertion(Annotation(<urn:meta> _:metadata) "
        ":p _:edge :named) "
        "AnnotationAssertion("
        "<http://www.w3.org/2000/01/rdf-schema#label> :A _:value)"
    )
    expected = Projector().project(
        view,
        options=ProjectionOptions(
            backend="python",
            include_literals=True,
            only_taxonomy=only_taxonomy,
            order="encounter",
        ),
    )
    actual, statistics = prepare_native_encoded_direct(_lease(view)).compile_batch(
        bidirectional=False,
        max_edges=2,
        max_iri_bytes=1024 * 1024,
        include_literals=True,
        only_taxonomy=only_taxonomy,
    )

    assert actual == expected
    assert len(actual) == 2
    generated = {
        value
        for edge in actual
        for value in (edge.source, edge.destination)
        if value.startswith("_:genid")
    }
    assert len(generated) == 2
    assert generated <= {
        f"_:genid{2_147_483_648 + index}" for index in range(4)
    }
    assert statistics.anonymous_individuals == 4
    assert statistics.annotation_edges == 1


def test_asserted_taxonomy_preflights_anonymous_axioms_without_leakage() -> None:
    view = _snapshot(
        "SubClassOf(:A :B) "
        "ObjectPropertyAssertion(:p _:source :named) "
        "NegativeObjectPropertyAssertion(:p _:negative :named) "
        'DataPropertyAssertion(:dp _:data "value") '
        "Declaration(Class(:A)) AnnotationAssertion("
        "<http://www.w3.org/2000/01/rdf-schema#label> :A _:label)"
    )
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
        max_edges=2,
        max_iri_bytes=1024 * 1024,
        asserted_taxonomy_only=True,
        include_literals=True,
    )

    assert actual == expected == [
        Edge("urn:native-direct#A", SUBCLASS_OF, "urn:native-direct#B"),
        Edge("urn:native-direct#B", SUPERCLASS_OF, "urn:native-direct#A"),
    ]
    assert statistics.anonymous_individuals == 4
    assert statistics.annotation_edges == 0
    assert statistics.skipped_axioms == 0


def test_many_anonymous_assertions_use_one_bounded_call_and_contiguous_ids() -> None:
    assertions = " ".join(
        f"ObjectPropertyAssertion(:p _:source{index:03d} :named{index:03d})"
        for index in range(250)
    )
    compiler = prepare_native_encoded_direct(_lease(_snapshot(assertions)))
    edges, statistics = compiler.compile_batch(
        bidirectional=False,
        max_edges=250,
        max_iri_bytes=1024 * 1024,
    )

    assert len(edges) == 250
    assert {edge.source for edge in edges} == {
        f"_:genid{2_147_483_648 + index}" for index in range(250)
    }
    assert statistics.anonymous_individuals == 250
    assert statistics.object_property_assertions == 250
    assert statistics.ingestion_counters["native_boundary_calls"] == 1


def test_noncanonical_axiom_anonymous_order_fails_before_output() -> None:
    lease = _lease(
        _snapshot(
            "ObjectPropertyAssertion(:p _:first :named) "
            "ObjectPropertyAssertion(:p _:third :named)"
        )
    )
    tags = lease.buffers["node_tags"]
    anonymous_ids = [
        node_id
        for node_id in range(1, tags.nbytes // 2 + 1)
        if int.from_bytes(tags[(node_id - 1) * 2 : node_id * 2], "little") == 3
    ]
    assert len(anonymous_ids) == 2
    offsets = lease.buffers["node_field_offsets"]
    field_values = lease.buffers["field_values"]
    field_lengths = lease.buffers["field_lengths"]

    def scalar_range(node_id: int, field_delta: int) -> tuple[int, int]:
        field_start = int.from_bytes(
            offsets[(node_id - 1) * 8 : node_id * 8],
            "little",
        )
        field_index = field_start + field_delta
        start = int.from_bytes(
            field_values[field_index * 8 : (field_index + 1) * 8],
            "little",
        )
        length = int.from_bytes(
            field_lengths[field_index * 8 : (field_index + 1) * 8],
            "little",
        )
        return start, length

    scalar = bytearray(lease.buffers["scalar_bytes"])
    first_scope = scalar_range(anonymous_ids[0], 0)
    second_scope = scalar_range(anonymous_ids[1], 0)
    assert scalar[first_scope[0] : first_scope[0] + first_scope[1]] == scalar[
        second_scope[0] : second_scope[0] + second_scope[1]
    ]
    first_key = scalar_range(anonymous_ids[0], 1)
    second_key = scalar_range(anonymous_ids[1], 1)
    assert first_key[1] == second_key[1] > 0
    scalar[first_key[0] : first_key[0] + first_key[1]] = b"z" * first_key[1]
    scalar[second_key[0] : second_key[0] + second_key[1]] = b"a" * second_key[1]
    compiler = prepare_native_encoded_direct(
        _replace_buffers(
            lease,
            {"scalar_bytes": memoryview(bytes(scalar))},
        )
    )

    with pytest.raises(SnapshotCompatibilityError, match="anonymous individuals are not canonical"):
        compiler.compile_batch(
            bidirectional=False,
            max_edges=2,
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
    with pytest.raises(SnapshotCompatibilityError, match="individual"):
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
        "SubClassOf("
        f":A{index:03d} ObjectSomeValuesFrom(ObjectInverseOf(:p) :B{index:03d}))"
        for index in range(250)
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

    def create_batches() -> tuple[
        NativeEncodedDirectBatchIterator,
        weakref.ReferenceType[object],
    ]:
        compiler, owner_ref = create()
        batches = compiler.iter_batches(
            bidirectional=False,
            max_edges=16,
            max_iri_bytes=1024 * 1024,
            batch_edges=2,
        )
        return batches, owner_ref

    batches, owner_ref = create_batches()
    gc.collect()
    assert owner_ref() is not None
    assert len(next(batches)) == 2
    assert batches.close() is True
    gc.collect()
    assert owner_ref() is None

    collected_batches, collected_owner_ref = create_batches()
    assert len(next(collected_batches)) == 2
    del collected_batches
    gc.collect()
    assert collected_owner_ref() is None


def test_retained_role_state_matches_ordered_scala_instance_calls_across_views() -> None:
    role_view = _snapshot(
        "SubObjectPropertyOf(:child :p) InverseObjectProperties(:p :pinv)"
    )
    consumer_view = _snapshot(
        "SubClassOf(:A ObjectSomeValuesFrom(:p :B)) "
        "ObjectPropertyDomain(:p :D) ObjectPropertyRange(:p :R)"
    )
    conflict_view = _snapshot(
        "SubObjectPropertyOf(:other :p) InverseObjectProperties(:p :otherInverse) "
        "SubClassOf(:X ObjectSomeValuesFrom(:p :Y))"
    )
    options = ProjectionOptions(
        backend="python",
        compatibility_state="scala-instance",
        order="encounter",
    )
    scalar = Projector()
    expected = [
        scalar.project(role_view, options=options),
        scalar.project(consumer_view, options=options),
        scalar.project(conflict_view, options=options),
    ]

    role_state = prepare_native_encoded_role_state()
    assert isinstance(role_state, NativeEncodedDirectRoleState)
    assert role_state.in_use is False
    assert role_state.subrole_property_count == 0
    assert role_state.inverse_property_count == 0
    assert role_state.snapshot() == RoleState.empty()
    actual: list[list[Edge]] = []
    for view, maximum in zip(
        (role_view, consumer_view, conflict_view),
        (1, len(expected[1]), len(expected[2])),
        strict=True,
    ):
        edges, _statistics = prepare_native_encoded_direct(_lease(view)).compile_batch(
            bidirectional=False,
            max_edges=maximum,
            max_iri_bytes=1024 * 1024,
            role_state=role_state,
        )
        actual.append(edges)

    assert actual == expected
    assert actual[0] == []
    assert actual[1] == [
        Edge("urn:native-direct#A", "urn:native-direct#p", "urn:native-direct#B"),
        Edge("urn:native-direct#A", "urn:native-direct#child", "urn:native-direct#B"),
        Edge("urn:native-direct#B", "urn:native-direct#pinv", "urn:native-direct#A"),
        Edge("urn:native-direct#D", "urn:native-direct#p", "urn:native-direct#R"),
        Edge("urn:native-direct#D", "urn:native-direct#child", "urn:native-direct#R"),
        Edge("urn:native-direct#R", "urn:native-direct#pinv", "urn:native-direct#D"),
    ]
    assert actual[2] == [
        Edge("urn:native-direct#X", "urn:native-direct#p", "urn:native-direct#Y"),
        Edge("urn:native-direct#X", "urn:native-direct#other", "urn:native-direct#Y"),
        Edge(
            "urn:native-direct#Y",
            "urn:native-direct#otherInverse",
            "urn:native-direct#X",
        ),
    ]
    assert role_state.in_use is False
    assert role_state.subrole_property_count == 1
    assert role_state.inverse_property_count == 3
    assert role_state.snapshot() == RoleState(
        {"urn:native-direct#p": ("urn:native-direct#other",)},
        {
            "urn:native-direct#p": "urn:native-direct#otherInverse",
            "urn:native-direct#pinv": "urn:native-direct#p",
            "urn:native-direct#otherInverse": "urn:native-direct#p",
        },
    )


def test_private_native_batches_preserve_exact_order_and_bound_each_ffi_transfer() -> None:
    view = _snapshot(
        "SubObjectPropertyOf(:child :p) InverseObjectProperties(:p :pinv) "
        "SubClassOf(:A :B) SubClassOf(:A ObjectSomeValuesFrom(:p :C)) "
        "EquivalentClasses(:A :D :E) ClassAssertion(:A :individual) "
        "ObjectPropertyAssertion(:p :individual :other) "
        "ObjectPropertyDomain(:p :Domain) ObjectPropertyRange(:p :Range)"
    )
    expected = Projector().project(
        view,
        options=ProjectionOptions(
            backend="python",
            order="encounter",
            duplicates="preserve",
        ),
    )
    compiler = prepare_native_encoded_direct(_lease(view))
    batches = compiler.iter_batches(
        bidirectional=False,
        max_edges=len(expected),
        max_iri_bytes=1024 * 1024,
        batch_edges=3,
    )

    assert isinstance(batches, NativeEncodedDirectBatchIterator)
    assert batches.state == "active"
    assert batches.remaining_edges == len(expected)
    assert batches.boundary_calls == 1
    assert batches.edge_batches == 0
    assert batches.peak_buffered_edges == 0
    assert compiler.batch_intermediate_list_edges == 0

    actual_batches = list(batches)
    assert all(type(batch) is tuple and 1 <= len(batch) <= 3 for batch in actual_batches)
    assert [edge for batch in actual_batches for edge in batch] == expected
    assert batches.state == "exhausted"
    assert batches.remaining_edges == 0
    assert batches.yielded_edges == len(expected)
    assert batches.edge_batches == (len(expected) + 2) // 3
    assert batches.boundary_calls == batches.edge_batches + 1
    assert batches.peak_buffered_edges == min(3, len(expected))
    assert dict(batches.ingestion_counters) == {
        "configured_batch_edges": 3,
        "native_boundary_calls": batches.edge_batches + 1,
        "native_edge_batches": batches.edge_batches,
        "native_peak_buffered_edges": min(3, len(expected)),
        "per_row_ffi_calls": 0,
        "published_edges": len(expected),
    }
    assert compiler.state == "finished"

    sink_batches: list[tuple[Edge, ...]] = []
    sink_statistics = prepare_native_encoded_direct(_lease(view)).compile_to_sink(
        sink_batches.append,
        bidirectional=False,
        max_edges=len(expected),
        max_iri_bytes=1024 * 1024,
        batch_edges=4,
    )
    assert [edge for batch in sink_batches for edge in batch] == expected
    assert [len(batch) for batch in sink_batches] == [4] * (len(expected) // 4) + (
        [len(expected) % 4] if len(expected) % 4 else []
    )
    assert sink_statistics.edges == len(expected)


def test_private_native_batch_final_edges_commit_with_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _snapshot("SubClassOf(:A :B) SubClassOf(:C :D) SubClassOf(:E :F)")
    expected = Projector().project(
        view,
        options=ProjectionOptions(
            backend="python",
            order="encounter",
            duplicates="preserve",
        ),
    )
    compiler = prepare_native_encoded_direct(_lease(view))
    batches = compiler.iter_batches(
        bidirectional=False,
        max_edges=len(expected),
        max_iri_bytes=1024 * 1024,
        batch_edges=2,
    )
    remaining_edges = batches.remaining_edges
    boundary_calls = batches.boundary_calls
    edge_calls = 0

    def failing_edge(source: str, relation: str, destination: str) -> Edge:
        nonlocal edge_calls
        edge_calls += 1
        if edge_calls == 2:
            raise MemoryError("injected final batch edge construction failure")
        return Edge(source, relation, destination)

    with monkeypatch.context() as patch:
        patch.setattr("pyowl2vec_star_projector.native.Edge", failing_edge)
        with pytest.raises(ProjectionResourceError, match="configured edge resources"):
            next(batches)

    assert edge_calls == 2
    assert batches.state == "active"
    assert batches.yielded_edges == 0
    assert batches.remaining_edges == remaining_edges
    assert batches.boundary_calls == boundary_calls
    assert batches.edge_batches == 0
    assert batches.peak_buffered_edges == 0
    assert compiler.batch_intermediate_list_edges == 0
    assert [edge for batch in batches for edge in batch] == expected
    assert batches.state == "exhausted"


@pytest.mark.parametrize(
    "exact_result",
    (False, True),
    ids=("malformed-result", "canonical-result-from-replaced-factory"),
)
def test_private_native_batch_edge_factory_validates_before_cursor_commit(
    monkeypatch: pytest.MonkeyPatch,
    exact_result: bool,
) -> None:
    view = _snapshot("SubClassOf(:A :B) SubClassOf(:C :D) SubClassOf(:E :F)")
    expected = Projector().project(
        view,
        options=ProjectionOptions(
            backend="python",
            order="encounter",
            duplicates="preserve",
        ),
    )
    compiler = prepare_native_encoded_direct(_lease(view))
    batches = compiler.iter_batches(
        bidirectional=False,
        max_edges=len(expected),
        max_iri_bytes=1024 * 1024,
        batch_edges=2,
    )
    remaining_edges = batches.remaining_edges
    boundary_calls = batches.boundary_calls
    edge_calls = 0

    def replaced_edge(source: str, relation: str, destination: str) -> object:
        nonlocal edge_calls
        edge_calls += 1
        if exact_result:
            return Edge(source, relation, destination)
        return object()

    with monkeypatch.context() as patch:
        patch.setattr("pyowl2vec_star_projector.native.Edge", replaced_edge)
        with pytest.raises(ProjectionError, match="native projector execution failed"):
            next(batches)

    assert edge_calls == (2 if exact_result else 1)
    assert batches.state == "active"
    assert batches.yielded_edges == 0
    assert batches.remaining_edges == remaining_edges
    assert batches.boundary_calls == boundary_calls
    assert batches.edge_batches == 0
    assert batches.peak_buffered_edges == 0
    assert compiler.batch_intermediate_list_edges == 0
    assert [edge for batch in batches for edge in batch] == expected
    assert batches.state == "exhausted"


def test_private_native_batch_uses_canonical_edge_type_after_factory_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _snapshot("SubClassOf(:A :B) SubClassOf(:C :D) SubClassOf(:E :F)")
    expected = Projector().project(
        view,
        options=ProjectionOptions(
            backend="python",
            order="encounter",
            duplicates="preserve",
        ),
    )
    compiler = prepare_native_encoded_direct(_lease(view))
    batches = compiler.iter_batches(
        bidirectional=False,
        max_edges=len(expected),
        max_iri_bytes=1024 * 1024,
        batch_edges=2,
    )
    native_bridge = cast(Any, sys.modules["pyowl2vec_star_projector.native"])
    canonical_post_init = Edge.__post_init__
    mutated = False

    def mutating_post_init(edge: Edge) -> None:
        nonlocal mutated
        canonical_post_init(edge)
        if not mutated:
            mutated = True
            patch.setattr(native_bridge, "Edge", object)

    with monkeypatch.context() as patch:
        patch.setattr(Edge, "__post_init__", mutating_post_init)
        first = next(batches)
        patch.setattr(native_bridge, "Edge", Edge)

    assert mutated is True
    assert first == tuple(expected[:2])
    assert all(type(edge) is Edge for edge in first)
    assert batches.yielded_edges == 2
    assert batches.remaining_edges == 1
    assert batches.boundary_calls == 2
    assert batches.edge_batches == 1
    assert batches.peak_buffered_edges == 2
    assert [edge for batch in batches for edge in batch] == expected[2:]
    assert batches.state == "exhausted"


def test_private_native_batch_validates_edge_payload_before_cursor_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _snapshot("SubClassOf(:A :B) SubClassOf(:C :D) SubClassOf(:E :F)")
    expected = Projector().project(
        view,
        options=ProjectionOptions(
            backend="python",
            order="encounter",
            duplicates="preserve",
        ),
    )
    compiler = prepare_native_encoded_direct(_lease(view))
    batches = compiler.iter_batches(
        bidirectional=False,
        max_edges=len(expected),
        max_iri_bytes=1024 * 1024,
        batch_edges=2,
    )
    remaining_edges = batches.remaining_edges
    boundary_calls = batches.boundary_calls
    canonical_post_init = Edge.__post_init__

    def corrupting_post_init(edge: Edge) -> None:
        canonical_post_init(edge)
        object.__setattr__(edge, "source", "urn:corrupted")

    with monkeypatch.context() as patch:
        patch.setattr(Edge, "__post_init__", corrupting_post_init)
        with pytest.raises(ProjectionError, match="native projector execution failed"):
            next(batches)

    assert batches.state == "active"
    assert batches.yielded_edges == 0
    assert batches.remaining_edges == remaining_edges
    assert batches.boundary_calls == boundary_calls
    assert batches.edge_batches == 0
    assert batches.peak_buffered_edges == 0
    assert compiler.batch_intermediate_list_edges == 0
    assert [edge for batch in batches for edge in batch] == expected
    assert batches.state == "exhausted"


def test_private_native_batch_statistics_factory_is_in_session_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _snapshot(
        "SubObjectPropertyOf(:child :p) InverseObjectProperties(:p :pinv) "
        "SubClassOf(:A ObjectSomeValuesFrom(:p :B))"
    )
    statistics_calls = 0

    def failing_statistics(*values: int) -> NativeEncodedDirectStatistics:
        nonlocal statistics_calls
        statistics_calls += 1
        assert len(values) == 60
        raise MemoryError("injected final batch statistics construction failure")

    role_state = prepare_native_encoded_role_state()
    compiler = prepare_native_encoded_direct(_lease(view))
    with monkeypatch.context() as patch:
        patch.setattr(
            "pyowl2vec_star_projector.native.NativeEncodedDirectStatistics",
            failing_statistics,
        )
        with pytest.raises(ProjectionResourceError, match="configured edge resources"):
            compiler.iter_batches(
                bidirectional=False,
                max_edges=3,
                max_iri_bytes=1024 * 1024,
                batch_edges=2,
                role_state=role_state,
            )

    assert statistics_calls == 1
    assert compiler.state == "failed"
    assert compiler._kernel.batch_state == "absent"
    assert compiler._kernel.remaining_batch_edges == 0
    assert compiler._kernel.batch_boundary_calls == 0
    assert compiler._kernel.emitted_edge_batches == 0
    assert compiler._kernel.peak_buffered_batch_edges == 0
    assert role_state.in_use is False
    assert role_state.subrole_property_count == 0
    assert role_state.inverse_property_count == 0


def test_private_native_batch_iterator_factory_is_in_session_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _snapshot(
        "SubObjectPropertyOf(:child :p) InverseObjectProperties(:p :pinv) "
        "SubClassOf(:A ObjectSomeValuesFrom(:p :B))"
    )
    iterator_calls = 0
    role_state = prepare_native_encoded_role_state()
    compiler = prepare_native_encoded_direct(_lease(view))

    def failing_iterator(
        compiler_owner: NativeEncodedDirectCompiler,
        statistics: NativeEncodedDirectStatistics,
        batch_edges: int,
    ) -> NativeEncodedDirectBatchIterator:
        nonlocal iterator_calls
        iterator_calls += 1
        assert compiler_owner is compiler
        assert type(statistics) is NativeEncodedDirectStatistics
        assert statistics.edges == 3
        assert batch_edges == 2
        raise MemoryError("injected final batch iterator construction failure")

    with monkeypatch.context() as patch:
        patch.setattr(
            "pyowl2vec_star_projector.native.NativeEncodedDirectBatchIterator",
            failing_iterator,
        )
        with pytest.raises(ProjectionResourceError, match="configured edge resources"):
            compiler.iter_batches(
                bidirectional=False,
                max_edges=3,
                max_iri_bytes=1024 * 1024,
                batch_edges=2,
                role_state=role_state,
            )

    assert iterator_calls == 1
    assert compiler.state == "failed"
    assert compiler._kernel.batch_state == "absent"
    assert compiler._kernel.remaining_batch_edges == 0
    assert compiler._kernel.batch_boundary_calls == 0
    assert compiler._kernel.emitted_edge_batches == 0
    assert compiler._kernel.peak_buffered_batch_edges == 0
    assert role_state.in_use is False
    assert role_state.subrole_property_count == 0
    assert role_state.inverse_property_count == 0


@pytest.mark.parametrize("factory_name", ("statistics", "iterator"))
def test_private_native_batch_factory_results_are_validated_before_session_commit(
    monkeypatch: pytest.MonkeyPatch,
    factory_name: str,
) -> None:
    view = _snapshot(
        "SubObjectPropertyOf(:child :p) InverseObjectProperties(:p :pinv) "
        "SubClassOf(:A ObjectSomeValuesFrom(:p :B))"
    )
    calls = {"statistics": 0, "iterator": 0}
    observed_final_statistics = False
    role_state = prepare_native_encoded_role_state()
    compiler = prepare_native_encoded_direct(_lease(view))

    def invalid_statistics(*values: int) -> object:
        calls["statistics"] += 1
        assert len(values) == 60
        return object()

    def invalid_iterator(
        compiler_owner: NativeEncodedDirectCompiler,
        statistics: NativeEncodedDirectStatistics,
        batch_edges: int,
    ) -> object:
        nonlocal observed_final_statistics
        calls["iterator"] += 1
        assert compiler_owner is compiler
        observed_final_statistics = type(statistics) is NativeEncodedDirectStatistics
        assert statistics.edges == 3
        assert batch_edges == 2
        return object()

    factory = invalid_statistics if factory_name == "statistics" else invalid_iterator
    target = (
        "NativeEncodedDirectStatistics"
        if factory_name == "statistics"
        else "NativeEncodedDirectBatchIterator"
    )
    with monkeypatch.context() as patch:
        patch.setattr(f"pyowl2vec_star_projector.native.{target}", factory)
        with pytest.raises(ProjectionError, match="native projector execution failed"):
            compiler.iter_batches(
                bidirectional=False,
                max_edges=3,
                max_iri_bytes=1024 * 1024,
                batch_edges=2,
                role_state=role_state,
            )

    assert calls[factory_name] == 1
    assert observed_final_statistics is (factory_name == "iterator")
    assert compiler.state == "failed"
    assert compiler._kernel.batch_state == "absent"
    assert compiler._kernel.remaining_batch_edges == 0
    assert compiler._kernel.batch_boundary_calls == 0
    assert compiler._kernel.emitted_edge_batches == 0
    assert compiler._kernel.peak_buffered_batch_edges == 0
    assert role_state.in_use is False
    assert role_state.subrole_property_count == 0
    assert role_state.inverse_property_count == 0


def test_private_native_coarse_list_uses_bounded_internal_chunks() -> None:
    edge_count = 600
    view = _snapshot(
        " ".join(f"SubClassOf(:C{index} :Top)" for index in range(edge_count))
    )
    expected = Projector().project(
        view,
        options=ProjectionOptions(
            backend="python",
            order="encounter",
            duplicates="preserve",
        ),
    )
    compiler = prepare_native_encoded_direct(_lease(view))
    assert compiler.coarse_output_chunks == 0
    assert compiler.coarse_output_vector_edges == 0
    assert compiler.coarse_intermediate_list_edges == 0
    assert compiler.peak_buffered_coarse_edges == 0

    actual, statistics = compiler.compile_batch(
        bidirectional=False,
        max_edges=edge_count,
        max_iri_bytes=1024 * 1024,
    )

    assert actual == expected
    assert statistics.edges == edge_count
    assert compiler.coarse_chunk_edges == 256
    assert compiler.coarse_output_chunks == 3
    assert compiler.coarse_output_vector_edges == 0
    assert compiler.coarse_intermediate_list_edges == 0
    assert compiler.peak_buffered_coarse_edges == 256
    assert compiler.peak_buffered_coarse_edges < statistics.edges


def test_private_native_coarse_result_factories_are_in_role_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _snapshot(
        "SubObjectPropertyOf(:child :p) InverseObjectProperties(:p :pinv) "
        "SubClassOf(:A ObjectSomeValuesFrom(:p :B))"
    )
    edge_calls = 0

    def failing_edge(source: str, relation: str, destination: str) -> Edge:
        nonlocal edge_calls
        edge_calls += 1
        if edge_calls == 2:
            raise MemoryError("injected final edge construction failure")
        return Edge(source, relation, destination)

    role_state = prepare_native_encoded_role_state()
    compiler = prepare_native_encoded_direct(_lease(view))
    with monkeypatch.context() as patch:
        patch.setattr("pyowl2vec_star_projector.native.Edge", failing_edge)
        with pytest.raises(ProjectionResourceError, match="configured edge resources"):
            compiler.compile_batch(
                bidirectional=False,
                max_edges=3,
                max_iri_bytes=1024 * 1024,
                role_state=role_state,
            )
    assert edge_calls == 2
    assert compiler.state == "failed"
    assert compiler.coarse_output_chunks == 0
    assert role_state.in_use is False
    assert role_state.subrole_property_count == 0
    assert role_state.inverse_property_count == 0

    statistics_calls = 0

    def failing_statistics(*values: int) -> NativeEncodedDirectStatistics:
        nonlocal statistics_calls
        statistics_calls += 1
        assert len(values) == 60
        raise MemoryError("injected final statistics construction failure")

    role_state = prepare_native_encoded_role_state()
    compiler = prepare_native_encoded_direct(_lease(view))
    with monkeypatch.context() as patch:
        patch.setattr(
            "pyowl2vec_star_projector.native.NativeEncodedDirectStatistics",
            failing_statistics,
        )
        with pytest.raises(ProjectionResourceError, match="configured edge resources"):
            compiler.compile_batch(
                bidirectional=False,
                max_edges=3,
                max_iri_bytes=1024 * 1024,
                role_state=role_state,
            )
    assert statistics_calls == 1
    assert compiler.state == "failed"
    assert compiler.coarse_output_chunks == 0
    assert role_state.in_use is False
    assert role_state.subrole_property_count == 0
    assert role_state.inverse_property_count == 0


@pytest.mark.parametrize(
    ("factory_name", "exact_result"),
    (
        ("edge", False),
        ("edge", True),
        ("statistics", False),
        ("statistics", True),
    ),
    ids=(
        "malformed-edge",
        "canonical-edge-from-replaced-factory",
        "malformed-statistics",
        "canonical-statistics-from-replaced-factory",
    ),
)
def test_private_native_coarse_factory_results_validate_before_role_commit(
    monkeypatch: pytest.MonkeyPatch,
    factory_name: str,
    exact_result: bool,
) -> None:
    view = _snapshot(
        "SubObjectPropertyOf(:child :p) InverseObjectProperties(:p :pinv) "
        "SubClassOf(:A ObjectSomeValuesFrom(:p :B))"
    )
    calls = 0

    def replaced_edge(source: str, relation: str, destination: str) -> object:
        nonlocal calls
        calls += 1
        if exact_result:
            return Edge(source, relation, destination)
        return object()

    def replaced_statistics(*values: int) -> object:
        nonlocal calls
        calls += 1
        assert len(values) == 60
        if exact_result:
            return NativeEncodedDirectStatistics(*values)
        return object()

    factory = replaced_edge if factory_name == "edge" else replaced_statistics
    target = "Edge" if factory_name == "edge" else "NativeEncodedDirectStatistics"
    role_state = prepare_native_encoded_role_state()
    compiler = prepare_native_encoded_direct(_lease(view))
    with monkeypatch.context() as patch:
        patch.setattr(f"pyowl2vec_star_projector.native.{target}", factory)
        with pytest.raises(ProjectionError, match="native projector execution failed"):
            compiler.compile_batch(
                bidirectional=False,
                max_edges=3,
                max_iri_bytes=1024 * 1024,
                role_state=role_state,
            )

    expected_calls = 3 if factory_name == "edge" and exact_result else 1
    assert calls == expected_calls
    assert compiler.state == "failed"
    assert compiler.coarse_output_chunks == 0
    assert compiler.coarse_output_vector_edges == 0
    assert compiler.coarse_intermediate_list_edges == 0
    assert compiler.peak_buffered_coarse_edges == 0
    assert role_state.in_use is False
    assert role_state.subrole_property_count == 0
    assert role_state.inverse_property_count == 0


@pytest.mark.parametrize(
    ("surface", "factory_name"),
    (
        ("coarse", "edge"),
        ("coarse", "statistics"),
        ("batches", "statistics"),
        ("batches", "iterator"),
    ),
    ids=(
        "coarse-edge",
        "coarse-statistics",
        "batch-statistics",
        "batch-iterator",
    ),
)
def test_private_native_wrappers_retain_canonical_types_during_factory_mutation(
    monkeypatch: pytest.MonkeyPatch,
    surface: str,
    factory_name: str,
) -> None:
    view = _snapshot(
        "SubObjectPropertyOf(:child :p) InverseObjectProperties(:p :pinv) "
        "SubClassOf(:A ObjectSomeValuesFrom(:p :B))"
    )
    expected = Projector().project(
        view,
        options=ProjectionOptions(
            backend="python",
            order="encounter",
            duplicates="preserve",
        ),
    )
    native_bridge = cast(Any, sys.modules["pyowl2vec_star_projector.native"])
    role_state = prepare_native_encoded_role_state()
    compiler = prepare_native_encoded_direct(_lease(view))
    target = {
        "edge": "Edge",
        "statistics": "NativeEncodedDirectStatistics",
        "iterator": "NativeEncodedDirectBatchIterator",
    }[factory_name]
    canonical_target = {
        "edge": Edge,
        "statistics": NativeEncodedDirectStatistics,
        "iterator": NativeEncodedDirectBatchIterator,
    }[factory_name]
    mutated = False

    def mutate_factory_global() -> None:
        nonlocal mutated
        if not mutated:
            mutated = True
            patch.setattr(native_bridge, target, object)

    canonical_edge_post_init = Edge.__post_init__
    canonical_statistics_post_init = NativeEncodedDirectStatistics.__post_init__
    canonical_iterator_init = NativeEncodedDirectBatchIterator.__init__

    def mutating_edge_post_init(edge: Edge) -> None:
        canonical_edge_post_init(edge)
        mutate_factory_global()

    def mutating_statistics_post_init(statistics: NativeEncodedDirectStatistics) -> None:
        canonical_statistics_post_init(statistics)
        mutate_factory_global()

    def mutating_iterator_init(
        iterator: NativeEncodedDirectBatchIterator,
        compiler_owner: NativeEncodedDirectCompiler,
        statistics: NativeEncodedDirectStatistics,
        batch_edges: int,
    ) -> None:
        canonical_iterator_init(iterator, compiler_owner, statistics, batch_edges)
        mutate_factory_global()

    coarse_result: tuple[list[Edge], NativeEncodedDirectStatistics] | None = None
    batches: NativeEncodedDirectBatchIterator | None = None
    with monkeypatch.context() as patch:
        if factory_name == "edge":
            patch.setattr(Edge, "__post_init__", mutating_edge_post_init)
        elif factory_name == "statistics":
            patch.setattr(
                NativeEncodedDirectStatistics,
                "__post_init__",
                mutating_statistics_post_init,
            )
        else:
            patch.setattr(
                NativeEncodedDirectBatchIterator,
                "__init__",
                mutating_iterator_init,
            )
        if surface == "coarse":
            coarse_result = compiler.compile_batch(
                bidirectional=False,
                max_edges=len(expected),
                max_iri_bytes=1024 * 1024,
                role_state=role_state,
            )
        else:
            batches = compiler.iter_batches(
                bidirectional=False,
                max_edges=len(expected),
                max_iri_bytes=1024 * 1024,
                batch_edges=2,
                role_state=role_state,
            )
        patch.setattr(native_bridge, target, canonical_target)

    assert mutated is True
    assert compiler.state == "finished"
    assert role_state.in_use is False
    assert role_state.subrole_property_count > 0
    assert role_state.inverse_property_count > 0
    if coarse_result is not None:
        actual, statistics = coarse_result
        assert actual == expected
        assert all(type(edge) is Edge for edge in actual)
        assert type(statistics) is NativeEncodedDirectStatistics
    else:
        assert batches is not None
        assert type(batches) is NativeEncodedDirectBatchIterator
        assert type(batches.statistics) is NativeEncodedDirectStatistics
        assert [edge for batch in batches for edge in batch] == expected
        assert batches.state == "exhausted"


@pytest.mark.parametrize(
    ("surface", "factory_name"),
    (
        ("coarse", "edge"),
        ("coarse", "statistics"),
        ("batches", "statistics"),
        ("batches", "iterator"),
    ),
    ids=(
        "coarse-edge",
        "coarse-statistics",
        "batch-statistics",
        "batch-iterator",
    ),
)
def test_private_native_final_payloads_validate_before_state_commit(
    monkeypatch: pytest.MonkeyPatch,
    surface: str,
    factory_name: str,
) -> None:
    view = _snapshot(
        "SubObjectPropertyOf(:child :p) InverseObjectProperties(:p :pinv) "
        "SubClassOf(:A ObjectSomeValuesFrom(:p :B))"
    )
    role_state = prepare_native_encoded_role_state()
    compiler = prepare_native_encoded_direct(_lease(view))
    canonical_edge_post_init = Edge.__post_init__
    canonical_statistics_post_init = NativeEncodedDirectStatistics.__post_init__
    canonical_iterator_init = NativeEncodedDirectBatchIterator.__init__

    def corrupting_edge_post_init(edge: Edge) -> None:
        canonical_edge_post_init(edge)
        object.__setattr__(edge, "source", "urn:corrupted")

    def corrupting_statistics_post_init(statistics: NativeEncodedDirectStatistics) -> None:
        canonical_statistics_post_init(statistics)
        field = "roots" if surface == "coarse" else "root_provenance_buffer_bytes"
        object.__setattr__(statistics, field, getattr(statistics, field) + 1)

    def corrupting_iterator_init(
        iterator: NativeEncodedDirectBatchIterator,
        compiler_owner: NativeEncodedDirectCompiler,
        statistics: NativeEncodedDirectStatistics,
        batch_edges: int,
    ) -> None:
        canonical_iterator_init(iterator, compiler_owner, statistics, batch_edges)
        iterator._compiler = None

    with monkeypatch.context() as patch:
        if factory_name == "edge":
            patch.setattr(Edge, "__post_init__", corrupting_edge_post_init)
        elif factory_name == "statistics":
            patch.setattr(
                NativeEncodedDirectStatistics,
                "__post_init__",
                corrupting_statistics_post_init,
            )
        else:
            patch.setattr(
                NativeEncodedDirectBatchIterator,
                "__init__",
                corrupting_iterator_init,
            )
        with pytest.raises(ProjectionError, match="native projector execution failed"):
            if surface == "coarse":
                compiler.compile_batch(
                    bidirectional=False,
                    max_edges=3,
                    max_iri_bytes=1024 * 1024,
                    role_state=role_state,
                )
            else:
                compiler.iter_batches(
                    bidirectional=False,
                    max_edges=3,
                    max_iri_bytes=1024 * 1024,
                    batch_edges=2,
                    role_state=role_state,
                )

    assert compiler.state == "failed"
    assert role_state.in_use is False
    assert role_state.subrole_property_count == 0
    assert role_state.inverse_property_count == 0
    if surface == "coarse":
        assert compiler.coarse_output_chunks == 0
        assert compiler.coarse_output_vector_edges == 0
        assert compiler.coarse_intermediate_list_edges == 0
        assert compiler.peak_buffered_coarse_edges == 0
    else:
        assert compiler._kernel.batch_state == "absent"
        assert compiler._kernel.remaining_batch_edges == 0
        assert compiler._kernel.batch_boundary_calls == 0
        assert compiler._kernel.emitted_edge_batches == 0
        assert compiler._kernel.peak_buffered_batch_edges == 0


def test_private_native_batch_close_and_sink_failure_clear_unpublished_output() -> None:
    view = _snapshot(" ".join(f"SubClassOf(:C{index} :Top)" for index in range(25)))
    compiler = prepare_native_encoded_direct(_lease(view))
    batches = compiler.iter_batches(
        bidirectional=False,
        max_edges=25,
        max_iri_bytes=1024 * 1024,
        batch_edges=4,
    )
    assert len(next(batches)) == 4
    assert batches.remaining_edges == 21
    assert batches.close() is True
    assert batches.state == "cancelled"
    assert batches.remaining_edges == 0
    assert batches.boundary_calls == 2
    assert batches.edge_batches == 1
    assert batches.close() is False
    with pytest.raises(StopIteration):
        next(batches)

    class FailingSink:
        def __init__(self) -> None:
            self.batches: list[tuple[Edge, ...]] = []

        def write_batch(self, batch: tuple[Edge, ...]) -> None:
            self.batches.append(batch)
            if len(self.batches) == 2:
                raise RuntimeError("injected sink failure")

    failing = FailingSink()
    sink_compiler = prepare_native_encoded_direct(_lease(view))
    with pytest.raises(RuntimeError, match="injected sink failure"):
        sink_compiler.compile_to_sink(
            failing,
            bidirectional=False,
            max_edges=25,
            max_iri_bytes=1024 * 1024,
            batch_edges=4,
        )
    assert [len(batch) for batch in failing.batches] == [4, 4]
    assert sink_compiler._kernel.batch_state == "cancelled"
    assert sink_compiler._kernel.remaining_batch_edges == 0
    assert sink_compiler._kernel.batch_boundary_calls == 3
    assert sink_compiler._kernel.emitted_edge_batches == 2


def test_failed_private_batch_compile_does_not_commit_retained_role_state() -> None:
    failing_view = _snapshot(
        "SubObjectPropertyOf(:child :p) InverseObjectProperties(:p :pinv) "
        "SubClassOf(:A ObjectSomeValuesFrom(:p :B))"
    )
    coarse_role_state = prepare_native_encoded_role_state()
    coarse_compiler = prepare_native_encoded_direct(_lease(failing_view))
    with pytest.raises(ProjectionResourceError, match="configured edge resources"):
        coarse_compiler.compile_batch(
            bidirectional=False,
            max_edges=2,
            max_iri_bytes=1024 * 1024,
            role_state=coarse_role_state,
        )
    assert coarse_compiler.state == "failed"
    assert coarse_compiler.coarse_output_chunks == 0
    assert coarse_compiler.coarse_output_vector_edges == 0
    assert coarse_compiler.coarse_intermediate_list_edges == 0
    assert coarse_compiler.peak_buffered_coarse_edges == 0
    assert coarse_role_state.in_use is False
    assert coarse_role_state.subrole_property_count == 0
    assert coarse_role_state.inverse_property_count == 0

    role_state = prepare_native_encoded_role_state()
    compiler = prepare_native_encoded_direct(_lease(failing_view))
    with pytest.raises(ProjectionResourceError, match="configured edge resources"):
        compiler.iter_batches(
            bidirectional=False,
            max_edges=2,
            max_iri_bytes=1024 * 1024,
            batch_edges=1,
            role_state=role_state,
        )
    assert compiler.state == "failed"
    assert role_state.in_use is False
    assert role_state.subrole_property_count == 0
    assert role_state.inverse_property_count == 0

    role_view = _snapshot(
        "SubObjectPropertyOf(:child :p) InverseObjectProperties(:p :pinv)"
    )
    empty_batches = prepare_native_encoded_direct(_lease(role_view)).iter_batches(
        bidirectional=False,
        max_edges=1,
        max_iri_bytes=1024 * 1024,
        batch_edges=1,
        role_state=role_state,
    )
    assert list(empty_batches) == []
    assert empty_batches.boundary_calls == 1
    assert role_state.subrole_property_count == 1
    assert role_state.inverse_property_count == 2

    consumer_view = _snapshot("SubClassOf(:X ObjectSomeValuesFrom(:p :Y))")
    expected = Projector()
    options = ProjectionOptions(
        backend="python",
        compatibility_state="scala-instance",
        order="encounter",
    )
    expected.project(role_view, options=options)
    expected_edges = expected.project(consumer_view, options=options)
    consumer_batches = prepare_native_encoded_direct(_lease(consumer_view)).iter_batches(
        bidirectional=False,
        max_edges=len(expected_edges),
        max_iri_bytes=1024 * 1024,
        batch_edges=2,
        role_state=role_state,
    )
    assert [edge for batch in consumer_batches for edge in batch] == expected_edges
    assert consumer_batches.edge_batches == 2
    assert consumer_batches.boundary_calls == 3


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
