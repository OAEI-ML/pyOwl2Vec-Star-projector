from __future__ import annotations

import io
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import Any
from unittest.mock import patch

import pyowl_core
import pytest

import pyowl2vec_star_projector.api as api_module
import pyowl2vec_star_projector.encoded as encoded_module
import pyowl2vec_star_projector.encoded_compiler as encoded_compiler_module
import pyowl2vec_star_projector.native as native_module
from pyowl2vec_star_projector import (
    BATCH_SINK_PROTOCOL_VERSION,
    Edge,
    ProjectionOptions,
    Projector,
    probe_native_backend,
)
from pyowl2vec_star_projector.backend import BackendSelection
from pyowl2vec_star_projector.encoded import ENCODED_NATIVE_FEATURE
from pyowl2vec_star_projector.provenance import ProjectionReport

NATIVE_AVAILABLE = probe_native_backend().available

pytestmark = pytest.mark.skipif(
    not NATIVE_AVAILABLE,
    reason="optional native extension is not installed",
)


def _snapshot(
    *,
    backend: pyowl_core.BackendPreference,
    body: bytes | None = None,
) -> object:
    ontology_body = (
        body
        if body is not None
        else (
            b"SubClassOf(:A :B) "
            b'SubClassOf(Annotation(<urn:meta> "duplicate") :A :B) '
            b"SubClassOf(:C :A) "
            b'AnnotationAssertion(<http://www.w3.org/2000/01/rdf-schema#label> :A "caf\xc3\xa9")'
        )
    )
    source = b"Prefix(:=<urn:public-direct#>) Ontology(<urn:public-direct> " + ontology_body + b")"
    return pyowl_core.load_snapshot(
        source,
        options=pyowl_core.LoadOptions(
            imports=pyowl_core.ImportPolicy.IGNORE,
            backend=backend,
        ),
    )


@contextmanager
def _advertised_public_direct_session() -> Iterator[None]:
    with (
        patch.object(
            api_module,
            "prepare_encoded_subset_compilation",
            side_effect=AssertionError("public direct session reached broad encoded compilation"),
        ),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("public direct session reached scalar compilation"),
        ),
        patch.object(
            api_module,
            "iter_native_passthrough",
            side_effect=AssertionError("public direct session reached per-edge native passthrough"),
        ),
    ):
        yield


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


def _assert_public_direct_report(
    projector: Projector,
    view: object,
    report: ProjectionReport,
    expected: ProjectionReport,
) -> None:
    assert projector.last_view is view
    assert projector.last_report is report
    _assert_semantic_report_parity(expected, report)
    ingestion = report.provenance.ingestion
    assert ingestion.path == "encoded-native"
    assert ingestion.reason is None
    assert ingestion.encoded_schema_name == "pyowl-core/structural-columns"
    assert ingestion.encoded_schema_version == 1
    counters = ingestion.counters
    assert counters["native_compiled_edges"] == 4
    assert counters["native_output_vector_edges"] == 0
    assert counters["native_peak_buffered_edges"] == 2
    assert counters["encoded_compiler_gil_released"] is True
    for name in (
        "base_flattening_bytes",
        "encoded_indexed_buffer_count",
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


class _Sink:
    protocol_version = BATCH_SINK_PROTOCOL_VERSION

    def __init__(self) -> None:
        self.batches: list[tuple[Edge, ...]] = []
        self.report: ProjectionReport | None = None

    def write_batch(self, batch: tuple[Edge, ...]) -> None:
        self.batches.append(batch)

    def finish(self, report: ProjectionReport) -> None:
        self.report = report


@pytest.mark.parametrize(
    "provider_backend",
    [
        pyowl_core.BackendPreference.PYTHON,
        pyowl_core.BackendPreference.NATIVE,
    ],
    ids=["independent-bytes", "packed-bytes"],
)
def test_public_surfaces_bind_the_advertised_direct_session(
    provider_backend: pyowl_core.BackendPreference,
    tmp_path: Any,
) -> None:
    view = _snapshot(backend=provider_backend)
    assert view.report.backend == provider_backend.value  # type: ignore[attr-defined]
    python_options = ProjectionOptions(
        backend="python",
        order="canonical",
        duplicates="unique",
        include_literals=True,
    )
    native_options = replace(python_options, backend="native")

    expected_iterator_projector = Projector()
    expected_edges = list(
        expected_iterator_projector.iter_edges(
            view,
            options=python_options,
            buffer_edges=2,
            temp_directory=tmp_path,
        )
    )
    expected_iterator_report = expected_iterator_projector.last_report
    assert expected_iterator_report is not None

    expected_sink = _Sink()
    expected_sink_report = Projector().project_to_sink(
        view,
        expected_sink,
        options=python_options,
        batch_size=2,
        buffer_edges=2,
        temp_directory=tmp_path,
    )
    expected_digest = Projector().canonical_digest(
        view,
        options=python_options,
        buffer_edges=2,
        temp_directory=tmp_path,
    )
    expected_destination = io.BytesIO()
    expected_artifact = Projector().write_artifact(
        view,
        expected_destination,
        options=python_options,
        buffer_edges=2,
        temp_directory=tmp_path,
    )

    with _advertised_public_direct_session():
        iterator_projector = Projector()
        actual_edges = list(
            iterator_projector.iter_edges(
                view,
                options=native_options,
                buffer_edges=2,
                temp_directory=tmp_path,
            )
        )
        iterator_report = iterator_projector.last_report
        assert iterator_report is not None

        sink = _Sink()
        sink_projector = Projector()
        sink_report = sink_projector.project_to_sink(
            view,
            sink,
            options=native_options,
            batch_size=2,
            buffer_edges=2,
            temp_directory=tmp_path,
        )

        digest_projector = Projector()
        digest = digest_projector.canonical_digest(
            view,
            options=native_options,
            buffer_edges=2,
            temp_directory=tmp_path,
        )

        artifact_projector = Projector()
        destination = io.BytesIO()
        artifact = artifact_projector.write_artifact(
            view,
            destination,
            options=native_options,
            buffer_edges=2,
            temp_directory=tmp_path,
        )

    assert actual_edges == expected_edges
    _assert_public_direct_report(
        iterator_projector,
        view,
        iterator_report,
        expected_iterator_report,
    )

    assert sink.batches == expected_sink.batches
    assert sink.report is sink_report
    _assert_public_direct_report(sink_projector, view, sink_report, expected_sink_report)

    assert (
        digest.sha256,
        digest.edge_count,
        digest.duplicate_count,
    ) == (
        expected_digest.sha256,
        expected_digest.edge_count,
        expected_digest.duplicate_count,
    )
    _assert_public_direct_report(
        digest_projector,
        view,
        digest.report,
        expected_digest.report,
    )

    assert destination.getvalue() == expected_destination.getvalue()
    assert (
        artifact.artifact_sha256,
        artifact.canonical_edges_sha256,
        artifact.edge_count,
        artifact.duplicate_count,
        artifact.bytes_written,
        artifact.metadata,
    ) == (
        expected_artifact.artifact_sha256,
        expected_artifact.canonical_edges_sha256,
        expected_artifact.edge_count,
        expected_artifact.duplicate_count,
        expected_artifact.bytes_written,
        expected_artifact.metadata,
    )
    _assert_public_direct_report(
        artifact_projector,
        view,
        artifact.report,
        expected_artifact.report,
    )
    assert list(tmp_path.iterdir()) == []


def test_public_direct_decline_selects_transactional_scalar_fallback() -> None:
    view = _snapshot(backend=pyowl_core.BackendPreference.PYTHON)
    options = ProjectionOptions(backend="native", order="encounter")
    selection = BackendSelection("native", "native")
    lease = encoded_module.select_private_direct_ingestion(
        view,
        selected_backend="native",
    ).lease
    assert lease is not None
    subset_compiler = encoded_compiler_module.prepare_encoded_subset_compilation

    with (
        patch.object(api_module, "select_backend", return_value=selection),
        patch.object(
            api_module,
            "_activate_selection",
            return_value=(
                selection,
                "test-public-direct",
                frozenset({ENCODED_NATIVE_FEATURE}),
            ),
        ),
        patch.object(
            api_module,
            "select_ingestion",
            return_value=encoded_module.EncodedNegotiation("encoded-native", lease=lease),
        ),
        patch.object(
            api_module,
            "prepare_native_encoded_compilation",
            return_value=(None, "unsupported public direct shape"),
        ),
        patch.object(
            api_module,
            "prepare_encoded_subset_compilation",
            wraps=subset_compiler,
        ) as subset_call,
    ):
        projector = Projector()
        list(projector.iter_edges(view, options=options))

    subset_call.assert_called_once()
    assert subset_call.call_args.args[0] is view
    fallback = subset_call.call_args.args[2]
    assert fallback.path == "scalar-native"
    assert fallback.reason == (
        "unsupported public direct shape; selected whole-operation scalar compiler"
    )
    report = projector.last_report
    assert report is not None
    ingestion = report.provenance.ingestion
    assert ingestion.path == "scalar-native"
    assert ingestion.reason == fallback.reason
    assert ingestion.encoded_view_publication_seconds is None
    assert all(
        value is False if name == "encoded_compiler_gil_released" else value == 0
        for name, value in ingestion.counters.items()
    )


@pytest.mark.parametrize(
    "provider_backend",
    [
        pyowl_core.BackendPreference.PYTHON,
        pyowl_core.BackendPreference.NATIVE,
    ],
    ids=["independent-bytes", "packed-bytes"],
)
def test_public_asserted_taxonomy_binds_the_advertised_direct_session(
    provider_backend: pyowl_core.BackendPreference,
    tmp_path: Any,
) -> None:
    view = _snapshot(
        backend=provider_backend,
        body=(
            b"SubClassOf(:A :B) "
            b"EquivalentClasses(:A :C :D) "
            b"ClassAssertion(:A :i) "
            b"ObjectPropertyAssertion(:p :i :j) "
            b"SubClassOf(:C ObjectSomeValuesFrom(:p :D))"
        ),
    )
    expected = Projector().project_taxonomy(
        view,
        bidirectional=True,
        duplicates="unique",
        order="canonical",
        backend="python",
        buffer_edges=1,
        temp_directory=tmp_path,
    )
    captured: list[Any] = []
    direct_preparation = native_module.prepare_native_encoded_compilation

    def capture_preparation(*args: Any, **kwargs: Any) -> Any:
        result = direct_preparation(*args, **kwargs)
        captured.append((args, kwargs, result))
        return result

    with (
        _advertised_public_direct_session(),
        patch.object(
            api_module,
            "prepare_native_encoded_compilation",
            side_effect=capture_preparation,
        ),
    ):
        projector = Projector()
        actual = projector.project_taxonomy(
            view,
            bidirectional=True,
            duplicates="unique",
            order="canonical",
            backend="native",
            buffer_edges=1,
            temp_directory=tmp_path,
        )

    assert actual == expected
    assert len(actual) == 2
    assert projector.last_view is view
    assert projector.last_encoded_counters is None
    assert len(captured) == 1
    _args, kwargs, result = captured[0]
    assert kwargs["asserted_taxonomy_only"] is True
    compilation, fallback_reason = result
    assert fallback_reason is None
    assert compilation is not None
    assert compilation.batches.state == "exhausted"
    counters = compilation.ingestion_counters
    assert counters["native_compiled_edges"] == 2
    assert counters["native_edge_batches"] == 2
    assert counters["native_boundary_calls"] == 3
    assert counters["native_peak_buffered_edges"] == 1
    assert counters["materialized_scalar_rows"] == 0
    assert counters["per_row_ffi_calls"] == 0
    assert list(tmp_path.iterdir()) == []
