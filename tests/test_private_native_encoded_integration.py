from __future__ import annotations

from dataclasses import replace
from typing import Any

import pyowl_core
import pytest

import pyowl2vec_star_projector.api as api_module
from pyowl2vec_star_projector import (
    ProjectionOptions,
    ProjectionResourceError,
    Projector,
    StreamingLimits,
    probe_native_backend,
)
from pyowl2vec_star_projector.encoded import ENCODED_NATIVE_FEATURE
from pyowl2vec_star_projector.native import (
    ENCODED_DIRECT_BUFFER_ORDER,
    NativeEncodedDirectBatchIterator,
    NativeEncodedDirectCompilation,
    load_native_module,
)
from pyowl2vec_star_projector.provenance import ProjectionReport

NATIVE_AVAILABLE = probe_native_backend().available

pytestmark = pytest.mark.skipif(
    not NATIVE_AVAILABLE,
    reason="optional native extension is not installed",
)


def _snapshot(body: str) -> object:
    source = f"Prefix(:=<urn:native-integration#>) Ontology(<urn:native-integration> {body})"
    return pyowl_core.load_snapshot(
        source.encode(),
        options=pyowl_core.LoadOptions(
            imports=pyowl_core.ImportPolicy.IGNORE,
            backend=pyowl_core.BackendPreference.PYTHON,
        ),
    )


def _completed_report(projector: Projector) -> ProjectionReport:
    report = projector.last_report
    assert report is not None
    return report


def _assert_semantic_report_parity(
    expected: ProjectionReport,
    actual: ProjectionReport,
) -> None:
    expected_provenance = expected.provenance
    actual_provenance = actual.provenance
    assert actual.diagnostics == expected.diagnostics
    assert actual_provenance.core == expected_provenance.core
    assert actual_provenance.counts == expected_provenance.counts
    assert actual_provenance.diagnostics_digest == expected_provenance.diagnostics_digest
    assert actual_provenance.invocation_count == expected_provenance.invocation_count
    assert actual_provenance.call_history_digest == expected_provenance.call_history_digest


@pytest.mark.parametrize(
    ("python_options", "raw_edges"),
    [
        (
            ProjectionOptions(
                backend="python",
                order="encounter",
                duplicates="preserve",
            ),
            4,
        ),
        (
            ProjectionOptions(
                backend="python",
                order="canonical",
                duplicates="unique",
                bidirectional_taxonomy=True,
            ),
            8,
        ),
    ],
)
def test_hidden_iterator_matches_scalar_and_reports_exact_native_batches(
    python_options: ProjectionOptions,
    raw_edges: int,
) -> None:
    view = _snapshot(
        "Declaration(Class(:A)) Declaration(Class(:B)) Declaration(Class(:C)) "
        "SubClassOf(:A :B) "
        'SubClassOf(Annotation(<urn:meta> "variant") :A :B) '
        "SubClassOf(:C :A) SubClassOf(:D :C)"
    )
    expected_projector = Projector()
    expected = expected_projector.project(view, options=python_options)
    expected_report = _completed_report(expected_projector)

    native_projector = Projector()
    native_options = replace(python_options, backend="native")
    actual = list(
        native_projector._iter_native_encoded_edges(
            view,
            options=native_options,
            buffer_edges=2,
        )
    )
    actual_report = _completed_report(native_projector)

    assert actual == expected
    _assert_semantic_report_parity(expected_report, actual_report)
    ingestion = actual_report.provenance.ingestion
    assert ingestion.path == "encoded-native"
    assert ingestion.reason is None
    assert ingestion.encoded_schema_name == "pyowl-core/structural-columns"
    assert ingestion.encoded_schema_version == 1
    assert ingestion.encoded_view_publication_seconds is not None
    assert ingestion.consumer_compile_seconds is not None
    counters = dict(ingestion.counters)
    assert counters["native_batch_edges"] == 2
    assert counters["native_edge_batches"] == (raw_edges + 1) // 2
    assert counters["native_boundary_calls"] == 1 + (raw_edges + 1) // 2
    assert counters["native_output_vector_edges"] == raw_edges
    assert counters["encoded_buffer_count"] == len(ENCODED_DIRECT_BUFFER_ORDER)
    assert counters["encoded_detached_buffer_count"] == len(ENCODED_DIRECT_BUFFER_ORDER)
    assert counters["encoded_zero_copy_buffers"] == len(ENCODED_DIRECT_BUFFER_ORDER)
    assert counters["encoded_segment_count"] == 1
    assert counters["encoded_buffer_bytes"] > 0
    assert counters["encoded_compiler_gil_released"] is True
    for name in (
        "base_flattening_bytes",
        "encoded_indexed_buffer_count",
        "encoded_posting_bytes",
        "encoded_referenced_view_count",
        "encoded_staging_copy_bytes",
        "materialized_scalar_rows",
        "parser_calls",
        "per_row_ffi_calls",
        "resolver_calls",
        "scalar_axiom_materializations",
        "scalar_term_materializations",
        "structural_copy_bytes",
        "wire_decoder_calls",
        "wire_encoder_calls",
    ):
        assert counters[name] == 0


@pytest.mark.parametrize(
    ("python_options", "raw_edges"),
    [
        (
            ProjectionOptions(
                backend="python",
                order="encounter",
                duplicates="preserve",
            ),
            4,
        ),
        (
            ProjectionOptions(
                backend="python",
                order="canonical",
                duplicates="unique",
                bidirectional_taxonomy=True,
            ),
            6,
        ),
        (
            ProjectionOptions(
                backend="python",
                order="encounter",
                only_taxonomy=True,
                include_literals=True,
            ),
            4,
        ),
    ],
)
def test_hidden_iterator_admits_exact_named_tbox_and_abox_edges(
    python_options: ProjectionOptions,
    raw_edges: int,
) -> None:
    view = _snapshot(
        "Declaration(Class(:Z)) Declaration(Class(:AA)) Declaration(Class(:B)) "
        "Declaration(Class(:Top)) Declaration(NamedIndividual(:i)) "
        "Declaration(NamedIndividual(:j)) Declaration(ObjectProperty(:p)) "
        "SubClassOf(:Z :Top) "
        'EquivalentClasses(Annotation(<urn:meta> "equivalence") :Z :AA :B) '
        'ClassAssertion(Annotation(<urn:meta> "type") :Z :i) '
        'ObjectPropertyAssertion(Annotation(<urn:meta> "property") :p :i :j)'
    )
    expected_projector = Projector()
    expected = expected_projector.project(view, options=python_options)
    expected_report = _completed_report(expected_projector)

    native_projector = Projector()
    actual = list(
        native_projector._iter_native_encoded_edges(
            view,
            options=replace(python_options, backend="native"),
            buffer_edges=2,
        )
    )
    actual_report = _completed_report(native_projector)

    assert actual == expected
    _assert_semantic_report_parity(expected_report, actual_report)
    ingestion = actual_report.provenance.ingestion
    assert ingestion.path == "encoded-native"
    assert ingestion.reason is None
    counters = ingestion.counters
    assert counters["native_batch_edges"] == 2
    assert counters["native_edge_batches"] == (raw_edges + 1) // 2
    assert counters["native_boundary_calls"] == 1 + (raw_edges + 1) // 2
    assert counters["native_output_vector_edges"] == raw_edges
    assert counters["per_row_ffi_calls"] == 0
    assert counters["scalar_axiom_materializations"] == 0
    assert counters["scalar_term_materializations"] == 0


@pytest.mark.parametrize(
    ("python_options", "raw_edges", "ignored_shapes"),
    [
        (
            ProjectionOptions(backend="python", order="encounter"),
            9,
            0,
        ),
        (
            ProjectionOptions(
                backend="python",
                order="canonical",
                duplicates="unique",
                bidirectional_taxonomy=True,
            ),
            11,
            0,
        ),
        (
            ProjectionOptions(
                backend="python",
                order="encounter",
                only_taxonomy=True,
            ),
            4,
            5,
        ),
    ],
)
def test_hidden_iterator_admits_supported_direct_restrictions_with_exact_diagnostics(
    python_options: ProjectionOptions,
    raw_edges: int,
    ignored_shapes: int,
) -> None:
    view = _snapshot(
        "SubClassOf(:TaxA :TaxB) "
        "SubClassOf(:A ObjectSomeValuesFrom(:p :B)) "
        "SubClassOf(ObjectAllValuesFrom(ObjectInverseOf(:q) :C) :D) "
        "SubClassOf(:E ObjectMinCardinality(2 :r :F)) "
        'SubClassOf(Annotation(<urn:meta> "duplicate") '
        ":E ObjectMinCardinality(7 :r :F)) "
        "SubClassOf(ObjectMaxCardinality(3 :s :G) :H) "
        "EquivalentClasses(:EqA :EqB) ClassAssertion(:EqA :individual) "
        "ObjectPropertyAssertion(:op :individual :other)"
    )
    expected_projector = Projector()
    expected = expected_projector.project(view, options=python_options)
    expected_report = _completed_report(expected_projector)

    native_projector = Projector()
    actual = list(
        native_projector._iter_native_encoded_edges(
            view,
            options=replace(python_options, backend="native"),
            buffer_edges=2,
        )
    )
    actual_report = _completed_report(native_projector)

    assert actual == expected
    _assert_semantic_report_parity(expected_report, actual_report)
    assert actual_report.provenance.counts.ignored_shapes == ignored_shapes
    if ignored_shapes:
        assert len(actual_report.diagnostics) == 1
        diagnostic = actual_report.diagnostics[0]
        assert diagnostic.code == "MOWL_IGNORED_SHAPE"
        assert diagnostic.constructor == "SubClassOf"
        assert diagnostic.count == ignored_shapes
    else:
        assert actual_report.diagnostics == ()
    ingestion = actual_report.provenance.ingestion
    assert ingestion.path == "encoded-native"
    assert ingestion.counters["native_batch_edges"] == 2
    assert ingestion.counters["native_edge_batches"] == (raw_edges + 1) // 2
    assert ingestion.counters["native_boundary_calls"] == 1 + (raw_edges + 1) // 2
    assert ingestion.counters["native_output_vector_edges"] == raw_edges
    assert ingestion.counters["per_row_ffi_calls"] == 0


@pytest.mark.parametrize(
    ("python_options", "raw_edges"),
    [
        (ProjectionOptions(backend="python", order="encounter"), 7),
        (
            ProjectionOptions(
                backend="python",
                order="canonical",
                duplicates="unique",
                bidirectional_taxonomy=True,
            ),
            8,
        ),
        (
            ProjectionOptions(
                backend="python",
                order="encounter",
                only_taxonomy=True,
            ),
            7,
        ),
    ],
)
def test_hidden_iterator_admits_complete_named_domain_range_product(
    python_options: ProjectionOptions,
    raw_edges: int,
) -> None:
    view = _snapshot(
        "SubClassOf(:TaxA :TaxB) "
        "ObjectPropertyDomain(:p :D2) ObjectPropertyDomain(:p :D1) "
        'ObjectPropertyDomain(Annotation(<urn:meta> "duplicate") :p :D1) '
        "ObjectPropertyRange(:p :R2) ObjectPropertyRange(:p :R1)"
    )
    expected_projector = Projector()
    expected = expected_projector.project(view, options=python_options)
    expected_report = _completed_report(expected_projector)

    native_projector = Projector()
    actual = list(
        native_projector._iter_native_encoded_edges(
            view,
            options=replace(python_options, backend="native"),
            buffer_edges=2,
        )
    )
    actual_report = _completed_report(native_projector)

    assert actual == expected
    _assert_semantic_report_parity(expected_report, actual_report)
    assert actual_report.provenance.ingestion.path == "encoded-native"
    counters = actual_report.provenance.ingestion.counters
    assert counters["native_edge_batches"] == (raw_edges + 1) // 2
    assert counters["native_boundary_calls"] == 1 + (raw_edges + 1) // 2
    assert counters["native_output_vector_edges"] == raw_edges
    assert counters["scalar_axiom_materializations"] == 0
    assert counters["per_row_ffi_calls"] == 0


@pytest.mark.parametrize(
    ("python_options", "raw_edges", "ignored_shapes"),
    [
        (ProjectionOptions(backend="python", order="encounter"), 10, 0),
        (
            ProjectionOptions(
                backend="python",
                order="canonical",
                duplicates="unique",
                bidirectional_taxonomy=True,
            ),
            12,
            0,
        ),
        (
            ProjectionOptions(
                backend="python",
                order="encounter",
                only_taxonomy=True,
            ),
            7,
            1,
        ),
    ],
)
def test_hidden_iterator_admits_same_call_named_role_expansion(
    python_options: ProjectionOptions,
    raw_edges: int,
    ignored_shapes: int,
) -> None:
    view = _snapshot(
        'SubObjectPropertyOf(Annotation(<urn:meta> "subrole") :child :p) '
        'InverseObjectProperties(Annotation(<urn:meta> "inverse") :p :pinv) '
        "SubClassOf(:TaxA :TaxB) EquivalentClasses(:EqA :EqB) "
        "SubClassOf(:A ObjectSomeValuesFrom(:p :B)) "
        "ClassAssertion(:EqA :individual) "
        "ObjectPropertyAssertion(:p :individual :other) "
        "ObjectPropertyDomain(:p :D) ObjectPropertyRange(:p :R)"
    )
    expected_projector = Projector()
    expected = expected_projector.project(view, options=python_options)
    expected_report = _completed_report(expected_projector)

    native_projector = Projector()
    actual = list(
        native_projector._iter_native_encoded_edges(
            view,
            options=replace(python_options, backend="native"),
            buffer_edges=2,
        )
    )
    actual_report = _completed_report(native_projector)

    assert actual == expected
    _assert_semantic_report_parity(expected_report, actual_report)
    assert actual_report.provenance.counts.ignored_shapes == ignored_shapes
    assert actual_report.provenance.ingestion.path == "encoded-native"
    counters = actual_report.provenance.ingestion.counters
    assert counters["native_edge_batches"] == (raw_edges + 1) // 2
    assert counters["native_boundary_calls"] == 1 + (raw_edges + 1) // 2
    assert counters["native_output_vector_edges"] == raw_edges
    assert counters["scalar_axiom_materializations"] == 0
    assert counters["per_row_ffi_calls"] == 0


def test_hidden_iterator_keeps_scala_instance_role_lifecycle_on_scalar_path() -> None:
    view = _snapshot(
        "SubObjectPropertyOf(:child :p) InverseObjectProperties(:p :pinv) "
        "SubClassOf(:A ObjectSomeValuesFrom(:p :B))"
    )
    python_options = ProjectionOptions(
        backend="python",
        order="encounter",
        compatibility_state="scala-instance",
    )
    expected_projector = Projector()
    expected = expected_projector.project(view, options=python_options)
    expected_report = _completed_report(expected_projector)

    native_projector = Projector()
    actual = list(
        native_projector._iter_native_encoded_edges(
            view,
            options=replace(python_options, backend="native"),
            buffer_edges=2,
        )
    )
    actual_report = _completed_report(native_projector)

    assert actual == expected
    _assert_semantic_report_parity(expected_report, actual_report)
    ingestion = actual_report.provenance.ingestion
    assert ingestion.path == "scalar-native"
    assert ingestion.reason is not None
    assert ingestion.reason.startswith(
        "private native direct batches do not bind Scala-instance state"
    )
    assert not any(name.startswith("native_") for name in ingestion.counters)


@pytest.mark.parametrize(
    ("python_options", "raw_edges", "ignored_shapes", "warnings"),
    [
        (
            ProjectionOptions(
                backend="python",
                order="encounter",
                include_literals=True,
            ),
            7,
            0,
            1,
        ),
        (
            ProjectionOptions(
                backend="python",
                order="canonical",
                duplicates="unique",
                bidirectional_taxonomy=True,
                include_literals=True,
            ),
            8,
            0,
            1,
        ),
        (
            ProjectionOptions(
                backend="python",
                order="encounter",
                only_taxonomy=True,
                include_literals=True,
            ),
            6,
            1,
            1,
        ),
        (
            ProjectionOptions(
                backend="python",
                order="encounter",
                include_literals=False,
            ),
            2,
            0,
            0,
        ),
    ],
)
def test_hidden_iterator_admits_fully_selected_class_annotations(
    python_options: ProjectionOptions,
    raw_edges: int,
    ignored_shapes: int,
    warnings: int,
) -> None:
    label = "<http://www.w3.org/2000/01/rdf-schema#label>"
    comment = "<http://www.w3.org/2000/01/rdf-schema#comment>"
    integer = "<http://www.w3.org/2001/XMLSchema#integer>"
    view = _snapshot(
        "Declaration(Class(:A)) SubClassOf(:A :Top) "
        "SubClassOf(:A ObjectSomeValuesFrom(:p :B)) "
        f"AnnotationAssertion({label} :A <urn:value>) "
        f'AnnotationAssertion({comment} :A "bonjour"@fr) '
        f'AnnotationAssertion({label} :A "7"^^{integer}) '
        f'AnnotationAssertion({label} :A "duplicate") '
        f'AnnotationAssertion(Annotation(<urn:meta> "variant") {label} :A "duplicate")'
    )
    expected_projector = Projector()
    expected = expected_projector.project(view, options=python_options)
    expected_report = _completed_report(expected_projector)

    native_projector = Projector()
    actual = list(
        native_projector._iter_native_encoded_edges(
            view,
            options=replace(python_options, backend="native"),
            buffer_edges=2,
        )
    )
    actual_report = _completed_report(native_projector)

    assert actual == expected
    _assert_semantic_report_parity(expected_report, actual_report)
    assert actual_report.provenance.counts.ignored_shapes == ignored_shapes
    assert actual_report.provenance.counts.warnings == warnings
    expected_codes = (
        ("MOWL_IGNORED_SHAPE", "MOWL_NON_STRING_LITERAL_RENDERING")
        if ignored_shapes and warnings
        else ("MOWL_NON_STRING_LITERAL_RENDERING",)
        if warnings
        else ()
    )
    assert tuple(item.code for item in actual_report.diagnostics) == expected_codes
    ingestion = actual_report.provenance.ingestion
    assert ingestion.path == "encoded-native"
    assert ingestion.counters["native_edge_batches"] == (raw_edges + 1) // 2
    assert ingestion.counters["native_boundary_calls"] == 1 + (raw_edges + 1) // 2
    assert ingestion.counters["native_output_vector_edges"] == raw_edges
    assert ingestion.counters["scalar_axiom_materializations"] == 0
    assert ingestion.counters["per_row_ffi_calls"] == 0


def test_hidden_iterator_annotation_selection_is_option_dependent() -> None:
    view = _snapshot(
        "Declaration(Class(:A)) AnnotationAssertion(<urn:unsupported> :A \"ignored\")"
    )
    for include_literals, expected_path in ((False, "encoded-native"), (True, "scalar-native")):
        python_options = ProjectionOptions(
            backend="python",
            order="encounter",
            include_literals=include_literals,
        )
        expected_projector = Projector()
        expected = expected_projector.project(view, options=python_options)
        expected_report = _completed_report(expected_projector)

        native_projector = Projector()
        actual = list(
            native_projector._iter_native_encoded_edges(
                view,
                options=replace(python_options, backend="native"),
                buffer_edges=2,
            )
        )
        actual_report = _completed_report(native_projector)

        assert actual == expected == []
        _assert_semantic_report_parity(expected_report, actual_report)
        assert actual_report.provenance.ingestion.path == expected_path
        if include_literals:
            assert actual_report.provenance.ingestion.reason is not None
            assert actual_report.provenance.counts.ignored_shapes == 1
        else:
            assert actual_report.provenance.ingestion.reason is None
            assert actual_report.provenance.counts.ignored_shapes == 0


def test_public_iterator_keeps_private_capability_and_dispatch_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    features = frozenset(load_native_module().FEATURES)
    assert features == {"abi3-py310", "bounded-batches"}
    assert ENCODED_NATIVE_FEATURE not in features

    def fail_if_private_dispatches(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("public iterator reached private native encoded dispatch")

    monkeypatch.setattr(
        api_module,
        "prepare_native_encoded_compilation",
        fail_if_private_dispatches,
    )
    projector = Projector()
    edges = list(
        projector.iter_edges(
            _snapshot("SubClassOf(:A :B)"),
            options=ProjectionOptions(backend="native", order="encounter"),
        )
    )

    assert len(edges) == 1
    ingestion = _completed_report(projector).provenance.ingestion
    assert ingestion.path == "scalar-native"
    assert ingestion.reason == "native extension does not advertise the P7 encoded compiler"
    assert not any(name.startswith("native_") for name in ingestion.counters)


@pytest.mark.parametrize(
    "body",
    [
        "Declaration(Class(:A)) Declaration(Class(:B)) "
        "EquivalentClasses(:A ObjectSomeValuesFrom(:p :B)) "
        "ClassAssertion(:A :individual)",
        'ClassAssertion(:A :individual) DataPropertyAssertion(:dp :individual "value")',
        "ObjectPropertyAssertion(:p _:anonymous :named)",
        "ObjectPropertyDomain(:p ObjectUnionOf(:A :B)) ObjectPropertyRange(:p :R)",
        "ObjectPropertyDomain(:p :D) ObjectPropertyRange(:q :R)",
        "SubObjectPropertyOf(ObjectPropertyChain(:left :right) :p) "
        "SubClassOf(:A ObjectSomeValuesFrom(:p :B))",
    ],
    ids=[
        "ignored-equivalence",
        "skipped-data-assertion",
        "anonymous-abox",
        "ignored-domain",
        "incomplete-domain-range-product",
        "property-chain-role-map",
    ],
)
def test_hidden_iterator_falls_back_before_output_and_closes_declined_session(
    monkeypatch: pytest.MonkeyPatch,
    body: str,
) -> None:
    view = _snapshot(body)
    python_options = ProjectionOptions(backend="python", order="encounter")
    expected_projector = Projector()
    expected = expected_projector.project(view, options=python_options)
    expected_report = _completed_report(expected_projector)

    closed: list[tuple[str, int]] = []
    real_close = NativeEncodedDirectBatchIterator.close

    def tracking_close(self: NativeEncodedDirectBatchIterator) -> bool:
        result = real_close(self)
        closed.append((self.state, self.remaining_edges))
        return result

    monkeypatch.setattr(NativeEncodedDirectBatchIterator, "close", tracking_close)
    projector = Projector()
    actual = list(
        projector._iter_native_encoded_edges(
            view,
            options=replace(python_options, backend="native"),
            buffer_edges=2,
        )
    )
    actual_report = _completed_report(projector)

    assert actual == expected
    _assert_semantic_report_parity(expected_report, actual_report)
    assert len(closed) == 1
    assert closed[0][0] in {"cancelled", "exhausted"}
    assert closed[0][1] == 0
    ingestion = actual_report.provenance.ingestion
    assert ingestion.path == "scalar-native"
    assert ingestion.reason is not None
    assert ingestion.reason.startswith(
        "private native batch integration accepts only declarations and diagnostic-free named "
        "subclass or supported restriction, equivalence, class-assertion, or "
        "object-property-assertion axioms, direct named/inverse role maps, plus one complete "
        "named domain/range product and fully selected class annotations"
    )
    assert ingestion.reason.endswith("selected whole-operation scalar compiler")
    assert ingestion.encoded_view_publication_seconds is None
    assert not any(name.startswith("native_") for name in ingestion.counters)
    assert all(
        value is False if name == "encoded_compiler_gil_released" else value == 0
        for name, value in ingestion.counters.items()
    )


def test_hidden_iterator_close_and_cancellation_clear_unpublished_native_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _snapshot(" ".join(f"SubClassOf(:C{index} :Top)" for index in range(8)))
    captured: list[NativeEncodedDirectCompilation] = []
    real_prepare = api_module.prepare_native_encoded_compilation

    def capture_compilation(
        *args: Any,
        **kwargs: Any,
    ) -> tuple[NativeEncodedDirectCompilation | None, str | None]:
        result = real_prepare(*args, **kwargs)
        if result[0] is not None:
            captured.append(result[0])
        return result

    monkeypatch.setattr(api_module, "prepare_native_encoded_compilation", capture_compilation)
    options = ProjectionOptions(backend="native", order="encounter")

    closed_projector = Projector()
    closed_iterator = closed_projector._iter_native_encoded_edges(
        view,
        options=options,
        buffer_edges=3,
    )
    assert next(closed_iterator)
    closed_compilation = captured[-1]
    assert closed_compilation.batches.state == "active"
    closed_iterator.close()  # type: ignore[attr-defined]
    assert closed_compilation.batches.state == "cancelled"
    assert closed_compilation.batches.remaining_edges == 0
    assert closed_projector.last_report is None

    class ToggleCancellation:
        cancelled = False

        def check(self) -> None:
            if self.cancelled:
                raise RuntimeError("injected native integration cancellation")

    cancellation = ToggleCancellation()
    cancelled_projector = Projector()
    cancelled_iterator = cancelled_projector._iter_native_encoded_edges(
        view,
        options=options,
        buffer_edges=3,
        cancellation_token=cancellation,
    )
    assert [next(cancelled_iterator) for _ in range(3)]
    cancelled_compilation = captured[-1]
    cancellation.cancelled = True
    with pytest.raises(RuntimeError, match="injected native integration cancellation"):
        next(cancelled_iterator)
    assert cancelled_compilation.batches.state == "cancelled"
    assert cancelled_compilation.batches.remaining_edges == 0
    assert cancelled_compilation.batches.edge_batches == 2
    assert cancelled_compilation.batches.boundary_calls == 3
    assert cancelled_projector.last_report is None


def test_hidden_iterator_native_resource_failure_publishes_no_report() -> None:
    projector = Projector()
    iterator = projector._iter_native_encoded_edges(
        _snapshot("SubClassOf(:A :Top) SubClassOf(:B :Top) SubClassOf(:C :Top)"),
        options=ProjectionOptions(backend="native", order="encounter"),
        buffer_edges=1,
        streaming_limits=StreamingLimits(max_total_edges=2),
    )

    with pytest.raises(ProjectionResourceError, match="configured edge resources"):
        next(iterator)
    assert projector.last_report is None
