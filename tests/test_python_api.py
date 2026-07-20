from __future__ import annotations

import itertools
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, cast

import pyowl_core
import pytest
from pyowl_core import OperationCancelledError, UnresolvedImportError

from pyowl2vec_star_projector import (
    InvalidProjectionOptionsError,
    ProjectionOptions,
    Projector,
    SnapshotCompatibilityError,
    iter_source_edges,
    project_source,
    project_taxonomy,
)
from pyowl2vec_star_projector import api as api_module

from .support.core_views import Capabilities, ConformingView, Provider, fixture_view


def test_strict_view_boundary_preserves_concrete_identity_and_nodes() -> None:
    view = fixture_view("taxonomy-restrictions")
    original_ids = {id(axiom) for axiom in view.documents[0].axioms}
    projector = Projector()
    edges = projector.project(view, options=ProjectionOptions(backend="python"))
    assert edges
    assert projector.last_view is view
    assert set(view.iterated_identities).issubset(original_ids)


def test_overlay_identity_is_not_materialized() -> None:
    base = fixture_view("equivalence-ordering")

    class OverlayView(ConformingView):
        def __init__(self, base_view: ConformingView) -> None:
            super().__init__(base_view.documents)
            self.base = base_view

    overlay = OverlayView(base)
    projector = Projector()
    assert projector.project(overlay, options=ProjectionOptions(backend="python"))
    assert projector.last_view is overlay
    assert overlay.base is base


def test_provider_and_wire_activation_coerce_once_by_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = ConformingView(fixture_view("equivalence-ordering").documents, wire_verified=True)
    provider = Provider(view)
    calls: list[tuple[object, object, object]] = []

    def coerce(source: object, *, options: object, resolver: object) -> object:
        calls.append((source, options, resolver))
        return source.owl_snapshot()  # type: ignore[attr-defined]

    monkeypatch.setattr(pyowl_core, "coerce_snapshot", coerce, raising=False)
    options_marker = object()
    resolver_marker = object()
    edges = project_source(
        provider,
        options=ProjectionOptions(backend="python"),
        load_options=options_marker,
        resolver=resolver_marker,
    )
    assert edges
    assert provider.calls == 1
    assert calls == [(provider, options_marker, resolver_marker)]


def test_iter_source_coerces_before_returning_iterator(monkeypatch: pytest.MonkeyPatch) -> None:
    view = fixture_view("equivalence-ordering")
    calls = 0

    def coerce(source: object, *, options: object, resolver: object) -> object:
        nonlocal calls
        del source, options, resolver
        calls += 1
        return view

    monkeypatch.setattr(pyowl_core, "coerce_snapshot", coerce, raising=False)
    iterator = iter_source_edges("logical source", options=ProjectionOptions(backend="python"))
    assert calls == 1
    assert list(iterator)
    assert calls == 1


def test_core_loader_failures_remain_typed_and_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = UnresolvedImportError("missing import")

    def coerce(source: object, *, options: object, resolver: object) -> object:
        del source, options, resolver
        raise expected

    monkeypatch.setattr(pyowl_core, "coerce_snapshot", coerce, raising=False)
    with pytest.raises(UnresolvedImportError) as raised:
        project_source("missing.ofn", options=ProjectionOptions(backend="python"))
    assert raised.value is expected


def test_delayed_facade_error_is_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(pyowl_core, "coerce_snapshot", raising=False)
    with pytest.raises(SnapshotCompatibilityError, match="core WP03"):
        project_source("ontology.ofn", options=ProjectionOptions(backend="python"))


def test_materialized_iterator_and_batch_sink_are_equivalent() -> None:
    view = fixture_view("domain-range")
    options = ProjectionOptions(backend="python", order="encounter")
    expected = Projector().project(view, options=options)
    projector = Projector()
    batches: list[tuple[object, ...]] = []
    report = projector.project_to_sink(view, batches.append, options=options, batch_size=4)
    flattened = [edge for batch in batches for edge in batch]
    assert flattened == expected
    assert batches and all(len(batch) <= 4 for batch in batches)
    assert report.provenance.counts.edges == len(expected)


def test_duplicate_policy_and_utf8_canonical_order() -> None:
    view = fixture_view("domain-range")
    preserve = Projector().project(view, options=ProjectionOptions(backend="python"))
    unique = Projector().project(
        view,
        options=ProjectionOptions(backend="python", duplicates="unique"),
    )
    assert len(preserve) == 27
    assert len(unique) == 12
    assert unique == sorted(set(preserve), key=lambda edge: edge.canonical_key())


def test_asserted_taxonomy_is_separate_from_only_taxonomy_defect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = fixture_view("taxonomy-restrictions")
    monkeypatch.setattr(
        pyowl_core,
        "coerce_snapshot",
        lambda source, **kwargs: source,
        raising=False,
    )
    taxonomy = project_taxonomy(view, backend="python")
    compatibility = Projector().project(
        view,
        options=ProjectionOptions(backend="python", only_taxonomy=True),
    )
    assert [edge.as_tuple() for edge in taxonomy] == [
        ("urn:oracle:taxonomy#A", "http://subclassof", "urn:oracle:taxonomy#B")
    ]
    assert compatibility == taxonomy
    assert all(edge.relation == "http://subclassof" for edge in taxonomy)


def test_imported_only_annotation_is_not_projected() -> None:
    view = fixture_view("imports-one-level")
    edges = Projector().project(
        view,
        options=ProjectionOptions(backend="python", include_literals=True),
    )
    destinations = {edge.destination for edge in edges}
    assert "urn:oracle:imports-one#RootTop" in destinations
    assert "Imported leaf label" not in destinations


def test_report_groups_diagnostics_without_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    projector = Projector()
    result = projector.project_with_report(
        fixture_view("annotations"),
        options=ProjectionOptions(backend="python", include_literals=True),
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    warnings = [
        item
        for item in result.report.diagnostics
        if item.code == "MOWL_NON_STRING_LITERAL_RENDERING"
    ]
    assert warnings and warnings[0].count == 3
    assert result.report.provenance.counts.warnings == 3
    assert result.report.provenance.selected_backend == "python"
    assert result.report.provenance.ingestion.path == "scalar-python"
    assert len(result.report.provenance.diagnostics_digest) == 64


def test_scalar_report_exposes_compiler_timing_without_encoded_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticks = iter((10.0, 10.125, 20.0, 20.375))
    monkeypatch.setattr(api_module, "perf_counter", lambda: next(ticks))
    projector = Projector()

    projector.project(
        fixture_view("equivalence-ordering"),
        options=ProjectionOptions(backend="python"),
    )

    assert projector.last_report is not None
    ingestion = projector.last_report.provenance.ingestion
    assert ingestion.path == "scalar-python"
    assert ingestion.encoded_view_publication_seconds is None
    assert ingestion.consumer_compile_seconds == pytest.approx(0.375)
    assert ingestion.counters == {
        "encoded_buffer_bytes": 0,
        "encoded_buffer_count": 0,
        "encoded_compiler_gil_released": False,
        "encoded_detached_buffer_count": 0,
        "encoded_indexed_buffer_count": 0,
        "encoded_posting_bytes": 0,
        "encoded_referenced_view_count": 0,
        "encoded_segment_count": 0,
        "encoded_staging_copy_bytes": 0,
        "encoded_zero_copy_buffers": 0,
        "materialized_scalar_rows": 0,
    }


def test_isolated_projector_is_reentrant_across_threads() -> None:
    view = fixture_view("rbox-collisions")
    projector = Projector()
    options = ProjectionOptions(backend="python", compatibility_state="isolated")
    expected = projector.project(view, options=options)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: projector.project(view, options=options), range(16)))
    assert all(result == expected for result in results)


def test_scala_instance_rejects_overlap_and_close_releases_state_lock() -> None:
    view = fixture_view("rbox-collisions")
    projector = Projector()
    options = ProjectionOptions(
        backend="python",
        order="encounter",
        compatibility_state="scala-instance",
    )
    first = projector.iter_edges(view, options=options)
    assert next(first)
    with pytest.raises(InvalidProjectionOptionsError, match="non-concurrent"):
        list(projector.iter_edges(view, options=options))
    cast(Any, first).close()
    assert list(projector.iter_edges(view, options=options))


def test_cancellation_propagates_the_core_exception() -> None:
    class CancellingView(ConformingView):
        def iter_axioms(self, axiom_type: object = None, *, scope: object = "closure") -> object:
            if str(getattr(scope, "value", scope)).lower() == "root":
                return super().iter_axioms(axiom_type, scope=scope)  # type: ignore[arg-type]

            def cancelled() -> object:
                yield from ()
                raise OperationCancelledError(reason="test")

            return cancelled()

    base = fixture_view("equivalence-ordering")
    with pytest.raises(OperationCancelledError) as raised:
        Projector().project(
            CancellingView(base.documents),
            options=ProjectionOptions(backend="python"),
        )
    assert raised.value.reason == "test"


@dataclass(frozen=True, slots=True)
class BadCapabilities:
    adapter_protocol: int = 2
    model_schema: int = 99
    wire_format: tuple[int, int] = (1, 0)


def test_malformed_and_incompatible_views_fail_before_compilation() -> None:
    with pytest.raises(SnapshotCompatibilityError, match="iter_axioms"):
        Projector().project(object(), options=ProjectionOptions(backend="python"))

    class BadView:
        capabilities = BadCapabilities()

        def iter_axioms(self, axiom_type: object = None, *, scope: object = None) -> object:
            del axiom_type, scope
            return iter(())

        def signature(self, kind: object = None, *, scope: object = None) -> tuple[()]:
            del kind, scope
            return ()

    with pytest.raises(SnapshotCompatibilityError, match="schema"):
        Projector().project(BadView(), options=ProjectionOptions(backend="python"))

    class MalformedCapabilities:
        adapter_protocol = True
        model_schema = 1
        wire_format = (1, "zero")

    BadView.capabilities = MalformedCapabilities()  # type: ignore[assignment]
    with pytest.raises(SnapshotCompatibilityError, match="schema"):
        Projector().project(BadView(), options=ProjectionOptions(backend="python"))

    MalformedCapabilities.adapter_protocol = 1
    MalformedCapabilities.wire_format = (2, 0)
    with pytest.raises(SnapshotCompatibilityError, match="wire"):
        Projector().project(BadView(), options=ProjectionOptions(backend="python"))


def test_wire_source_kind_and_taxonomy_backend_validation() -> None:
    view = ConformingView(fixture_view("equivalence-ordering").documents, wire_verified=True)
    projector = Projector()
    projector.project(view, options=ProjectionOptions(backend="python"))
    assert projector.last_report is not None
    assert projector.last_report.provenance.source_kind == "wire"
    with pytest.raises(InvalidProjectionOptionsError, match="backend"):
        Projector().project_taxonomy(view, backend="gpu")  # type: ignore[arg-type]


def test_determinism_is_independent_of_document_axiom_permutation() -> None:
    documents = fixture_view("equivalence-ordering").documents
    view = ConformingView(documents)
    baseline = Projector().project(view, options=ProjectionOptions(backend="python"))
    # Repeated view scans and every flag combination retain canonical output.
    for bidirectional, only_taxonomy, include_literals in itertools.product(
        (False, True), repeat=3
    ):
        options = ProjectionOptions(
            backend="python",
            bidirectional_taxonomy=bidirectional,
            only_taxonomy=only_taxonomy,
            include_literals=include_literals,
        )
        assert Projector().project(view, options=options) == Projector().project(
            view, options=options
        )
    assert Projector().project(view, options=ProjectionOptions(backend="python")) == baseline


def test_batch_and_buffer_sizes_are_strict_positive_ints() -> None:
    view = fixture_view("equivalence-ordering")
    with pytest.raises(InvalidProjectionOptionsError):
        Projector().iter_edges(view, buffer_edges=0)
    with pytest.raises(InvalidProjectionOptionsError):
        Projector().project_to_sink(view, lambda batch: None, batch_size=True)


def test_scala_state_invocation_history_is_recorded() -> None:
    projector = Projector()
    options = ProjectionOptions(backend="python", compatibility_state="scala-instance")
    projector.project(fixture_view("lifecycle-a"), options=options)
    first = projector.last_report
    projector.project(fixture_view("lifecycle-b"), options=options)
    second = projector.last_report
    assert first is not None and second is not None
    assert first.provenance.invocation_count == 1
    assert second.provenance.invocation_count == 2
    assert first.provenance.call_history_digest != second.provenance.call_history_digest


def test_isolated_provenance_is_not_call_history_sensitive() -> None:
    projector = Projector()
    view = fixture_view("equivalence-ordering")
    options = ProjectionOptions(backend="python")
    projector.project(view, options=options)
    first = projector.last_report
    projector.project(view, options=options)
    second = projector.last_report
    assert first is not None and second is not None
    assert first.provenance.invocation_count == second.provenance.invocation_count == 1
    assert first.provenance.call_history_digest == second.provenance.call_history_digest


def test_identity_view_protocol_does_not_require_subclassing() -> None:
    view = fixture_view("equivalence-ordering")
    assert view.capabilities == Capabilities()
    assert not isinstance(view, threading.Thread)
    assert Projector().project(view, options=ProjectionOptions(backend="python"))
