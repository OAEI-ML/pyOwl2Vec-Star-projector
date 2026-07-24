from __future__ import annotations

import gc
import io
import os
import subprocess
import sys
import weakref
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import MappingProxyType
from typing import Any, cast
from unittest.mock import patch

import pyowl_core
import pytest
from pyowl_core.backends.python import PythonParser

import pyowl2vec_star_projector.api as api_module
import pyowl2vec_star_projector.native as native_module
from pyowl2vec_star_projector import (
    BATCH_SINK_PROTOCOL_VERSION,
    Edge,
    ProjectionOptions,
    ProjectionResourceError,
    Projector,
    SnapshotCompatibilityError,
    StreamingLimits,
    probe_native_backend,
)
from pyowl2vec_star_projector.compiler import RoleState
from pyowl2vec_star_projector.encoded import (
    ENCODED_NATIVE_FEATURE,
    EncodedNegotiation,
    EncodedStructuralLease,
    _resolve_private_single_overlay_delta,
    select_private_direct_ingestion,
)
from pyowl2vec_star_projector.native import (
    ENCODED_DIRECT_BUFFER_ORDER,
    NativeEncodedDirectBatchIterator,
    NativeEncodedDirectCompilation,
    NativeEncodedDirectCompiler,
    load_native_module,
    prepare_native_encoded_direct,
)
from pyowl2vec_star_projector.provenance import ProjectionReport

NATIVE_AVAILABLE = probe_native_backend().available

pytestmark = pytest.mark.skipif(
    not NATIVE_AVAILABLE,
    reason="optional native extension is not installed",
)


def _snapshot(
    body: str,
    *,
    backend: pyowl_core.BackendPreference = pyowl_core.BackendPreference.PYTHON,
) -> object:
    source = f"Prefix(:=<urn:native-integration#>) Ontology(<urn:native-integration> {body})"
    return pyowl_core.load_snapshot(
        source.encode(),
        options=pyowl_core.LoadOptions(
            imports=pyowl_core.ImportPolicy.IGNORE,
            backend=backend,
        ),
    )


def _imported_snapshot() -> object:
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
    return pyowl_core.load_snapshot(
        root,
        options=pyowl_core.LoadOptions(
            imports=pyowl_core.ImportPolicy.RESOLVE_LOCAL,
            backend=pyowl_core.BackendPreference.PYTHON,
        ),
        resolver=pyowl_core.MappingResolver({"urn:leaf": leaf}),
    )


def _imported_snapshot_without_annotations() -> object:
    root = b"Prefix(:=<urn:root#>) Ontology(<urn:root> Import(<urn:leaf>) Declaration(Class(:A)))"
    leaf = (
        b"Prefix(:=<urn:leaf#>) Ontology(<urn:leaf> Declaration(Class(:L)) "
        b"SubClassOf(:L <urn:root#A>))"
    )
    return pyowl_core.load_snapshot(
        root,
        options=pyowl_core.LoadOptions(
            imports=pyowl_core.ImportPolicy.RESOLVE_LOCAL,
            backend=pyowl_core.BackendPreference.PYTHON,
        ),
        resolver=pyowl_core.MappingResolver({"urn:leaf": leaf}),
    )


def _imported_snapshot_with_only_leaf_annotation() -> object:
    root = b"Prefix(:=<urn:root#>) Ontology(<urn:root> Import(<urn:leaf>) Declaration(Class(:A)))"
    leaf = (
        b"Prefix(:=<urn:leaf#>) Ontology(<urn:leaf> Declaration(Class(:L)) "
        b"SubClassOf(:L <urn:root#A>) "
        b'AnnotationAssertion(<http://www.w3.org/2000/01/rdf-schema#label> :L "leaf"))'
    )
    return pyowl_core.load_snapshot(
        root,
        options=pyowl_core.LoadOptions(
            imports=pyowl_core.ImportPolicy.RESOLVE_LOCAL,
            backend=pyowl_core.BackendPreference.PYTHON,
        ),
        resolver=pyowl_core.MappingResolver({"urn:leaf": leaf}),
    )


def _imported_snapshot_with_anonymous_annotation_values() -> object:
    root = (
        b"Prefix(:=<urn:root#>) Ontology(<urn:root> Import(<urn:leaf>) "
        b"Declaration(Class(:A)) "
        b"AnnotationAssertion(<http://www.w3.org/2000/01/rdf-schema#label> :A _:rootValue))"
    )
    leaf = (
        b"Prefix(:=<urn:leaf#>) Ontology(<urn:leaf> Declaration(Class(:L)) "
        b"SubClassOf(:L <urn:root#A>) "
        b"AnnotationAssertion(<http://www.w3.org/2000/01/rdf-schema#label> :L _:leafValue))"
    )
    return pyowl_core.load_snapshot(
        root,
        options=pyowl_core.LoadOptions(
            imports=pyowl_core.ImportPolicy.RESOLVE_LOCAL,
            backend=pyowl_core.BackendPreference.PYTHON,
        ),
        resolver=pyowl_core.MappingResolver({"urn:leaf": leaf}),
    )


def _diamond_import_snapshot() -> object:
    label = b"<http://www.w3.org/2000/01/rdf-schema#label>"
    root = (
        b"Prefix(:=<urn:diamond#>) Ontology(<urn:diamond-root> "
        b"Import(<urn:diamond-left>) Import(<urn:diamond-right>) "
        b"SubClassOf(:Root :Top) AnnotationAssertion("
        + label
        + b' :Root "root"))'
    )
    left = (
        b"Prefix(:=<urn:diamond#>) Ontology(<urn:diamond-left> "
        b"Import(<urn:diamond-common>) SubClassOf(:Left :Top) AnnotationAssertion("
        + label
        + b' :Left "left"))'
    )
    right = (
        b"Prefix(:=<urn:diamond#>) Ontology(<urn:diamond-right> "
        b"Import(<urn:diamond-common>) SubClassOf(:Right :Top) AnnotationAssertion("
        + label
        + b' :Right "right"))'
    )
    common = (
        b"Prefix(:=<urn:diamond#>) Ontology(<urn:diamond-common> "
        b"SubClassOf(:Common :Top) AnnotationAssertion("
        + label
        + b' :Common "common"))'
    )
    return pyowl_core.load_snapshot(
        root,
        options=pyowl_core.LoadOptions(
            imports=pyowl_core.ImportPolicy.RESOLVE_LOCAL,
            backend=pyowl_core.BackendPreference.PYTHON,
        ),
        resolver=pyowl_core.MappingResolver(
            {
                "urn:diamond-left": left,
                "urn:diamond-right": right,
                "urn:diamond-common": common,
            }
        ),
    )


def _cyclic_import_snapshot() -> object:
    label = b"<http://www.w3.org/2000/01/rdf-schema#label>"
    first = (
        b"Prefix(:=<urn:cycle#>) Ontology(<urn:cycle-a> Import(<urn:cycle-b>) "
        b"SubClassOf(:A :Top) AnnotationAssertion("
        + label
        + b' :A "a-root"))'
    )
    second = (
        b"Prefix(:=<urn:cycle#>) Ontology(<urn:cycle-b> Import(<urn:cycle-a>) "
        b"SubClassOf(:B :Top) AnnotationAssertion("
        + label
        + b' :B "b-imported"))'
    )
    return pyowl_core.load_snapshot(
        first,
        options=pyowl_core.LoadOptions(
            imports=pyowl_core.ImportPolicy.RESOLVE_LOCAL,
            backend=pyowl_core.BackendPreference.PYTHON,
        ),
        resolver=pyowl_core.MappingResolver(
            {
                "urn:cycle-a": first,
                "urn:cycle-b": second,
            }
        ),
    )


def _swrl_snapshot(body: str) -> object:
    source = f"Prefix(:=<urn:native-integration#>) Ontology(<urn:native-integration> {body})"
    options = pyowl_core.LoadOptions(
        imports=pyowl_core.ImportPolicy.IGNORE,
        backend=pyowl_core.BackendPreference.PYTHON,
    )
    document = PythonParser().parse(source.encode(), options=options, allow_swrl=True)
    return pyowl_core.load_snapshot(document, options=options)


def _completed_report(projector: Projector) -> ProjectionReport:
    report = projector.last_report
    assert report is not None
    return report


def _assert_bounded_native_output(
    counters: Mapping[str, int | bool],
    *,
    compiled_edges: int,
    batch_edges: int,
) -> None:
    assert counters["native_compiled_edges"] == compiled_edges
    assert counters["native_output_vector_edges"] == 0
    assert counters["native_peak_buffered_edges"] == min(batch_edges, compiled_edges)


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
    _assert_bounded_native_output(counters, compiled_edges=raw_edges, batch_edges=2)
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


def test_hidden_cursor_feeds_sink_digest_and_artifact_surfaces(tmp_path: Any) -> None:
    view = _snapshot(
        "SubClassOf(:A :B) "
        'SubClassOf(Annotation(<urn:meta> "duplicate") :A :B) '
        "SubClassOf(:C :A) "
        'AnnotationAssertion(<http://www.w3.org/2000/01/rdf-schema#label> :A "café")'
    )
    python_options = ProjectionOptions(
        backend="python",
        order="canonical",
        duplicates="unique",
        include_literals=True,
    )
    native_options = replace(python_options, backend="native")

    class Sink:
        protocol_version = BATCH_SINK_PROTOCOL_VERSION

        def __init__(self) -> None:
            self.batches: list[tuple[Edge, ...]] = []
            self.report: ProjectionReport | None = None

        def write_batch(self, batch: tuple[Edge, ...]) -> None:
            self.batches.append(batch)

        def finish(self, report: ProjectionReport) -> None:
            self.report = report

    expected_sink = Sink()
    expected_sink_projector = Projector()
    expected_sink_report = expected_sink_projector.project_to_sink(
        view,
        expected_sink,
        options=python_options,
        batch_size=2,
        buffer_edges=2,
        temp_directory=tmp_path,
    )
    native_sink = Sink()
    native_sink_projector = Projector()
    native_sink_report = native_sink_projector._project_native_encoded_to_sink(
        view,
        native_sink,
        options=native_options,
        batch_size=2,
        buffer_edges=2,
        temp_directory=tmp_path,
    )

    assert native_sink.batches == expected_sink.batches
    assert native_sink.report is native_sink_report
    assert all(0 < len(batch) <= 2 for batch in native_sink.batches)
    _assert_semantic_report_parity(expected_sink_report, native_sink_report)
    assert native_sink_report.provenance.ingestion.path == "encoded-native"
    _assert_bounded_native_output(
        native_sink_report.provenance.ingestion.counters,
        compiled_edges=4,
        batch_edges=2,
    )

    expected_digest = Projector().canonical_digest(
        view,
        options=python_options,
        buffer_edges=2,
        temp_directory=tmp_path,
    )
    native_digest = Projector()._canonical_native_encoded_digest(
        view,
        options=native_options,
        buffer_edges=2,
        temp_directory=tmp_path,
    )
    assert (
        native_digest.sha256,
        native_digest.edge_count,
        native_digest.duplicate_count,
    ) == (
        expected_digest.sha256,
        expected_digest.edge_count,
        expected_digest.duplicate_count,
    )
    _assert_semantic_report_parity(expected_digest.report, native_digest.report)
    assert native_digest.report.provenance.ingestion.path == "encoded-native"

    expected_destination = io.BytesIO()
    expected_artifact = Projector().write_artifact(
        view,
        expected_destination,
        options=python_options,
        buffer_edges=2,
        temp_directory=tmp_path,
    )
    native_destination = io.BytesIO()
    native_artifact = Projector()._write_native_encoded_artifact(
        view,
        native_destination,
        options=native_options,
        buffer_edges=2,
        temp_directory=tmp_path,
    )
    assert native_destination.getvalue() == expected_destination.getvalue()
    assert (
        native_artifact.artifact_sha256,
        native_artifact.canonical_edges_sha256,
        native_artifact.edge_count,
        native_artifact.duplicate_count,
        native_artifact.bytes_written,
        native_artifact.metadata,
    ) == (
        expected_artifact.artifact_sha256,
        expected_artifact.canonical_edges_sha256,
        expected_artifact.edge_count,
        expected_artifact.duplicate_count,
        expected_artifact.bytes_written,
        expected_artifact.metadata,
    )
    _assert_semantic_report_parity(expected_artifact.report, native_artifact.report)
    assert native_artifact.report.provenance.ingestion.path == "encoded-native"
    assert list(tmp_path.iterdir()) == []


def test_hidden_projector_sink_failure_closes_unpublished_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    def fail(_batch: tuple[Edge, ...]) -> None:
        raise RuntimeError("injected Projector sink failure")

    monkeypatch.setattr(api_module, "prepare_native_encoded_compilation", capture_compilation)
    projector = Projector()
    with pytest.raises(RuntimeError, match="injected Projector sink failure"):
        projector._project_native_encoded_to_sink(
            _snapshot("SubClassOf(:A :B) SubClassOf(:C :A)"),
            fail,
            options=ProjectionOptions(backend="native", order="encounter"),
            batch_size=1,
            buffer_edges=1,
        )

    assert len(captured) == 1
    assert captured[0].batches.state == "cancelled"
    assert captured[0].batches.remaining_edges == 0
    assert projector.last_report is None


def test_hidden_projector_iterator_moves_between_threads() -> None:
    view = _snapshot(" ".join(f"SubClassOf(:C{index} :Top)" for index in range(12)))
    python_options = ProjectionOptions(backend="python", order="encounter")
    expected = Projector().project(view, options=python_options)
    projector = Projector()
    iterator = projector._iter_native_encoded_edges(
        view,
        options=replace(python_options, backend="native"),
        buffer_edges=3,
    )

    first = next(iterator)
    with ThreadPoolExecutor(max_workers=1) as executor:
        remaining = executor.submit(list, iterator).result(timeout=10)

    assert [first, *remaining] == expected
    report = _completed_report(projector)
    assert report.provenance.ingestion.path == "encoded-native"
    _assert_bounded_native_output(
        report.provenance.ingestion.counters,
        compiled_edges=12,
        batch_edges=3,
    )


def test_hidden_isolated_projector_is_reentrant_across_threads() -> None:
    view = _snapshot(
        "SubClassOf(:A :B) SubClassOf(:C :A) "
        "SubClassOf(:D ObjectSomeValuesFrom(:p :E)) "
        "ObjectPropertyDomain(:p :Domain) ObjectPropertyRange(:p :Range)"
    )
    python_options = ProjectionOptions(
        backend="python",
        order="canonical",
        duplicates="unique",
        compatibility_state="isolated",
    )
    expected = Projector().project(view, options=python_options)
    native_options = replace(python_options, backend="native")
    projector = Projector()

    def project(_index: int) -> list[Edge]:
        return list(
            projector._iter_native_encoded_edges(
                view,
                options=native_options,
                buffer_edges=2,
            )
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(project, range(12)))

    assert all(result == expected for result in results)


def test_quiescent_hidden_cursor_is_independent_after_fork() -> None:
    if not hasattr(os, "fork"):
        pytest.skip("os.fork is unavailable")
    source = (
        "Prefix(:=<urn:native-fork#>) Ontology(<urn:native-fork> "
        + " ".join(f"SubClassOf(:C{index} :Top)" for index in range(12))
        + ")"
    ).encode()
    script = f"""
import os
import pyowl_core
from pyowl2vec_star_projector import ProjectionOptions, Projector

view = pyowl_core.load_snapshot(
    bytes.fromhex({source.hex()!r}),
    options=pyowl_core.LoadOptions(
        imports=pyowl_core.ImportPolicy.IGNORE,
        backend=pyowl_core.BackendPreference.PYTHON,
    ),
)
projector = Projector()
iterator = projector._iter_native_encoded_edges(
    view,
    options=ProjectionOptions(backend="native", order="encounter"),
    buffer_edges=3,
)
first = next(iterator)
pid = os.fork()
if pid == 0:
    try:
        remaining = list(iterator)
        os._exit(0 if len(remaining) == 11 else 2)
    except BaseException:
        os._exit(3)
remaining = list(iterator)
_, status = os.waitpid(pid, 0)
if len(remaining) != 11 or os.waitstatus_to_exitcode(status) != 0:
    raise SystemExit(4)
report = projector.last_report
if report is None or report.provenance.ingestion.path != "encoded-native":
    raise SystemExit(5)
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(sys.path)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert completed.returncode == 0, completed.stderr


def test_unfinished_hidden_cursor_is_safe_during_interpreter_shutdown() -> None:
    source = (
        b"Prefix(:=<urn:native-shutdown#>) Ontology(<urn:native-shutdown> "
        b"SubClassOf(:A :B) SubClassOf(:C :A) SubClassOf(:D :C))"
    )
    script = f"""
import pyowl_core
from pyowl2vec_star_projector import ProjectionOptions, Projector

view = pyowl_core.load_snapshot(
    bytes.fromhex({source.hex()!r}),
    options=pyowl_core.LoadOptions(
        imports=pyowl_core.ImportPolicy.IGNORE,
        backend=pyowl_core.BackendPreference.PYTHON,
    ),
)
projector = Projector()
iterator = projector._iter_native_encoded_edges(
    view,
    options=ProjectionOptions(backend="native", order="encounter"),
    buffer_edges=1,
)
next(iterator)
retained = (projector, iterator)
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(sys.path)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert completed.returncode == 0, completed.stderr


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
    _assert_bounded_native_output(counters, compiled_edges=raw_edges, batch_edges=2)
    assert counters["per_row_ffi_calls"] == 0
    assert counters["scalar_axiom_materializations"] == 0
    assert counters["scalar_term_materializations"] == 0


@pytest.mark.parametrize(
    ("python_options", "raw_edges", "ignored_shapes"),
    [
        (ProjectionOptions(backend="python", order="encounter"), 5, 2),
        (
            ProjectionOptions(
                backend="python",
                order="canonical",
                duplicates="unique",
                bidirectional_taxonomy=True,
            ),
            7,
            2,
        ),
        (
            ProjectionOptions(
                backend="python",
                order="encounter",
                only_taxonomy=True,
            ),
            2,
            1,
        ),
    ],
)
def test_hidden_iterator_admits_exact_aggregate_and_ignored_equivalences(
    python_options: ProjectionOptions,
    raw_edges: int,
    ignored_shapes: int,
) -> None:
    view = _snapshot(
        "SubObjectPropertyOf(:child :p) InverseObjectProperties(:p :pinv) "
        "EquivalentClasses(:PairA :PairB) "
        "EquivalentClasses(:A ObjectIntersectionOf("
        ":B ObjectSomeValuesFrom(:p :C) ObjectHasSelf(:q))) "
        "EquivalentClasses(:Ignored ObjectSomeValuesFrom(:r :Y))"
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
    assert len(actual) == raw_edges
    _assert_semantic_report_parity(expected_report, actual_report)
    assert actual_report.provenance.counts.ignored_shapes == ignored_shapes
    assert tuple(
        (item.code, item.constructor, item.count) for item in actual_report.diagnostics
    ) == (("MOWL_IGNORED_SHAPE", "EquivalentClasses", ignored_shapes),)
    ingestion = actual_report.provenance.ingestion
    assert ingestion.path == "encoded-native"
    assert ingestion.counters["native_edge_batches"] == (raw_edges + 1) // 2
    assert ingestion.counters["native_boundary_calls"] == 1 + (raw_edges + 1) // 2
    _assert_bounded_native_output(
        ingestion.counters, compiled_edges=raw_edges, batch_edges=2
    )
    assert ingestion.counters["scalar_axiom_materializations"] == 0


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
    _assert_bounded_native_output(
        ingestion.counters, compiled_edges=raw_edges, batch_edges=2
    )
    assert ingestion.counters["per_row_ffi_calls"] == 0


@pytest.mark.parametrize(
    ("python_options", "raw_edges", "ignored_shapes", "subclass_ignores"),
    [
        (ProjectionOptions(backend="python", order="encounter"), 3, 3, 1),
        (
            ProjectionOptions(
                backend="python",
                order="canonical",
                duplicates="unique",
                bidirectional_taxonomy=True,
            ),
            4,
            3,
            1,
        ),
        (
            ProjectionOptions(
                backend="python",
                order="encounter",
                only_taxonomy=True,
            ),
            2,
            4,
            2,
        ),
    ],
)
def test_hidden_iterator_admits_exact_ignored_subclasses_and_class_assertions(
    python_options: ProjectionOptions,
    raw_edges: int,
    ignored_shapes: int,
    subclass_ignores: int,
) -> None:
    view = _snapshot(
        "SubClassOf(:TaxA :TaxB) "
        "SubClassOf(:A ObjectSomeValuesFrom(:p :B)) "
        "SubClassOf(:Ignored ObjectOneOf(:member)) "
        "ClassAssertion(:TaxA :named) "
        "ClassAssertion(:TaxA _:anonymous) "
        "ClassAssertion(ObjectHasSelf(:p) :named)"
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
    assert len(actual) == raw_edges
    _assert_semantic_report_parity(expected_report, actual_report)
    assert actual_report.provenance.counts.ignored_shapes == ignored_shapes
    assert tuple(
        (item.code, item.constructor, item.count) for item in actual_report.diagnostics
    ) == (
        ("MOWL_IGNORED_SHAPE", "ClassAssertion", 2),
        ("MOWL_IGNORED_SHAPE", "SubClassOf", subclass_ignores),
    )
    ingestion = actual_report.provenance.ingestion
    assert ingestion.path == "encoded-native"
    assert ingestion.counters["native_edge_batches"] == (raw_edges + 1) // 2
    assert ingestion.counters["native_boundary_calls"] == 1 + (raw_edges + 1) // 2
    _assert_bounded_native_output(
        ingestion.counters, compiled_edges=raw_edges, batch_edges=2
    )
    assert ingestion.counters["scalar_axiom_materializations"] == 0


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
    _assert_bounded_native_output(counters, compiled_edges=raw_edges, batch_edges=2)
    assert counters["scalar_axiom_materializations"] == 0
    assert counters["per_row_ffi_calls"] == 0


@pytest.mark.parametrize(
    ("python_options", "raw_edges"),
    [
        (ProjectionOptions(backend="python", order="encounter"), 14),
        (
            ProjectionOptions(
                backend="python",
                order="canonical",
                duplicates="unique",
                bidirectional_taxonomy=True,
            ),
            15,
        ),
        (
            ProjectionOptions(
                backend="python",
                order="encounter",
                only_taxonomy=True,
            ),
            14,
        ),
    ],
)
def test_hidden_iterator_admits_partitioned_multi_property_domain_ranges(
    python_options: ProjectionOptions,
    raw_edges: int,
) -> None:
    view = _snapshot(
        "SubObjectPropertyOf(:child :p) InverseObjectProperties(:p :pinv) "
        "SubClassOf(:TaxA :TaxB) "
        "ObjectPropertyDomain(:p :D1) ObjectPropertyDomain(:p :D2) "
        "ObjectPropertyRange(:p :R1) ObjectPropertyRange(:p :R2) "
        "ObjectPropertyDomain(:q :QD) ObjectPropertyRange(:q :QR) "
        "ObjectPropertyDomain(:domainOnly :UnpairedD) "
        "ObjectPropertyRange(:rangeOnly :UnpairedR) "
        "ObjectPropertyDomain(ObjectInverseOf(:p) :IgnoredInverseD) "
        "ObjectPropertyDomain(:complexD ObjectUnionOf(:A :B)) "
        "ObjectPropertyRange(ObjectInverseOf(:p) :IgnoredInverseR) "
        "ObjectPropertyRange(:complexR ObjectComplementOf(:C))"
    )
    expected_projector = Projector()
    expected = expected_projector.project(view, options=python_options)
    expected_report = _completed_report(expected_projector)

    native_projector = Projector()
    actual = list(
        native_projector._iter_native_encoded_edges(
            view,
            options=replace(python_options, backend="native"),
            buffer_edges=3,
        )
    )
    actual_report = _completed_report(native_projector)

    assert actual == expected
    assert len(actual) == raw_edges
    _assert_semantic_report_parity(expected_report, actual_report)
    assert actual_report.provenance.counts.ignored_shapes == 4
    assert tuple(
        (item.code, item.constructor, item.count) for item in actual_report.diagnostics
    ) == (
        ("MOWL_IGNORED_SHAPE", "ObjectPropertyDomain", 2),
        ("MOWL_IGNORED_SHAPE", "ObjectPropertyRange", 2),
    )
    ingestion = actual_report.provenance.ingestion
    assert ingestion.path == "encoded-native"
    assert ingestion.counters["native_edge_batches"] == (raw_edges + 2) // 3
    assert ingestion.counters["native_boundary_calls"] == 1 + (raw_edges + 2) // 3
    _assert_bounded_native_output(
        ingestion.counters, compiled_edges=raw_edges, batch_edges=3
    )
    assert ingestion.counters["scalar_axiom_materializations"] == 0


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
    _assert_bounded_native_output(counters, compiled_edges=raw_edges, batch_edges=2)
    assert counters["scalar_axiom_materializations"] == 0
    assert counters["per_row_ffi_calls"] == 0


def test_hidden_iterator_retains_scala_instance_role_lifecycle_natively() -> None:
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
    python_options = ProjectionOptions(
        backend="python",
        order="encounter",
        compatibility_state="scala-instance",
    )
    expected_projector = Projector()
    expected_role_edges = expected_projector.project(role_view, options=python_options)
    expected_role_report = _completed_report(expected_projector)
    expected_consumer_edges = expected_projector.project(consumer_view, options=python_options)
    expected_consumer_report = _completed_report(expected_projector)
    expected_conflict_edges = expected_projector.project(conflict_view, options=python_options)
    expected_conflict_report = _completed_report(expected_projector)

    native_projector = Projector()
    actual_role_edges = list(
        native_projector._iter_native_encoded_edges(
            role_view,
            options=replace(python_options, backend="native"),
            buffer_edges=2,
        )
    )
    actual_role_report = _completed_report(native_projector)
    actual_consumer_edges = list(
        native_projector._iter_native_encoded_edges(
            consumer_view,
            options=replace(python_options, backend="native"),
            buffer_edges=2,
        )
    )
    actual_consumer_report = _completed_report(native_projector)
    actual_conflict_edges = list(
        native_projector._iter_native_encoded_edges(
            conflict_view,
            options=replace(python_options, backend="native"),
            buffer_edges=2,
        )
    )
    actual_conflict_report = _completed_report(native_projector)

    assert actual_role_edges == expected_role_edges == []
    assert actual_consumer_edges == expected_consumer_edges
    assert actual_conflict_edges == expected_conflict_edges
    _assert_semantic_report_parity(expected_role_report, actual_role_report)
    _assert_semantic_report_parity(expected_consumer_report, actual_consumer_report)
    _assert_semantic_report_parity(expected_conflict_report, actual_conflict_report)
    assert actual_role_report.provenance.ingestion.path == "encoded-native"
    assert actual_consumer_report.provenance.ingestion.path == "encoded-native"
    assert actual_conflict_report.provenance.ingestion.path == "encoded-native"
    assert actual_conflict_report.provenance.invocation_count == 3
    assert native_projector._scala_state == expected_projector._scala_state
    assert native_projector._scala_state == RoleState(
        {"urn:native-integration#p": ("urn:native-integration#other",)},
        {
            "urn:native-integration#p": "urn:native-integration#otherInverse",
            "urn:native-integration#pinv": "urn:native-integration#p",
            "urn:native-integration#otherInverse": "urn:native-integration#p",
        },
    )


def test_hidden_iterator_transitions_retained_scala_state_to_scalar_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role_view = _snapshot(
        "SubObjectPropertyOf(:child :p) InverseObjectProperties(:p :pinv)"
    )
    restriction_view = _snapshot("SubClassOf(:A ObjectSomeValuesFrom(:p :B))")
    domain_range_view = _snapshot(
        "ObjectPropertyDomain(:p :D) ObjectPropertyRange(:p :R)"
    )
    python_options = ProjectionOptions(
        backend="python",
        order="encounter",
        compatibility_state="scala-instance",
    )
    expected_projector = Projector()
    expected = tuple(
        expected_projector.project(view, options=python_options)
        for view in (role_view, restriction_view, domain_range_view)
    )
    expected_report = _completed_report(expected_projector)

    native_projector = Projector()
    first = list(
        native_projector._iter_native_encoded_edges(
            role_view,
            options=replace(python_options, backend="native"),
            buffer_edges=1,
        )
    )
    assert _completed_report(native_projector).provenance.ingestion.path == "encoded-native"

    monkeypatch.setattr(
        api_module,
        "prepare_native_encoded_compilation",
        lambda *args, **kwargs: (None, "injected stateful native decline"),
    )
    second = list(
        native_projector._iter_native_encoded_edges(
            restriction_view,
            options=replace(python_options, backend="native"),
            buffer_edges=1,
        )
    )
    second_report = _completed_report(native_projector)
    assert second_report.provenance.ingestion.path == "scalar-native"
    assert "injected stateful native decline" in (second_report.provenance.ingestion.reason or "")

    prepare_calls = 0

    def unexpected_native_prepare(*args: Any, **kwargs: Any) -> Any:
        nonlocal prepare_calls
        prepare_calls += 1
        raise AssertionError("scalar lifecycle re-entered retained native state")

    monkeypatch.setattr(
        api_module,
        "prepare_native_encoded_compilation",
        unexpected_native_prepare,
    )
    third = list(
        native_projector._iter_native_encoded_edges(
            domain_range_view,
            options=replace(python_options, backend="native"),
            buffer_edges=1,
        )
    )
    third_report = _completed_report(native_projector)

    assert (first, second, third) == expected
    _assert_semantic_report_parity(expected_report, third_report)
    assert prepare_calls == 0
    assert third_report.provenance.ingestion.path == "scalar-native"
    assert "previously selected scalar compilation" in (
        third_report.provenance.ingestion.reason or ""
    )
    assert native_projector._scala_state == expected_projector._scala_state


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
    _assert_bounded_native_output(
        ingestion.counters, compiled_edges=raw_edges, batch_edges=2
    )
    assert ingestion.counters["scalar_axiom_materializations"] == 0
    assert ingestion.counters["per_row_ffi_calls"] == 0


def test_hidden_iterator_admits_option_dependent_ignored_annotations() -> None:
    view = _snapshot('Declaration(Class(:A)) AnnotationAssertion(<urn:unsupported> :A "ignored")')
    for include_literals, ignored_shapes in ((False, 0), (True, 1)):
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
        assert actual_report.provenance.ingestion.path == "encoded-native"
        assert actual_report.provenance.counts.ignored_shapes == ignored_shapes
        if include_literals:
            assert tuple(
                (item.code, item.constructor, item.count) for item in actual_report.diagnostics
            ) == (("MOWL_IGNORED_SHAPE", "AnnotationAssertion", 1),)
        else:
            assert actual_report.provenance.ingestion.reason is None
            assert actual_report.diagnostics == ()


def test_hidden_iterator_joins_imported_annotation_provenance_in_native() -> None:
    view = _imported_snapshot()
    assert view.report.backend == "python"  # type: ignore[attr-defined]
    python_options = ProjectionOptions(
        backend="python",
        order="encounter",
        include_literals=True,
    )
    expected_projector = Projector()
    expected = expected_projector.project(view, options=python_options)
    expected_report = _completed_report(expected_projector)

    with patch.object(
        api_module,
        "prepare_streaming_compilation",
        side_effect=AssertionError("joined annotation provenance reached scalar traversal"),
    ):
        native_projector = Projector()
        actual = list(
            native_projector._iter_native_encoded_edges(
                view,
                options=replace(python_options, backend="native"),
                buffer_edges=1,
            )
        )
    actual_report = _completed_report(native_projector)

    assert actual == expected
    assert {edge.destination for edge in actual} >= {"urn:root#A", "root"}
    assert "leaf" not in {edge.destination for edge in actual}
    _assert_semantic_report_parity(expected_report, actual_report)
    ingestion = actual_report.provenance.ingestion
    assert ingestion.path == "encoded-native"
    assert ingestion.reason is None
    assert ingestion.counters["encoded_buffer_count"] == 2 * len(ENCODED_DIRECT_BUFFER_ORDER)
    assert ingestion.counters["encoded_detached_buffer_count"] == 2 * len(
        ENCODED_DIRECT_BUFFER_ORDER
    )
    assert ingestion.counters["encoded_referenced_view_count"] == 1
    assert ingestion.counters["encoded_segment_count"] == 2
    assert ingestion.counters["encoded_staging_copy_bytes"] == 0
    assert ingestion.counters["scalar_axiom_materializations"] == 0


def test_hidden_iterator_applies_native_edge_limit_after_root_annotation_join() -> None:
    view = _imported_snapshot()
    python_options = ProjectionOptions(
        backend="python",
        order="encounter",
        include_literals=True,
    )
    expected_projector = Projector()
    expected = expected_projector.project(view, options=python_options)
    expected_report = _completed_report(expected_projector)
    assert len(expected) == 2

    real_iter_batches = NativeEncodedDirectCompiler.iter_batches
    with (
        patch.object(
            NativeEncodedDirectCompiler,
            "iter_batches",
            autospec=True,
            side_effect=real_iter_batches,
        ) as iter_batches,
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("joined annotation limit reached scalar traversal"),
        ),
    ):
        native_projector = Projector()
        actual = list(
            native_projector._iter_native_encoded_edges(
                view,
                options=replace(python_options, backend="native"),
                buffer_edges=1,
                streaming_limits=StreamingLimits(max_total_edges=len(expected)),
            )
        )
    actual_report = _completed_report(native_projector)

    assert actual == expected
    _assert_semantic_report_parity(expected_report, actual_report)
    assert iter_batches.call_count == 1
    ingestion = actual_report.provenance.ingestion
    assert ingestion.path == "encoded-native"
    assert ingestion.reason is None
    _assert_bounded_native_output(
        ingestion.counters, compiled_edges=len(expected), batch_edges=1
    )


def test_hidden_iterator_native_join_suppresses_all_imported_only_annotations() -> None:
    view = _imported_snapshot_with_only_leaf_annotation()
    python_options = ProjectionOptions(
        backend="python",
        order="encounter",
        include_literals=True,
    )
    expected_projector = Projector()
    expected = expected_projector.project(view, options=python_options)
    expected_report = _completed_report(expected_projector)

    with patch.object(
        api_module,
        "prepare_streaming_compilation",
        side_effect=AssertionError("empty root annotation selection reached scalar traversal"),
    ):
        native_projector = Projector()
        actual = list(
            native_projector._iter_native_encoded_edges(
                view,
                options=replace(python_options, backend="native"),
                buffer_edges=1,
                streaming_limits=StreamingLimits(max_total_edges=1),
            )
        )
    actual_report = _completed_report(native_projector)

    assert actual == expected
    assert "leaf" not in {edge.destination for edge in actual}
    _assert_semantic_report_parity(expected_report, actual_report)
    assert actual_report.provenance.ingestion.path == "encoded-native"
    assert actual_report.provenance.counts.ignored_shapes == 0


def test_hidden_iterator_native_join_uses_closure_anonymous_identifier_space() -> None:
    view = _imported_snapshot_with_anonymous_annotation_values()
    python_options = ProjectionOptions(
        backend="python",
        order="encounter",
        include_literals=True,
    )
    expected_projector = Projector()
    expected = expected_projector.project(view, options=python_options)
    expected_report = _completed_report(expected_projector)

    with patch.object(
        api_module,
        "prepare_streaming_compilation",
        side_effect=AssertionError("anonymous root annotation reached scalar traversal"),
    ):
        native_projector = Projector()
        actual = list(
            native_projector._iter_native_encoded_edges(
                view,
                options=replace(python_options, backend="native"),
                buffer_edges=1,
            )
        )
    actual_report = _completed_report(native_projector)

    assert actual == expected
    assert len([edge for edge in actual if edge.relation == "rdfs:label"]) == 1
    _assert_semantic_report_parity(expected_report, actual_report)
    assert actual_report.provenance.ingestion.path == "encoded-native"


def test_hidden_iterator_does_not_root_preflight_annotation_free_imports() -> None:
    view = _imported_snapshot_without_annotations()
    python_options = ProjectionOptions(
        backend="python",
        order="encounter",
        include_literals=True,
    )
    expected_projector = Projector()
    expected = expected_projector.project(view, options=python_options)
    expected_report = _completed_report(expected_projector)

    with (
        patch.object(
            native_module,
            "_acquire_root_encoded_lease",
            side_effect=AssertionError("annotation-free closure requested root provenance"),
        ),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("annotation-free closure reached scalar traversal"),
        ),
    ):
        native_projector = Projector()
        actual = list(
            native_projector._iter_native_encoded_edges(
                view,
                options=replace(python_options, backend="native"),
                buffer_edges=1,
            )
        )
    actual_report = _completed_report(native_projector)

    assert actual == expected
    _assert_semantic_report_parity(expected_report, actual_report)
    assert actual_report.provenance.ingestion.path == "encoded-native"


@pytest.mark.parametrize(
    ("view_factory", "expected_edges", "root_label"),
    [
        (_diamond_import_snapshot, 5, "root"),
        (_cyclic_import_snapshot, 3, "a-root"),
    ],
    ids=["diamond", "cycle"],
)
def test_hidden_iterator_joins_root_annotations_across_import_topologies(
    view_factory: Any,
    expected_edges: int,
    root_label: str,
) -> None:
    view = view_factory()
    python_options = ProjectionOptions(
        backend="python",
        order="encounter",
        include_literals=True,
    )
    expected_projector = Projector()
    expected = expected_projector.project(view, options=python_options)
    expected_report = _completed_report(expected_projector)

    with patch.object(
        api_module,
        "prepare_streaming_compilation",
        side_effect=AssertionError("import topology reached scalar traversal"),
    ):
        native_projector = Projector()
        actual = list(
            native_projector._iter_native_encoded_edges(
                view,
                options=replace(python_options, backend="native"),
                buffer_edges=2,
                streaming_limits=StreamingLimits(max_total_edges=expected_edges),
            )
        )
    actual_report = _completed_report(native_projector)

    assert actual == expected
    assert len(actual) == expected_edges
    labels = [edge.destination for edge in actual if edge.relation == "rdfs:label"]
    assert labels == [root_label]
    _assert_semantic_report_parity(expected_report, actual_report)
    ingestion = actual_report.provenance.ingestion
    assert ingestion.path == "encoded-native"
    assert ingestion.counters["encoded_buffer_count"] == 22
    assert ingestion.counters["encoded_segment_count"] == 2
    assert ingestion.counters["scalar_axiom_materializations"] == 0


def test_hidden_iterator_keeps_imported_annotations_unobserved_on_native_path() -> None:
    view = _imported_snapshot()
    python_options = ProjectionOptions(
        backend="python",
        order="encounter",
        include_literals=False,
    )
    expected_projector = Projector()
    expected = expected_projector.project(view, options=python_options)
    expected_report = _completed_report(expected_projector)

    with patch.object(
        api_module,
        "prepare_streaming_compilation",
        side_effect=AssertionError("unobserved imported annotations reached scalar traversal"),
    ):
        native_projector = Projector()
        actual = list(
            native_projector._iter_native_encoded_edges(
                view,
                options=replace(python_options, backend="native"),
                buffer_edges=1,
            )
        )
    actual_report = _completed_report(native_projector)

    assert actual == expected
    _assert_semantic_report_parity(expected_report, actual_report)
    assert actual_report.provenance.ingestion.path == "encoded-native"
    assert actual_report.provenance.ingestion.reason is None


@pytest.mark.parametrize(
    "provider_backend",
    [
        pyowl_core.BackendPreference.PYTHON,
        pyowl_core.BackendPreference.NATIVE,
    ],
    ids=["independent-bytes", "packed-bytes"],
)
def test_hidden_iterator_proves_single_document_annotation_selection(
    provider_backend: pyowl_core.BackendPreference,
) -> None:
    view = _snapshot(
        "Declaration(Class(:A)) "
        'AnnotationAssertion(<http://www.w3.org/2000/01/rdf-schema#label> :A "root")',
        backend=provider_backend,
    )
    assert view.report.backend == provider_backend.value  # type: ignore[attr-defined]
    closure = view.view(  # type: ignore[attr-defined]
        pyowl_core.EncodedStructuralView,
        schema_version=1,
        scope=pyowl_core.AxiomScope.CLOSURE,
    )
    exporter_count = len({id(buffer.obj) for buffer in closure.buffers.values()})
    if provider_backend is pyowl_core.BackendPreference.NATIVE:
        assert exporter_count == 1
    else:
        assert exporter_count > 1
    python_options = ProjectionOptions(
        backend="python",
        order="encounter",
        include_literals=True,
    )
    expected_projector = Projector()
    expected = expected_projector.project(view, options=python_options)
    expected_report = _completed_report(expected_projector)
    real_acquire = native_module._acquire_root_encoded_lease

    with (
        patch.object(
            native_module,
            "_acquire_root_encoded_lease",
            wraps=real_acquire,
        ) as acquire_root,
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("equal root annotation selection reached scalar traversal"),
        ),
    ):
        native_projector = Projector()
        actual = list(
            native_projector._iter_native_encoded_edges(
                view,
                options=replace(python_options, backend="native"),
                buffer_edges=1,
            )
        )
    actual_report = _completed_report(native_projector)

    assert actual == expected
    _assert_semantic_report_parity(expected_report, actual_report)
    assert acquire_root.call_count == 1
    assert actual_report.provenance.ingestion.path == "encoded-native"


def test_hidden_iterator_rejects_sliced_root_annotation_provenance() -> None:
    view = _snapshot(
        "Declaration(Class(:A)) "
        'AnnotationAssertion(<http://www.w3.org/2000/01/rdf-schema#label> :A "root")'
    )
    selection = api_module.select_private_direct_ingestion(
        view,
        selected_backend="native",
    )
    assert selection.lease is not None
    direct_root = native_module._acquire_root_encoded_lease(view, selection.lease)
    assert direct_root is not None
    replacements = dict(direct_root.buffers)
    root_kinds = bytes(replacements["root_kinds"])
    replacements["root_kinds"] = memoryview(b"x" + root_kinds)[1:]
    sliced_buffers = MappingProxyType(replacements)
    sliced_encoded = replace(direct_root.encoded_view, buffers=sliced_buffers)
    sliced_root = replace(
        direct_root,
        encoded_view=sliced_encoded,
        buffers=sliced_buffers,
    )
    python_options = ProjectionOptions(
        backend="python",
        order="encounter",
        include_literals=True,
    )
    expected_projector = Projector()
    expected = expected_projector.project(view, options=python_options)
    expected_report = _completed_report(expected_projector)

    with patch.object(
        native_module,
        "_acquire_root_encoded_lease",
        return_value=sliced_root,
    ):
        native_projector = Projector()
        actual = list(
            native_projector._iter_native_encoded_edges(
                view,
                options=replace(python_options, backend="native"),
                buffer_edges=1,
            )
        )
    actual_report = _completed_report(native_projector)

    assert actual == expected
    _assert_semantic_report_parity(expected_report, actual_report)
    ingestion = actual_report.provenance.ingestion
    assert ingestion.path == "scalar-native"
    assert ingestion.reason is not None
    assert ingestion.reason.startswith(
        "root-scoped native annotation provenance is not exact-direct"
    )
    assert ingestion.reason.endswith("selected whole-operation scalar compiler")


def test_hidden_iterator_preserves_mixed_scalar_diagnostic_order() -> None:
    label = "<http://www.w3.org/2000/01/rdf-schema#label>"
    integer = "<http://www.w3.org/2001/XMLSchema#integer>"
    view = _snapshot(
        "Declaration(Class(:A)) "
        "SubClassOf(:A ObjectOneOf(:member)) "
        "ClassAssertion(:A _:anonymous) "
        'AnnotationAssertion(<urn:unsupported> :A "ignored") '
        f'AnnotationAssertion({label} :A "7"^^{integer}) '
        'DataPropertyAssertion(:dp :member "skipped")'
    )
    python_options = ProjectionOptions(
        backend="python",
        order="encounter",
        include_literals=True,
    )
    expected_projector = Projector()
    expected = expected_projector.project(view, options=python_options)
    expected_report = _completed_report(expected_projector)

    native_projector = Projector()
    actual = list(
        native_projector._iter_native_encoded_edges(
            view,
            options=replace(python_options, backend="native"),
            buffer_edges=1,
        )
    )
    actual_report = _completed_report(native_projector)

    assert actual == expected
    _assert_semantic_report_parity(expected_report, actual_report)
    assert tuple(
        (item.code, item.constructor, item.count) for item in actual_report.diagnostics
    ) == (
        ("MOWL_IGNORED_SHAPE", "AnnotationAssertion", 1),
        ("MOWL_IGNORED_SHAPE", "ClassAssertion", 1),
        ("MOWL_IGNORED_SHAPE", "SubClassOf", 1),
        ("MOWL_NON_STRING_LITERAL_RENDERING", "Literal", 1),
        ("MOWL_SKIPPED_AXIOM", "DataPropertyAssertion", 1),
    )
    assert actual_report.provenance.counts.ignored_shapes == 3
    assert actual_report.provenance.counts.skipped_axioms == 1
    assert actual_report.provenance.counts.warnings == 1
    assert actual_report.provenance.ingestion.path == "encoded-native"


@pytest.mark.parametrize(
    ("python_options", "raw_edges"),
    [
        (
            ProjectionOptions(
                backend="python",
                order="encounter",
                include_literals=True,
            ),
            3,
        ),
        (
            ProjectionOptions(
                backend="python",
                order="canonical",
                duplicates="unique",
                include_literals=True,
                bidirectional_taxonomy=True,
            ),
            3,
        ),
        (
            ProjectionOptions(
                backend="python",
                order="encounter",
                include_literals=False,
                only_taxonomy=True,
            ),
            2,
        ),
    ],
)
def test_hidden_iterator_admits_anonymous_assertions_and_selected_values(
    python_options: ProjectionOptions,
    raw_edges: int,
) -> None:
    label = "<http://www.w3.org/2000/01/rdf-schema#label>"
    view = _snapshot(
        "Declaration(Class(:A)) "
        "ObjectPropertyAssertion(:p _:source :named) "
        "ObjectPropertyAssertion(Annotation(<urn:meta> _:metadata) :p :named _:target) "
        f"AnnotationAssertion({label} :A _:annotationValue)"
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
    assert any("_:genid" in value for edge in actual for value in edge.as_tuple())
    _assert_semantic_report_parity(expected_report, actual_report)
    ingestion = actual_report.provenance.ingestion
    assert ingestion.path == "encoded-native"
    assert ingestion.counters["native_edge_batches"] == (raw_edges + 1) // 2
    assert ingestion.counters["native_boundary_calls"] == 1 + (raw_edges + 1) // 2
    _assert_bounded_native_output(
        ingestion.counters, compiled_edges=raw_edges, batch_edges=2
    )
    assert ingestion.counters["scalar_axiom_materializations"] == 0
    assert ingestion.counters["per_row_ffi_calls"] == 0


@pytest.mark.parametrize(
    ("python_options", "raw_edges"),
    [
        (ProjectionOptions(backend="python", order="encounter"), 1),
        (
            ProjectionOptions(
                backend="python",
                order="canonical",
                duplicates="unique",
                bidirectional_taxonomy=True,
                only_taxonomy=True,
            ),
            2,
        ),
    ],
)
def test_hidden_iterator_admits_exact_grouped_skipped_axioms(
    python_options: ProjectionOptions,
    raw_edges: int,
) -> None:
    view = _snapshot(
        "SubClassOf(:A :B) DisjointClasses(:A :B) DisjointUnion(:Defined :A :B) "
        "HasKey(:A (:op) (:dp)) SameIndividual(:i :j) DifferentIndividuals(:i :j) "
        "NegativeObjectPropertyAssertion(ObjectInverseOf(:op) :i :j) "
        "EquivalentObjectProperties(:op ObjectInverseOf(:oq)) "
        "DisjointObjectProperties(:op :oq) FunctionalObjectProperty(:op) "
        "InverseFunctionalObjectProperty(ObjectInverseOf(:op)) "
        "ReflexiveObjectProperty(:op) IrreflexiveObjectProperty(:op) "
        "SymmetricObjectProperty(:op) AsymmetricObjectProperty(:op) "
        "TransitiveObjectProperty(:op) SubDataPropertyOf(:dp :dq) "
        "EquivalentDataProperties(:dp :dq) DisjointDataProperties(:dp :dq) "
        "DataPropertyDomain(:dp :A) "
        "DataPropertyRange(:dp <http://www.w3.org/2001/XMLSchema#string>) "
        "FunctionalDataProperty(:dp) "
        "DatatypeDefinition(:custom <http://www.w3.org/2001/XMLSchema#string>) "
        'DataPropertyAssertion(:dp :i "value") '
        'NegativeDataPropertyAssertion(:dp :i "blocked") '
        "SubAnnotationPropertyOf(:ap :aq) AnnotationPropertyDomain(:ap <urn:domain>) "
        "AnnotationPropertyRange(:ap <urn:range>)"
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
    assert actual_report.provenance.counts.skipped_axioms == 27
    assert all(item.code == "MOWL_SKIPPED_AXIOM" for item in actual_report.diagnostics)
    assert tuple(item.constructor for item in actual_report.diagnostics) == (
        "AnnotationPropertyDomain",
        "AnnotationPropertyRange",
        "AsymmetricObjectProperty",
        "DataPropertyAssertion",
        "DataPropertyDomain",
        "DataPropertyRange",
        "DatatypeDefinition",
        "DifferentIndividuals",
        "DisjointClasses",
        "DisjointDataProperties",
        "DisjointObjectProperties",
        "DisjointUnion",
        "EquivalentDataProperties",
        "EquivalentObjectProperties",
        "FunctionalDataProperty",
        "FunctionalObjectProperty",
        "HasKey",
        "InverseFunctionalObjectProperty",
        "IrreflexiveObjectProperty",
        "NegativeDataPropertyAssertion",
        "NegativeObjectPropertyAssertion",
        "ReflexiveObjectProperty",
        "SameIndividual",
        "SubAnnotationPropertyOf",
        "SubDataPropertyOf",
        "SymmetricObjectProperty",
        "TransitiveObjectProperty",
    )
    ingestion = actual_report.provenance.ingestion
    assert ingestion.path == "encoded-native"
    assert ingestion.counters["native_edge_batches"] == (raw_edges + 1) // 2
    assert ingestion.counters["native_boundary_calls"] == 1 + (raw_edges + 1) // 2
    _assert_bounded_native_output(
        ingestion.counters, compiled_edges=raw_edges, batch_edges=2
    )
    assert ingestion.counters["scalar_axiom_materializations"] == 0


@pytest.mark.parametrize(
    ("python_options", "raw_edges", "ignored_shapes", "diagnostic_count"),
    [
        (ProjectionOptions(backend="python", order="encounter"), 2, 1, 0),
        (
            ProjectionOptions(
                backend="python",
                order="canonical",
                only_taxonomy=True,
            ),
            0,
            2,
            1,
        ),
    ],
)
def test_hidden_iterator_admits_ignored_property_chains_without_diagnostics(
    python_options: ProjectionOptions,
    raw_edges: int,
    ignored_shapes: int,
    diagnostic_count: int,
) -> None:
    view = _snapshot(
        "SubObjectPropertyOf(ObjectPropertyChain(:left :right) :p) "
        "SubObjectPropertyOf(:child :p) "
        "SubClassOf(:A ObjectSomeValuesFrom(:p :B))"
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
    assert len(actual_report.diagnostics) == diagnostic_count
    assert actual_report.provenance.ingestion.path == "encoded-native"
    _assert_bounded_native_output(
        actual_report.provenance.ingestion.counters,
        compiled_edges=raw_edges,
        batch_edges=2,
    )


def test_hidden_iterator_admits_silent_ontology_annotations_and_swrl() -> None:
    label = "<http://www.w3.org/2000/01/rdf-schema#label>"
    view = _swrl_snapshot(
        'Annotation(<urn:ontology-meta> "silent") '
        "SWRLRule((ClassAtom(:RuleClass Variable(:value))) ()) "
        "SubClassOf(:A :B) "
        f'AnnotationAssertion({label} :RuleClass "selected-from-rule-signature")'
    )
    python_options = ProjectionOptions(
        backend="python",
        order="encounter",
        include_literals=True,
    )
    expected_projector = Projector()
    expected = expected_projector.project(view, options=python_options)
    expected_report = _completed_report(expected_projector)

    native_projector = Projector()
    actual = list(
        native_projector._iter_native_encoded_edges(
            view,
            options=replace(python_options, backend="native"),
            buffer_edges=1,
        )
    )
    actual_report = _completed_report(native_projector)

    assert actual == expected
    assert len(actual) == 2
    _assert_semantic_report_parity(expected_report, actual_report)
    assert actual_report.diagnostics == ()
    assert actual_report.provenance.ingestion.path == "encoded-native"
    assert actual_report.provenance.ingestion.counters["native_edge_batches"] == 2


@pytest.mark.parametrize(
    "provider_backend",
    [
        pyowl_core.BackendPreference.PYTHON,
        pyowl_core.BackendPreference.NATIVE,
    ],
    ids=["independent-bytes", "packed-bytes"],
)
def test_hidden_iterator_compiles_empty_overlay_alias_without_flattening(
    provider_backend: pyowl_core.BackendPreference,
) -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "Declaration(Class(:A)) Declaration(Class(:B)) SubClassOf(:A :B)",
            backend=provider_backend,
        ),
    )
    overlay = pyowl_core.apply_delta(base, pyowl_core.OntologyDelta())
    top_encoded = overlay.view(
        pyowl_core.EncodedStructuralView,
        schema_version=1,
        scope=pyowl_core.AxiomScope.CLOSURE,
    )
    assert len(top_encoded.segments) == 1
    source_encoded = top_encoded.segments[0].source
    assert source_encoded is not None
    expected_buffer_bytes = sum(value.nbytes for value in top_encoded.buffers.values()) + sum(
        value.nbytes for value in source_encoded.buffers.values()
    )

    python_options = ProjectionOptions(backend="python", order="encounter")
    expected_projector = Projector()
    expected = expected_projector.project(overlay, options=python_options)
    expected_report = _completed_report(expected_projector)

    with patch.object(
        api_module,
        "prepare_streaming_compilation",
        side_effect=AssertionError("empty overlay alias reached scalar traversal"),
    ):
        native_projector = Projector()
        actual = list(
            native_projector._iter_native_encoded_edges(
                overlay,
                options=replace(python_options, backend="native"),
                buffer_edges=1,
            )
        )
    actual_report = _completed_report(native_projector)

    assert (
        actual
        == expected
        == [
            Edge(
                "urn:native-integration#A",
                "http://subclassof",
                "urn:native-integration#B",
            )
        ]
    )
    _assert_semantic_report_parity(expected_report, actual_report)
    assert native_projector.last_view is overlay
    ingestion = actual_report.provenance.ingestion
    assert ingestion.path == "encoded-native"
    assert ingestion.reason is None
    assert ingestion.counters["encoded_buffer_count"] == 22
    assert ingestion.counters["encoded_buffer_bytes"] == expected_buffer_bytes
    assert ingestion.counters["encoded_detached_buffer_count"] == 11
    assert ingestion.counters["encoded_zero_copy_buffers"] == 22
    assert ingestion.counters["encoded_referenced_view_count"] == 1
    assert ingestion.counters["encoded_segment_count"] == 2
    assert ingestion.counters["encoded_posting_bytes"] == 0
    assert ingestion.counters["base_flattening_bytes"] == 0
    assert ingestion.counters["encoded_staging_copy_bytes"] == 0
    assert ingestion.counters["scalar_axiom_materializations"] == 0
    assert ingestion.counters["per_row_ffi_calls"] == 0
    _assert_bounded_native_output(
        ingestion.counters,
        compiled_edges=len(actual),
        batch_edges=1,
    )


@pytest.mark.parametrize(
    "provider_backend",
    [
        pyowl_core.BackendPreference.PYTHON,
        pyowl_core.BackendPreference.NATIVE,
    ],
    ids=["independent-bytes", "packed-bytes"],
)
def test_hidden_iterator_compiles_one_named_subclass_overlay_delta_without_flattening(
    provider_backend: pyowl_core.BackendPreference,
) -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "Declaration(Class(:A)) Declaration(Class(:B)) Declaration(Class(:C)) "
            "SubClassOf(:A :B)",
            backend=provider_backend,
        ),
    )
    addition_source = cast(
        pyowl_core.OntologyView,
        _snapshot("SubClassOf(:B :C)", backend=provider_backend),
    )
    added = set(addition_source.iter_axioms())
    assert len(added) == 1
    overlay = pyowl_core.apply_delta(
        base,
        pyowl_core.OntologyDelta(add_axioms=cast(Any, added)),
    )
    top_encoded = overlay.view(
        pyowl_core.EncodedStructuralView,
        schema_version=1,
        scope=pyowl_core.AxiomScope.CLOSURE,
    )
    assert tuple(segment.role for segment in top_encoded.segments) == (2, 3)
    assert top_encoded.buffers["root_kinds"].nbytes == 1
    source_encoded = top_encoded.segments[0].source
    assert source_encoded is not None
    expected_buffer_bytes = sum(value.nbytes for value in top_encoded.buffers.values()) + sum(
        value.nbytes for value in source_encoded.buffers.values()
    )

    python_options = ProjectionOptions(backend="python", order="encounter")
    expected_projector = Projector()
    expected = expected_projector.project(overlay, options=python_options)
    expected_report = _completed_report(expected_projector)
    captured: list[NativeEncodedDirectCompilation] = []
    captured_compilers: list[NativeEncodedDirectCompiler] = []
    real_prepare = native_module.prepare_native_encoded_compilation

    def capture_compilation(
        *args: Any,
        **kwargs: Any,
    ) -> tuple[NativeEncodedDirectCompilation | None, str | None]:
        result = real_prepare(*args, **kwargs)
        if result[0] is not None:
            captured.append(result[0])
            compiler = result[0].batches._compiler
            assert compiler is not None
            captured_compilers.append(compiler)
        return result

    with (
        patch.object(
            api_module,
            "prepare_native_encoded_compilation",
            side_effect=capture_compilation,
        ),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("one-root local overlay reached scalar traversal"),
        ),
    ):
        projector = Projector()
        actual = list(
            projector._iter_native_encoded_edges(
                overlay,
                options=replace(python_options, backend="native"),
                buffer_edges=1,
            )
        )
    report = _completed_report(projector)

    assert (
        actual
        == expected
        == [
            Edge(
                "urn:native-integration#A",
                "http://subclassof",
                "urn:native-integration#B",
            ),
            Edge(
                "urn:native-integration#B",
                "http://subclassof",
                "urn:native-integration#C",
            ),
        ]
    )
    _assert_semantic_report_parity(expected_report, report)
    assert len(captured) == 1
    compilation = captured[0]
    assert compilation.view is overlay
    assert compilation.lease.owner is base
    assert compilation.local_delta_lease is compilation.container_leases[0]
    assert compilation.local_delta_lease is not None
    assert compilation.local_delta_lease.owner is overlay
    assert compilation.excluded_root_ids is None
    assert len(captured_compilers) == 1
    assert captured_compilers[0].retained_buffer_count == 22

    ingestion = report.provenance.ingestion
    assert ingestion.path == "encoded-native"
    assert ingestion.reason is None
    assert ingestion.counters["encoded_buffer_count"] == 22
    assert ingestion.counters["encoded_buffer_bytes"] == expected_buffer_bytes
    assert ingestion.counters["encoded_detached_buffer_count"] == 22
    assert ingestion.counters["encoded_zero_copy_buffers"] == 22
    assert ingestion.counters["encoded_referenced_view_count"] == 1
    assert ingestion.counters["encoded_segment_count"] == 3
    assert ingestion.counters["encoded_posting_bytes"] == 0
    assert ingestion.counters["encoded_indexed_buffer_count"] == 0
    assert ingestion.counters["base_flattening_bytes"] == 0
    assert ingestion.counters["encoded_staging_copy_bytes"] == 0
    assert ingestion.counters["scalar_axiom_materializations"] == 0
    assert ingestion.counters["scalar_term_materializations"] == 0
    assert ingestion.counters["per_row_ffi_calls"] == 0
    _assert_bounded_native_output(
        ingestion.counters,
        compiled_edges=len(actual),
        batch_edges=1,
    )


@pytest.mark.parametrize(
    "provider_backend",
    [
        pyowl_core.BackendPreference.PYTHON,
        pyowl_core.BackendPreference.NATIVE,
    ],
    ids=["independent-bytes", "packed-bytes"],
)
@pytest.mark.parametrize(
    ("removed_sources", "expected_sources"),
    [
        (frozenset({"C"}), ("A", "D", "E")),
        (frozenset({"E"}), ("A", "C", "D")),
        (frozenset({"A", "C", "E"}), ("D",)),
    ],
    ids=["exclude-before-local", "exclude-after-local", "exclude-all-base"],
)
def test_hidden_iterator_composes_one_base_exclusion_with_one_local_delta(
    provider_backend: pyowl_core.BackendPreference,
    removed_sources: frozenset[str],
    expected_sources: tuple[str, ...],
) -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "SubClassOf(:A :Top) SubClassOf(:C :Top) SubClassOf(:E :Top)",
            backend=provider_backend,
        ),
    )
    removed = {
        axiom
        for axiom in base.iter_axioms()
        if cast(Any, axiom).sub_class.iri.value.rsplit("#", 1)[-1] in removed_sources
    }
    assert len(removed) == len(removed_sources)
    addition_source = cast(
        pyowl_core.OntologyView,
        _snapshot("SubClassOf(:D :Top)", backend=provider_backend),
    )
    overlay = pyowl_core.apply_delta(
        base,
        pyowl_core.OntologyDelta(
            add_axioms=cast(Any, set(addition_source.iter_axioms())),
            remove_axioms=cast(Any, removed),
        ),
    )
    top_encoded = overlay.view(
        pyowl_core.EncodedStructuralView,
        schema_version=1,
        scope=pyowl_core.AxiomScope.CLOSURE,
    )
    assert tuple(segment.role for segment in top_encoded.segments) == (2, 3)
    base_segment = cast(Any, top_encoded.segments[0])
    delta_segment = cast(Any, top_encoded.segments[1])
    assert base_segment.posting_mode == 2
    assert base_segment.root_ids.nbytes == 4 * len(removed)
    assert delta_segment.posting_mode == 0
    assert delta_segment.root_ids.nbytes == 0
    source_encoded = base_segment.source
    assert source_encoded is not None
    expected_buffer_bytes = sum(value.nbytes for value in top_encoded.buffers.values()) + sum(
        value.nbytes for value in source_encoded.buffers.values()
    )

    python_options = ProjectionOptions(backend="python", order="encounter")
    expected_projector = Projector()
    expected = expected_projector.project(overlay, options=python_options)
    expected_report = _completed_report(expected_projector)
    captured: list[NativeEncodedDirectCompilation] = []
    captured_compilers: list[NativeEncodedDirectCompiler] = []
    real_prepare = native_module.prepare_native_encoded_compilation

    def capture_compilation(
        *args: Any,
        **kwargs: Any,
    ) -> tuple[NativeEncodedDirectCompilation | None, str | None]:
        result = real_prepare(*args, **kwargs)
        if result[0] is not None:
            captured.append(result[0])
            compiler = result[0].batches._compiler
            assert compiler is not None
            captured_compilers.append(compiler)
        return result

    with (
        patch.object(
            api_module,
            "prepare_native_encoded_compilation",
            side_effect=capture_compilation,
        ),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("mixed local overlay reached scalar traversal"),
        ),
    ):
        projector = Projector()
        actual = list(
            projector._iter_native_encoded_edges(
                overlay,
                options=replace(python_options, backend="native"),
                buffer_edges=1,
            )
        )
    report = _completed_report(projector)

    assert actual == expected == [
        Edge(
            f"urn:native-integration#{source}",
            "http://subclassof",
            "urn:native-integration#Top",
        )
        for source in expected_sources
    ]
    _assert_semantic_report_parity(expected_report, report)
    assert len(captured) == 1
    compilation = captured[0]
    assert compilation.view is overlay
    assert compilation.lease.owner is base
    assert compilation.local_delta_lease is compilation.container_leases[0]
    assert compilation.local_delta_lease is not None
    assert compilation.local_delta_lease.owner is overlay
    assert compilation.excluded_root_ids is base_segment.root_ids
    assert len(captured_compilers) == 1
    assert captured_compilers[0].retained_buffer_count == 23

    ingestion = report.provenance.ingestion
    assert ingestion.path == "encoded-native"
    assert ingestion.reason is None
    assert ingestion.counters["encoded_buffer_count"] == 22
    assert ingestion.counters["encoded_buffer_bytes"] == expected_buffer_bytes
    assert ingestion.counters["encoded_detached_buffer_count"] == 23
    assert ingestion.counters["encoded_zero_copy_buffers"] == 22
    assert ingestion.counters["encoded_referenced_view_count"] == 1
    assert ingestion.counters["encoded_segment_count"] == 3
    assert ingestion.counters["encoded_posting_bytes"] == base_segment.root_ids.nbytes
    assert ingestion.counters["encoded_indexed_buffer_count"] == 0
    assert ingestion.counters["base_flattening_bytes"] == 0
    assert ingestion.counters["encoded_staging_copy_bytes"] == 0
    assert ingestion.counters["scalar_axiom_materializations"] == 0
    assert ingestion.counters["scalar_term_materializations"] == 0
    assert ingestion.counters["per_row_ffi_calls"] == 0
    _assert_bounded_native_output(
        ingestion.counters,
        compiled_edges=len(actual),
        batch_edges=1,
    )


@pytest.mark.parametrize(
    "provider_backend",
    [
        pyowl_core.BackendPreference.PYTHON,
        pyowl_core.BackendPreference.NATIVE,
    ],
    ids=["independent-bytes", "packed-bytes"],
)
@pytest.mark.parametrize(
    "local_entity",
    [
        "Class(:D)",
        "ObjectProperty(:p)",
        "DataProperty(:p)",
        "AnnotationProperty(:p)",
        "NamedIndividual(:i)",
        "Datatype(:datatype)",
    ],
    ids=[
        "class",
        "object-property",
        "data-property",
        "annotation-property",
        "named-individual",
        "datatype",
    ],
)
@pytest.mark.parametrize(
    ("removed_sources", "expected_sources"),
    [
        (frozenset(), ("A", "C")),
        (frozenset({"C"}), ("A",)),
        (frozenset({"A", "C"}), ()),
    ],
    ids=["base-all", "base-exclude", "base-exclude-all"],
)
def test_hidden_iterator_compiles_one_silent_local_declaration(
    provider_backend: pyowl_core.BackendPreference,
    local_entity: str,
    removed_sources: frozenset[str],
    expected_sources: tuple[str, ...],
) -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "SubClassOf(:A :Top) SubClassOf(:C :Top)",
            backend=provider_backend,
        ),
    )
    removed = {
        axiom
        for axiom in base.iter_axioms()
        if cast(Any, axiom).sub_class.iri.value.rsplit("#", 1)[-1] in removed_sources
    }
    assert len(removed) == len(removed_sources)
    addition_source = cast(
        pyowl_core.OntologyView,
        _snapshot(f"Declaration({local_entity})", backend=provider_backend),
    )
    overlay = pyowl_core.apply_delta(
        base,
        pyowl_core.OntologyDelta(
            add_axioms=cast(Any, set(addition_source.iter_axioms())),
            remove_axioms=cast(Any, removed),
        ),
    )
    top_encoded = overlay.view(
        pyowl_core.EncodedStructuralView,
        schema_version=1,
        scope=pyowl_core.AxiomScope.CLOSURE,
    )
    assert tuple(segment.role for segment in top_encoded.segments) == (2, 3)
    base_segment = cast(Any, top_encoded.segments[0])
    delta_segment = cast(Any, top_encoded.segments[1])
    assert base_segment.posting_mode == (2 if removed_sources else 0)
    assert base_segment.root_ids.nbytes == 4 * len(removed_sources)
    assert delta_segment.posting_mode == 0
    assert delta_segment.root_ids.nbytes == 0
    source_encoded = base_segment.source
    assert source_encoded is not None
    expected_buffer_bytes = sum(value.nbytes for value in top_encoded.buffers.values()) + sum(
        value.nbytes for value in source_encoded.buffers.values()
    )

    python_options = ProjectionOptions(backend="python", order="encounter")
    expected_projector = Projector()
    expected = expected_projector.project(overlay, options=python_options)
    expected_report = _completed_report(expected_projector)
    captured: list[NativeEncodedDirectCompilation] = []
    real_prepare = native_module.prepare_native_encoded_compilation

    def capture_compilation(
        *args: Any,
        **kwargs: Any,
    ) -> tuple[NativeEncodedDirectCompilation | None, str | None]:
        result = real_prepare(*args, **kwargs)
        if result[0] is not None:
            captured.append(result[0])
        return result

    with (
        patch.object(
            api_module,
            "prepare_native_encoded_compilation",
            side_effect=capture_compilation,
        ),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("silent local declaration reached scalar traversal"),
        ),
    ):
        projector = Projector()
        actual = list(
            projector._iter_native_encoded_edges(
                overlay,
                options=replace(python_options, backend="native"),
                buffer_edges=1,
            )
        )
    report = _completed_report(projector)

    assert actual == expected == [
        Edge(
            f"urn:native-integration#{source}",
            "http://subclassof",
            "urn:native-integration#Top",
        )
        for source in expected_sources
    ]
    _assert_semantic_report_parity(expected_report, report)
    assert len(captured) == 1
    compilation = captured[0]
    assert compilation.view is overlay
    assert compilation.lease.owner is base
    assert compilation.local_delta_lease is compilation.container_leases[0]
    assert compilation.local_delta_lease is not None
    assert compilation.local_delta_lease.owner is overlay
    assert compilation.excluded_root_ids is (
        base_segment.root_ids if removed_sources else None
    )
    assert compilation.native_statistics.roots == len(expected_sources) + 1
    assert compilation.native_statistics.declarations == 1
    assert compilation.native_statistics.subclasses == len(expected_sources)
    assert compilation.native_statistics.edges == len(expected_sources)
    assert compilation.batches._compiler is None

    ingestion = report.provenance.ingestion
    assert ingestion.path == "encoded-native"
    assert ingestion.reason is None
    assert ingestion.counters["encoded_buffer_count"] == 22
    assert ingestion.counters["encoded_buffer_bytes"] == expected_buffer_bytes
    assert ingestion.counters["encoded_detached_buffer_count"] == 22 + int(bool(removed_sources))
    assert ingestion.counters["encoded_zero_copy_buffers"] == 22
    assert ingestion.counters["encoded_referenced_view_count"] == 1
    assert ingestion.counters["encoded_segment_count"] == 3
    assert ingestion.counters["encoded_posting_bytes"] == 4 * len(removed_sources)
    assert ingestion.counters["encoded_indexed_buffer_count"] == 0
    assert ingestion.counters["base_flattening_bytes"] == 0
    assert ingestion.counters["encoded_staging_copy_bytes"] == 0
    assert ingestion.counters["scalar_axiom_materializations"] == 0
    assert ingestion.counters["scalar_term_materializations"] == 0
    assert ingestion.counters["per_row_ffi_calls"] == 0
    _assert_bounded_native_output(
        ingestion.counters,
        compiled_edges=len(actual),
        batch_edges=1,
    )


@pytest.mark.parametrize(
    "provider_backend",
    [
        pyowl_core.BackendPreference.PYTHON,
        pyowl_core.BackendPreference.NATIVE,
    ],
    ids=["independent-bytes", "packed-bytes"],
)
@pytest.mark.parametrize(
    (
        "local_body",
        "removed_constructor",
        "only_taxonomy",
        "expected_relations",
    ),
    [
        (
            "SubClassOf(:A ObjectSomeValuesFrom(:p :B))",
            None,
            False,
            ("p", "child", "pinv"),
        ),
        (
            "SubClassOf(:A ObjectAllValuesFrom(ObjectInverseOf(:p) :B))",
            None,
            False,
            ("p", "child", "pinv"),
        ),
        (
            "SubClassOf(ObjectMinCardinality(1 :p :B) :A)",
            None,
            False,
            ("p", "child", "pinv"),
        ),
        (
            "SubClassOf(ObjectMaxCardinality(2 ObjectInverseOf(:p) :B) :A)",
            None,
            False,
            ("p", "child", "pinv"),
        ),
        (
            "SubClassOf(:A ObjectSomeValuesFrom(:p :B))",
            "SubObjectPropertyOf",
            False,
            ("p", "pinv"),
        ),
        (
            "SubClassOf(:A ObjectSomeValuesFrom(:p :B))",
            "InverseObjectProperties",
            False,
            ("p", "child"),
        ),
        (
            "SubClassOf(:A ObjectSomeValuesFrom(:p :B))",
            None,
            True,
            (),
        ),
    ],
    ids=[
        "some",
        "all-inverse-property",
        "min-restriction-first",
        "max-inverse-restriction-first",
        "exclude-subrole",
        "exclude-inverse",
        "only-taxonomy",
    ],
)
def test_hidden_iterator_compiles_one_named_local_restriction(
    provider_backend: pyowl_core.BackendPreference,
    local_body: str,
    removed_constructor: str | None,
    only_taxonomy: bool,
    expected_relations: tuple[str, ...],
) -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "SubObjectPropertyOf(:child :p) InverseObjectProperties(:p :pinv)",
            backend=provider_backend,
        ),
    )
    removed = {
        axiom
        for axiom in base.iter_axioms()
        if type(axiom).__name__ == removed_constructor
    }
    assert len(removed) == int(removed_constructor is not None)
    addition_source = cast(
        pyowl_core.OntologyView,
        _snapshot(local_body, backend=provider_backend),
    )
    overlay = pyowl_core.apply_delta(
        base,
        pyowl_core.OntologyDelta(
            add_axioms=cast(Any, set(addition_source.iter_axioms())),
            remove_axioms=cast(Any, removed),
        ),
    )
    top_encoded = overlay.view(
        pyowl_core.EncodedStructuralView,
        schema_version=1,
        scope=pyowl_core.AxiomScope.CLOSURE,
    )
    assert tuple(segment.role for segment in top_encoded.segments) == (2, 3)
    base_segment = cast(Any, top_encoded.segments[0])
    delta_segment = cast(Any, top_encoded.segments[1])
    assert base_segment.posting_mode == (2 if removed else 0)
    assert base_segment.root_ids.nbytes == 4 * len(removed)
    assert delta_segment.posting_mode == 0
    assert delta_segment.root_ids.nbytes == 0
    source_encoded = base_segment.source
    assert source_encoded is not None
    expected_buffer_bytes = sum(value.nbytes for value in top_encoded.buffers.values()) + sum(
        value.nbytes for value in source_encoded.buffers.values()
    )

    python_options = ProjectionOptions(
        backend="python",
        order="encounter",
        only_taxonomy=only_taxonomy,
    )
    expected_projector = Projector()
    expected = expected_projector.project(overlay, options=python_options)
    expected_report = _completed_report(expected_projector)
    captured: list[NativeEncodedDirectCompilation] = []
    captured_compilers: list[NativeEncodedDirectCompiler] = []
    real_prepare = native_module.prepare_native_encoded_compilation

    def capture_compilation(
        *args: Any,
        **kwargs: Any,
    ) -> tuple[NativeEncodedDirectCompilation | None, str | None]:
        result = real_prepare(*args, **kwargs)
        if result[0] is not None:
            captured.append(result[0])
            compiler = result[0].batches._compiler
            if compiler is not None:
                captured_compilers.append(compiler)
        return result

    with (
        patch.object(
            api_module,
            "prepare_native_encoded_compilation",
            side_effect=capture_compilation,
        ),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("named local restriction reached scalar traversal"),
        ),
    ):
        projector = Projector()
        actual = list(
            projector._iter_native_encoded_edges(
                overlay,
                options=replace(python_options, backend="native"),
                buffer_edges=1,
            )
        )
    report = _completed_report(projector)

    expected_edges = [
        (
            Edge(
                "urn:native-integration#B",
                "urn:native-integration#pinv",
                "urn:native-integration#A",
            )
            if relation == "pinv"
            else Edge(
                "urn:native-integration#A",
                f"urn:native-integration#{relation}",
                "urn:native-integration#B",
            )
        )
        for relation in expected_relations
    ]
    assert actual == expected == expected_edges
    _assert_semantic_report_parity(expected_report, report)
    assert len(captured) == 1
    compilation = captured[0]
    assert compilation.view is overlay
    assert compilation.lease.owner is base
    assert compilation.local_delta_lease is compilation.container_leases[0]
    assert compilation.local_delta_lease is not None
    assert compilation.local_delta_lease.owner is overlay
    assert compilation.excluded_root_ids is (
        base_segment.root_ids if removed else None
    )
    assert compilation.native_statistics.roots == 3 - len(removed)
    assert compilation.native_statistics.subclasses == 1
    assert compilation.native_statistics.restriction_subclasses == 1
    assert compilation.native_statistics.sub_object_properties == int(
        removed_constructor != "SubObjectPropertyOf"
    )
    assert compilation.native_statistics.inverse_object_properties == int(
        removed_constructor != "InverseObjectProperties"
    )
    assert compilation.native_statistics.role_expansion_edges == max(
        0,
        len(expected_relations) - 1,
    )
    assert compilation.native_statistics.edges == len(expected_edges)
    assert len(captured_compilers) == 1
    assert captured_compilers[0].retained_buffer_count == 22 + int(bool(removed))
    assert compilation.batches._compiler is None

    ingestion = report.provenance.ingestion
    assert ingestion.path == "encoded-native"
    assert ingestion.reason is None
    assert ingestion.counters["encoded_buffer_count"] == 22
    assert ingestion.counters["encoded_buffer_bytes"] == expected_buffer_bytes
    assert ingestion.counters["encoded_detached_buffer_count"] == 22 + int(bool(removed))
    assert ingestion.counters["encoded_zero_copy_buffers"] == 22
    assert ingestion.counters["encoded_referenced_view_count"] == 1
    assert ingestion.counters["encoded_segment_count"] == 3
    assert ingestion.counters["encoded_posting_bytes"] == 4 * len(removed)
    assert ingestion.counters["encoded_indexed_buffer_count"] == 0
    assert ingestion.counters["base_flattening_bytes"] == 0
    assert ingestion.counters["encoded_staging_copy_bytes"] == 0
    assert ingestion.counters["scalar_axiom_materializations"] == 0
    assert ingestion.counters["scalar_term_materializations"] == 0
    assert ingestion.counters["per_row_ffi_calls"] == 0
    _assert_bounded_native_output(
        ingestion.counters,
        compiled_edges=len(actual),
        batch_edges=1,
    )


@pytest.mark.parametrize(
    "provider_backend",
    [
        pyowl_core.BackendPreference.PYTHON,
        pyowl_core.BackendPreference.NATIVE,
    ],
    ids=["independent-bytes", "packed-bytes"],
)
@pytest.mark.parametrize(
    ("removed_indices", "only_taxonomy", "expected_pairs"),
    [
        ((), False, (("i", "A"), ("j", "B"), ("k", "C"))),
        ((0,), False, (("j", "B"), ("k", "C"))),
        ((1,), False, (("i", "A"), ("j", "B"))),
        ((0, 1), False, (("j", "B"),)),
        ((), True, (("i", "A"), ("j", "B"), ("k", "C"))),
    ],
    ids=[
        "base-all",
        "exclude-before-local",
        "exclude-after-local",
        "exclude-all-base",
        "only-taxonomy",
    ],
)
def test_hidden_iterator_compiles_one_named_local_class_assertion(
    provider_backend: pyowl_core.BackendPreference,
    removed_indices: tuple[int, ...],
    only_taxonomy: bool,
    expected_pairs: tuple[tuple[str, str], ...],
) -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "ClassAssertion(:A :i) ClassAssertion(:C :k)",
            backend=provider_backend,
        ),
    )
    base_axioms = tuple(base.iter_axioms())
    assert len(base_axioms) == 2
    removed = {base_axioms[index] for index in removed_indices}
    addition_source = cast(
        pyowl_core.OntologyView,
        _snapshot("ClassAssertion(:B :j)", backend=provider_backend),
    )
    overlay = pyowl_core.apply_delta(
        base,
        pyowl_core.OntologyDelta(
            add_axioms=cast(Any, set(addition_source.iter_axioms())),
            remove_axioms=cast(Any, removed),
        ),
    )
    top_encoded = overlay.view(
        pyowl_core.EncodedStructuralView,
        schema_version=1,
        scope=pyowl_core.AxiomScope.CLOSURE,
    )
    assert tuple(segment.role for segment in top_encoded.segments) == (2, 3)
    base_segment = cast(Any, top_encoded.segments[0])
    delta_segment = cast(Any, top_encoded.segments[1])
    assert base_segment.posting_mode == (2 if removed else 0)
    assert base_segment.root_ids.nbytes == 4 * len(removed)
    assert delta_segment.posting_mode == 0
    assert delta_segment.root_ids.nbytes == 0
    source_encoded = base_segment.source
    assert source_encoded is not None
    expected_buffer_bytes = sum(value.nbytes for value in top_encoded.buffers.values()) + sum(
        value.nbytes for value in source_encoded.buffers.values()
    )

    python_options = ProjectionOptions(
        backend="python",
        order="encounter",
        only_taxonomy=only_taxonomy,
    )
    expected_projector = Projector()
    expected = expected_projector.project(overlay, options=python_options)
    expected_report = _completed_report(expected_projector)
    captured: list[NativeEncodedDirectCompilation] = []
    captured_compilers: list[NativeEncodedDirectCompiler] = []
    real_prepare = native_module.prepare_native_encoded_compilation

    def capture_compilation(
        *args: Any,
        **kwargs: Any,
    ) -> tuple[NativeEncodedDirectCompilation | None, str | None]:
        result = real_prepare(*args, **kwargs)
        if result[0] is not None:
            captured.append(result[0])
            compiler = result[0].batches._compiler
            assert compiler is not None
            captured_compilers.append(compiler)
        return result

    with (
        patch.object(
            api_module,
            "prepare_native_encoded_compilation",
            side_effect=capture_compilation,
        ),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("named local ClassAssertion reached scalar traversal"),
        ),
    ):
        projector = Projector()
        actual = list(
            projector._iter_native_encoded_edges(
                overlay,
                options=replace(python_options, backend="native"),
                buffer_edges=1,
            )
        )
    report = _completed_report(projector)

    expected_edges = [
        Edge(
            f"urn:native-integration#{individual}",
            "http://type",
            f"urn:native-integration#{class_name}",
        )
        for individual, class_name in expected_pairs
    ]
    assert actual == expected == expected_edges
    _assert_semantic_report_parity(expected_report, report)
    assert len(captured) == 1
    compilation = captured[0]
    assert compilation.view is overlay
    assert compilation.lease.owner is base
    assert compilation.local_delta_lease is compilation.container_leases[0]
    assert compilation.local_delta_lease is not None
    assert compilation.local_delta_lease.owner is overlay
    assert compilation.excluded_root_ids is (
        base_segment.root_ids if removed else None
    )
    assert compilation.native_statistics.roots == len(expected_edges)
    assert compilation.native_statistics.class_assertions == len(expected_edges)
    assert compilation.native_statistics.ignored_class_assertions == 0
    assert compilation.native_statistics.edges == len(expected_edges)
    assert len(captured_compilers) == 1
    assert captured_compilers[0].retained_buffer_count == 22 + int(bool(removed))
    assert compilation.batches._compiler is None

    ingestion = report.provenance.ingestion
    assert ingestion.path == "encoded-native"
    assert ingestion.reason is None
    assert ingestion.counters["encoded_buffer_count"] == 22
    assert ingestion.counters["encoded_buffer_bytes"] == expected_buffer_bytes
    assert ingestion.counters["encoded_detached_buffer_count"] == 22 + int(bool(removed))
    assert ingestion.counters["encoded_zero_copy_buffers"] == 22
    assert ingestion.counters["encoded_referenced_view_count"] == 1
    assert ingestion.counters["encoded_segment_count"] == 3
    assert ingestion.counters["encoded_posting_bytes"] == 4 * len(removed)
    assert ingestion.counters["encoded_indexed_buffer_count"] == 0
    assert ingestion.counters["base_flattening_bytes"] == 0
    assert ingestion.counters["encoded_staging_copy_bytes"] == 0
    assert ingestion.counters["scalar_axiom_materializations"] == 0
    assert ingestion.counters["scalar_term_materializations"] == 0
    assert ingestion.counters["per_row_ffi_calls"] == 0
    _assert_bounded_native_output(
        ingestion.counters,
        compiled_edges=len(actual),
        batch_edges=1,
    )


@pytest.mark.parametrize(
    "provider_backend",
    [
        pyowl_core.BackendPreference.PYTHON,
        pyowl_core.BackendPreference.NATIVE,
    ],
    ids=["independent-bytes", "packed-bytes"],
)
@pytest.mark.parametrize(
    ("removed_indices", "only_taxonomy", "expected_triples"),
    [
        ((), False, (("i", "p", "A"), ("j", "p", "B"), ("k", "p", "C"))),
        ((0,), False, (("j", "p", "B"), ("k", "p", "C"))),
        ((1,), False, (("i", "p", "A"), ("j", "p", "B"))),
        ((0, 1), False, (("j", "p", "B"),)),
        ((), True, (("i", "p", "A"), ("j", "p", "B"), ("k", "p", "C"))),
    ],
    ids=[
        "base-all",
        "exclude-before-local",
        "exclude-after-local",
        "exclude-all-base",
        "only-taxonomy",
    ],
)
def test_hidden_iterator_compiles_one_named_local_object_property_assertion(
    provider_backend: pyowl_core.BackendPreference,
    removed_indices: tuple[int, ...],
    only_taxonomy: bool,
    expected_triples: tuple[tuple[str, str, str], ...],
) -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "ObjectPropertyAssertion(:p :i :A) ObjectPropertyAssertion(:p :k :C)",
            backend=provider_backend,
        ),
    )
    base_axioms = tuple(base.iter_axioms())
    assert len(base_axioms) == 2
    removed = {base_axioms[index] for index in removed_indices}
    addition_source = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "ObjectPropertyAssertion(:p :j :B)",
            backend=provider_backend,
        ),
    )
    overlay = pyowl_core.apply_delta(
        base,
        pyowl_core.OntologyDelta(
            add_axioms=cast(Any, set(addition_source.iter_axioms())),
            remove_axioms=cast(Any, removed),
        ),
    )
    top_encoded = overlay.view(
        pyowl_core.EncodedStructuralView,
        schema_version=1,
        scope=pyowl_core.AxiomScope.CLOSURE,
    )
    assert tuple(segment.role for segment in top_encoded.segments) == (2, 3)
    base_segment = cast(Any, top_encoded.segments[0])
    delta_segment = cast(Any, top_encoded.segments[1])
    assert base_segment.posting_mode == (2 if removed else 0)
    assert base_segment.root_ids.nbytes == 4 * len(removed)
    assert delta_segment.posting_mode == 0
    assert delta_segment.root_ids.nbytes == 0
    source_encoded = base_segment.source
    assert source_encoded is not None
    expected_buffer_bytes = sum(value.nbytes for value in top_encoded.buffers.values()) + sum(
        value.nbytes for value in source_encoded.buffers.values()
    )

    python_options = ProjectionOptions(
        backend="python",
        order="encounter",
        only_taxonomy=only_taxonomy,
    )
    expected_projector = Projector()
    expected = expected_projector.project(overlay, options=python_options)
    expected_report = _completed_report(expected_projector)
    captured: list[NativeEncodedDirectCompilation] = []
    captured_compilers: list[NativeEncodedDirectCompiler] = []
    real_prepare = native_module.prepare_native_encoded_compilation

    def capture_compilation(
        *args: Any,
        **kwargs: Any,
    ) -> tuple[NativeEncodedDirectCompilation | None, str | None]:
        result = real_prepare(*args, **kwargs)
        if result[0] is not None:
            captured.append(result[0])
            compiler = result[0].batches._compiler
            assert compiler is not None
            captured_compilers.append(compiler)
        return result

    with (
        patch.object(
            api_module,
            "prepare_native_encoded_compilation",
            side_effect=capture_compilation,
        ),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError(
                "named local ObjectPropertyAssertion reached scalar traversal"
            ),
        ),
    ):
        projector = Projector()
        actual = list(
            projector._iter_native_encoded_edges(
                overlay,
                options=replace(python_options, backend="native"),
                buffer_edges=1,
            )
        )
    report = _completed_report(projector)

    expected_edges = [
        Edge(
            f"urn:native-integration#{source}",
            f"urn:native-integration#{relation}",
            f"urn:native-integration#{destination}",
        )
        for source, relation, destination in expected_triples
    ]
    assert actual == expected == expected_edges
    _assert_semantic_report_parity(expected_report, report)
    assert len(captured) == 1
    compilation = captured[0]
    assert compilation.view is overlay
    assert compilation.lease.owner is base
    assert compilation.local_delta_lease is compilation.container_leases[0]
    assert compilation.local_delta_lease is not None
    assert compilation.local_delta_lease.owner is overlay
    assert compilation.excluded_root_ids is (
        base_segment.root_ids if removed else None
    )
    assert compilation.native_statistics.roots == len(expected_edges)
    assert compilation.native_statistics.object_property_assertions == len(expected_edges)
    assert compilation.native_statistics.edges == len(expected_edges)
    assert len(captured_compilers) == 1
    assert captured_compilers[0].retained_buffer_count == 22 + int(bool(removed))
    assert compilation.batches._compiler is None

    ingestion = report.provenance.ingestion
    assert ingestion.path == "encoded-native"
    assert ingestion.reason is None
    assert ingestion.counters["encoded_buffer_count"] == 22
    assert ingestion.counters["encoded_buffer_bytes"] == expected_buffer_bytes
    assert ingestion.counters["encoded_detached_buffer_count"] == 22 + int(bool(removed))
    assert ingestion.counters["encoded_zero_copy_buffers"] == 22
    assert ingestion.counters["encoded_referenced_view_count"] == 1
    assert ingestion.counters["encoded_segment_count"] == 3
    assert ingestion.counters["encoded_posting_bytes"] == 4 * len(removed)
    assert ingestion.counters["encoded_indexed_buffer_count"] == 0
    assert ingestion.counters["base_flattening_bytes"] == 0
    assert ingestion.counters["encoded_staging_copy_bytes"] == 0
    assert ingestion.counters["scalar_axiom_materializations"] == 0
    assert ingestion.counters["scalar_term_materializations"] == 0
    assert ingestion.counters["per_row_ffi_calls"] == 0
    _assert_bounded_native_output(
        ingestion.counters,
        compiled_edges=len(actual),
        batch_edges=1,
    )


@pytest.mark.parametrize(
    "provider_backend",
    [
        pyowl_core.BackendPreference.PYTHON,
        pyowl_core.BackendPreference.NATIVE,
    ],
    ids=["independent-bytes", "packed-bytes"],
)
@pytest.mark.parametrize(
    "local_property",
    [":p", "ObjectInverseOf(:p)"],
    ids=["named-property", "inverse-property"],
)
@pytest.mark.parametrize(
    ("removed_sources", "only_taxonomy", "expected_sources"),
    [
        (frozenset(), False, ("A", "C")),
        (frozenset({"C"}), False, ("A",)),
        (frozenset({"A", "C"}), False, ()),
        (frozenset(), True, ("A", "C")),
    ],
    ids=["base-all", "base-exclude", "base-exclude-all", "only-taxonomy"],
)
def test_hidden_iterator_compiles_one_silent_local_negative_object_assertion(
    provider_backend: pyowl_core.BackendPreference,
    local_property: str,
    removed_sources: frozenset[str],
    only_taxonomy: bool,
    expected_sources: tuple[str, ...],
) -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "SubClassOf(:A :Top) SubClassOf(:C :Top)",
            backend=provider_backend,
        ),
    )
    removed = {
        axiom
        for axiom in base.iter_axioms()
        if cast(Any, axiom).sub_class.iri.value.rsplit("#", 1)[-1] in removed_sources
    }
    assert len(removed) == len(removed_sources)
    addition_source = cast(
        pyowl_core.OntologyView,
        _snapshot(
            f"NegativeObjectPropertyAssertion({local_property} :i :j)",
            backend=provider_backend,
        ),
    )
    overlay = pyowl_core.apply_delta(
        base,
        pyowl_core.OntologyDelta(
            add_axioms=cast(Any, set(addition_source.iter_axioms())),
            remove_axioms=cast(Any, removed),
        ),
    )
    top_encoded = overlay.view(
        pyowl_core.EncodedStructuralView,
        schema_version=1,
        scope=pyowl_core.AxiomScope.CLOSURE,
    )
    assert tuple(segment.role for segment in top_encoded.segments) == (2, 3)
    base_segment = cast(Any, top_encoded.segments[0])
    delta_segment = cast(Any, top_encoded.segments[1])
    assert base_segment.posting_mode == (2 if removed else 0)
    assert base_segment.root_ids.nbytes == 4 * len(removed)
    assert delta_segment.posting_mode == 0
    assert delta_segment.root_ids.nbytes == 0
    source_encoded = base_segment.source
    assert source_encoded is not None
    expected_buffer_bytes = sum(value.nbytes for value in top_encoded.buffers.values()) + sum(
        value.nbytes for value in source_encoded.buffers.values()
    )

    python_options = ProjectionOptions(
        backend="python",
        order="encounter",
        only_taxonomy=only_taxonomy,
    )
    expected_projector = Projector()
    expected = expected_projector.project(overlay, options=python_options)
    expected_report = _completed_report(expected_projector)
    captured: list[NativeEncodedDirectCompilation] = []
    real_prepare = native_module.prepare_native_encoded_compilation

    def capture_compilation(
        *args: Any,
        **kwargs: Any,
    ) -> tuple[NativeEncodedDirectCompilation | None, str | None]:
        result = real_prepare(*args, **kwargs)
        if result[0] is not None:
            captured.append(result[0])
        return result

    with (
        patch.object(
            api_module,
            "prepare_native_encoded_compilation",
            side_effect=capture_compilation,
        ),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError(
                "silent local NegativeObjectPropertyAssertion reached scalar traversal"
            ),
        ),
    ):
        projector = Projector()
        actual = list(
            projector._iter_native_encoded_edges(
                overlay,
                options=replace(python_options, backend="native"),
                buffer_edges=1,
            )
        )
    report = _completed_report(projector)

    expected_edges = [
        Edge(
            f"urn:native-integration#{source}",
            "http://subclassof",
            "urn:native-integration#Top",
        )
        for source in expected_sources
    ]
    assert actual == expected == expected_edges
    _assert_semantic_report_parity(expected_report, report)
    assert len(captured) == 1
    compilation = captured[0]
    assert compilation.view is overlay
    assert compilation.lease.owner is base
    assert compilation.local_delta_lease is compilation.container_leases[0]
    assert compilation.local_delta_lease is not None
    assert compilation.local_delta_lease.owner is overlay
    assert compilation.excluded_root_ids is (
        base_segment.root_ids if removed else None
    )
    assert compilation.native_statistics.roots == len(expected_sources) + 1
    assert compilation.native_statistics.subclasses == len(expected_sources)
    assert compilation.native_statistics.negative_object_property_assertions == 1
    assert compilation.native_statistics.skipped_axioms == 1
    assert compilation.native_statistics.edges == len(expected_edges)
    assert compilation.batches._compiler is None

    ingestion = report.provenance.ingestion
    assert ingestion.path == "encoded-native"
    assert ingestion.reason is None
    assert ingestion.counters["encoded_buffer_count"] == 22
    assert ingestion.counters["encoded_buffer_bytes"] == expected_buffer_bytes
    assert ingestion.counters["encoded_detached_buffer_count"] == 22 + int(bool(removed))
    assert ingestion.counters["encoded_zero_copy_buffers"] == 22
    assert ingestion.counters["encoded_referenced_view_count"] == 1
    assert ingestion.counters["encoded_segment_count"] == 3
    assert ingestion.counters["encoded_posting_bytes"] == 4 * len(removed)
    assert ingestion.counters["encoded_indexed_buffer_count"] == 0
    assert ingestion.counters["base_flattening_bytes"] == 0
    assert ingestion.counters["encoded_staging_copy_bytes"] == 0
    assert ingestion.counters["scalar_axiom_materializations"] == 0
    assert ingestion.counters["scalar_term_materializations"] == 0
    assert ingestion.counters["per_row_ffi_calls"] == 0
    _assert_bounded_native_output(
        ingestion.counters,
        compiled_edges=len(actual),
        batch_edges=1,
    )


@pytest.mark.parametrize(
    "provider_backend",
    [
        pyowl_core.BackendPreference.PYTHON,
        pyowl_core.BackendPreference.NATIVE,
    ],
    ids=["independent-bytes", "packed-bytes"],
)
@pytest.mark.parametrize(
    "local_literal",
    [
        '"value"',
        '"7"^^<http://www.w3.org/2001/XMLSchema#integer>',
    ],
    ids=["string-literal", "typed-integer-literal"],
)
@pytest.mark.parametrize(
    ("removed_sources", "only_taxonomy", "expected_sources"),
    [
        (frozenset(), False, ("A", "C")),
        (frozenset({"C"}), False, ("A",)),
        (frozenset({"A", "C"}), False, ()),
        (frozenset(), True, ("A", "C")),
    ],
    ids=["base-all", "base-exclude", "base-exclude-all", "only-taxonomy"],
)
def test_hidden_iterator_compiles_one_silent_local_data_property_assertion(
    provider_backend: pyowl_core.BackendPreference,
    local_literal: str,
    removed_sources: frozenset[str],
    only_taxonomy: bool,
    expected_sources: tuple[str, ...],
) -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "SubClassOf(:A :Top) SubClassOf(:C :Top)",
            backend=provider_backend,
        ),
    )
    removed = {
        axiom
        for axiom in base.iter_axioms()
        if cast(Any, axiom).sub_class.iri.value.rsplit("#", 1)[-1] in removed_sources
    }
    assert len(removed) == len(removed_sources)
    addition_source = cast(
        pyowl_core.OntologyView,
        _snapshot(
            f"DataPropertyAssertion(:dp :i {local_literal})",
            backend=provider_backend,
        ),
    )
    overlay = pyowl_core.apply_delta(
        base,
        pyowl_core.OntologyDelta(
            add_axioms=cast(Any, set(addition_source.iter_axioms())),
            remove_axioms=cast(Any, removed),
        ),
    )
    top_encoded = overlay.view(
        pyowl_core.EncodedStructuralView,
        schema_version=1,
        scope=pyowl_core.AxiomScope.CLOSURE,
    )
    assert tuple(segment.role for segment in top_encoded.segments) == (2, 3)
    base_segment = cast(Any, top_encoded.segments[0])
    delta_segment = cast(Any, top_encoded.segments[1])
    assert base_segment.posting_mode == (2 if removed else 0)
    assert base_segment.root_ids.nbytes == 4 * len(removed)
    assert delta_segment.posting_mode == 0
    assert delta_segment.root_ids.nbytes == 0
    source_encoded = base_segment.source
    assert source_encoded is not None
    expected_buffer_bytes = sum(value.nbytes for value in top_encoded.buffers.values()) + sum(
        value.nbytes for value in source_encoded.buffers.values()
    )

    python_options = ProjectionOptions(
        backend="python",
        order="encounter",
        only_taxonomy=only_taxonomy,
    )
    expected_projector = Projector()
    expected = expected_projector.project(overlay, options=python_options)
    expected_report = _completed_report(expected_projector)
    captured: list[NativeEncodedDirectCompilation] = []
    real_prepare = native_module.prepare_native_encoded_compilation

    def capture_compilation(
        *args: Any,
        **kwargs: Any,
    ) -> tuple[NativeEncodedDirectCompilation | None, str | None]:
        result = real_prepare(*args, **kwargs)
        if result[0] is not None:
            captured.append(result[0])
        return result

    with (
        patch.object(
            api_module,
            "prepare_native_encoded_compilation",
            side_effect=capture_compilation,
        ),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError(
                "silent local DataPropertyAssertion reached scalar traversal"
            ),
        ),
    ):
        projector = Projector()
        actual = list(
            projector._iter_native_encoded_edges(
                overlay,
                options=replace(python_options, backend="native"),
                buffer_edges=1,
            )
        )
    report = _completed_report(projector)

    expected_edges = [
        Edge(
            f"urn:native-integration#{source}",
            "http://subclassof",
            "urn:native-integration#Top",
        )
        for source in expected_sources
    ]
    assert actual == expected == expected_edges
    _assert_semantic_report_parity(expected_report, report)
    assert len(captured) == 1
    compilation = captured[0]
    assert compilation.view is overlay
    assert compilation.lease.owner is base
    assert compilation.local_delta_lease is compilation.container_leases[0]
    assert compilation.local_delta_lease is not None
    assert compilation.local_delta_lease.owner is overlay
    assert compilation.excluded_root_ids is (
        base_segment.root_ids if removed else None
    )
    assert compilation.native_statistics.roots == len(expected_sources) + 1
    assert compilation.native_statistics.subclasses == len(expected_sources)
    assert compilation.native_statistics.data_property_assertions == 1
    assert compilation.native_statistics.skipped_axioms == 1
    assert compilation.native_statistics.edges == len(expected_edges)
    assert compilation.batches._compiler is None

    ingestion = report.provenance.ingestion
    assert ingestion.path == "encoded-native"
    assert ingestion.reason is None
    assert ingestion.counters["encoded_buffer_count"] == 22
    assert ingestion.counters["encoded_buffer_bytes"] == expected_buffer_bytes
    assert ingestion.counters["encoded_detached_buffer_count"] == 22 + int(bool(removed))
    assert ingestion.counters["encoded_zero_copy_buffers"] == 22
    assert ingestion.counters["encoded_referenced_view_count"] == 1
    assert ingestion.counters["encoded_segment_count"] == 3
    assert ingestion.counters["encoded_posting_bytes"] == 4 * len(removed)
    assert ingestion.counters["encoded_indexed_buffer_count"] == 0
    assert ingestion.counters["base_flattening_bytes"] == 0
    assert ingestion.counters["encoded_staging_copy_bytes"] == 0
    assert ingestion.counters["scalar_axiom_materializations"] == 0
    assert ingestion.counters["scalar_term_materializations"] == 0
    assert ingestion.counters["per_row_ffi_calls"] == 0
    _assert_bounded_native_output(
        ingestion.counters,
        compiled_edges=len(actual),
        batch_edges=1,
    )


@pytest.mark.parametrize(
    "provider_backend",
    [
        pyowl_core.BackendPreference.PYTHON,
        pyowl_core.BackendPreference.NATIVE,
    ],
    ids=["independent-bytes", "packed-bytes"],
)
@pytest.mark.parametrize(
    "local_literal",
    [
        '"blocked"',
        '"9"^^<http://www.w3.org/2001/XMLSchema#integer>',
    ],
    ids=["string-literal", "typed-integer-literal"],
)
@pytest.mark.parametrize(
    ("removed_sources", "only_taxonomy", "expected_sources"),
    [
        (frozenset(), False, ("A", "C")),
        (frozenset({"C"}), False, ("A",)),
        (frozenset({"A", "C"}), False, ()),
        (frozenset(), True, ("A", "C")),
    ],
    ids=["base-all", "base-exclude", "base-exclude-all", "only-taxonomy"],
)
def test_hidden_iterator_compiles_one_silent_local_negative_data_property_assertion(
    provider_backend: pyowl_core.BackendPreference,
    local_literal: str,
    removed_sources: frozenset[str],
    only_taxonomy: bool,
    expected_sources: tuple[str, ...],
) -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "SubClassOf(:A :Top) SubClassOf(:C :Top)",
            backend=provider_backend,
        ),
    )
    removed = {
        axiom
        for axiom in base.iter_axioms()
        if cast(Any, axiom).sub_class.iri.value.rsplit("#", 1)[-1] in removed_sources
    }
    assert len(removed) == len(removed_sources)
    addition_source = cast(
        pyowl_core.OntologyView,
        _snapshot(
            f"NegativeDataPropertyAssertion(:dp :i {local_literal})",
            backend=provider_backend,
        ),
    )
    overlay = pyowl_core.apply_delta(
        base,
        pyowl_core.OntologyDelta(
            add_axioms=cast(Any, set(addition_source.iter_axioms())),
            remove_axioms=cast(Any, removed),
        ),
    )
    top_encoded = overlay.view(
        pyowl_core.EncodedStructuralView,
        schema_version=1,
        scope=pyowl_core.AxiomScope.CLOSURE,
    )
    assert tuple(segment.role for segment in top_encoded.segments) == (2, 3)
    base_segment = cast(Any, top_encoded.segments[0])
    delta_segment = cast(Any, top_encoded.segments[1])
    assert base_segment.posting_mode == (2 if removed else 0)
    assert base_segment.root_ids.nbytes == 4 * len(removed)
    assert delta_segment.posting_mode == 0
    assert delta_segment.root_ids.nbytes == 0
    source_encoded = base_segment.source
    assert source_encoded is not None
    expected_buffer_bytes = sum(
        value.nbytes for value in top_encoded.buffers.values()
    ) + sum(value.nbytes for value in source_encoded.buffers.values())

    python_options = ProjectionOptions(
        backend="python",
        order="encounter",
        only_taxonomy=only_taxonomy,
    )
    expected_projector = Projector()
    expected = expected_projector.project(overlay, options=python_options)
    expected_report = _completed_report(expected_projector)
    captured: list[NativeEncodedDirectCompilation] = []
    real_prepare = native_module.prepare_native_encoded_compilation

    def capture_compilation(
        *args: Any,
        **kwargs: Any,
    ) -> tuple[NativeEncodedDirectCompilation | None, str | None]:
        result = real_prepare(*args, **kwargs)
        if result[0] is not None:
            captured.append(result[0])
        return result

    with (
        patch.object(
            api_module,
            "prepare_native_encoded_compilation",
            side_effect=capture_compilation,
        ),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError(
                "silent local NegativeDataPropertyAssertion reached scalar traversal"
            ),
        ),
    ):
        projector = Projector()
        actual = list(
            projector._iter_native_encoded_edges(
                overlay,
                options=replace(python_options, backend="native"),
                buffer_edges=1,
            )
        )
    report = _completed_report(projector)

    expected_edges = [
        Edge(
            f"urn:native-integration#{source}",
            "http://subclassof",
            "urn:native-integration#Top",
        )
        for source in expected_sources
    ]
    assert actual == expected == expected_edges
    _assert_semantic_report_parity(expected_report, report)
    assert len(captured) == 1
    compilation = captured[0]
    assert compilation.view is overlay
    assert compilation.lease.owner is base
    assert compilation.local_delta_lease is compilation.container_leases[0]
    assert compilation.local_delta_lease is not None
    assert compilation.local_delta_lease.owner is overlay
    assert compilation.excluded_root_ids is (
        base_segment.root_ids if removed else None
    )
    assert compilation.native_statistics.roots == len(expected_sources) + 1
    assert compilation.native_statistics.subclasses == len(expected_sources)
    assert compilation.native_statistics.negative_data_property_assertions == 1
    assert compilation.native_statistics.skipped_axioms == 1
    assert compilation.native_statistics.edges == len(expected_edges)
    assert compilation.batches._compiler is None

    ingestion = report.provenance.ingestion
    assert ingestion.path == "encoded-native"
    assert ingestion.reason is None
    assert ingestion.counters["encoded_buffer_count"] == 22
    assert ingestion.counters["encoded_buffer_bytes"] == expected_buffer_bytes
    assert ingestion.counters["encoded_detached_buffer_count"] == 22 + int(bool(removed))
    assert ingestion.counters["encoded_zero_copy_buffers"] == 22
    assert ingestion.counters["encoded_referenced_view_count"] == 1
    assert ingestion.counters["encoded_segment_count"] == 3
    assert ingestion.counters["encoded_posting_bytes"] == 4 * len(removed)
    assert ingestion.counters["encoded_indexed_buffer_count"] == 0
    assert ingestion.counters["base_flattening_bytes"] == 0
    assert ingestion.counters["encoded_staging_copy_bytes"] == 0
    assert ingestion.counters["scalar_axiom_materializations"] == 0
    assert ingestion.counters["scalar_term_materializations"] == 0
    assert ingestion.counters["per_row_ffi_calls"] == 0
    _assert_bounded_native_output(
        ingestion.counters,
        compiled_edges=len(actual),
        batch_edges=1,
    )


def test_one_root_local_overlay_requires_exact_base_posting_identity() -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot("SubClassOf(:A :Top) SubClassOf(:C :Top)"),
    )
    removed = {
        next(
            axiom
            for axiom in base.iter_axioms()
            if cast(Any, axiom).sub_class.iri.value.endswith("#C")
        )
    }
    addition_source = cast(pyowl_core.OntologyView, _snapshot("SubClassOf(:D :Top)"))
    overlay = pyowl_core.apply_delta(
        base,
        pyowl_core.OntologyDelta(
            add_axioms=cast(Any, set(addition_source.iter_axioms())),
            remove_axioms=cast(Any, removed),
        ),
    )
    negotiation = select_private_direct_ingestion(
        overlay,
        selected_backend="native",
    )
    top_lease = negotiation.lease
    assert top_lease is not None
    resolved = _resolve_private_single_overlay_delta(top_lease)
    assert resolved is not None
    base_lease, excluded_root_ids, max_work, max_workspace = resolved
    assert excluded_root_ids is cast(Any, top_lease.segments[0]).root_ids
    compiler = prepare_native_encoded_direct(
        base_lease,
        local_delta_lease=top_lease,
        excluded_root_ids=excluded_root_ids,
        canonical_work_limit=max_work,
        canonical_workspace_limit=max_workspace,
    )
    edges, statistics = compiler.compile_batch(
        bidirectional=False,
        max_edges=2,
        max_iri_bytes=1024,
    )
    assert [edge.as_tuple() for edge in edges] == [
        (
            "urn:native-integration#A",
            "http://subclassof",
            "urn:native-integration#Top",
        ),
        (
            "urn:native-integration#D",
            "http://subclassof",
            "urn:native-integration#Top",
        ),
    ]
    assert statistics.roots == statistics.subclasses == statistics.edges == 2
    assert compiler.retained_buffer_count == 23
    assert compiler.state == "finished"

    forged_posting = memoryview(bytes(cast(memoryview, excluded_root_ids)))

    with pytest.raises(
        SnapshotCompatibilityError,
        match="does not retain the exact EXCLUDE table",
    ):
        prepare_native_encoded_direct(
            base_lease,
            local_delta_lease=top_lease,
            excluded_root_ids=forged_posting,
            canonical_work_limit=max_work,
            canonical_workspace_limit=max_workspace,
        )


def test_hidden_iterator_keeps_multi_root_mixed_overlay_on_whole_call_fallback() -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot("SubClassOf(:A :Top) SubClassOf(:C :Top)"),
    )
    removed = {
        next(
            axiom
            for axiom in base.iter_axioms()
            if cast(Any, axiom).sub_class.iri.value.endswith("#A")
        )
    }
    addition_source = cast(
        pyowl_core.OntologyView,
        _snapshot("SubClassOf(:D :Top) SubClassOf(:E :Top)"),
    )
    overlay = pyowl_core.apply_delta(
        base,
        pyowl_core.OntologyDelta(
            add_axioms=cast(Any, set(addition_source.iter_axioms())),
            remove_axioms=cast(Any, removed),
        ),
    )
    python_options = ProjectionOptions(backend="python", order="encounter")
    expected = Projector().project(overlay, options=python_options)

    projector = Projector()
    actual = list(
        projector._iter_native_encoded_edges(
            overlay,
            options=replace(python_options, backend="native"),
            buffer_edges=1,
        )
    )
    report = _completed_report(projector)

    assert actual == expected
    assert report.provenance.ingestion.path == "scalar-native"
    assert report.provenance.ingestion.reason is not None
    assert report.provenance.ingestion.counters.get("native_compiled_edges", 0) == 0
    assert report.provenance.ingestion.counters["encoded_buffer_count"] == 0


@pytest.mark.parametrize(
    ("local_body", "reason"),
    [
        (
            "SubClassOf(:B :C) SubClassOf(:C :D)",
            "direct native slice does not support segmented encoded views",
        ),
        (
            'SubClassOf(Annotation(:label "x") :B ObjectSomeValuesFrom(:p :C))',
            "bounded local-overlay SubClassOf root must be unannotated",
        ),
        (
            "SubClassOf(:B ObjectSomeValuesFrom("
            ":p ObjectIntersectionOf(:C :D)))",
            "bounded local-overlay root must be one unannotated Declaration",
        ),
        (
            'ClassAssertion(Annotation(:label "x") :B :i)',
            "bounded local-overlay ClassAssertion root must be unannotated",
        ),
        (
            "ClassAssertion(:B _:anonymous)",
            "bounded local-overlay root must be one unannotated Declaration",
        ),
        (
            "ClassAssertion(ObjectSomeValuesFrom(:p :B) :i)",
            "bounded local-overlay root must be one unannotated Declaration",
        ),
        (
            'ObjectPropertyAssertion(Annotation(:label "x") :p :i :j)',
            "bounded local-overlay ObjectPropertyAssertion root must be unannotated",
        ),
        (
            "ObjectPropertyAssertion(:p _:anonymous :j)",
            "bounded local-overlay ObjectPropertyAssertion root requires a named "
            "property and named individuals",
        ),
        (
            'NegativeObjectPropertyAssertion(Annotation(:label "x") :p :i :j)',
            "bounded local-overlay NegativeObjectPropertyAssertion root must be "
            "unannotated",
        ),
        (
            "NegativeObjectPropertyAssertion(:p _:anonymous :j)",
            "bounded local-overlay NegativeObjectPropertyAssertion root requires "
            "named individuals",
        ),
        (
            'DataPropertyAssertion(Annotation(:label "x") :dp :i "value")',
            "bounded local-overlay DataPropertyAssertion root must be unannotated",
        ),
        (
            'DataPropertyAssertion(:dp _:anonymous "value")',
            "bounded local-overlay DataPropertyAssertion root requires a named "
            "individual",
        ),
        (
            'NegativeDataPropertyAssertion(Annotation(:label "x") :dp :i "blocked")',
            "bounded local-overlay NegativeDataPropertyAssertion root must be "
            "unannotated",
        ),
        (
            'NegativeDataPropertyAssertion(:dp _:anonymous "blocked")',
            "bounded local-overlay NegativeDataPropertyAssertion root requires a named "
            "individual",
        ),
        (
            'Declaration(Annotation(:label "x") Class(:D))',
            "bounded local-overlay Declaration root must be unannotated",
        ),
    ],
    ids=[
        "multiple-local-roots",
        "annotated-local-restriction",
        "complex-local-restriction-filler",
        "annotated-local-class-assertion",
        "anonymous-local-class-assertion",
        "complex-local-class-assertion",
        "annotated-local-object-property-assertion",
        "anonymous-local-object-property-assertion",
        "annotated-local-negative-object-property-assertion",
        "anonymous-local-negative-object-property-assertion",
        "annotated-local-data-property-assertion",
        "anonymous-local-data-property-assertion",
        "annotated-local-negative-data-property-assertion",
        "anonymous-local-negative-data-property-assertion",
        "annotated-local-declaration",
    ],
)
def test_hidden_iterator_keeps_adjacent_local_overlay_shapes_on_whole_call_fallback(
    local_body: str,
    reason: str,
) -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "Declaration(Class(:A)) Declaration(Class(:B)) Declaration(Class(:C)) "
            "Declaration(ObjectProperty(:p)) SubClassOf(:A :B)"
        ),
    )
    addition_source = cast(pyowl_core.OntologyView, _snapshot(local_body))
    added = set(addition_source.iter_axioms())
    overlay = pyowl_core.apply_delta(
        base,
        pyowl_core.OntologyDelta(add_axioms=cast(Any, added)),
    )
    python_options = ProjectionOptions(backend="python", order="encounter")
    expected = Projector().project(overlay, options=python_options)

    projector = Projector()
    actual = list(
        projector._iter_native_encoded_edges(
            overlay,
            options=replace(python_options, backend="native"),
            buffer_edges=1,
        )
    )
    report = _completed_report(projector)

    assert actual == expected
    assert report.provenance.ingestion.path == "scalar-native"
    assert report.provenance.ingestion.reason is not None
    assert reason in report.provenance.ingestion.reason
    assert report.provenance.ingestion.counters.get("native_compiled_edges", 0) == 0
    assert report.provenance.ingestion.counters["encoded_buffer_count"] == 0


def test_hidden_iterator_keeps_literal_sensitive_local_overlay_on_whole_call_fallback() -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot("Declaration(Class(:A)) Declaration(Class(:B)) SubClassOf(:A :B)"),
    )
    addition_source = cast(pyowl_core.OntologyView, _snapshot("SubClassOf(:B :C)"))
    overlay = pyowl_core.apply_delta(
        base,
        pyowl_core.OntologyDelta(
            add_axioms=cast(Any, set(addition_source.iter_axioms())),
        ),
    )
    python_options = ProjectionOptions(
        backend="python",
        order="encounter",
        include_literals=True,
    )
    expected = Projector().project(overlay, options=python_options)

    projector = Projector()
    actual = list(
        projector._iter_native_encoded_edges(
            overlay,
            options=replace(python_options, backend="native"),
            buffer_edges=1,
        )
    )
    report = _completed_report(projector)

    assert actual == expected
    assert report.provenance.ingestion.path == "scalar-native"
    assert report.provenance.ingestion.reason is not None
    assert "local-overlay slice does not support literal projection" in (
        report.provenance.ingestion.reason
    )
    assert report.provenance.ingestion.counters.get("native_compiled_edges", 0) == 0


@pytest.mark.parametrize(
    ("max_work", "max_workspace", "message"),
    [
        (1, 1024 * 1024, "work units"),
        (1024 * 1024, 1, "workspace bytes"),
    ],
    ids=["canonical-work", "canonical-workspace"],
)
def test_one_root_mixed_overlay_fails_before_output_on_canonical_resource_bounds(
    max_work: int,
    max_workspace: int,
    message: str,
) -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot("SubClassOf(:A :B) SubClassOf(:B :C)"),
    )
    removed = {
        next(
            axiom
            for axiom in base.iter_axioms()
            if cast(Any, axiom).sub_class.iri.value.endswith("#A")
        )
    }
    addition_source = cast(pyowl_core.OntologyView, _snapshot("SubClassOf(:C :D)"))
    overlay = pyowl_core.apply_delta(
        base,
        pyowl_core.OntologyDelta(
            add_axioms=cast(Any, set(addition_source.iter_axioms())),
            remove_axioms=cast(Any, removed),
        ),
    )
    negotiation = select_private_direct_ingestion(
        overlay,
        selected_backend="native",
    )
    top_lease = negotiation.lease
    assert top_lease is not None
    resolved = _resolve_private_single_overlay_delta(top_lease)
    assert resolved is not None
    base_lease, excluded_root_ids, _public_work, _public_workspace = resolved
    assert excluded_root_ids is cast(Any, top_lease.segments[0]).root_ids
    compiler = prepare_native_encoded_direct(
        base_lease,
        local_delta_lease=top_lease,
        excluded_root_ids=excluded_root_ids,
        canonical_work_limit=max_work,
        canonical_workspace_limit=max_workspace,
    )

    with pytest.raises(ProjectionResourceError) as captured:
        compiler.iter_batches(
            bidirectional=False,
            max_edges=2,
            max_iri_bytes=1024,
            batch_edges=1,
        )

    assert captured.value.__cause__ is not None
    assert message in str(captured.value.__cause__)
    assert compiler.state == "failed"
    assert compiler.retained_buffer_count == 23
    assert compiler.cancel() is False


def _recursive_empty_overlay_lease(
    base: pyowl_core.OntologyView,
    *,
    depth: int,
) -> tuple[
    pyowl_core.OntologyView,
    EncodedStructuralLease,
    tuple[EncodedStructuralLease, ...],
    object,
]:
    assert depth >= 1
    source_encoded = base.view(
        pyowl_core.EncodedStructuralView,
        schema_version=1,
        scope=pyowl_core.AxiomScope.CLOSURE,
    )
    direct_encoded = source_encoded
    source_owner: object = base
    chain: list[EncodedStructuralLease] = []
    top_view: pyowl_core.OntologyView = base
    top_lease: EncodedStructuralLease | None = None
    for _index in range(depth):
        top_view = pyowl_core.apply_delta(base, pyowl_core.OntologyDelta())
        selection = select_private_direct_ingestion(
            top_view,
            selected_backend="native",
        )
        assert selection.lease is not None
        segment = replace(
            cast(Any, selection.lease.segments[0]),
            owner=source_owner,
            source=source_encoded,
        )
        encoded = replace(
            cast(Any, selection.lease.encoded_view),
            segments=(segment,),
        )
        top_lease = replace(
            selection.lease,
            encoded_view=encoded,
            segments=(segment,),
        )
        chain.append(top_lease)
        source_encoded = encoded
        source_owner = top_view
    assert top_lease is not None
    return top_view, top_lease, tuple(chain), direct_encoded


def _recursive_overlay_lease_with_exclusions(
    base: pyowl_core.OntologyView,
    *,
    depth: int,
    exclusion_levels: frozenset[int],
    removed: set[object],
) -> tuple[
    EncodedStructuralLease,
    tuple[EncodedStructuralLease, ...],
    object,
]:
    assert depth >= 1
    assert exclusion_levels
    assert all(0 <= level < depth for level in exclusion_levels)
    source_encoded = base.view(
        pyowl_core.EncodedStructuralView,
        schema_version=1,
        scope=pyowl_core.AxiomScope.CLOSURE,
    )
    direct_encoded = source_encoded
    source_owner: object = base
    chain: list[EncodedStructuralLease] = []
    top_lease: EncodedStructuralLease | None = None
    for level in range(depth):
        delta = (
            pyowl_core.OntologyDelta(remove_axioms=cast(Any, removed))
            if level in exclusion_levels
            else pyowl_core.OntologyDelta()
        )
        template_view = pyowl_core.apply_delta(base, delta)
        selection = select_private_direct_ingestion(
            template_view,
            selected_backend="native",
        )
        assert selection.lease is not None
        segment = replace(
            cast(Any, selection.lease.segments[0]),
            owner=source_owner,
            source=source_encoded,
        )
        encoded = replace(
            cast(Any, selection.lease.encoded_view),
            segments=(segment,),
        )
        top_lease = replace(
            selection.lease,
            encoded_view=encoded,
            segments=(segment,),
        )
        chain.append(top_lease)
        source_encoded = encoded
        source_owner = template_view
    assert top_lease is not None
    return top_lease, tuple(chain), direct_encoded


@pytest.mark.parametrize(
    "provider_backend",
    [
        pyowl_core.BackendPreference.PYTHON,
        pyowl_core.BackendPreference.NATIVE,
    ],
    ids=["independent-bytes", "packed-bytes"],
)
def test_hidden_iterator_compiles_recursive_empty_overlay_aliases(
    provider_backend: pyowl_core.BackendPreference,
) -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "Declaration(Class(:A)) Declaration(Class(:B)) SubClassOf(:A :B)",
            backend=provider_backend,
        ),
    )
    depth = 3
    top_view, top_lease, chain, direct_encoded = _recursive_empty_overlay_lease(
        base,
        depth=depth,
    )
    expected_buffer_bytes = sum(
        value.nbytes for value in cast(Any, direct_encoded).buffers.values()
    ) + sum(sum(value.nbytes for value in lease.buffers.values()) for lease in chain)
    python_options = ProjectionOptions(backend="python", order="encounter")
    expected_projector = Projector()
    expected = expected_projector.project(top_view, options=python_options)
    expected_report = _completed_report(expected_projector)
    captured: list[NativeEncodedDirectCompilation] = []
    real_prepare = native_module.prepare_native_encoded_compilation

    def capture_compilation(
        *args: Any,
        **kwargs: Any,
    ) -> tuple[NativeEncodedDirectCompilation | None, str | None]:
        result = real_prepare(*args, **kwargs)
        if result[0] is not None:
            captured.append(result[0])
        return result

    with (
        patch.object(
            api_module,
            "select_private_direct_ingestion",
            return_value=EncodedNegotiation("encoded-native", lease=top_lease),
        ),
        patch.object(
            api_module,
            "prepare_native_encoded_compilation",
            side_effect=capture_compilation,
        ),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("recursive empty aliases reached scalar traversal"),
        ),
    ):
        projector = Projector()
        actual = list(
            projector._iter_native_encoded_edges(
                top_view,
                options=replace(python_options, backend="native"),
                buffer_edges=1,
            )
        )
    report = _completed_report(projector)

    assert actual == expected
    _assert_semantic_report_parity(expected_report, report)
    assert len(captured) == 1
    compilation = captured[0]
    assert compilation.lease.encoded_view is direct_encoded
    assert tuple(item.encoded_view for item in compilation.container_leases) == tuple(
        item.encoded_view for item in reversed(chain)
    )
    assert tuple(item.owner for item in compilation.container_leases) == tuple(
        item.owner for item in reversed(chain)
    )
    ingestion = report.provenance.ingestion
    assert ingestion.path == "encoded-native"
    assert ingestion.reason is None
    assert ingestion.counters["encoded_buffer_count"] == 11 * (depth + 1)
    assert ingestion.counters["encoded_buffer_bytes"] == expected_buffer_bytes
    assert ingestion.counters["encoded_detached_buffer_count"] == 11
    assert ingestion.counters["encoded_zero_copy_buffers"] == 11 * (depth + 1)
    assert ingestion.counters["encoded_referenced_view_count"] == depth
    assert ingestion.counters["encoded_segment_count"] == depth + 1
    assert ingestion.counters["encoded_posting_bytes"] == 0
    assert ingestion.counters["base_flattening_bytes"] == 0
    assert ingestion.counters["encoded_staging_copy_bytes"] == 0
    assert ingestion.counters["scalar_axiom_materializations"] == 0
    assert ingestion.counters["per_row_ffi_calls"] == 0
    _assert_bounded_native_output(
        ingestion.counters,
        compiled_edges=len(actual),
        batch_edges=1,
    )


def test_recursive_empty_overlay_owners_live_until_cursor_close() -> None:
    class Owner:
        def __init__(self, template: pyowl_core.OntologyView) -> None:
            self.capabilities = template.capabilities
            self.load_options = getattr(template, "load_options", None)
            self.structural_fingerprint = template.structural_fingerprint

    def create() -> tuple[
        pyowl_core.OntologyView,
        EncodedNegotiation,
        tuple[weakref.ReferenceType[Owner], ...],
    ]:
        base = cast(
            pyowl_core.OntologyView,
            _snapshot(
                "SubClassOf(:A :Top) SubClassOf(:B :Top) SubClassOf(:C :Top)"
            ),
        )
        direct_selection = select_private_direct_ingestion(
            base,
            selected_backend="native",
        )
        assert direct_selection.lease is not None
        source_owner = Owner(base)
        direct_segment = replace(
            cast(Any, direct_selection.lease.segments[0]),
            owner=source_owner,
        )
        source_encoded = replace(
            cast(Any, direct_selection.lease.encoded_view),
            owner=source_owner,
            segments=(direct_segment,),
        )
        owners = [source_owner]
        source_owner_object: object = source_owner
        top_view = base
        top_lease: EncodedStructuralLease | None = None
        for _index in range(2):
            top_view = pyowl_core.apply_delta(base, pyowl_core.OntologyDelta())
            template = select_private_direct_ingestion(
                top_view,
                selected_backend="native",
            )
            assert template.lease is not None
            owner = Owner(top_view)
            segment = replace(
                cast(Any, template.lease.segments[0]),
                owner=source_owner_object,
                source=source_encoded,
            )
            encoded = replace(
                cast(Any, template.lease.encoded_view),
                owner=owner,
                segments=(segment,),
            )
            top_lease = replace(
                template.lease,
                encoded_view=encoded,
                owner=owner,
                segments=(segment,),
            )
            owners.append(owner)
            source_owner_object = owner
            source_encoded = encoded
        assert top_lease is not None
        return (
            top_view,
            EncodedNegotiation("encoded-native", lease=top_lease),
            tuple(weakref.ref(owner) for owner in owners),
        )

    top_view, negotiation, owner_refs = create()
    expected = Projector().project(
        top_view,
        options=ProjectionOptions(backend="python", order="encounter"),
    )
    projector = Projector()
    with (
        patch.object(
            api_module,
            "select_private_direct_ingestion",
            return_value=negotiation,
        ),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("retained aliases reached scalar traversal"),
        ),
    ):
        iterator = projector._iter_native_encoded_edges(
            top_view,
            options=ProjectionOptions(backend="native", order="encounter"),
            buffer_edges=1,
        )
        assert next(iterator) == expected[0]

    del negotiation
    gc.collect()
    assert all(reference() is not None for reference in owner_refs)
    cast(Any, iterator).close()
    del iterator
    gc.collect()
    assert all(reference() is None for reference in owner_refs)
    assert projector.last_report is None


@pytest.mark.parametrize(
    ("limit_name", "maximum", "depth"),
    [
        ("max_overlay_depth", 2, 3),
        ("max_canonical_work", 700, 2),
    ],
)
def test_hidden_iterator_bounds_recursive_empty_overlay_alias_work(
    limit_name: str,
    maximum: int,
    depth: int,
) -> None:
    limits = replace(pyowl_core.ParseLimits(), **{limit_name: maximum})
    source = (
        b"Prefix(:=<urn:native-integration#>) Ontology(<urn:native-integration> "
        b"Declaration(Class(:A)) Declaration(Class(:B)) SubClassOf(:A :B))"
    )
    base = cast(
        pyowl_core.OntologyView,
        pyowl_core.load_snapshot(
            source,
            options=pyowl_core.LoadOptions(
                imports=pyowl_core.ImportPolicy.IGNORE,
                backend=pyowl_core.BackendPreference.PYTHON,
                limits=limits,
            ),
        ),
    )
    top_view, top_lease, _chain, _direct = _recursive_empty_overlay_lease(
        base,
        depth=depth,
    )
    projector = Projector()
    with (
        patch.object(
            api_module,
            "select_private_direct_ingestion",
            return_value=EncodedNegotiation("encoded-native", lease=top_lease),
        ),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("over-budget aliases reached scalar traversal"),
        ),
        pytest.raises(
            SnapshotCompatibilityError,
            match=f"public {limit_name}",
        ),
    ):
        list(
            projector._iter_native_encoded_edges(
                top_view,
                options=ProjectionOptions(backend="native", order="encounter"),
                buffer_edges=1,
            )
        )
    assert projector.last_report is None


def test_hidden_iterator_rejects_recursive_empty_overlay_cycle_before_output() -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot("Declaration(Class(:A)) SubClassOf(:A :Top)"),
    )
    top_view, top_lease, chain, _direct = _recursive_empty_overlay_lease(
        base,
        depth=2,
    )
    inner_encoded = cast(Any, chain[0].encoded_view)
    inner_segment = replace(
        cast(Any, inner_encoded.segments[0]),
        owner=top_lease.owner,
        source=top_lease.encoded_view,
    )
    object.__setattr__(inner_encoded, "segments", (inner_segment,))

    projector = Projector()
    with (
        patch.object(
            api_module,
            "select_private_direct_ingestion",
            return_value=EncodedNegotiation("encoded-native", lease=top_lease),
        ),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("cyclic aliases reached scalar traversal"),
        ),
        pytest.raises(
            SnapshotCompatibilityError,
            match="empty-overlay alias graph is cyclic",
        ),
    ):
        list(
            projector._iter_native_encoded_edges(
                top_view,
                options=ProjectionOptions(backend="native", order="encounter"),
                buffer_edges=1,
            )
        )
    assert projector.last_report is None


@pytest.mark.parametrize(
    "provider_backend",
    [
        pyowl_core.BackendPreference.PYTHON,
        pyowl_core.BackendPreference.NATIVE,
    ],
    ids=["independent-bytes", "packed-bytes"],
)
@pytest.mark.parametrize(
    "removed_constructors",
    [
        frozenset({"SubClassOf"}),
        frozenset({"SubObjectPropertyOf"}),
        frozenset({"InverseObjectProperties"}),
        frozenset({"ObjectPropertyDomain"}),
        frozenset({"ObjectPropertyRange"}),
        frozenset({"AnnotationAssertion"}),
        frozenset(
            {
                "EquivalentClasses",
                "ClassAssertion",
                "ObjectPropertyAssertion",
            }
        ),
    ],
    ids=[
        "taxonomy-and-restriction",
        "subrole-state",
        "inverse-state",
        "domain-product",
        "range-product",
        "silent-annotation",
        "nonadjacent-projecting-roots",
    ],
)
def test_hidden_iterator_compiles_one_excluding_overlay_without_flattening(
    provider_backend: pyowl_core.BackendPreference,
    removed_constructors: frozenset[str],
) -> None:
    padding = " ".join(f"Declaration(Class(:Padding{index}))" for index in range(24))
    base = cast(
        pyowl_core.OntologyView,
        _snapshot(
            f"{padding} "
            "Declaration(Class(:A)) Declaration(Class(:B)) Declaration(Class(:C)) "
            "Declaration(ObjectProperty(:p)) Declaration(ObjectProperty(:q)) "
            "Declaration(ObjectProperty(:r)) Declaration(NamedIndividual(:i)) "
            "Declaration(NamedIndividual(:j)) SubClassOf(:A :B) "
            "SubClassOf(:B ObjectSomeValuesFrom(:p :C)) EquivalentClasses(:B :C) "
            "ClassAssertion(:C :i) ObjectPropertyAssertion(:p :i :j) "
            "SubObjectPropertyOf(:p :q) InverseObjectProperties(:q :r) "
            "ObjectPropertyDomain(:p :A) ObjectPropertyRange(:p :B) "
            'AnnotationAssertion(<http://www.w3.org/2000/01/rdf-schema#label> :A "label")',
            backend=provider_backend,
        ),
    )
    removed = {
        axiom
        for axiom in base.iter_axioms()
        if type(axiom).__name__ in removed_constructors
    }
    assert {type(axiom).__name__ for axiom in removed} == removed_constructors
    overlay = pyowl_core.apply_delta(
        base,
        pyowl_core.OntologyDelta(remove_axioms=cast(Any, removed)),
    )
    encoded = overlay.view(
        pyowl_core.EncodedStructuralView,
        schema_version=1,
        scope=pyowl_core.AxiomScope.CLOSURE,
    )
    assert len(encoded.segments) == 1
    segment = cast(Any, encoded.segments[0])
    assert segment.posting_mode == 2
    assert segment.root_ids.nbytes == 4 * len(removed)
    source_encoded = segment.source
    assert source_encoded is not None
    expected_buffer_bytes = sum(value.nbytes for value in encoded.buffers.values()) + sum(
        value.nbytes for value in source_encoded.buffers.values()
    )

    python_options = ProjectionOptions(backend="python", order="encounter")
    expected_projector = Projector()
    expected = expected_projector.project(overlay, options=python_options)
    expected_report = _completed_report(expected_projector)
    captured: list[NativeEncodedDirectCompilation] = []
    real_prepare = native_module.prepare_native_encoded_compilation

    def capture_compilation(
        *args: Any,
        **kwargs: Any,
    ) -> tuple[NativeEncodedDirectCompilation | None, str | None]:
        result = real_prepare(*args, **kwargs)
        if result[0] is not None:
            captured.append(result[0])
        return result

    with (
        patch.object(
            api_module,
            "prepare_native_encoded_compilation",
            side_effect=capture_compilation,
        ),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("excluding overlay reached scalar traversal"),
        ),
    ):
        projector = Projector()
        actual = list(
            projector._iter_native_encoded_edges(
                overlay,
                options=replace(python_options, backend="native"),
                buffer_edges=2,
            )
        )
    report = _completed_report(projector)

    assert actual == expected
    _assert_semantic_report_parity(expected_report, report)
    assert len(captured) == 1
    compilation = captured[0]
    assert compilation.lease.encoded_view is source_encoded
    assert len(compilation.container_leases) == 1
    assert compilation.container_leases[0].owner is overlay
    assert compilation.excluded_root_ids is not None
    assert compilation.excluded_root_ids == segment.root_ids
    ingestion = report.provenance.ingestion
    assert ingestion.path == "encoded-native"
    assert ingestion.reason is None
    assert ingestion.counters["encoded_buffer_count"] == 22
    assert ingestion.counters["encoded_buffer_bytes"] == expected_buffer_bytes
    assert ingestion.counters["encoded_detached_buffer_count"] == 12
    assert ingestion.counters["encoded_zero_copy_buffers"] == 22
    assert ingestion.counters["encoded_referenced_view_count"] == 1
    assert ingestion.counters["encoded_segment_count"] == 2
    assert ingestion.counters["encoded_posting_bytes"] == segment.root_ids.nbytes
    assert ingestion.counters["encoded_indexed_buffer_count"] == 0
    assert ingestion.counters["base_flattening_bytes"] == 0
    assert ingestion.counters["encoded_staging_copy_bytes"] == 0
    assert ingestion.counters["scalar_axiom_materializations"] == 0
    assert ingestion.counters["scalar_term_materializations"] == 0
    assert ingestion.counters["per_row_ffi_calls"] == 0
    _assert_bounded_native_output(
        ingestion.counters,
        compiled_edges=len(actual),
        batch_edges=2,
    )


@pytest.mark.parametrize(
    "provider_backend",
    [
        pyowl_core.BackendPreference.PYTHON,
        pyowl_core.BackendPreference.NATIVE,
    ],
    ids=["independent-bytes", "packed-bytes"],
)
def test_excluding_overlay_recomputes_anonymous_ids_from_retained_roots(
    provider_backend: pyowl_core.BackendPreference,
) -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "ObjectPropertyAssertion(:p _:removed :x) "
            "ObjectPropertyAssertion(:p _:retained :y)",
            backend=provider_backend,
        ),
    )
    removed = next(iter(base.iter_axioms()))
    overlay = pyowl_core.apply_delta(
        base,
        pyowl_core.OntologyDelta(remove_axioms=cast(Any, {removed})),
    )
    python_options = ProjectionOptions(backend="python", order="encounter")
    expected_projector = Projector()
    expected = expected_projector.project(overlay, options=python_options)
    expected_report = _completed_report(expected_projector)

    with patch.object(
        api_module,
        "prepare_streaming_compilation",
        side_effect=AssertionError("anonymous excluding overlay reached scalar traversal"),
    ):
        projector = Projector()
        actual = list(
            projector._iter_native_encoded_edges(
                overlay,
                options=replace(python_options, backend="native"),
                buffer_edges=1,
            )
        )
    report = _completed_report(projector)

    assert actual == expected
    assert len(actual) == 1
    assert actual[0].source == "_:genid2147483648"
    _assert_semantic_report_parity(expected_report, report)
    ingestion = report.provenance.ingestion
    assert ingestion.path == "encoded-native"
    assert ingestion.counters["encoded_posting_bytes"] == 4
    assert ingestion.counters["encoded_detached_buffer_count"] == 12
    assert ingestion.counters["base_flattening_bytes"] == 0
    assert ingestion.counters["encoded_staging_copy_bytes"] == 0


def test_hidden_iterator_compiles_terminal_adjacent_exclusion_through_recursive_aliases(
) -> None:
    exclusion_level = 0
    base = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "Declaration(Class(:A)) Declaration(Class(:B)) Declaration(Class(:C)) "
            "SubClassOf(:A :B) SubClassOf(:B :C)"
        ),
    )
    removed = {
        next(axiom for axiom in base.iter_axioms() if type(axiom).__name__ == "SubClassOf")
    }
    semantic_view = pyowl_core.apply_delta(
        base,
        pyowl_core.OntologyDelta(remove_axioms=cast(Any, removed)),
    )
    top_lease, chain, direct_encoded = _recursive_overlay_lease_with_exclusions(
        base,
        depth=3,
        exclusion_levels=frozenset({exclusion_level}),
        removed=cast(set[object], removed),
    )
    exclusion_segment = cast(Any, chain[exclusion_level].segments[0])
    assert exclusion_segment.posting_mode == 2

    python_options = ProjectionOptions(backend="python", order="encounter")
    expected_projector = Projector()
    expected = expected_projector.project(semantic_view, options=python_options)
    expected_report = _completed_report(expected_projector)
    captured: list[NativeEncodedDirectCompilation] = []
    real_prepare = native_module.prepare_native_encoded_compilation

    def capture_compilation(
        *args: Any,
        **kwargs: Any,
    ) -> tuple[NativeEncodedDirectCompilation | None, str | None]:
        result = real_prepare(*args, **kwargs)
        if result[0] is not None:
            captured.append(result[0])
        return result

    with (
        patch.object(
            api_module,
            "select_private_direct_ingestion",
            return_value=EncodedNegotiation("encoded-native", lease=top_lease),
        ),
        patch.object(
            api_module,
            "prepare_native_encoded_compilation",
            side_effect=capture_compilation,
        ),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("recursive excluding overlay reached scalar traversal"),
        ),
    ):
        projector = Projector()
        actual = list(
            projector._iter_native_encoded_edges(
                semantic_view,
                options=replace(python_options, backend="native"),
                buffer_edges=1,
            )
        )
    report = _completed_report(projector)

    assert actual == expected
    _assert_semantic_report_parity(expected_report, report)
    assert len(captured) == 1
    compilation = captured[0]
    assert compilation.lease.encoded_view is direct_encoded
    assert tuple(item.encoded_view for item in compilation.container_leases) == tuple(
        item.encoded_view for item in reversed(chain)
    )
    assert compilation.excluded_root_ids is exclusion_segment.root_ids
    ingestion = report.provenance.ingestion
    assert ingestion.path == "encoded-native"
    assert ingestion.counters["encoded_buffer_count"] == 44
    assert ingestion.counters["encoded_detached_buffer_count"] == 12
    assert ingestion.counters["encoded_zero_copy_buffers"] == 44
    assert ingestion.counters["encoded_referenced_view_count"] == 3
    assert ingestion.counters["encoded_segment_count"] == 4
    assert ingestion.counters["encoded_posting_bytes"] == 4
    assert ingestion.counters["encoded_indexed_buffer_count"] == 0
    assert ingestion.counters["base_flattening_bytes"] == 0
    assert ingestion.counters["encoded_staging_copy_bytes"] == 0
    assert ingestion.counters["scalar_axiom_materializations"] == 0
    assert ingestion.counters["per_row_ffi_calls"] == 0


def test_hidden_iterator_keeps_nonterminal_exclusion_on_whole_call_fallback() -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "Declaration(Class(:A)) Declaration(Class(:B)) Declaration(Class(:C)) "
            "SubClassOf(:A :B) SubClassOf(:B :C)"
        ),
    )
    removed = {
        next(axiom for axiom in base.iter_axioms() if type(axiom).__name__ == "SubClassOf")
    }
    semantic_view = pyowl_core.apply_delta(
        base,
        pyowl_core.OntologyDelta(remove_axioms=cast(Any, removed)),
    )
    top_lease, _chain, _direct = _recursive_overlay_lease_with_exclusions(
        base,
        depth=3,
        exclusion_levels=frozenset({2}),
        removed=cast(set[object], removed),
    )
    python_options = ProjectionOptions(backend="python", order="encounter")
    expected = Projector().project(semantic_view, options=python_options)

    with patch.object(
        api_module,
        "select_private_direct_ingestion",
        return_value=EncodedNegotiation("encoded-native", lease=top_lease),
    ):
        projector = Projector()
        actual = list(
            projector._iter_native_encoded_edges(
                semantic_view,
                options=replace(python_options, backend="native"),
                buffer_edges=1,
            )
        )
    report = _completed_report(projector)

    assert actual == expected
    ingestion = report.provenance.ingestion
    assert ingestion.path == "scalar-native"
    assert ingestion.reason is not None
    assert ingestion.reason.startswith(
        "private native direct compiler unavailable: "
        "direct native slice requires the canonical direct segment role"
    )
    assert ingestion.reason.endswith("selected whole-operation scalar compiler")
    assert all(
        value is False if name == "encoded_compiler_gil_released" else value == 0
        for name, value in ingestion.counters.items()
    )


def test_private_selection_rejects_nonterminal_exclusion_during_real_validation() -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "Declaration(Class(:A)) Declaration(Class(:B)) Declaration(Class(:C)) "
            "SubClassOf(:A :B) SubClassOf(:B :C)"
        ),
    )
    removed = {
        next(axiom for axiom in base.iter_axioms() if type(axiom).__name__ == "SubClassOf")
    }
    top_lease, _chain, _direct = _recursive_overlay_lease_with_exclusions(
        base,
        depth=3,
        exclusion_levels=frozenset({2}),
        removed=cast(set[object], removed),
    )

    class EncodedPublisher:
        def __init__(self, encoded: object, template: object) -> None:
            self._encoded = encoded
            self.capabilities = cast(Any, template).capabilities
            self.structural_fingerprint = cast(Any, template).structural_fingerprint

        def view(self, *_args: object, **_kwargs: object) -> object:
            return replace(cast(Any, self._encoded), owner=self)

    publisher = EncodedPublisher(top_lease.encoded_view, top_lease.owner)
    with pytest.raises(
        SnapshotCompatibilityError,
        match="segment postings are not sorted unique in-range references",
    ):
        select_private_direct_ingestion(
            publisher,
            selected_backend="native",
        )


def test_hidden_iterator_keeps_multiple_exclusion_layers_on_whole_call_fallback() -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "Declaration(Class(:A)) Declaration(Class(:B)) Declaration(Class(:C)) "
            "SubClassOf(:A :B) SubClassOf(:B :C)"
        ),
    )
    removed = {
        next(axiom for axiom in base.iter_axioms() if type(axiom).__name__ == "SubClassOf")
    }
    semantic_view = pyowl_core.apply_delta(
        base,
        pyowl_core.OntologyDelta(remove_axioms=cast(Any, removed)),
    )
    top_lease, _chain, _direct = _recursive_overlay_lease_with_exclusions(
        base,
        depth=2,
        exclusion_levels=frozenset({0, 1}),
        removed=cast(set[object], removed),
    )
    python_options = ProjectionOptions(backend="python", order="encounter")
    expected = Projector().project(semantic_view, options=python_options)

    with patch.object(
        api_module,
        "select_private_direct_ingestion",
        return_value=EncodedNegotiation("encoded-native", lease=top_lease),
    ):
        projector = Projector()
        actual = list(
            projector._iter_native_encoded_edges(
                semantic_view,
                options=replace(python_options, backend="native"),
                buffer_edges=1,
            )
        )
    report = _completed_report(projector)

    assert actual == expected
    ingestion = report.provenance.ingestion
    assert ingestion.path == "scalar-native"
    assert ingestion.reason is not None
    assert ingestion.reason.startswith(
        "private native direct compiler unavailable: "
        "direct native slice requires the canonical direct segment role"
    )
    assert ingestion.reason.endswith("selected whole-operation scalar compiler")
    assert all(
        value is False if name == "encoded_compiler_gil_released" else value == 0
        for name, value in ingestion.counters.items()
    )


def test_hidden_iterator_compiles_zero_output_excluding_overlay() -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot("Declaration(Class(:A)) Declaration(Class(:B)) SubClassOf(:A :B)"),
    )
    removed = next(axiom for axiom in base.iter_axioms() if type(axiom).__name__ == "SubClassOf")
    overlay = pyowl_core.apply_delta(
        base,
        pyowl_core.OntologyDelta(remove_axioms=cast(Any, {removed})),
    )
    python_options = ProjectionOptions(backend="python", order="encounter")
    expected = Projector().project(overlay, options=python_options)

    with patch.object(
        api_module,
        "prepare_streaming_compilation",
        side_effect=AssertionError("zero-output excluding overlay reached scalar traversal"),
    ):
        projector = Projector()
        actual = list(
            projector._iter_native_encoded_edges(
                overlay,
                options=replace(python_options, backend="native"),
                buffer_edges=1,
            )
        )
    report = _completed_report(projector)

    assert actual == expected == []
    ingestion = report.provenance.ingestion
    assert ingestion.path == "encoded-native"
    assert ingestion.reason is None
    assert ingestion.counters["encoded_posting_bytes"] == 4
    assert ingestion.counters["encoded_detached_buffer_count"] == 12
    assert ingestion.counters["base_flattening_bytes"] == 0
    assert ingestion.counters["encoded_staging_copy_bytes"] == 0
    assert ingestion.counters["scalar_axiom_materializations"] == 0
    assert ingestion.counters["per_row_ffi_calls"] == 0
    _assert_bounded_native_output(
        ingestion.counters,
        compiled_edges=0,
        batch_edges=1,
    )


def test_hidden_iterator_defers_empty_overlay_root_annotation_provenance() -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "Declaration(Class(:A)) "
            'AnnotationAssertion(<http://www.w3.org/2000/01/rdf-schema#label> :A "label")'
        ),
    )
    overlay = pyowl_core.apply_delta(base, pyowl_core.OntologyDelta())
    python_options = ProjectionOptions(
        backend="python",
        order="encounter",
        include_literals=True,
    )
    expected = Projector().project(overlay, options=python_options)

    projector = Projector()
    actual = list(
        projector._iter_native_encoded_edges(
            overlay,
            options=replace(python_options, backend="native"),
            buffer_edges=1,
        )
    )
    report = _completed_report(projector)

    assert actual == expected
    ingestion = report.provenance.ingestion
    assert ingestion.path == "scalar-native"
    assert ingestion.reason is not None
    assert ingestion.reason.startswith(
        "private native empty-overlay alias does not support root-scoped annotation provenance"
    )
    assert ingestion.reason.endswith("selected whole-operation scalar compiler")


def test_hidden_iterator_rejects_invalid_referenced_overlay_source_before_output() -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot("Declaration(Class(:A)) SubClassOf(:A :Top)"),
    )
    overlay = pyowl_core.apply_delta(base, pyowl_core.OntologyDelta())
    selection = select_private_direct_ingestion(
        overlay,
        selected_backend="native",
    )
    assert selection.lease is not None
    source_segment = cast(Any, selection.lease.segments[0])
    source_encoded = source_segment.source
    assert source_encoded is not None
    replacements = dict(source_encoded.buffers)
    replacements["node_tags"] = memoryview(bytes(replacements["node_tags"])[:-1])
    invalid_source = replace(
        source_encoded,
        buffers=MappingProxyType(replacements),
    )
    invalid_segment = replace(source_segment, source=invalid_source)
    invalid_encoded = replace(
        cast(Any, selection.lease.encoded_view),
        segments=(invalid_segment,),
    )
    invalid_lease = replace(
        selection.lease,
        encoded_view=invalid_encoded,
        segments=(invalid_segment,),
    )

    projector = Projector()
    with (
        patch.object(
            api_module,
            "select_private_direct_ingestion",
            return_value=EncodedNegotiation("encoded-native", lease=invalid_lease),
        ),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("invalid overlay source reached scalar traversal"),
        ),
        pytest.raises(
            SnapshotCompatibilityError,
            match="buffer length is not divisible",
        ),
    ):
        list(
            projector._iter_native_encoded_edges(
                overlay,
                options=ProjectionOptions(backend="native", order="encounter"),
                buffer_edges=1,
            )
        )
    assert projector.last_report is None


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


def test_hidden_iterator_falls_back_before_output_and_closes_declined_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _snapshot("SubClassOf(:A :B)")
    python_options = ProjectionOptions(backend="python", order="encounter")
    expected_projector = Projector()
    expected = expected_projector.project(view, options=python_options)
    expected_report = _completed_report(expected_projector)

    closed: list[tuple[str, int]] = []
    real_close = NativeEncodedDirectBatchIterator.close
    real_iter_batches = NativeEncodedDirectCompiler.iter_batches

    def inconsistent_iter_batches(
        self: NativeEncodedDirectCompiler,
        *args: Any,
        **kwargs: Any,
    ) -> NativeEncodedDirectBatchIterator:
        batches = real_iter_batches(self, *args, **kwargs)
        batches.statistics = replace(
            batches.statistics,
            roots=batches.statistics.roots + 1,
        )
        return batches

    def tracking_close(self: NativeEncodedDirectBatchIterator) -> bool:
        result = real_close(self)
        closed.append((self.state, self.remaining_edges))
        return result

    monkeypatch.setattr(NativeEncodedDirectBatchIterator, "close", tracking_close)
    monkeypatch.setattr(
        NativeEncodedDirectCompiler,
        "iter_batches",
        inconsistent_iter_batches,
    )
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
        "private native batch integration requires exact root partitions, base-edge totals, "
        "role expansion, diagnostics, and skipped or silent ledgers"
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
