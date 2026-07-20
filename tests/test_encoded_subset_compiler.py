from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast
from unittest.mock import patch

import pyowl_core
import pytest
from pyowl_core import BackendPreference, ImportPolicy, LoadOptions

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
    prepare_encoded_subset_compilation,
)
from pyowl2vec_star_projector.errors import (
    SnapshotCompatibilityError,
    UnsupportedAxiomShapeError,
)

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
        "EquivalentClasses(:E ObjectIntersectionOf(:F ObjectSomeValuesFrom(:p :D)))"
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
    assert counters.anonymous_individuals == 1
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


def test_incomplete_slice_is_not_advertised_by_the_native_feature_ledger() -> None:
    native_source = (ROOT / "native" / "src" / "lib.rs").read_text("utf-8")
    assert ENCODED_NATIVE_FEATURE not in native_source
