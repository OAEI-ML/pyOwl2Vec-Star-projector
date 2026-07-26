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
    _resolve_private_three_member_composite,
    _resolve_private_two_member_composite,
    select_private_direct_ingestion,
)
from pyowl2vec_star_projector.native import (
    ENCODED_DIRECT_BUFFER_ORDER,
    NativeEncodedDirectBatchIterator,
    NativeEncodedDirectCancelled,
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
@pytest.mark.parametrize(
    ("removal", "only_taxonomy", "expected_relations"),
    [
        ("none", False, ("p", "child", "pinv")),
        ("sub-property", False, ("p", "pinv")),
        ("inverse", False, ("p", "child")),
        ("both", False, ("p",)),
        ("none", True, ("p", "child", "pinv")),
    ],
    ids=[
        "base-all",
        "exclude-sub-property",
        "exclude-inverse",
        "exclude-both",
        "only-taxonomy",
    ],
)
def test_hidden_iterator_projects_two_local_object_property_class_roots(
    provider_backend: pyowl_core.BackendPreference,
    removal: str,
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
    removed_constructors = {
        "none": frozenset(),
        "sub-property": frozenset({"SubObjectPropertyOf"}),
        "inverse": frozenset({"InverseObjectProperties"}),
        "both": frozenset({"SubObjectPropertyOf", "InverseObjectProperties"}),
    }[removal]
    removed = {
        axiom for axiom in base.iter_axioms() if type(axiom).__name__ in removed_constructors
    }
    assert len(removed) == len(removed_constructors)
    addition_source = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "ObjectPropertyDomain(:p :D) ObjectPropertyRange(:p :R)",
            backend=provider_backend,
        ),
    )
    added = set(addition_source.iter_axioms())
    assert len(added) == 2
    overlay = pyowl_core.apply_delta(
        base,
        pyowl_core.OntologyDelta(
            add_axioms=cast(Any, added),
            remove_axioms=cast(Any, removed),
        ),
    )
    top_encoded = overlay.view(
        pyowl_core.EncodedStructuralView,
        schema_version=1,
        scope=pyowl_core.AxiomScope.CLOSURE,
    )
    assert tuple(segment.role for segment in top_encoded.segments) == (2, 3)
    assert top_encoded.buffers["root_kinds"].nbytes == 2
    assert top_encoded.buffers["root_ids"].nbytes == 8
    base_segment = cast(Any, top_encoded.segments[0])
    delta_segment = cast(Any, top_encoded.segments[1])
    assert base_segment.posting_mode == (2 if removed else 0)
    assert base_segment.root_ids.nbytes == 4 * len(removed)
    assert delta_segment.posting_mode == 0
    assert delta_segment.root_ids.nbytes == 0
    assert delta_segment.anonymous_scope_map.nbytes == 0
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
                "two-root local object-property domain/range reached scalar traversal"
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
            ("urn:native-integration#R" if relation == "pinv" else "urn:native-integration#D"),
            f"urn:native-integration#{relation}",
            ("urn:native-integration#D" if relation == "pinv" else "urn:native-integration#R"),
        )
        for relation in expected_relations
    ]
    assert actual == expected == expected_edges
    _assert_semantic_report_parity(expected_report, report)
    assert report.provenance.counts.ignored_shapes == 0
    assert report.diagnostics == ()
    assert len(captured) == 1
    compilation = captured[0]
    assert compilation.view is overlay
    assert compilation.lease.owner is base
    assert compilation.local_delta_lease is compilation.container_leases[0]
    assert compilation.local_delta_lease is not None
    assert compilation.local_delta_lease.owner is overlay
    assert compilation.excluded_root_ids is (base_segment.root_ids if removed else None)
    statistics = compilation.native_statistics
    assert statistics.roots == 4 - len(removed)
    assert statistics.sub_object_properties == int(
        "SubObjectPropertyOf" not in removed_constructors
    )
    assert statistics.inverse_object_properties == int(
        "InverseObjectProperties" not in removed_constructors
    )
    assert statistics.object_property_domains == 1
    assert statistics.object_property_ranges == 1
    assert statistics.ignored_object_property_domains == 0
    assert statistics.ignored_object_property_ranges == 0
    assert statistics.domain_range_edges == 1
    assert statistics.role_expansion_edges == len(expected_edges) - 1
    assert statistics.skipped_axioms == 0
    assert statistics.edges == len(expected_edges)
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
    ("removed_sources", "bidirectional", "only_taxonomy", "expected_sources"),
    [
        (frozenset(), False, False, ("A", "B", "C", "D", "E")),
        (frozenset({"C"}), False, False, ("A", "B", "D", "E")),
        (frozenset({"A", "C", "E"}), False, False, ("B", "D")),
        (frozenset(), True, False, ("A", "B", "C", "D", "E")),
        (frozenset(), False, True, ("A", "B", "C", "D", "E")),
    ],
    ids=[
        "base-all",
        "base-exclude-one",
        "base-exclude-all",
        "bidirectional",
        "only-taxonomy",
    ],
)
def test_hidden_iterator_projects_two_local_named_subclasses(
    provider_backend: pyowl_core.BackendPreference,
    removed_sources: frozenset[str],
    bidirectional: bool,
    only_taxonomy: bool,
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
        _snapshot(
            "SubClassOf(:B :Top) SubClassOf(:D :Top)",
            backend=provider_backend,
        ),
    )
    added = set(addition_source.iter_axioms())
    assert len(added) == 2
    overlay = pyowl_core.apply_delta(
        base,
        pyowl_core.OntologyDelta(
            add_axioms=cast(Any, added),
            remove_axioms=cast(Any, removed),
        ),
    )
    top_encoded = overlay.view(
        pyowl_core.EncodedStructuralView,
        schema_version=1,
        scope=pyowl_core.AxiomScope.CLOSURE,
    )
    assert tuple(segment.role for segment in top_encoded.segments) == (2, 3)
    assert top_encoded.buffers["root_kinds"].nbytes == 2
    assert top_encoded.buffers["root_ids"].nbytes == 8
    base_segment = cast(Any, top_encoded.segments[0])
    delta_segment = cast(Any, top_encoded.segments[1])
    assert base_segment.posting_mode == (2 if removed else 0)
    assert base_segment.root_ids.nbytes == 4 * len(removed)
    assert delta_segment.posting_mode == 0
    assert delta_segment.root_ids.nbytes == 0
    assert delta_segment.anonymous_scope_map.nbytes == 0
    source_encoded = base_segment.source
    assert source_encoded is not None
    expected_buffer_bytes = sum(value.nbytes for value in top_encoded.buffers.values()) + sum(
        value.nbytes for value in source_encoded.buffers.values()
    )

    python_options = ProjectionOptions(
        backend="python",
        order="encounter",
        bidirectional_taxonomy=bidirectional,
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
                "two-root local named SubClassOf envelope reached scalar traversal"
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

    expected_edges: list[Edge] = []
    for source in expected_sources:
        source_iri = f"urn:native-integration#{source}"
        top_iri = "urn:native-integration#Top"
        expected_edges.append(Edge(source_iri, "http://subclassof", top_iri))
        if bidirectional:
            expected_edges.append(Edge(top_iri, "http://superclassof", source_iri))
    assert actual == expected == expected_edges
    _assert_semantic_report_parity(expected_report, report)
    assert report.diagnostics == ()
    assert len(captured) == 1
    compilation = captured[0]
    assert compilation.view is overlay
    assert compilation.lease.owner is base
    assert compilation.local_delta_lease is compilation.container_leases[0]
    assert compilation.local_delta_lease is not None
    assert compilation.local_delta_lease.owner is overlay
    assert compilation.excluded_root_ids is (base_segment.root_ids if removed else None)
    statistics = compilation.native_statistics
    assert statistics.roots == len(expected_sources)
    assert statistics.subclasses == len(expected_sources)
    assert statistics.restriction_subclasses == 0
    assert statistics.ignored_subclasses == 0
    assert statistics.skipped_axioms == 0
    assert statistics.edges == len(expected_edges)
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
def test_two_local_named_subclasses_fail_preoutput_and_retry_in_asserted_mode(
    provider_backend: pyowl_core.BackendPreference,
) -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "SubClassOf(:A :Top) SubClassOf(:C :Top) SubClassOf(:E :Top)",
            backend=provider_backend,
        ),
    )
    addition_source = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "SubClassOf(:B :Top) SubClassOf(:D :Top)",
            backend=provider_backend,
        ),
    )
    overlay = pyowl_core.apply_delta(
        base,
        pyowl_core.OntologyDelta(
            add_axioms=cast(Any, set(addition_source.iter_axioms())),
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
    assert excluded_root_ids is None

    failing = prepare_native_encoded_direct(
        base_lease,
        local_delta_lease=top_lease,
        canonical_work_limit=max_work,
        canonical_workspace_limit=max_workspace,
    )
    with pytest.raises(ProjectionResourceError, match="configured edge resources"):
        failing.compile_batch(
            bidirectional=False,
            asserted_taxonomy_only=True,
            max_edges=4,
            max_iri_bytes=1024,
        )
    assert failing.state == "failed"
    assert failing.retained_buffer_count == 22
    assert failing.cancel() is False

    retry = prepare_native_encoded_direct(
        base_lease,
        local_delta_lease=top_lease,
        canonical_work_limit=max_work,
        canonical_workspace_limit=max_workspace,
    )
    edges, statistics = retry.compile_batch(
        bidirectional=False,
        asserted_taxonomy_only=True,
        max_edges=5,
        max_iri_bytes=1024,
    )
    assert edges == [
        Edge(
            f"urn:native-integration#{source}",
            "http://subclassof",
            "urn:native-integration#Top",
        )
        for source in ("A", "B", "C", "D", "E")
    ]
    assert statistics.roots == 5
    assert statistics.subclasses == 5
    assert statistics.edges == 5
    assert retry.state == "finished"


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
        ((), False, (("i", "A"), ("j", "B"), ("k", "C"), ("l", "D"), ("m", "E"))),
        ((1,), False, (("i", "A"), ("j", "B"), ("l", "D"), ("m", "E"))),
        ((0, 1, 2), False, (("j", "B"), ("l", "D"))),
        ((), True, (("i", "A"), ("j", "B"), ("k", "C"), ("l", "D"), ("m", "E"))),
    ],
    ids=["base-all", "base-exclude-one", "base-exclude-all", "only-taxonomy"],
)
def test_hidden_iterator_projects_two_local_named_class_assertions(
    provider_backend: pyowl_core.BackendPreference,
    removed_indices: tuple[int, ...],
    only_taxonomy: bool,
    expected_pairs: tuple[tuple[str, str], ...],
) -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "ClassAssertion(:A :i) ClassAssertion(:C :k) ClassAssertion(:E :m)",
            backend=provider_backend,
        ),
    )
    base_axioms = tuple(base.iter_axioms())
    assert len(base_axioms) == 3
    removed = {base_axioms[index] for index in removed_indices}
    addition_source = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "ClassAssertion(:B :j) ClassAssertion(:D :l)",
            backend=provider_backend,
        ),
    )
    added = set(addition_source.iter_axioms())
    assert len(added) == 2
    overlay = pyowl_core.apply_delta(
        base,
        pyowl_core.OntologyDelta(
            add_axioms=cast(Any, added),
            remove_axioms=cast(Any, removed),
        ),
    )
    top_encoded = overlay.view(
        pyowl_core.EncodedStructuralView,
        schema_version=1,
        scope=pyowl_core.AxiomScope.CLOSURE,
    )
    assert tuple(segment.role for segment in top_encoded.segments) == (2, 3)
    assert top_encoded.buffers["root_kinds"].nbytes == 2
    assert top_encoded.buffers["root_ids"].nbytes == 8
    base_segment = cast(Any, top_encoded.segments[0])
    delta_segment = cast(Any, top_encoded.segments[1])
    assert base_segment.posting_mode == (2 if removed else 0)
    assert base_segment.root_ids.nbytes == 4 * len(removed)
    assert delta_segment.posting_mode == 0
    assert delta_segment.root_ids.nbytes == 0
    assert delta_segment.anonymous_scope_map.nbytes == 0
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
                "two-root local named ClassAssertion envelope reached scalar traversal"
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
            f"urn:native-integration#{individual}",
            "http://type",
            f"urn:native-integration#{class_name}",
        )
        for individual, class_name in expected_pairs
    ]
    assert actual == expected == expected_edges
    _assert_semantic_report_parity(expected_report, report)
    assert report.diagnostics == ()
    assert len(captured) == 1
    compilation = captured[0]
    assert compilation.view is overlay
    assert compilation.lease.owner is base
    assert compilation.local_delta_lease is compilation.container_leases[0]
    assert compilation.local_delta_lease is not None
    assert compilation.local_delta_lease.owner is overlay
    assert compilation.excluded_root_ids is (base_segment.root_ids if removed else None)
    statistics = compilation.native_statistics
    assert statistics.roots == len(expected_pairs)
    assert statistics.class_assertions == len(expected_pairs)
    assert statistics.ignored_class_assertions == 0
    assert statistics.skipped_axioms == 0
    assert statistics.edges == len(expected_edges)
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
def test_two_local_named_class_assertions_fail_preoutput_retry_and_asserted_suppression(
    provider_backend: pyowl_core.BackendPreference,
) -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "ClassAssertion(:A :i) ClassAssertion(:C :k) ClassAssertion(:E :m)",
            backend=provider_backend,
        ),
    )
    addition_source = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "ClassAssertion(:B :j) ClassAssertion(:D :l)",
            backend=provider_backend,
        ),
    )
    overlay = pyowl_core.apply_delta(
        base,
        pyowl_core.OntologyDelta(
            add_axioms=cast(Any, set(addition_source.iter_axioms())),
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
    assert excluded_root_ids is None

    failing = prepare_native_encoded_direct(
        base_lease,
        local_delta_lease=top_lease,
        canonical_work_limit=max_work,
        canonical_workspace_limit=max_workspace,
    )
    with pytest.raises(ProjectionResourceError, match="configured edge resources"):
        failing.compile_batch(
            bidirectional=False,
            max_edges=4,
            max_iri_bytes=1024,
        )
    assert failing.state == "failed"
    assert failing.retained_buffer_count == 22
    assert failing.cancel() is False

    retry = prepare_native_encoded_direct(
        base_lease,
        local_delta_lease=top_lease,
        canonical_work_limit=max_work,
        canonical_workspace_limit=max_workspace,
    )
    edges, statistics = retry.compile_batch(
        bidirectional=False,
        max_edges=5,
        max_iri_bytes=1024,
    )
    assert edges == [
        Edge(
            f"urn:native-integration#{individual}",
            "http://type",
            f"urn:native-integration#{class_name}",
        )
        for individual, class_name in (
            ("i", "A"),
            ("j", "B"),
            ("k", "C"),
            ("l", "D"),
            ("m", "E"),
        )
    ]
    assert statistics.roots == 5
    assert statistics.class_assertions == 5
    assert statistics.edges == 5
    assert retry.state == "finished"

    asserted = prepare_native_encoded_direct(
        base_lease,
        local_delta_lease=top_lease,
        canonical_work_limit=max_work,
        canonical_workspace_limit=max_workspace,
    )
    asserted_edges, asserted_statistics = asserted.compile_batch(
        bidirectional=False,
        asserted_taxonomy_only=True,
        max_edges=1,
        max_iri_bytes=1024,
    )
    assert asserted_edges == []
    assert asserted_statistics.roots == 5
    assert asserted_statistics.class_assertions == 5
    assert asserted_statistics.edges == 0
    assert asserted.state == "finished"


def test_two_local_object_property_class_roots_fail_before_output_and_retry() -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot("SubObjectPropertyOf(:child :p) InverseObjectProperties(:p :pinv)"),
    )
    addition_source = cast(
        pyowl_core.OntologyView,
        _snapshot("ObjectPropertyDomain(:p :D) ObjectPropertyRange(:p :R)"),
    )
    overlay = pyowl_core.apply_delta(
        base,
        pyowl_core.OntologyDelta(
            add_axioms=cast(Any, set(addition_source.iter_axioms())),
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
    assert excluded_root_ids is None

    failing = prepare_native_encoded_direct(
        base_lease,
        local_delta_lease=top_lease,
        canonical_work_limit=max_work,
        canonical_workspace_limit=max_workspace,
    )
    with pytest.raises(ProjectionResourceError, match="configured edge resources"):
        failing.compile_batch(
            bidirectional=False,
            max_edges=2,
            max_iri_bytes=1024,
        )
    assert failing.state == "failed"
    assert failing.retained_buffer_count == 22
    assert failing.cancel() is False

    retry = prepare_native_encoded_direct(
        base_lease,
        local_delta_lease=top_lease,
        canonical_work_limit=max_work,
        canonical_workspace_limit=max_workspace,
    )
    edges, statistics = retry.compile_batch(
        bidirectional=False,
        max_edges=3,
        max_iri_bytes=1024,
    )
    assert [edge.as_tuple() for edge in edges] == [
        (
            "urn:native-integration#D",
            "urn:native-integration#p",
            "urn:native-integration#R",
        ),
        (
            "urn:native-integration#D",
            "urn:native-integration#child",
            "urn:native-integration#R",
        ),
        (
            "urn:native-integration#R",
            "urn:native-integration#pinv",
            "urn:native-integration#D",
        ),
    ]
    assert statistics.roots == 4
    assert statistics.object_property_domains == 1
    assert statistics.object_property_ranges == 1
    assert statistics.domain_range_edges == 1
    assert statistics.role_expansion_edges == 2
    assert statistics.edges == 3
    assert retry.state == "finished"

    failed_projector = Projector()
    failed_iterator = failed_projector._iter_native_encoded_edges(
        overlay,
        options=ProjectionOptions(backend="native", order="encounter"),
        buffer_edges=1,
        streaming_limits=StreamingLimits(max_total_edges=2),
    )
    with pytest.raises(ProjectionResourceError, match="configured edge resources"):
        next(failed_iterator)
    assert failed_projector.last_report is None

    retry_projector = Projector()
    actual = list(
        retry_projector._iter_native_encoded_edges(
            overlay,
            options=ProjectionOptions(backend="native", order="encounter"),
            buffer_edges=1,
            streaming_limits=StreamingLimits(max_total_edges=3),
        )
    )
    assert actual == edges
    report = _completed_report(retry_projector)
    assert report.provenance.ingestion.path == "encoded-native"
    assert report.provenance.ingestion.counters["native_compiled_edges"] == 3


@pytest.mark.parametrize(
    "provider_backend",
    [
        pyowl_core.BackendPreference.PYTHON,
        pyowl_core.BackendPreference.NATIVE,
    ],
    ids=["independent-bytes", "packed-bytes"],
)
@pytest.mark.parametrize(
    ("local_body", "constructor"),
    [
        (
            "SubClassOf(:Ignored ObjectSomeValuesFrom("
            ":p ObjectIntersectionOf(:B :C)))",
            "SubClassOf",
        ),
        (
            "SubClassOf(ObjectUnionOf(:A :B) :Ignored)",
            "SubClassOf",
        ),
        (
            "ClassAssertion(ObjectSomeValuesFrom("
            ":p ObjectIntersectionOf(:B :C)) :i)",
            "ClassAssertion",
        ),
        (
            "ClassAssertion(ObjectComplementOf(ObjectUnionOf(:A :B)) :i)",
            "ClassAssertion",
        ),
    ],
    ids=[
        "subclass-complex-restriction",
        "subclass-aggregate",
        "assertion-complex-restriction",
        "assertion-recursive",
    ],
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
def test_hidden_iterator_compiles_one_silent_local_ignored_class_axiom(
    provider_backend: pyowl_core.BackendPreference,
    local_body: str,
    constructor: str,
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
    assert delta_segment.anonymous_scope_map.nbytes == 0
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
                "ignored local class axiom reached scalar traversal"
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
    assert report.provenance.counts.ignored_shapes == 1
    assert tuple(
        (item.code, item.constructor, item.count) for item in report.diagnostics
    ) == (("MOWL_IGNORED_SHAPE", constructor, 1),)
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
    statistics = compilation.native_statistics
    is_subclass = constructor == "SubClassOf"
    assert statistics.roots == len(expected_sources) + 1
    assert statistics.subclasses == len(expected_sources) + int(is_subclass)
    assert statistics.ignored_subclasses == int(is_subclass)
    assert statistics.class_assertions == int(not is_subclass)
    assert statistics.ignored_class_assertions == int(not is_subclass)
    assert statistics.skipped_axioms == 0
    assert statistics.edges == len(expected_edges)
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
    ("local_body", "constructor"),
    [
        (
            "SubClassOf(ObjectUnionOf(:A :B) :Ignored)",
            "SubClassOf",
        ),
        (
            "ClassAssertion(ObjectComplementOf(ObjectUnionOf(:A :B)) :i)",
            "ClassAssertion",
        ),
    ],
    ids=["subclass", "class-assertion"],
)
def test_private_overlay_ignored_class_axiom_preserves_asserted_taxonomy(
    local_body: str,
    constructor: str,
) -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot("SubClassOf(:A :Top) SubClassOf(:C :Top)"),
    )
    addition_source = cast(pyowl_core.OntologyView, _snapshot(local_body))
    overlay = pyowl_core.apply_delta(
        base,
        pyowl_core.OntologyDelta(
            add_axioms=cast(Any, set(addition_source.iter_axioms())),
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
    assert excluded_root_ids is None
    compiler = prepare_native_encoded_direct(
        base_lease,
        local_delta_lease=top_lease,
        canonical_work_limit=max_work,
        canonical_workspace_limit=max_workspace,
    )
    edges, statistics = compiler.compile_batch(
        bidirectional=False,
        max_edges=2,
        max_iri_bytes=1024,
        asserted_taxonomy_only=True,
        only_taxonomy=True,
    )

    assert [edge.as_tuple() for edge in edges] == [
        (
            "urn:native-integration#A",
            "http://subclassof",
            "urn:native-integration#Top",
        ),
        (
            "urn:native-integration#C",
            "http://subclassof",
            "urn:native-integration#Top",
        ),
    ]
    is_subclass = constructor == "SubClassOf"
    assert statistics.roots == 3
    assert statistics.subclasses == 2 + int(is_subclass)
    assert statistics.ignored_subclasses == int(is_subclass)
    assert statistics.class_assertions == int(not is_subclass)
    assert statistics.ignored_class_assertions == int(not is_subclass)
    assert statistics.skipped_axioms == 0
    assert statistics.edges == 2


@pytest.mark.parametrize(
    "provider_backend",
    [
        pyowl_core.BackendPreference.PYTHON,
        pyowl_core.BackendPreference.NATIVE,
    ],
    ids=["independent-bytes", "packed-bytes"],
)
@pytest.mark.parametrize(
    "local_body",
    [
        "EquivalentClasses(:Ignored ObjectSomeValuesFrom("
        ":p ObjectIntersectionOf(:B :C)))",
        "EquivalentClasses(ObjectSomeValuesFrom(:p :B) ObjectComplementOf(:C))",
        "EquivalentClasses(:Ignored ObjectComplementOf(ObjectUnionOf(:A :B)))",
        "EquivalentClasses(:Ignored ObjectSomeValuesFrom(:p :B) "
        "ObjectComplementOf(:C))",
    ],
    ids=[
        "named-restriction",
        "all-complex",
        "named-recursive",
        "ternary-ignored",
    ],
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
def test_hidden_iterator_compiles_one_silent_local_ignored_equivalence(
    provider_backend: pyowl_core.BackendPreference,
    local_body: str,
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
    assert delta_segment.anonymous_scope_map.nbytes == 0
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
                "ignored local EquivalentClasses reached scalar traversal"
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
    assert report.provenance.counts.ignored_shapes == 1
    assert tuple(
        (item.code, item.constructor, item.count) for item in report.diagnostics
    ) == (("MOWL_IGNORED_SHAPE", "EquivalentClasses", 1),)
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
    statistics = compilation.native_statistics
    assert statistics.roots == len(expected_sources) + 1
    assert statistics.subclasses == len(expected_sources)
    assert statistics.equivalents == 1
    assert statistics.aggregate_equivalents == 0
    assert statistics.ignored_equivalents == 1
    assert statistics.skipped_axioms == 0
    assert statistics.edges == len(expected_edges)
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


def test_private_overlay_ignored_equivalence_preserves_asserted_taxonomy() -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot("SubClassOf(:A :Top) SubClassOf(:C :Top)"),
    )
    addition_source = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "EquivalentClasses(:Ignored "
            "ObjectComplementOf(ObjectUnionOf(:A :B)))"
        ),
    )
    overlay = pyowl_core.apply_delta(
        base,
        pyowl_core.OntologyDelta(
            add_axioms=cast(Any, set(addition_source.iter_axioms())),
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
    assert excluded_root_ids is None
    compiler = prepare_native_encoded_direct(
        base_lease,
        local_delta_lease=top_lease,
        canonical_work_limit=max_work,
        canonical_workspace_limit=max_workspace,
    )
    edges, statistics = compiler.compile_batch(
        bidirectional=False,
        max_edges=2,
        max_iri_bytes=1024,
        asserted_taxonomy_only=True,
        only_taxonomy=True,
    )

    assert [edge.as_tuple() for edge in edges] == [
        (
            "urn:native-integration#A",
            "http://subclassof",
            "urn:native-integration#Top",
        ),
        (
            "urn:native-integration#C",
            "http://subclassof",
            "urn:native-integration#Top",
        ),
    ]
    assert statistics.roots == 3
    assert statistics.subclasses == 2
    assert statistics.equivalents == 1
    assert statistics.aggregate_equivalents == 0
    assert statistics.ignored_equivalents == 0
    assert statistics.skipped_axioms == 0
    assert statistics.edges == 2


@pytest.mark.parametrize(
    "provider_backend",
    [
        pyowl_core.BackendPreference.PYTHON,
        pyowl_core.BackendPreference.NATIVE,
    ],
    ids=["independent-bytes", "packed-bytes"],
)
@pytest.mark.parametrize(
    ("local_body", "constructor"),
    [
        (
            "ObjectPropertyDomain(ObjectInverseOf(:p) :Domain)",
            "ObjectPropertyDomain",
        ),
        (
            "ObjectPropertyDomain(:p "
            "ObjectComplementOf(ObjectUnionOf(:A :B)))",
            "ObjectPropertyDomain",
        ),
        (
            "ObjectPropertyRange(ObjectInverseOf(:p) :Range)",
            "ObjectPropertyRange",
        ),
        (
            "ObjectPropertyRange(:p "
            "ObjectComplementOf(ObjectIntersectionOf(:B :C)))",
            "ObjectPropertyRange",
        ),
    ],
    ids=[
        "inverse-domain",
        "complex-domain",
        "inverse-range",
        "complex-range",
    ],
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
def test_hidden_iterator_compiles_one_silent_local_ignored_object_property_class_axiom(
    provider_backend: pyowl_core.BackendPreference,
    local_body: str,
    constructor: str,
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
    assert delta_segment.anonymous_scope_map.nbytes == 0
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
                "ignored local object-property domain/range reached scalar traversal"
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
    assert report.provenance.counts.ignored_shapes == 1
    assert tuple(
        (item.code, item.constructor, item.count) for item in report.diagnostics
    ) == (("MOWL_IGNORED_SHAPE", constructor, 1),)
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
    statistics = compilation.native_statistics
    is_domain = constructor == "ObjectPropertyDomain"
    assert statistics.roots == len(expected_sources) + 1
    assert statistics.subclasses == len(expected_sources)
    assert statistics.object_property_domains == int(is_domain)
    assert statistics.object_property_ranges == int(not is_domain)
    assert statistics.ignored_object_property_domains == int(is_domain)
    assert statistics.ignored_object_property_ranges == int(not is_domain)
    assert statistics.domain_range_edges == 0
    assert statistics.role_expansion_edges == 0
    assert statistics.skipped_axioms == 0
    assert statistics.edges == len(expected_edges)
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
    ("local_body", "constructor"),
    [
        (
            "ObjectPropertyDomain(ObjectInverseOf(:p) :Domain)",
            "ObjectPropertyDomain",
        ),
        (
            "ObjectPropertyRange(:p ObjectComplementOf(:Range))",
            "ObjectPropertyRange",
        ),
    ],
    ids=["domain", "range"],
)
def test_private_overlay_ignored_object_property_class_axiom_preserves_asserted_taxonomy(
    local_body: str,
    constructor: str,
) -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot("SubClassOf(:A :Top) SubClassOf(:C :Top)"),
    )
    addition_source = cast(pyowl_core.OntologyView, _snapshot(local_body))
    overlay = pyowl_core.apply_delta(
        base,
        pyowl_core.OntologyDelta(
            add_axioms=cast(Any, set(addition_source.iter_axioms())),
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
    assert excluded_root_ids is None
    compiler = prepare_native_encoded_direct(
        base_lease,
        local_delta_lease=top_lease,
        canonical_work_limit=max_work,
        canonical_workspace_limit=max_workspace,
    )
    edges, statistics = compiler.compile_batch(
        bidirectional=False,
        max_edges=2,
        max_iri_bytes=1024,
        asserted_taxonomy_only=True,
        only_taxonomy=True,
    )

    assert [edge.as_tuple() for edge in edges] == [
        (
            "urn:native-integration#A",
            "http://subclassof",
            "urn:native-integration#Top",
        ),
        (
            "urn:native-integration#C",
            "http://subclassof",
            "urn:native-integration#Top",
        ),
    ]
    is_domain = constructor == "ObjectPropertyDomain"
    assert statistics.roots == 3
    assert statistics.subclasses == 2
    assert statistics.object_property_domains == int(is_domain)
    assert statistics.object_property_ranges == int(not is_domain)
    assert statistics.ignored_object_property_domains == int(is_domain)
    assert statistics.ignored_object_property_ranges == int(not is_domain)
    assert statistics.domain_range_edges == 0
    assert statistics.role_expansion_edges == 0
    assert statistics.skipped_axioms == 0
    assert statistics.edges == 2


@pytest.mark.parametrize(
    "provider_backend",
    [
        pyowl_core.BackendPreference.PYTHON,
        pyowl_core.BackendPreference.NATIVE,
    ],
    ids=["independent-bytes", "packed-bytes"],
)
@pytest.mark.parametrize(
    ("local_body", "constructor", "counterpart"),
    [
        (
            "ObjectPropertyDomain(:p :A)",
            "ObjectPropertyDomain",
            "ObjectPropertyRange",
        ),
        (
            "ObjectPropertyRange(:p :A)",
            "ObjectPropertyRange",
            "ObjectPropertyDomain",
        ),
    ],
    ids=["domain", "range"],
)
@pytest.mark.parametrize(
    ("removal", "only_taxonomy", "expected_products"),
    [
        ("none", False, 2),
        ("none", True, 2),
        ("same", False, 1),
        ("counterpart", False, 0),
    ],
    ids=[
        "base-all",
        "only-taxonomy",
        "exclude-same-kind",
        "exclude-counterpart",
    ],
)
def test_hidden_iterator_projects_one_local_object_property_class_axiom(
    provider_backend: pyowl_core.BackendPreference,
    local_body: str,
    constructor: str,
    counterpart: str,
    removal: str,
    only_taxonomy: bool,
    expected_products: int,
) -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "SubObjectPropertyOf(:child :p) "
            "InverseObjectProperties(:p :pinv) "
            "ObjectPropertyDomain(:p :D) "
            "ObjectPropertyRange(:p :R)",
            backend=provider_backend,
        ),
    )
    removed_constructor = {
        "none": None,
        "same": constructor,
        "counterpart": counterpart,
    }[removal]
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
    assert delta_segment.anonymous_scope_map.nbytes == 0
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
                "projecting local object-property domain/range reached scalar traversal"
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

    expected_edges: list[Edge] = []
    is_domain = constructor == "ObjectPropertyDomain"
    pairs = (
        (("A", "R"), ("D", "R"))
        if is_domain
        else (("D", "A"), ("D", "R"))
    )
    for domain, range_ in pairs[:expected_products]:
        expected_edges.extend(
            [
                Edge(
                    f"urn:native-integration#{domain}",
                    "urn:native-integration#p",
                    f"urn:native-integration#{range_}",
                ),
                Edge(
                    f"urn:native-integration#{domain}",
                    "urn:native-integration#child",
                    f"urn:native-integration#{range_}",
                ),
                Edge(
                    f"urn:native-integration#{range_}",
                    "urn:native-integration#pinv",
                    f"urn:native-integration#{domain}",
                ),
            ]
        )
    assert actual == expected == expected_edges
    _assert_semantic_report_parity(expected_report, report)
    assert report.provenance.counts.ignored_shapes == 0
    assert report.diagnostics == ()
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
    statistics = compilation.native_statistics
    assert statistics.roots == 5 - len(removed)
    assert statistics.sub_object_properties == 1
    assert statistics.object_property_chains == 0
    assert statistics.inverse_object_properties == 1
    assert statistics.object_property_domains == (
        1 - int(removed_constructor == "ObjectPropertyDomain") + int(is_domain)
    )
    assert statistics.object_property_ranges == (
        1 - int(removed_constructor == "ObjectPropertyRange") + int(not is_domain)
    )
    assert statistics.ignored_object_property_domains == 0
    assert statistics.ignored_object_property_ranges == 0
    assert statistics.domain_range_edges == expected_products
    assert statistics.role_expansion_edges == expected_products * 2
    assert statistics.skipped_axioms == 0
    assert statistics.edges == len(expected_edges)
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
    "local_body",
    [
        "SubObjectPropertyOf(ObjectPropertyChain(:left :right) :p)",
        "SubObjectPropertyOf(ObjectPropertyChain(:left :middle :right) :p)",
        "SubObjectPropertyOf("
        "ObjectPropertyChain(:left ObjectInverseOf(:right)) :p)",
        "SubObjectPropertyOf("
        "ObjectPropertyChain(ObjectInverseOf(:left) :right) "
        "ObjectInverseOf(:p))",
    ],
    ids=[
        "binary-named",
        "ternary-named",
        "binary-inverse-member",
        "binary-inverse-member-and-super",
    ],
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
def test_hidden_iterator_compiles_one_state_neutral_local_property_chain(
    provider_backend: pyowl_core.BackendPreference,
    local_body: str,
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
    assert delta_segment.anonymous_scope_map.nbytes == 0
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
                "state-neutral local property chain reached scalar traversal"
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
    assert report.provenance.counts.ignored_shapes == 1
    assert report.diagnostics == ()
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
    statistics = compilation.native_statistics
    assert statistics.roots == len(expected_sources) + 1
    assert statistics.subclasses == len(expected_sources)
    assert statistics.sub_object_properties == 1
    assert statistics.object_property_chains == 1
    assert statistics.role_expansion_edges == 0
    assert statistics.skipped_axioms == 0
    assert statistics.edges == len(expected_edges)
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


def test_private_overlay_property_chain_preserves_asserted_taxonomy() -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot("SubClassOf(:A :Top) SubClassOf(:C :Top)"),
    )
    addition_source = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "SubObjectPropertyOf("
            "ObjectPropertyChain(:left ObjectInverseOf(:right)) :p)"
        ),
    )
    overlay = pyowl_core.apply_delta(
        base,
        pyowl_core.OntologyDelta(
            add_axioms=cast(Any, set(addition_source.iter_axioms())),
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
    assert excluded_root_ids is None
    compiler = prepare_native_encoded_direct(
        base_lease,
        local_delta_lease=top_lease,
        canonical_work_limit=max_work,
        canonical_workspace_limit=max_workspace,
    )
    edges, statistics = compiler.compile_batch(
        bidirectional=False,
        max_edges=2,
        max_iri_bytes=1024,
        asserted_taxonomy_only=True,
        only_taxonomy=True,
    )

    assert [edge.as_tuple() for edge in edges] == [
        (
            "urn:native-integration#A",
            "http://subclassof",
            "urn:native-integration#Top",
        ),
        (
            "urn:native-integration#C",
            "http://subclassof",
            "urn:native-integration#Top",
        ),
    ]
    assert statistics.roots == 3
    assert statistics.subclasses == 2
    assert statistics.sub_object_properties == 1
    assert statistics.object_property_chains == 1
    assert statistics.role_expansion_edges == 0
    assert statistics.skipped_axioms == 0
    assert statistics.edges == 2


@pytest.mark.parametrize(
    "provider_backend",
    [
        pyowl_core.BackendPreference.PYTHON,
        pyowl_core.BackendPreference.NATIVE,
    ],
    ids=["independent-bytes", "packed-bytes"],
)
@pytest.mark.parametrize(
    ("local_body", "root_family"),
    [
        ("SubObjectPropertyOf(:p :super)", "sub-property"),
        (
            "SubObjectPropertyOf("
            "ObjectInverseOf(:p) ObjectInverseOf(:super))",
            "sub-property",
        ),
        ("InverseObjectProperties(:p :super)", "inverse-properties"),
        (
            "InverseObjectProperties("
            "ObjectInverseOf(:p) ObjectInverseOf(:super))",
            "inverse-properties",
        ),
    ],
    ids=[
        "named-sub-property",
        "inverse-expression-sub-property",
        "named-inverse-properties",
        "inverse-expression-inverse-properties",
    ],
)
@pytest.mark.parametrize(
    ("removed_sources", "only_taxonomy", "keep_taxonomy", "keep_restriction"),
    [
        (frozenset(), False, True, True),
        (frozenset({"C"}), False, True, False),
        (frozenset({"A"}), False, False, True),
        (frozenset(), True, True, False),
    ],
    ids=[
        "base-all",
        "exclude-restriction",
        "exclude-taxonomy",
        "only-taxonomy",
    ],
)
def test_hidden_iterator_recomputes_base_for_one_local_role_axiom(
    provider_backend: pyowl_core.BackendPreference,
    local_body: str,
    root_family: str,
    removed_sources: frozenset[str],
    only_taxonomy: bool,
    keep_taxonomy: bool,
    keep_restriction: bool,
) -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "SubClassOf(:A :Top) "
            "SubClassOf(:C ObjectSomeValuesFrom(:super :D))",
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
        _snapshot(local_body, backend=provider_backend),
    )
    added = set(addition_source.iter_axioms())
    assert len(added) == 1
    overlay = pyowl_core.apply_delta(
        base,
        pyowl_core.OntologyDelta(
            add_axioms=cast(Any, added),
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
    assert delta_segment.anonymous_scope_map.nbytes == 0
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
                "stateful local role axiom reached scalar traversal"
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

    expected_edges = []
    if keep_taxonomy:
        expected_edges.append(
            Edge(
                "urn:native-integration#A",
                "http://subclassof",
                "urn:native-integration#Top",
            )
        )
    if keep_restriction:
        expected_edges.append(
            Edge(
                "urn:native-integration#C",
                "urn:native-integration#super",
                "urn:native-integration#D",
            )
        )
        if root_family == "sub-property":
            expected_edges.append(
                Edge(
                    "urn:native-integration#C",
                    "urn:native-integration#p",
                    "urn:native-integration#D",
                )
            )
        else:
            expected_edges.append(
                Edge(
                    "urn:native-integration#D",
                    "urn:native-integration#p",
                    "urn:native-integration#C",
                )
            )
    assert actual == expected == expected_edges
    _assert_semantic_report_parity(expected_report, report)
    assert report.provenance.counts.ignored_shapes == int(
        only_taxonomy and "C" not in removed_sources
    )
    expected_diagnostics = (
        (("MOWL_IGNORED_SHAPE", "SubClassOf", 1),)
        if only_taxonomy and "C" not in removed_sources
        else ()
    )
    assert tuple(
        (diagnostic.code, diagnostic.constructor, diagnostic.count)
        for diagnostic in report.diagnostics
    ) == expected_diagnostics
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
    statistics = compilation.native_statistics
    selected_base_roots = 2 - len(removed_sources)
    is_sub_property = root_family == "sub-property"
    assert statistics.roots == selected_base_roots + 1
    assert statistics.subclasses == selected_base_roots
    assert statistics.restriction_subclasses == int(
        "C" not in removed_sources
    )
    assert statistics.sub_object_properties == int(is_sub_property)
    assert statistics.object_property_chains == 0
    assert statistics.inverse_object_properties == int(not is_sub_property)
    assert statistics.role_expansion_edges == int(keep_restriction)
    assert statistics.skipped_axioms == 0
    assert statistics.edges == len(expected_edges)
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
    ("local_body", "root_family"),
    [
        ("SubObjectPropertyOf(:p :super)", "sub-property"),
        ("InverseObjectProperties(:p :super)", "inverse-properties"),
    ],
    ids=["sub-property", "inverse-properties"],
)
def test_hidden_iterator_merges_local_axiom_with_base_role_state(
    provider_backend: pyowl_core.BackendPreference,
    local_body: str,
    root_family: str,
) -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "SubClassOf(:A :Top) "
            "SubClassOf(:C ObjectSomeValuesFrom(:super :D)) "
            "SubObjectPropertyOf(:baseChild :super) "
            "InverseObjectProperties(:baseInv :super)",
            backend=provider_backend,
        ),
    )
    addition_source = cast(
        pyowl_core.OntologyView,
        _snapshot(local_body, backend=provider_backend),
    )
    overlay = pyowl_core.apply_delta(
        base,
        pyowl_core.OntologyDelta(
            add_axioms=cast(Any, set(addition_source.iter_axioms())),
        ),
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
            side_effect=AssertionError(
                "merged local role axiom reached scalar traversal"
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

    is_sub_property = root_family == "sub-property"
    expected_expansion = (
        Edge(
            "urn:native-integration#C",
            "urn:native-integration#p",
            "urn:native-integration#D",
        )
        if is_sub_property
        else Edge(
            "urn:native-integration#D",
            "urn:native-integration#p",
            "urn:native-integration#C",
        )
    )
    expected_other_role = (
        Edge(
            "urn:native-integration#D",
            "urn:native-integration#baseInv",
            "urn:native-integration#C",
        )
        if is_sub_property
        else Edge(
            "urn:native-integration#C",
            "urn:native-integration#baseChild",
            "urn:native-integration#D",
        )
    )
    expected_role_edges = (
        [expected_expansion, expected_other_role]
        if is_sub_property
        else [expected_other_role, expected_expansion]
    )
    expected_edges = [
        Edge(
            "urn:native-integration#A",
            "http://subclassof",
            "urn:native-integration#Top",
        ),
        Edge(
            "urn:native-integration#C",
            "urn:native-integration#super",
            "urn:native-integration#D",
        ),
        *expected_role_edges,
    ]
    assert actual == expected == expected_edges
    _assert_semantic_report_parity(expected_report, report)
    assert report.diagnostics == ()
    assert len(captured) == 1
    statistics = captured[0].native_statistics
    assert statistics.roots == 5
    assert statistics.subclasses == 2
    assert statistics.restriction_subclasses == 1
    assert statistics.sub_object_properties == 1 + int(is_sub_property)
    assert statistics.inverse_object_properties == 1 + int(not is_sub_property)
    assert statistics.role_expansion_edges == 2
    assert statistics.skipped_axioms == 0
    assert statistics.edges == 4


@pytest.mark.parametrize(
    ("local_body", "root_family"),
    [
        ("SubObjectPropertyOf(:p :super)", "sub-property"),
        (
            "SubObjectPropertyOf("
            "ObjectInverseOf(:p) ObjectInverseOf(:super))",
            "sub-property",
        ),
        ("InverseObjectProperties(:p :super)", "inverse-properties"),
        (
            "InverseObjectProperties("
            "ObjectInverseOf(:p) ObjectInverseOf(:super))",
            "inverse-properties",
        ),
    ],
    ids=[
        "named-sub-property",
        "inverse-expression-sub-property",
        "named-inverse-properties",
        "inverse-expression-inverse-properties",
    ],
)
def test_private_overlay_stateful_role_axiom_preserves_asserted_taxonomy(
    local_body: str,
    root_family: str,
) -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "SubClassOf(:A :Top) "
            "SubClassOf(:C ObjectSomeValuesFrom(:super :D))"
        ),
    )
    addition_source = cast(pyowl_core.OntologyView, _snapshot(local_body))
    overlay = pyowl_core.apply_delta(
        base,
        pyowl_core.OntologyDelta(
            add_axioms=cast(Any, set(addition_source.iter_axioms())),
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
    assert excluded_root_ids is None
    compiler = prepare_native_encoded_direct(
        base_lease,
        local_delta_lease=top_lease,
        canonical_work_limit=max_work,
        canonical_workspace_limit=max_workspace,
    )
    edges, statistics = compiler.compile_batch(
        bidirectional=False,
        max_edges=1,
        max_iri_bytes=1024,
        asserted_taxonomy_only=True,
        only_taxonomy=True,
    )

    assert [edge.as_tuple() for edge in edges] == [
        (
            "urn:native-integration#A",
            "http://subclassof",
            "urn:native-integration#Top",
        ),
    ]
    is_sub_property = root_family == "sub-property"
    assert statistics.roots == 3
    assert statistics.subclasses == 2
    assert statistics.restriction_subclasses == 1
    assert statistics.sub_object_properties == int(is_sub_property)
    assert statistics.object_property_chains == 0
    assert statistics.inverse_object_properties == int(not is_sub_property)
    assert statistics.role_expansion_edges == 0
    assert statistics.skipped_axioms == 0
    assert statistics.edges == 1


@pytest.mark.parametrize(
    "provider_backend",
    [
        pyowl_core.BackendPreference.PYTHON,
        pyowl_core.BackendPreference.NATIVE,
    ],
    ids=["independent-bytes", "packed-bytes"],
)
@pytest.mark.parametrize(
    ("local_body", "root_family"),
    [
        ('Annotation(<urn:meta> "value")', "ontology-annotation"),
        ("Annotation(<urn:meta> <urn:value>)", "ontology-annotation"),
        (
            'AnnotationAssertion(<urn:meta> <urn:subject> "value")',
            "annotation-assertion",
        ),
        (
            "AnnotationAssertion(<urn:meta> <urn:subject> <urn:value>)",
            "annotation-assertion",
        ),
    ],
    ids=[
        "ontology-literal",
        "ontology-iri",
        "assertion-literal",
        "assertion-iri",
    ],
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
def test_hidden_iterator_compiles_one_state_neutral_local_annotation_root(
    provider_backend: pyowl_core.BackendPreference,
    local_body: str,
    root_family: str,
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
        _snapshot(local_body, backend=provider_backend),
    )
    if root_family == "ontology-annotation":
        added_annotations = set(cast(Any, addition_source).ontology_annotations())
        assert len(added_annotations) == 1
        delta = pyowl_core.OntologyDelta(
            add_ontology_annotations=cast(Any, added_annotations),
            remove_axioms=cast(Any, removed),
        )
    else:
        added_axioms = set(addition_source.iter_axioms())
        assert len(added_axioms) == 1
        delta = pyowl_core.OntologyDelta(
            add_axioms=cast(Any, added_axioms),
            remove_axioms=cast(Any, removed),
        )
    overlay = pyowl_core.apply_delta(base, delta)
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
    assert delta_segment.anonymous_scope_map.nbytes == 0
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
                "state-neutral local annotation root reached scalar traversal"
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
    assert report.provenance.counts.ignored_shapes == 0
    assert report.diagnostics == ()
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
    statistics = compilation.native_statistics
    is_ontology_annotation = root_family == "ontology-annotation"
    assert statistics.roots == len(expected_sources) + 1
    assert statistics.subclasses == len(expected_sources)
    assert statistics.ontology_annotations == int(is_ontology_annotation)
    assert statistics.annotation_assertions == int(not is_ontology_annotation)
    assert statistics.selected_annotation_assertions == int(
        not is_ontology_annotation
    )
    assert statistics.annotation_edges == 0
    assert statistics.non_string_literal_renderings == 0
    assert statistics.skipped_axioms == 0
    assert statistics.edges == len(expected_edges)
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
    ("local_body", "root_family"),
    [
        ('Annotation(<urn:meta> "value")', "ontology-annotation"),
        (
            'AnnotationAssertion(<urn:meta> <urn:subject> "value")',
            "annotation-assertion",
        ),
    ],
    ids=["ontology-annotation", "annotation-assertion"],
)
def test_private_overlay_annotation_root_preserves_asserted_taxonomy(
    local_body: str,
    root_family: str,
) -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot("SubClassOf(:A :Top) SubClassOf(:C :Top)"),
    )
    addition_source = cast(pyowl_core.OntologyView, _snapshot(local_body))
    if root_family == "ontology-annotation":
        delta = pyowl_core.OntologyDelta(
            add_ontology_annotations=cast(
                Any,
                set(cast(Any, addition_source).ontology_annotations()),
            ),
        )
    else:
        delta = pyowl_core.OntologyDelta(
            add_axioms=cast(Any, set(addition_source.iter_axioms())),
        )
    overlay = pyowl_core.apply_delta(base, delta)
    negotiation = select_private_direct_ingestion(
        overlay,
        selected_backend="native",
    )
    top_lease = negotiation.lease
    assert top_lease is not None
    resolved = _resolve_private_single_overlay_delta(top_lease)
    assert resolved is not None
    base_lease, excluded_root_ids, max_work, max_workspace = resolved
    assert excluded_root_ids is None
    compiler = prepare_native_encoded_direct(
        base_lease,
        local_delta_lease=top_lease,
        canonical_work_limit=max_work,
        canonical_workspace_limit=max_workspace,
    )
    edges, statistics = compiler.compile_batch(
        bidirectional=False,
        max_edges=2,
        max_iri_bytes=1024,
        asserted_taxonomy_only=True,
        only_taxonomy=True,
    )

    assert [edge.as_tuple() for edge in edges] == [
        (
            "urn:native-integration#A",
            "http://subclassof",
            "urn:native-integration#Top",
        ),
        (
            "urn:native-integration#C",
            "http://subclassof",
            "urn:native-integration#Top",
        ),
    ]
    is_ontology_annotation = root_family == "ontology-annotation"
    assert statistics.roots == 3
    assert statistics.subclasses == 2
    assert statistics.ontology_annotations == int(is_ontology_annotation)
    assert statistics.annotation_assertions == int(not is_ontology_annotation)
    assert statistics.selected_annotation_assertions == int(
        not is_ontology_annotation
    )
    assert statistics.annotation_edges == 0
    assert statistics.non_string_literal_renderings == 0
    assert statistics.skipped_axioms == 0
    assert statistics.edges == 2


@pytest.mark.parametrize(
    "provider_backend",
    [
        pyowl_core.BackendPreference.PYTHON,
        pyowl_core.BackendPreference.NATIVE,
    ],
    ids=["independent-bytes", "packed-bytes"],
)
@pytest.mark.parametrize(
    ("local_body", "statistics_field"),
    [
        ("DisjointClasses(:A :B)", "disjoint_classes"),
        ("DisjointClasses(:A :B :C)", "disjoint_classes"),
        (
            "DisjointClasses(:A ObjectComplementOf(:B))",
            "disjoint_classes",
        ),
        (
            "DisjointUnion(:Defined :A ObjectUnionOf(:B :C))",
            "disjoint_unions",
        ),
    ],
    ids=["binary", "ternary", "recursive", "defined-union-recursive"],
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
def test_hidden_iterator_compiles_one_silent_local_class_disjointness_axiom(
    provider_backend: pyowl_core.BackendPreference,
    local_body: str,
    statistics_field: str,
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
    assert delta_segment.anonymous_scope_map.nbytes == 0
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
                "silent local class-disjointness axiom reached scalar traversal"
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
    statistics = compilation.native_statistics
    assert statistics.roots == len(expected_sources) + 1
    assert statistics.subclasses == len(expected_sources)
    assert getattr(statistics, statistics_field) == 1
    assert statistics.disjoint_classes + statistics.disjoint_unions == 1
    assert statistics.skipped_axioms == 1
    assert statistics.edges == len(expected_edges)
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
    ("local_body", "statistics_field"),
    [
        (
            "SubAnnotationPropertyOf(:ap :aq)",
            "sub_annotation_properties",
        ),
        (
            "AnnotationPropertyDomain(:ap <urn:domain>)",
            "annotation_property_domains",
        ),
        (
            "AnnotationPropertyRange(:ap <urn:range>)",
            "annotation_property_ranges",
        ),
    ],
    ids=["sub-property", "domain", "range"],
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
def test_hidden_iterator_compiles_one_silent_local_annotation_property_axiom(
    provider_backend: pyowl_core.BackendPreference,
    local_body: str,
    statistics_field: str,
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
    assert delta_segment.anonymous_scope_map.nbytes == 0
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
                "silent local annotation-property axiom reached scalar traversal"
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
    statistics = compilation.native_statistics
    assert statistics.roots == len(expected_sources) + 1
    assert statistics.subclasses == len(expected_sources)
    assert getattr(statistics, statistics_field) == 1
    assert (
        statistics.sub_annotation_properties
        + statistics.annotation_property_domains
        + statistics.annotation_property_ranges
        == 1
    )
    assert statistics.skipped_axioms == 1
    assert statistics.edges == len(expected_edges)
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
    "local_body",
    [
        "HasKey(:KeyClass (:op) ())",
        "HasKey(:KeyClass () (:dp))",
        "HasKey(:KeyClass (:op) (:dp))",
        "HasKey(ObjectSomeValuesFrom(:op :Filler) (ObjectInverseOf(:op)) (:dp))",
    ],
    ids=["object-only", "data-only", "mixed-named", "mixed-recursive-inverse"],
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
def test_hidden_iterator_compiles_one_silent_local_has_key(
    provider_backend: pyowl_core.BackendPreference,
    local_body: str,
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
    assert delta_segment.anonymous_scope_map.nbytes == 0
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
            side_effect=AssertionError("silent local HasKey reached scalar traversal"),
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
    assert compilation.native_statistics.has_keys == 1
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
    ("local_body", "statistics_field"),
    [
        (
            "EquivalentObjectProperties(:op ObjectInverseOf(:oq) :or)",
            "equivalent_object_properties",
        ),
        (
            "DisjointObjectProperties(:op ObjectInverseOf(:oq))",
            "disjoint_object_properties",
        ),
        ("FunctionalObjectProperty(:op)", "functional_object_properties"),
        (
            "InverseFunctionalObjectProperty(ObjectInverseOf(:op))",
            "inverse_functional_object_properties",
        ),
        ("ReflexiveObjectProperty(:op)", "reflexive_object_properties"),
        (
            "IrreflexiveObjectProperty(ObjectInverseOf(:op))",
            "irreflexive_object_properties",
        ),
        ("SymmetricObjectProperty(:op)", "symmetric_object_properties"),
        (
            "AsymmetricObjectProperty(ObjectInverseOf(:op))",
            "asymmetric_object_properties",
        ),
        ("TransitiveObjectProperty(:op)", "transitive_object_properties"),
    ],
    ids=[
        "equivalent-set",
        "disjoint-set",
        "functional",
        "inverse-functional",
        "reflexive",
        "irreflexive",
        "symmetric",
        "asymmetric",
        "transitive",
    ],
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
def test_hidden_iterator_compiles_one_silent_local_object_property_axiom(
    provider_backend: pyowl_core.BackendPreference,
    local_body: str,
    statistics_field: str,
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
    assert delta_segment.anonymous_scope_map.nbytes == 0
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
                "silent local object-property axiom reached scalar traversal"
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
    statistics = compilation.native_statistics
    assert statistics.roots == len(expected_sources) + 1
    assert statistics.subclasses == len(expected_sources)
    assert getattr(statistics, statistics_field) == 1
    assert (
        statistics.equivalent_object_properties
        + statistics.disjoint_object_properties
        + statistics.functional_object_properties
        + statistics.inverse_functional_object_properties
        + statistics.reflexive_object_properties
        + statistics.irreflexive_object_properties
        + statistics.symmetric_object_properties
        + statistics.asymmetric_object_properties
        + statistics.transitive_object_properties
        == 1
    )
    assert statistics.skipped_axioms == 1
    assert statistics.edges == len(expected_edges)
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


@pytest.mark.parametrize(
    "provider_backend",
    [
        pyowl_core.BackendPreference.PYTHON,
        pyowl_core.BackendPreference.NATIVE,
    ],
    ids=["independent-bytes", "packed-bytes"],
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
def test_hidden_iterator_compiles_one_silent_local_sub_data_property_axiom(
    provider_backend: pyowl_core.BackendPreference,
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
            "SubDataPropertyOf(:dp :dq)",
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
                "silent local SubDataPropertyOf reached scalar traversal"
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
    assert compilation.native_statistics.sub_data_properties == 1
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
    "local_body",
    [
        "EquivalentDataProperties(:dp :dq)",
        "EquivalentDataProperties(:dp :dq :dr)",
    ],
    ids=["binary-set", "ternary-set"],
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
def test_hidden_iterator_compiles_one_silent_local_equivalent_data_properties(
    provider_backend: pyowl_core.BackendPreference,
    local_body: str,
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
                "silent local EquivalentDataProperties reached scalar traversal"
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
    assert compilation.native_statistics.equivalent_data_properties == 1
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
    "local_body",
    [
        "DisjointDataProperties(:dp :dq)",
        "DisjointDataProperties(:dp :dq :dr)",
    ],
    ids=["binary-set", "ternary-set"],
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
def test_hidden_iterator_compiles_one_silent_local_disjoint_data_properties(
    provider_backend: pyowl_core.BackendPreference,
    local_body: str,
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
                "silent local DisjointDataProperties reached scalar traversal"
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
    assert compilation.native_statistics.disjoint_data_properties == 1
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
    "local_body",
    [
        "DataPropertyDomain(:dp :Domain)",
        "DataPropertyDomain(:dp "
        "ObjectIntersectionOf(:Domain ObjectUnionOf(:Other :Third)))",
    ],
    ids=["named-domain", "recursive-domain"],
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
def test_hidden_iterator_compiles_one_silent_local_data_property_domain(
    provider_backend: pyowl_core.BackendPreference,
    local_body: str,
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
                "silent local DataPropertyDomain reached scalar traversal"
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
    assert compilation.native_statistics.data_property_domains == 1
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
    "local_body",
    [
        "DataPropertyRange(:dp <http://www.w3.org/2001/XMLSchema#string>)",
        "DataPropertyRange(:dp DataUnionOf("
        "<http://www.w3.org/2001/XMLSchema#string> "
        "DataComplementOf(<http://www.w3.org/2001/XMLSchema#integer>)))",
    ],
    ids=["named-range", "recursive-range"],
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
def test_hidden_iterator_compiles_one_silent_local_data_property_range(
    provider_backend: pyowl_core.BackendPreference,
    local_body: str,
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
                "silent local DataPropertyRange reached scalar traversal"
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
    assert compilation.native_statistics.data_property_ranges == 1
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
    ("removed_sources", "only_taxonomy", "expected_sources"),
    [
        (frozenset(), False, ("A", "C")),
        (frozenset({"C"}), False, ("A",)),
        (frozenset({"A", "C"}), False, ()),
        (frozenset(), True, ("A", "C")),
    ],
    ids=["base-all", "base-exclude", "base-exclude-all", "only-taxonomy"],
)
def test_hidden_iterator_compiles_one_silent_local_functional_data_property(
    provider_backend: pyowl_core.BackendPreference,
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
        _snapshot("FunctionalDataProperty(:dp)", backend=provider_backend),
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
                "silent local FunctionalDataProperty reached scalar traversal"
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
    assert compilation.native_statistics.functional_data_properties == 1
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
    "local_body",
    [
        "DatatypeDefinition(:custom "
        "<http://www.w3.org/2001/XMLSchema#string>)",
        "DatatypeDefinition(:custom DataUnionOf("
        "<http://www.w3.org/2001/XMLSchema#string> "
        "DataComplementOf(<http://www.w3.org/2001/XMLSchema#integer>)))",
    ],
    ids=["named-range", "recursive-range"],
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
def test_hidden_iterator_compiles_one_silent_local_datatype_definition(
    provider_backend: pyowl_core.BackendPreference,
    local_body: str,
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
                "silent local DatatypeDefinition reached scalar traversal"
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
    assert compilation.native_statistics.datatype_definitions == 1
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
    ("constructor", "statistics_field"),
    [
        ("SameIndividual", "same_individuals"),
        ("DifferentIndividuals", "different_individuals"),
    ],
    ids=["same-individual", "different-individuals"],
)
@pytest.mark.parametrize(
    "individual_members",
    [
        ":i :j",
        ":k :i :j",
    ],
    ids=["binary-set", "ternary-set"],
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
def test_hidden_iterator_compiles_one_silent_local_individual_set(
    provider_backend: pyowl_core.BackendPreference,
    constructor: str,
    statistics_field: str,
    individual_members: str,
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
            f"{constructor}({individual_members})",
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
    assert delta_segment.anonymous_scope_map.nbytes == 0
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
                f"silent local {constructor} reached scalar traversal"
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
    assert getattr(compilation.native_statistics, statistics_field) == 1
    assert (
        compilation.native_statistics.same_individuals
        + compilation.native_statistics.different_individuals
        == 1
    )
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


@pytest.mark.parametrize(
    "provider_backend",
    [
        pyowl_core.BackendPreference.PYTHON,
        pyowl_core.BackendPreference.NATIVE,
    ],
    ids=["independent-bytes", "packed-bytes"],
)
@pytest.mark.parametrize(
    ("removed_indices", "only_taxonomy", "bidirectional"),
    [
        ((), False, False),
        ((1, 3, 4), False, False),
        ((), True, False),
        ((), False, True),
    ],
    ids=["base-all", "base-exclude-gaps", "only-taxonomy", "bidirectional"],
)
def test_hidden_iterator_projects_nonempty_multi_root_emitting_overlay(
    provider_backend: pyowl_core.BackendPreference,
    removed_indices: tuple[int, ...],
    only_taxonomy: bool,
    bidirectional: bool,
) -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "SubClassOf(:A :Top) SubClassOf(:Z :Top) "
            "ClassAssertion(:A :a) ClassAssertion(:Z :z) "
            "ObjectPropertyAssertion(:p :a :z)",
            backend=provider_backend,
        ),
    )
    base_axioms = tuple(base.iter_axioms())
    assert len(base_axioms) == 5
    removed = {base_axioms[index] for index in removed_indices}
    addition_source = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "SubClassOf(:B :Top) "
            "SubClassOf(:C ObjectSomeValuesFrom(:p :D)) "
            "SubClassOf(:Y :Top) ClassAssertion(:B :b) "
            "ObjectPropertyAssertion(:q :z :a)",
            backend=provider_backend,
        ),
    )
    added = set(addition_source.iter_axioms())
    assert len(added) == 5
    overlay = pyowl_core.apply_delta(
        base,
        pyowl_core.OntologyDelta(
            add_axioms=cast(Any, added),
            remove_axioms=cast(Any, removed),
        ),
    )
    top_encoded = overlay.view(
        pyowl_core.EncodedStructuralView,
        schema_version=1,
        scope=pyowl_core.AxiomScope.CLOSURE,
    )
    assert tuple(segment.role for segment in top_encoded.segments) == (2, 3)
    assert top_encoded.buffers["root_kinds"].nbytes == 5
    assert top_encoded.buffers["root_ids"].nbytes == 20
    base_segment = cast(Any, top_encoded.segments[0])
    delta_segment = cast(Any, top_encoded.segments[1])
    assert base_segment.posting_mode == (2 if removed else 0)
    assert base_segment.root_ids.nbytes == 4 * len(removed)
    assert delta_segment.posting_mode == 0
    assert delta_segment.root_ids.nbytes == 0
    assert delta_segment.anonymous_scope_map.nbytes == 0
    source_encoded = base_segment.source
    assert source_encoded is not None
    expected_buffer_bytes = sum(
        value.nbytes for value in top_encoded.buffers.values()
    ) + sum(value.nbytes for value in source_encoded.buffers.values())

    python_options = ProjectionOptions(
        backend="python",
        order="encounter",
        only_taxonomy=only_taxonomy,
        bidirectional_taxonomy=bidirectional,
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
                "nonempty multi-root emitting overlay reached scalar traversal"
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

    assert actual == expected
    _assert_semantic_report_parity(expected_report, report)
    assert report.diagnostics == expected_report.diagnostics
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
    statistics = compilation.native_statistics
    assert statistics.roots == 10 - len(removed)
    assert statistics.subclasses == 5 - sum(
        index in removed_indices for index in (0, 1)
    )
    assert statistics.restriction_subclasses == 1
    assert statistics.class_assertions == 3 - sum(
        index in removed_indices for index in (2, 3)
    )
    assert statistics.object_property_assertions == 2 - int(4 in removed_indices)
    assert statistics.skipped_axioms == 0
    assert statistics.edges == len(expected)
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
def test_nonempty_multi_root_emitting_overlay_fails_preoutput_and_retries(
    provider_backend: pyowl_core.BackendPreference,
) -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "SubClassOf(:A :Top) SubClassOf(:Z :Top) "
            "ClassAssertion(:A :a) ClassAssertion(:Z :z) "
            "ObjectPropertyAssertion(:p :a :z)",
            backend=provider_backend,
        ),
    )
    addition_source = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "SubClassOf(:B :Top) "
            "SubClassOf(:C ObjectSomeValuesFrom(:p :D)) "
            "SubClassOf(:Y :Top) ClassAssertion(:B :b) "
            "ObjectPropertyAssertion(:q :z :a)",
            backend=provider_backend,
        ),
    )
    overlay = pyowl_core.apply_delta(
        base,
        pyowl_core.OntologyDelta(
            add_axioms=cast(Any, set(addition_source.iter_axioms())),
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
    assert excluded_root_ids is None

    failing = prepare_native_encoded_direct(
        base_lease,
        local_delta_lease=top_lease,
        canonical_work_limit=max_work,
        canonical_workspace_limit=max_workspace,
    )
    with pytest.raises(ProjectionResourceError, match="configured edge resources"):
        failing.compile_batch(
            bidirectional=False,
            max_edges=9,
            max_iri_bytes=1024,
        )
    assert failing.state == "failed"
    assert failing.retained_buffer_count == 22
    assert failing.cancel() is False

    retry = prepare_native_encoded_direct(
        base_lease,
        local_delta_lease=top_lease,
        canonical_work_limit=max_work,
        canonical_workspace_limit=max_workspace,
    )
    edges, statistics = retry.compile_batch(
        bidirectional=False,
        max_edges=10,
        max_iri_bytes=1024,
    )
    assert len(edges) == 10
    assert statistics.roots == 10
    assert statistics.subclasses == 5
    assert statistics.restriction_subclasses == 1
    assert statistics.class_assertions == 3
    assert statistics.object_property_assertions == 2
    assert statistics.edges == 10
    assert retry.state == "finished"

    asserted = prepare_native_encoded_direct(
        base_lease,
        local_delta_lease=top_lease,
        canonical_work_limit=max_work,
        canonical_workspace_limit=max_workspace,
    )
    asserted_edges, asserted_statistics = asserted.compile_batch(
        bidirectional=False,
        asserted_taxonomy_only=True,
        max_edges=4,
        max_iri_bytes=1024,
    )
    assert [edge.as_tuple() for edge in asserted_edges] == [
        (
            f"urn:native-integration#{source}",
            "http://subclassof",
            "urn:native-integration#Top",
        )
        for source in ("A", "B", "Y", "Z")
    ]
    assert asserted_statistics.roots == 10
    assert asserted_statistics.subclasses == 5
    assert asserted_statistics.restriction_subclasses == 1
    assert asserted_statistics.class_assertions == 3
    assert asserted_statistics.object_property_assertions == 2
    assert asserted_statistics.edges == 4
    assert asserted.state == "finished"


def _two_member_subclass_composite(
    provider_backend: pyowl_core.BackendPreference,
    *,
    remove_left: bool,
) -> pyowl_core.OntologyView:
    left = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "SubClassOf(:A :Top) SubClassOf(:C :Top)",
            backend=provider_backend,
        ),
    )
    right = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "SubClassOf(:B :Top) SubClassOf(:D :Top)",
            backend=provider_backend,
        ),
    )
    removed: set[object] = set()
    if remove_left:
        removed.add(
            next(
                axiom
                for axiom in left.iter_axioms()
                if cast(Any, axiom).sub_class.iri.value.endswith("#C")
            )
        )
    return cast(
        pyowl_core.OntologyView,
        pyowl_core.compose_views(
            left,
            right,
            delta=pyowl_core.OntologyDelta(remove_axioms=cast(Any, removed)),
        ),
    )


def _two_member_duplicate_subclass_composite(
    provider_backend: pyowl_core.BackendPreference,
) -> pyowl_core.OntologyView:
    left = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "SubClassOf(:A :Top) SubClassOf(:B :Top)",
            backend=provider_backend,
        ),
    )
    right = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "SubClassOf(:A :Top) SubClassOf(:C :Top)",
            backend=provider_backend,
        ),
    )
    return cast(
        pyowl_core.OntologyView,
        pyowl_core.compose_views(left, right),
    )


def _two_member_dual_exclude_subclass_composite(
    provider_backend: pyowl_core.BackendPreference,
) -> pyowl_core.OntologyView:
    left = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "SubClassOf(:A :Top) SubClassOf(:B :Top) SubClassOf(:C :Top)",
            backend=provider_backend,
        ),
    )
    right = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "SubClassOf(:A :Top) SubClassOf(:D :Top) SubClassOf(:E :Top)",
            backend=provider_backend,
        ),
    )
    removed = {
        next(
            axiom
            for axiom in left.iter_axioms()
            if cast(Any, axiom).sub_class.iri.value.endswith("#B")
        ),
        next(
            axiom
            for axiom in right.iter_axioms()
            if cast(Any, axiom).sub_class.iri.value.endswith("#E")
        ),
    }
    return cast(
        pyowl_core.OntologyView,
        pyowl_core.compose_views(
            left,
            right,
            delta=pyowl_core.OntologyDelta(remove_axioms=cast(Any, removed)),
        ),
    )


def _three_member_all_exclude_subclass_composite(
    provider_backend: pyowl_core.BackendPreference,
) -> pyowl_core.OntologyView:
    first = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "SubClassOf(:A :Top) SubClassOf(:Drop1 :Top) SubClassOf(:Shared :Top)",
            backend=provider_backend,
        ),
    )
    second = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "SubClassOf(:B :Top) SubClassOf(:Shared :Top)",
            backend=provider_backend,
        ),
    )
    third = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "SubClassOf(:C :Top) SubClassOf(:Drop3a :Top) "
            "SubClassOf(:Drop3b :Top) SubClassOf(:Shared :Top)",
            backend=provider_backend,
        ),
    )
    removed = {
        next(
            axiom
            for axiom in first.iter_axioms()
            if cast(Any, axiom).sub_class.iri.value.endswith("#Drop1")
        ),
        *(
            axiom
            for axiom in third.iter_axioms()
            if cast(Any, axiom).sub_class.iri.value.endswith(("#Drop3a", "#Drop3b"))
        ),
    }
    assert len(removed) == 3
    return cast(
        pyowl_core.OntologyView,
        pyowl_core.compose_views(
            first,
            second,
            third,
            delta=pyowl_core.OntologyDelta(remove_axioms=cast(Any, removed)),
        ),
    )


def _forged_one_include_subclass_composite(
    provider_backend: pyowl_core.BackendPreference,
) -> tuple[pyowl_core.OntologyView, EncodedStructuralLease, memoryview]:
    left = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "SubClassOf(:A :Top) SubClassOf(:B :Top) SubClassOf(:C :Top)",
            backend=provider_backend,
        ),
    )
    right = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "SubClassOf(:A :Top) SubClassOf(:D :Top)",
            backend=provider_backend,
        ),
    )
    removed = {
        axiom
        for axiom in left.iter_axioms()
        if cast(Any, axiom).sub_class.iri.value.endswith(("#B", "#C"))
    }
    assert len(removed) == 2
    composite = cast(
        pyowl_core.OntologyView,
        pyowl_core.compose_views(
            left,
            right,
            delta=pyowl_core.OntologyDelta(remove_axioms=cast(Any, removed)),
        ),
    )
    top_lease = select_private_direct_ingestion(
        composite,
        selected_backend="native",
    ).lease
    assert top_lease is not None
    excluded_index = next(
        index
        for index, segment in enumerate(top_lease.segments)
        if cast(Any, segment).posting_mode == 2
    )
    excluded_segment = cast(Any, top_lease.segments[excluded_index])
    source = excluded_segment.source
    assert source is not None
    root_count = source.buffers["root_kinds"].nbytes
    excluded_positions = {
        int.from_bytes(excluded_segment.root_ids[offset : offset + 4], "little")
        for offset in range(0, excluded_segment.root_ids.nbytes, 4)
    }
    included_positions = [
        position for position in range(1, root_count + 1) if position not in excluded_positions
    ]
    assert included_positions == [1]
    included_root_ids = memoryview(
        b"".join(position.to_bytes(4, "little") for position in included_positions)
    )
    segments = list(top_lease.segments)
    segments[excluded_index] = replace(
        excluded_segment,
        posting_mode=1,
        root_ids=included_root_ids,
    )
    encoded_view = replace(
        cast(Any, top_lease.encoded_view),
        segments=tuple(segments),
    )
    forged_lease = replace(
        top_lease,
        encoded_view=encoded_view,
        segments=tuple(segments),
    )
    return composite, forged_lease, included_root_ids


@pytest.mark.parametrize(
    "provider_backend",
    [
        pyowl_core.BackendPreference.PYTHON,
        pyowl_core.BackendPreference.NATIVE,
    ],
    ids=["independent-bytes", "packed-bytes"],
)
@pytest.mark.parametrize("remove_left", [False, True], ids=["all", "one-exclude"])
def test_hidden_iterator_compiles_exact_two_member_composite_without_flattening(
    provider_backend: pyowl_core.BackendPreference,
    remove_left: bool,
) -> None:
    composite = _two_member_subclass_composite(
        provider_backend,
        remove_left=remove_left,
    )
    top_encoded = composite.view(
        pyowl_core.EncodedStructuralView,
        schema_version=1,
        scope=pyowl_core.AxiomScope.CLOSURE,
    )
    assert tuple(segment.role for segment in top_encoded.segments) == (4, 4)
    assert top_encoded.buffers["root_kinds"].nbytes == 0
    assert top_encoded.buffers["root_ids"].nbytes == 0
    assert sum(segment.root_ids.nbytes for segment in top_encoded.segments) == (
        4 if remove_left else 0
    )
    expected_buffer_bytes = sum(
        value.nbytes for value in top_encoded.buffers.values()
    ) + sum(
        value.nbytes
        for segment in top_encoded.segments
        for value in cast(Any, segment.source).buffers.values()
    )
    python_options = ProjectionOptions(backend="python", order="encounter")
    expected_projector = Projector()
    expected = expected_projector.project(composite, options=python_options)
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
            side_effect=AssertionError("two-member composite reached scalar traversal"),
        ),
    ):
        projector = Projector()
        actual = list(
            projector._iter_native_encoded_edges(
                composite,
                options=replace(python_options, backend="native"),
                buffer_edges=1,
            )
        )
    report = _completed_report(projector)

    expected_sources = ["A", "B", "D"] if remove_left else ["A", "B", "C", "D"]
    assert actual == expected == [
        Edge(
            f"urn:native-integration#{source}",
            "http://subclassof",
            "urn:native-integration#Top",
        )
        for source in expected_sources
    ]
    _assert_semantic_report_parity(expected_report, report)
    assert report.diagnostics == expected_report.diagnostics == ()
    assert len(captured) == len(captured_compilers) == 1
    compilation = captured[0]
    compiler = captured_compilers[0]
    assert compilation.view is composite
    assert compiler.merge_manifest_lease is compilation.container_leases[0]
    assert compiler.merge_manifest_lease is not None
    assert compiler.merge_manifest_lease.owner is composite
    assert compilation.local_delta_lease is compilation.container_leases[1]
    assert compilation.local_delta_lease is not None
    assert compilation.lease.owner is not compilation.local_delta_lease.owner
    excluded_segments = [
        segment for segment in top_encoded.segments if segment.posting_mode == 2
    ]
    assert compilation.excluded_root_ids is (
        excluded_segments[0].root_ids if remove_left else None
    )
    statistics = compilation.native_statistics
    assert statistics.roots == len(expected_sources)
    assert statistics.subclasses == len(expected_sources)
    assert statistics.restriction_subclasses == 0
    assert statistics.skipped_axioms == 0
    assert statistics.edges == len(expected_sources)
    assert compiler.retained_buffer_count == 22 + int(remove_left)
    assert compilation.batches._compiler is None

    ingestion = report.provenance.ingestion
    assert ingestion.path == "encoded-native"
    assert ingestion.reason is None
    assert ingestion.counters["encoded_buffer_count"] == 33
    assert ingestion.counters["encoded_buffer_bytes"] == expected_buffer_bytes
    assert ingestion.counters["encoded_detached_buffer_count"] == (
        22 + int(remove_left)
    )
    assert ingestion.counters["encoded_zero_copy_buffers"] == 33
    assert ingestion.counters["encoded_referenced_view_count"] == 2
    assert ingestion.counters["encoded_segment_count"] == 4
    assert ingestion.counters["encoded_posting_bytes"] == 4 * int(remove_left)
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
def test_two_member_composite_preserves_cancel_limit_and_retry_preoutput(
    provider_backend: pyowl_core.BackendPreference,
) -> None:
    composite = _two_member_subclass_composite(
        provider_backend,
        remove_left=True,
    )
    negotiation = select_private_direct_ingestion(
        composite,
        selected_backend="native",
    )
    top_lease = negotiation.lease
    assert top_lease is not None
    resolved = _resolve_private_two_member_composite(top_lease)
    assert resolved is not None
    (
        base_lease,
        right_lease,
        included,
        excluded,
        right_excluded,
        max_work,
        max_workspace,
    ) = resolved
    assert included is None
    assert excluded is not None
    assert right_excluded is None
    assert max_work is not None
    assert max_workspace is not None

    def compiler(
        *,
        work: int = max_work,
        workspace: int = max_workspace,
    ) -> NativeEncodedDirectCompiler:
        return prepare_native_encoded_direct(
            base_lease,
            local_delta_lease=right_lease,
            merge_manifest_lease=top_lease,
            excluded_root_ids=excluded,
            canonical_work_limit=work,
            canonical_workspace_limit=workspace,
        )

    cancelled = compiler()
    assert cancelled.cancel() is True
    with pytest.raises(NativeEncodedDirectCancelled):
        cancelled.compile_batch(
            bidirectional=False,
            max_edges=3,
            max_iri_bytes=1024,
        )
    assert cancelled.state == "cancelled"
    assert cancelled.retained_buffer_count == 23

    for work, workspace, expected_message in [
        (1, max_workspace, "work"),
        (max_work, 1, "workspace"),
    ]:
        limited = compiler(work=work, workspace=workspace)
        with pytest.raises(ProjectionResourceError) as captured:
            limited.iter_batches(
                bidirectional=False,
                max_edges=3,
                max_iri_bytes=1024,
                batch_edges=1,
            )
        assert captured.value.__cause__ is not None
        assert expected_message in str(captured.value.__cause__)
        assert limited.state == "failed"
        assert limited.retained_buffer_count == 23
        assert limited.coarse_output_chunks == 0
        assert limited.peak_buffered_coarse_edges == 0

    retry = compiler()
    edges, statistics = retry.compile_batch(
        bidirectional=False,
        max_edges=3,
        max_iri_bytes=1024,
    )
    assert [edge.source.rsplit("#", 1)[-1] for edge in edges] == ["A", "B", "D"]
    assert statistics.roots == statistics.subclasses == statistics.edges == 3
    assert retry.state == "finished"
    assert retry.cancel() is False

    unrelated = cast(
        pyowl_core.OntologyView,
        _snapshot("SubClassOf(:Other :Top)", backend=provider_backend),
    )
    unrelated_lease = select_private_direct_ingestion(
        unrelated,
        selected_backend="native",
    ).lease
    assert unrelated_lease is not None
    with pytest.raises(
        SnapshotCompatibilityError,
        match="composite member lost its retained source identity",
    ):
        prepare_native_encoded_direct(
            base_lease,
            local_delta_lease=unrelated_lease,
            merge_manifest_lease=top_lease,
            excluded_root_ids=excluded,
            canonical_work_limit=max_work,
            canonical_workspace_limit=max_workspace,
        )

    excluded_index = next(
        index
        for index, segment in enumerate(top_lease.segments)
        if cast(Any, segment).posting_mode == 2
    )
    include_segments = list(top_lease.segments)
    include_segments[excluded_index] = replace(
        cast(Any, include_segments[excluded_index]),
        posting_mode=1,
    )
    all_index = 1 - excluded_index
    include_segments[all_index] = replace(
        cast(Any, include_segments[all_index]),
        posting_mode=1,
        root_ids=memoryview((1).to_bytes(4, "little")),
    )
    include_encoded = replace(
        cast(Any, top_lease.encoded_view),
        segments=tuple(include_segments),
    )
    include_lease = replace(
        top_lease,
        encoded_view=include_encoded,
        segments=tuple(include_segments),
    )
    assert _resolve_private_two_member_composite(include_lease) is None


@pytest.mark.parametrize(
    "provider_backend",
    [
        pyowl_core.BackendPreference.PYTHON,
        pyowl_core.BackendPreference.NATIVE,
    ],
    ids=["independent-bytes", "packed-bytes"],
)
def test_hidden_iterator_deduplicates_two_member_composite_canonically(
    provider_backend: pyowl_core.BackendPreference,
) -> None:
    composite = _two_member_duplicate_subclass_composite(provider_backend)
    top_encoded = composite.view(
        pyowl_core.EncodedStructuralView,
        schema_version=1,
        scope=pyowl_core.AxiomScope.CLOSURE,
    )
    assert tuple(segment.role for segment in top_encoded.segments) == (4, 4)
    assert all(segment.posting_mode == 0 for segment in top_encoded.segments)
    expected_buffer_bytes = sum(
        value.nbytes for value in top_encoded.buffers.values()
    ) + sum(
        value.nbytes
        for segment in top_encoded.segments
        for value in cast(Any, segment.source).buffers.values()
    )
    python_options = ProjectionOptions(backend="python", order="encounter")
    expected_projector = Projector()
    expected = expected_projector.project(composite, options=python_options)
    expected_report = _completed_report(expected_projector)

    with patch.object(
        api_module,
        "prepare_streaming_compilation",
        side_effect=AssertionError("deduplicated composite reached scalar traversal"),
    ):
        projector = Projector()
        actual = list(
            projector._iter_native_encoded_edges(
                composite,
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
        for source in ["A", "B", "C"]
    ]
    _assert_semantic_report_parity(expected_report, report)
    ingestion = report.provenance.ingestion
    assert ingestion.path == "encoded-native"
    assert ingestion.reason is None
    assert ingestion.counters["encoded_buffer_count"] == 33
    assert ingestion.counters["encoded_buffer_bytes"] == expected_buffer_bytes
    assert ingestion.counters["encoded_detached_buffer_count"] == 22
    assert ingestion.counters["encoded_zero_copy_buffers"] == 33
    assert ingestion.counters["encoded_referenced_view_count"] == 2
    assert ingestion.counters["encoded_segment_count"] == 4
    assert ingestion.counters["encoded_posting_bytes"] == 0
    assert ingestion.counters["base_flattening_bytes"] == 0
    assert ingestion.counters["encoded_staging_copy_bytes"] == 0
    assert ingestion.counters["scalar_axiom_materializations"] == 0
    assert ingestion.counters["scalar_term_materializations"] == 0
    assert ingestion.counters["per_row_ffi_calls"] == 0
    _assert_bounded_native_output(
        ingestion.counters,
        compiled_edges=3,
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
def test_two_member_composite_dedup_preserves_cancel_limits_and_retry(
    provider_backend: pyowl_core.BackendPreference,
) -> None:
    composite = _two_member_duplicate_subclass_composite(provider_backend)
    top_lease = select_private_direct_ingestion(
        composite,
        selected_backend="native",
    ).lease
    assert top_lease is not None
    resolved = _resolve_private_two_member_composite(top_lease)
    assert resolved is not None
    (
        left_lease,
        right_lease,
        included,
        excluded,
        right_excluded,
        max_work,
        max_workspace,
    ) = resolved
    assert included is None
    assert excluded is None
    assert right_excluded is None
    assert max_work is not None
    assert max_workspace is not None

    def compiler(
        *,
        work: int = max_work,
        workspace: int = max_workspace,
    ) -> NativeEncodedDirectCompiler:
        return prepare_native_encoded_direct(
            left_lease,
            local_delta_lease=right_lease,
            merge_manifest_lease=top_lease,
            canonical_work_limit=work,
            canonical_workspace_limit=workspace,
        )

    cancelled = compiler()
    assert cancelled.cancel() is True
    with pytest.raises(NativeEncodedDirectCancelled):
        cancelled.compile_batch(
            bidirectional=False,
            max_edges=3,
            max_iri_bytes=1024,
        )
    assert cancelled.state == "cancelled"
    assert cancelled.retained_buffer_count == 22

    for work, workspace, expected_message in [
        (1, max_workspace, "work"),
        (max_work, 1, "workspace"),
    ]:
        limited = compiler(work=work, workspace=workspace)
        with pytest.raises(ProjectionResourceError) as captured:
            limited.iter_batches(
                bidirectional=False,
                max_edges=3,
                max_iri_bytes=1024,
                batch_edges=1,
            )
        assert captured.value.__cause__ is not None
        assert expected_message in str(captured.value.__cause__)
        assert limited.state == "failed"
        assert limited.coarse_output_chunks == 0
        assert limited.peak_buffered_coarse_edges == 0

    retry = compiler()
    edges, statistics = retry.compile_batch(
        bidirectional=False,
        max_edges=3,
        max_iri_bytes=1024,
    )
    assert [edge.source.rsplit("#", 1)[-1] for edge in edges] == ["A", "B", "C"]
    assert statistics.roots == statistics.subclasses == statistics.edges == 3
    assert retry.state == "finished"
    assert retry.retained_buffer_count == 22
    assert retry.cancel() is False


@pytest.mark.parametrize(
    "provider_backend",
    [
        pyowl_core.BackendPreference.PYTHON,
        pyowl_core.BackendPreference.NATIVE,
    ],
    ids=["independent-bytes", "packed-bytes"],
)
def test_hidden_iterator_compiles_one_exact_include_composite_member(
    provider_backend: pyowl_core.BackendPreference,
) -> None:
    composite, forged_lease, included_root_ids = _forged_one_include_subclass_composite(
        provider_backend
    )
    expected_projector = Projector()
    python_options = ProjectionOptions(backend="python", order="encounter")
    expected = expected_projector.project(composite, options=python_options)
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
            return_value=EncodedNegotiation(
                "encoded-native",
                lease=forged_lease,
            ),
        ),
        patch.object(
            api_module,
            "prepare_native_encoded_compilation",
            side_effect=capture_compilation,
        ),
        patch.object(
            api_module,
            "prepare_streaming_compilation",
            side_effect=AssertionError("one-INCLUDE composite reached scalar traversal"),
        ),
    ):
        projector = Projector()
        actual = list(
            projector._iter_native_encoded_edges(
                composite,
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
        for source in ["A", "D"]
    ]
    _assert_semantic_report_parity(expected_report, report)
    assert len(captured) == 1
    compilation = captured[0]
    assert compilation.included_root_ids is included_root_ids
    assert compilation.excluded_root_ids is None
    assert compilation.native_statistics.roots == 2
    assert compilation.native_statistics.subclasses == 2
    assert compilation.native_statistics.edges == 2
    ingestion = report.provenance.ingestion
    assert ingestion.path == "encoded-native"
    assert ingestion.reason is None
    assert ingestion.counters["encoded_buffer_count"] == 33
    assert ingestion.counters["encoded_detached_buffer_count"] == 23
    assert ingestion.counters["encoded_zero_copy_buffers"] == 33
    assert ingestion.counters["encoded_referenced_view_count"] == 2
    assert ingestion.counters["encoded_segment_count"] == 4
    assert ingestion.counters["encoded_posting_bytes"] == 4
    assert ingestion.counters["base_flattening_bytes"] == 0
    assert ingestion.counters["encoded_staging_copy_bytes"] == 0
    assert ingestion.counters["scalar_axiom_materializations"] == 0
    assert ingestion.counters["scalar_term_materializations"] == 0
    assert ingestion.counters["per_row_ffi_calls"] == 0
    _assert_bounded_native_output(
        ingestion.counters,
        compiled_edges=2,
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
def test_one_include_composite_preserves_identity_cancel_limits_and_retry(
    provider_backend: pyowl_core.BackendPreference,
) -> None:
    _composite, forged_lease, included_root_ids = _forged_one_include_subclass_composite(
        provider_backend
    )
    resolved = _resolve_private_two_member_composite(forged_lease)
    assert resolved is not None
    (
        left_lease,
        right_lease,
        included,
        excluded,
        right_excluded,
        max_work,
        max_workspace,
    ) = resolved
    assert included is included_root_ids
    assert excluded is None
    assert right_excluded is None
    assert max_work is not None
    assert max_workspace is not None

    def compiler(
        *,
        work: int = max_work,
        workspace: int = max_workspace,
    ) -> NativeEncodedDirectCompiler:
        return prepare_native_encoded_direct(
            left_lease,
            local_delta_lease=right_lease,
            merge_manifest_lease=forged_lease,
            included_root_ids=included,
            canonical_work_limit=work,
            canonical_workspace_limit=workspace,
        )

    cancelled = compiler()
    assert cancelled.cancel() is True
    with pytest.raises(NativeEncodedDirectCancelled):
        cancelled.compile_batch(
            bidirectional=False,
            max_edges=2,
            max_iri_bytes=1024,
        )
    assert cancelled.state == "cancelled"
    assert cancelled.retained_buffer_count == 23

    for work, workspace, expected_message in [
        (1, max_workspace, "work"),
        (max_work, 1, "workspace"),
    ]:
        limited = compiler(work=work, workspace=workspace)
        with pytest.raises(ProjectionResourceError) as captured:
            limited.iter_batches(
                bidirectional=False,
                max_edges=2,
                max_iri_bytes=1024,
                batch_edges=1,
            )
        assert captured.value.__cause__ is not None
        assert expected_message in str(captured.value.__cause__)
        assert limited.state == "failed"
        assert limited.coarse_output_chunks == 0
        assert limited.peak_buffered_coarse_edges == 0

    retry = compiler()
    edges, statistics = retry.compile_batch(
        bidirectional=False,
        max_edges=2,
        max_iri_bytes=1024,
    )
    assert [edge.source.rsplit("#", 1)[-1] for edge in edges] == ["A", "D"]
    assert statistics.roots == statistics.subclasses == statistics.edges == 2
    assert retry.state == "finished"
    assert retry.retained_buffer_count == 23

    equal_but_distinct_posting = memoryview(bytes(included_root_ids))
    with pytest.raises(
        SnapshotCompatibilityError,
        match="lost its exact INCLUDE table",
    ):
        prepare_native_encoded_direct(
            left_lease,
            local_delta_lease=right_lease,
            merge_manifest_lease=forged_lease,
            included_root_ids=equal_but_distinct_posting,
            canonical_work_limit=max_work,
            canonical_workspace_limit=max_workspace,
        )
    with pytest.raises(
        ValueError,
        match="cannot combine INCLUDE and EXCLUDE",
    ):
        prepare_native_encoded_direct(
            left_lease,
            local_delta_lease=right_lease,
            merge_manifest_lease=forged_lease,
            included_root_ids=included,
            excluded_root_ids=included,
            canonical_work_limit=max_work,
            canonical_workspace_limit=max_workspace,
        )

    segments = list(forged_lease.segments)
    all_index = next(
        index
        for index, segment in enumerate(segments)
        if cast(Any, segment).posting_mode == 0
    )
    segments[all_index] = replace(
        cast(Any, segments[all_index]),
        posting_mode=1,
        root_ids=memoryview((1).to_bytes(4, "little")),
    )
    two_include_view = replace(
        cast(Any, forged_lease.encoded_view),
        segments=tuple(segments),
    )
    two_include_lease = replace(
        forged_lease,
        encoded_view=two_include_view,
        segments=tuple(segments),
    )
    assert _resolve_private_two_member_composite(two_include_lease) is None


@pytest.mark.parametrize(
    "provider_backend",
    [
        pyowl_core.BackendPreference.PYTHON,
        pyowl_core.BackendPreference.NATIVE,
    ],
    ids=["independent-bytes", "packed-bytes"],
)
def test_hidden_iterator_compiles_two_exact_exclude_composite_members(
    provider_backend: pyowl_core.BackendPreference,
) -> None:
    composite = _two_member_dual_exclude_subclass_composite(provider_backend)
    top_encoded = composite.view(
        pyowl_core.EncodedStructuralView,
        schema_version=1,
        scope=pyowl_core.AxiomScope.CLOSURE,
    )
    assert tuple(segment.role for segment in top_encoded.segments) == (4, 4)
    assert all(segment.posting_mode == 2 for segment in top_encoded.segments)
    assert [segment.root_ids.nbytes for segment in top_encoded.segments] == [4, 4]
    expected_buffer_bytes = sum(
        value.nbytes for value in top_encoded.buffers.values()
    ) + sum(
        value.nbytes
        for segment in top_encoded.segments
        for value in cast(Any, segment.source).buffers.values()
    )
    expected_projector = Projector()
    python_options = ProjectionOptions(backend="python", order="encounter")
    expected = expected_projector.project(composite, options=python_options)
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
            side_effect=AssertionError("dual-EXCLUDE composite reached scalar traversal"),
        ),
    ):
        projector = Projector()
        actual = list(
            projector._iter_native_encoded_edges(
                composite,
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
        for source in ["A", "C", "D"]
    ]
    _assert_semantic_report_parity(expected_report, report)
    assert report.diagnostics == expected_report.diagnostics == ()
    assert len(captured) == len(captured_compilers) == 1
    compilation = captured[0]
    compiler = captured_compilers[0]
    assert compiler.merge_manifest_lease is compilation.container_leases[0]
    assert compiler.merge_manifest_lease is not None
    assert compilation.local_delta_lease is not None
    left_segment = next(
        cast(Any, segment)
        for segment in compiler.merge_manifest_lease.segments
        if cast(Any, segment).source is compilation.lease.encoded_view
    )
    right_segment = next(
        cast(Any, segment)
        for segment in compiler.merge_manifest_lease.segments
        if cast(Any, segment).source is compilation.local_delta_lease.encoded_view
    )
    assert compilation.excluded_root_ids is left_segment.root_ids
    assert compilation.right_excluded_root_ids is right_segment.root_ids
    assert compilation.included_root_ids is None
    assert compilation.native_statistics.roots == 3
    assert compilation.native_statistics.subclasses == 3
    assert compilation.native_statistics.edges == 3
    assert compiler.retained_buffer_count == 24
    assert compilation.batches._compiler is None

    ingestion = report.provenance.ingestion
    assert ingestion.path == "encoded-native"
    assert ingestion.reason is None
    assert ingestion.counters["encoded_buffer_count"] == 33
    assert ingestion.counters["encoded_buffer_bytes"] == expected_buffer_bytes
    assert ingestion.counters["encoded_detached_buffer_count"] == 24
    assert ingestion.counters["encoded_zero_copy_buffers"] == 33
    assert ingestion.counters["encoded_referenced_view_count"] == 2
    assert ingestion.counters["encoded_segment_count"] == 4
    assert ingestion.counters["encoded_posting_bytes"] == 8
    assert ingestion.counters["encoded_indexed_buffer_count"] == 0
    assert ingestion.counters["base_flattening_bytes"] == 0
    assert ingestion.counters["encoded_staging_copy_bytes"] == 0
    assert ingestion.counters["scalar_axiom_materializations"] == 0
    assert ingestion.counters["scalar_term_materializations"] == 0
    assert ingestion.counters["per_row_ffi_calls"] == 0
    _assert_bounded_native_output(
        ingestion.counters,
        compiled_edges=3,
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
def test_two_exclude_composite_preserves_identity_cancel_limits_and_retry(
    provider_backend: pyowl_core.BackendPreference,
) -> None:
    composite = _two_member_dual_exclude_subclass_composite(provider_backend)
    top_lease = select_private_direct_ingestion(
        composite,
        selected_backend="native",
    ).lease
    assert top_lease is not None
    resolved = _resolve_private_two_member_composite(top_lease)
    assert resolved is not None
    (
        left_lease,
        right_lease,
        included,
        excluded,
        right_excluded,
        max_work,
        max_workspace,
    ) = resolved
    assert included is None
    assert excluded is not None
    assert right_excluded is not None
    assert max_work is not None
    assert max_workspace is not None

    def compiler(
        *,
        work: int = max_work,
        workspace: int = max_workspace,
    ) -> NativeEncodedDirectCompiler:
        return prepare_native_encoded_direct(
            left_lease,
            local_delta_lease=right_lease,
            merge_manifest_lease=top_lease,
            excluded_root_ids=excluded,
            right_excluded_root_ids=right_excluded,
            canonical_work_limit=work,
            canonical_workspace_limit=workspace,
        )

    cancelled = compiler()
    assert cancelled.cancel() is True
    with pytest.raises(NativeEncodedDirectCancelled):
        cancelled.compile_batch(
            bidirectional=False,
            max_edges=3,
            max_iri_bytes=1024,
        )
    assert cancelled.state == "cancelled"
    assert cancelled.retained_buffer_count == 24

    for work, workspace, expected_message in [
        (1, max_workspace, "work"),
        (max_work, 1, "workspace"),
    ]:
        limited = compiler(work=work, workspace=workspace)
        with pytest.raises(ProjectionResourceError) as captured:
            limited.iter_batches(
                bidirectional=False,
                max_edges=3,
                max_iri_bytes=1024,
                batch_edges=1,
            )
        assert captured.value.__cause__ is not None
        assert expected_message in str(captured.value.__cause__)
        assert limited.state == "failed"
        assert limited.retained_buffer_count == 24
        assert limited.coarse_output_chunks == 0
        assert limited.peak_buffered_coarse_edges == 0

    retry = compiler()
    edges, statistics = retry.compile_batch(
        bidirectional=False,
        max_edges=3,
        max_iri_bytes=1024,
    )
    assert [edge.source.rsplit("#", 1)[-1] for edge in edges] == ["A", "C", "D"]
    assert statistics.roots == statistics.subclasses == statistics.edges == 3
    assert retry.state == "finished"
    assert retry.retained_buffer_count == 24
    assert retry.cancel() is False

    equal_but_distinct_posting = memoryview(bytes(right_excluded))
    with pytest.raises(
        SnapshotCompatibilityError,
        match="lost its exact EXCLUDE table",
    ):
        prepare_native_encoded_direct(
            left_lease,
            local_delta_lease=right_lease,
            merge_manifest_lease=top_lease,
            excluded_root_ids=excluded,
            right_excluded_root_ids=equal_but_distinct_posting,
            canonical_work_limit=max_work,
            canonical_workspace_limit=max_workspace,
        )
    with pytest.raises(
        SnapshotCompatibilityError,
        match="lost its exact EXCLUDE table",
    ):
        prepare_native_encoded_direct(
            left_lease,
            local_delta_lease=right_lease,
            merge_manifest_lease=top_lease,
            excluded_root_ids=right_excluded,
            right_excluded_root_ids=excluded,
            canonical_work_limit=max_work,
            canonical_workspace_limit=max_workspace,
        )
    with pytest.raises(
        ValueError,
        match="cannot mix INCLUDE and EXCLUDE",
    ):
        prepare_native_encoded_direct(
            left_lease,
            local_delta_lease=right_lease,
            merge_manifest_lease=top_lease,
            included_root_ids=excluded,
            right_excluded_root_ids=right_excluded,
            canonical_work_limit=max_work,
            canonical_workspace_limit=max_workspace,
        )

    segments = list(top_lease.segments)
    segments[0] = replace(cast(Any, segments[0]), posting_mode=1)
    mixed_view = replace(
        cast(Any, top_lease.encoded_view),
        segments=tuple(segments),
    )
    mixed_lease = replace(
        top_lease,
        encoded_view=mixed_view,
        segments=tuple(segments),
    )
    assert _resolve_private_two_member_composite(mixed_lease) is None


@pytest.mark.parametrize(
    "provider_backend",
    [
        pyowl_core.BackendPreference.PYTHON,
        pyowl_core.BackendPreference.NATIVE,
    ],
    ids=["independent-bytes", "packed-bytes"],
)
def test_hidden_iterator_merges_three_all_exclude_members_in_one_native_pass(
    provider_backend: pyowl_core.BackendPreference,
) -> None:
    composite = _three_member_all_exclude_subclass_composite(provider_backend)
    top_encoded = composite.view(
        pyowl_core.EncodedStructuralView,
        schema_version=1,
        scope=pyowl_core.AxiomScope.CLOSURE,
    )
    assert tuple(segment.role for segment in top_encoded.segments) == (4, 4, 4)
    assert sorted(segment.posting_mode for segment in top_encoded.segments) == [0, 2, 2]
    assert sorted(segment.root_ids.nbytes for segment in top_encoded.segments) == [0, 4, 8]
    expected_buffer_bytes = sum(
        value.nbytes for value in top_encoded.buffers.values()
    ) + sum(
        value.nbytes
        for segment in top_encoded.segments
        for value in cast(Any, segment.source).buffers.values()
    )
    python_options = ProjectionOptions(backend="python", order="encounter")
    expected_projector = Projector()
    expected = expected_projector.project(composite, options=python_options)
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
            side_effect=AssertionError("three-member composite reached scalar traversal"),
        ),
    ):
        projector = Projector()
        actual = list(
            projector._iter_native_encoded_edges(
                composite,
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
        for source in ["A", "B", "C", "Shared"]
    ]
    _assert_semantic_report_parity(expected_report, report)
    assert report.diagnostics == expected_report.diagnostics == ()
    assert len(captured) == len(captured_compilers) == 1
    compilation = captured[0]
    compiler = captured_compilers[0]
    assert compilation.view is composite
    assert compiler.merge_manifest_lease is compilation.container_leases[0]
    assert compiler.merge_manifest_lease is not None
    assert compilation.local_delta_lease is compilation.container_leases[1]
    assert compilation.third_member_lease is compilation.container_leases[2]
    assert compilation.local_delta_lease is not None
    assert compilation.third_member_lease is not None
    member_rows = [
        (compilation.lease, compilation.excluded_root_ids),
        (compilation.local_delta_lease, compilation.right_excluded_root_ids),
        (compilation.third_member_lease, compilation.third_excluded_root_ids),
    ]
    for member_lease, selector in member_rows:
        segment = next(
            cast(Any, candidate)
            for candidate in compiler.merge_manifest_lease.segments
            if cast(Any, candidate).source is member_lease.encoded_view
        )
        assert selector is (segment.root_ids if segment.posting_mode == 2 else None)
    statistics = compilation.native_statistics
    assert statistics.roots == 4
    assert statistics.subclasses == 4
    assert statistics.restriction_subclasses == 0
    assert statistics.skipped_axioms == 0
    assert statistics.edges == 4
    assert compiler.retained_buffer_count == 35
    assert compilation.batches._compiler is None

    ingestion = report.provenance.ingestion
    assert ingestion.path == "encoded-native"
    assert ingestion.reason is None
    assert ingestion.counters["encoded_buffer_count"] == 44
    assert ingestion.counters["encoded_buffer_bytes"] == expected_buffer_bytes
    assert ingestion.counters["encoded_detached_buffer_count"] == 35
    assert ingestion.counters["encoded_zero_copy_buffers"] == 44
    assert ingestion.counters["encoded_referenced_view_count"] == 3
    assert ingestion.counters["encoded_segment_count"] == 6
    assert ingestion.counters["encoded_posting_bytes"] == 12
    assert ingestion.counters["encoded_indexed_buffer_count"] == 0
    assert ingestion.counters["base_flattening_bytes"] == 0
    assert ingestion.counters["encoded_staging_copy_bytes"] == 0
    assert ingestion.counters["scalar_axiom_materializations"] == 0
    assert ingestion.counters["scalar_term_materializations"] == 0
    assert ingestion.counters["per_row_ffi_calls"] == 0
    _assert_bounded_native_output(
        ingestion.counters,
        compiled_edges=4,
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
def test_three_member_composite_preserves_selectors_cancel_limits_and_retry(
    provider_backend: pyowl_core.BackendPreference,
) -> None:
    composite = _three_member_all_exclude_subclass_composite(provider_backend)
    top_lease = select_private_direct_ingestion(
        composite,
        selected_backend="native",
    ).lease
    assert top_lease is not None
    resolved = _resolve_private_three_member_composite(top_lease)
    assert resolved is not None
    (
        first_lease,
        second_lease,
        third_lease,
        first_excluded,
        second_excluded,
        third_excluded,
        max_work,
        max_workspace,
    ) = resolved
    assert max_work is not None
    assert max_workspace is not None
    selectors = {
        "excluded_root_ids": first_excluded,
        "right_excluded_root_ids": second_excluded,
        "third_excluded_root_ids": third_excluded,
    }
    assert sorted(value.nbytes for value in selectors.values() if value is not None) == [4, 8]

    def compiler(
        *,
        work: int = max_work,
        workspace: int = max_workspace,
        selected: Mapping[str, memoryview | None] = selectors,
    ) -> NativeEncodedDirectCompiler:
        return prepare_native_encoded_direct(
            first_lease,
            local_delta_lease=second_lease,
            third_member_lease=third_lease,
            merge_manifest_lease=top_lease,
            excluded_root_ids=selected["excluded_root_ids"],
            right_excluded_root_ids=selected["right_excluded_root_ids"],
            third_excluded_root_ids=selected["third_excluded_root_ids"],
            canonical_work_limit=work,
            canonical_workspace_limit=workspace,
        )

    cancelled = compiler()
    assert cancelled.cancel() is True
    with pytest.raises(NativeEncodedDirectCancelled):
        cancelled.compile_batch(
            bidirectional=False,
            max_edges=4,
            max_iri_bytes=1024,
        )
    assert cancelled.state == "cancelled"
    assert cancelled.retained_buffer_count == 35

    for work, workspace, expected_message in [
        (1, max_workspace, "work"),
        (max_work, 1, "workspace"),
    ]:
        limited = compiler(work=work, workspace=workspace)
        with pytest.raises(ProjectionResourceError) as captured:
            limited.iter_batches(
                bidirectional=False,
                max_edges=4,
                max_iri_bytes=1024,
                batch_edges=1,
            )
        assert captured.value.__cause__ is not None
        assert expected_message in str(captured.value.__cause__)
        assert limited.state == "failed"
        assert limited.retained_buffer_count == 35
        assert limited.coarse_output_chunks == 0
        assert limited.peak_buffered_coarse_edges == 0

    retry = compiler()
    edges, statistics = retry.compile_batch(
        bidirectional=False,
        max_edges=4,
        max_iri_bytes=1024,
    )
    assert [edge.source.rsplit("#", 1)[-1] for edge in edges] == ["A", "B", "C", "Shared"]
    assert statistics.roots == statistics.subclasses == statistics.edges == 4
    assert retry.state == "finished"
    assert retry.retained_buffer_count == 35
    assert retry.cancel() is False

    selector_names = [name for name, value in selectors.items() if value is not None]
    assert len(selector_names) == 2
    identity_mismatch = dict(selectors)
    target = selector_names[-1]
    selected_value = identity_mismatch[target]
    assert selected_value is not None
    identity_mismatch[target] = memoryview(bytes(selected_value))
    with pytest.raises(
        SnapshotCompatibilityError,
        match="lost its exact EXCLUDE table",
    ):
        compiler(selected=identity_mismatch)

    swapped = dict(selectors)
    left_name, right_name = selector_names
    swapped[left_name], swapped[right_name] = swapped[right_name], swapped[left_name]
    with pytest.raises(
        SnapshotCompatibilityError,
        match="lost its exact EXCLUDE table",
    ):
        compiler(selected=swapped)

    segments = list(top_lease.segments)
    all_index = next(
        index
        for index, segment in enumerate(segments)
        if cast(Any, segment).posting_mode == 0
    )
    segments[all_index] = replace(
        cast(Any, segments[all_index]),
        posting_mode=1,
        root_ids=memoryview((1).to_bytes(4, "little")),
    )
    mixed_view = replace(
        cast(Any, top_lease.encoded_view),
        segments=tuple(segments),
    )
    mixed_lease = replace(
        top_lease,
        encoded_view=mixed_view,
        segments=tuple(segments),
    )
    assert _resolve_private_three_member_composite(mixed_lease) is None


@pytest.mark.parametrize(
    "provider_backend",
    [
        pyowl_core.BackendPreference.PYTHON,
        pyowl_core.BackendPreference.NATIVE,
    ],
    ids=["independent-bytes", "packed-bytes"],
)
@pytest.mark.parametrize("shape", ["nested", "bridge", "scope-remap"])
def test_hidden_iterator_keeps_adjacent_three_member_shapes_fail_closed(
    provider_backend: pyowl_core.BackendPreference,
    shape: str,
) -> None:
    if shape == "scope-remap":
        members = [
            cast(
                pyowl_core.OntologyView,
                _snapshot("ClassAssertion(:A _:same)", backend=provider_backend),
            )
            for _ in range(3)
        ]
        composite = pyowl_core.compose_views(*members)
    else:
        members = [
            cast(
                pyowl_core.OntologyView,
                _snapshot(f"SubClassOf(:{name} :Top)", backend=provider_backend),
            )
            for name in ("A", "B", "C", "D")
        ]
        if shape == "nested":
            nested = cast(
                pyowl_core.OntologyView,
                pyowl_core.compose_views(members[0], members[1]),
            )
            composite = pyowl_core.compose_views(nested, members[2], members[3])
        else:
            composite = pyowl_core.compose_views(
                *members[:3],
                delta=pyowl_core.OntologyDelta(
                    add_axioms=cast(Any, set(members[3].iter_axioms())),
                ),
            )
    composite = cast(pyowl_core.OntologyView, composite)
    top_lease = select_private_direct_ingestion(
        composite,
        selected_backend="native",
    ).lease
    assert top_lease is not None
    assert _resolve_private_three_member_composite(top_lease) is None
    if shape == "scope-remap":
        assert all(cast(Any, segment).anonymous_scope_map.nbytes for segment in top_lease.segments)
    elif shape == "bridge":
        assert top_lease.buffers["root_kinds"].nbytes == 1
    else:
        # Core preserves composition semantics by flattening the nested source.
        assert len(top_lease.segments) == 4

    python_options = ProjectionOptions(backend="python", order="encounter")
    expected = Projector().project(composite, options=python_options)
    projector = Projector()
    actual = list(
        projector._iter_native_encoded_edges(
            composite,
            options=replace(python_options, backend="native"),
            buffer_edges=1,
        )
    )
    report = _completed_report(projector)

    assert actual == expected
    ingestion = report.provenance.ingestion
    assert ingestion.path == "scalar-native"
    assert ingestion.reason is not None
    assert ingestion.counters.get("native_compiled_edges", 0) == 0
    assert ingestion.counters["encoded_buffer_count"] == 0


@pytest.mark.parametrize(
    "provider_backend",
    [
        pyowl_core.BackendPreference.PYTHON,
        pyowl_core.BackendPreference.NATIVE,
    ],
    ids=["independent-bytes", "packed-bytes"],
)
@pytest.mark.parametrize(
    "shape",
    ["scope-remap", "bridge"],
)
def test_hidden_iterator_keeps_adjacent_composite_shapes_fail_closed(
    provider_backend: pyowl_core.BackendPreference,
    shape: str,
) -> None:
    if shape == "scope-remap":
        left = cast(
            pyowl_core.OntologyView,
            _snapshot("ClassAssertion(:A _:same)", backend=provider_backend),
        )
        right = cast(
            pyowl_core.OntologyView,
            _snapshot("ClassAssertion(:A _:same)", backend=provider_backend),
        )
        composite = pyowl_core.compose_views(left, right)
    else:
        left = cast(
            pyowl_core.OntologyView,
            _snapshot("SubClassOf(:A :Top)", backend=provider_backend),
        )
        right = cast(
            pyowl_core.OntologyView,
            _snapshot("SubClassOf(:B :Top)", backend=provider_backend),
        )
        bridge_source = cast(
            pyowl_core.OntologyView,
            _snapshot("SubClassOf(:C :Top)", backend=provider_backend),
        )
        composite = pyowl_core.compose_views(
            left,
            right,
            delta=pyowl_core.OntologyDelta(
                add_axioms=cast(Any, set(bridge_source.iter_axioms())),
            ),
        )
    composite = cast(pyowl_core.OntologyView, composite)
    python_options = ProjectionOptions(backend="python", order="encounter")
    expected = Projector().project(composite, options=python_options)

    projector = Projector()
    actual = list(
        projector._iter_native_encoded_edges(
            composite,
            options=replace(python_options, backend="native"),
            buffer_edges=1,
        )
    )
    report = _completed_report(projector)

    assert actual == expected
    ingestion = report.provenance.ingestion
    assert ingestion.path == "scalar-native"
    assert ingestion.reason is not None
    assert ingestion.counters.get("native_compiled_edges", 0) == 0
    assert ingestion.counters["encoded_buffer_count"] == 0


@pytest.mark.parametrize(
    "provider_backend",
    [
        pyowl_core.BackendPreference.PYTHON,
        pyowl_core.BackendPreference.NATIVE,
    ],
    ids=["independent-bytes", "packed-bytes"],
)
@pytest.mark.parametrize(
    ("local_body", "expected_statistics"),
    [
        (
            'SubClassOf(Annotation(Annotation(:nested "y") :label "x") :B :C)',
            (2, 2, 0, 0, 0, 2),
        ),
        (
            'SubClassOf(Annotation(:label "x") :B '
            "ObjectSomeValuesFrom(:p :C))",
            (2, 2, 1, 0, 0, 2),
        ),
        (
            'ClassAssertion(Annotation(:label "x") :B :i)',
            (2, 1, 0, 1, 0, 2),
        ),
        (
            'ObjectPropertyAssertion(Annotation(:label "x") :p :i :j)',
            (2, 1, 0, 0, 1, 2),
        ),
        (
            'SubClassOf(Annotation(:label "taxonomy") :B :C) '
            'ClassAssertion(Annotation(:label "type") :D :i) '
            'ObjectPropertyAssertion(Annotation(:label "relation") :q :i :j)',
            (4, 2, 0, 1, 1, 4),
        ),
    ],
    ids=[
        "nested-metadata-taxonomy",
        "metadata-restriction",
        "metadata-class-assertion",
        "metadata-object-assertion",
        "metadata-cross-phase",
    ],
)
def test_hidden_iterator_compiles_metadata_annotated_local_projection_roots(
    provider_backend: pyowl_core.BackendPreference,
    local_body: str,
    expected_statistics: tuple[int, int, int, int, int, int],
) -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot("SubClassOf(:A :B)", backend=provider_backend),
    )
    addition_source = cast(
        pyowl_core.OntologyView,
        _snapshot(local_body, backend=provider_backend),
    )
    added = set(addition_source.iter_axioms())
    overlay = pyowl_core.apply_delta(
        base,
        pyowl_core.OntologyDelta(add_axioms=cast(Any, added)),
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
            side_effect=AssertionError(
                "metadata-annotated local projection reached scalar traversal"
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

    assert actual == expected
    _assert_semantic_report_parity(expected_report, report)
    assert report.diagnostics == expected_report.diagnostics
    assert len(captured) == 1
    statistics = captured[0].native_statistics
    assert (
        statistics.roots,
        statistics.subclasses,
        statistics.restriction_subclasses,
        statistics.class_assertions,
        statistics.object_property_assertions,
        statistics.edges,
    ) == expected_statistics
    assert statistics.skipped_axioms == 0
    assert captured[0].batches._compiler is None

    ingestion = report.provenance.ingestion
    assert ingestion.path == "encoded-native"
    assert ingestion.reason is None
    assert ingestion.counters["scalar_axiom_materializations"] == 0
    assert ingestion.counters["scalar_term_materializations"] == 0
    assert ingestion.counters["per_row_ffi_calls"] == 0
    assert ingestion.counters["base_flattening_bytes"] == 0
    assert ingestion.counters["encoded_staging_copy_bytes"] == 0
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
    ("local_body", "constructor"),
    [
        (
            "SubClassOf(Annotation(:label _:metadata) :B :C)",
            "SubClassOf",
        ),
        (
            "ClassAssertion(Annotation(:label _:metadata) :B :i)",
            "ClassAssertion",
        ),
        (
            "ObjectPropertyAssertion(Annotation(:label _:metadata) :p :i :j)",
            "ObjectPropertyAssertion",
        ),
    ],
    ids=["taxonomy", "class-assertion", "object-assertion"],
)
def test_hidden_iterator_declines_anonymous_local_projection_metadata_preoutput(
    provider_backend: pyowl_core.BackendPreference,
    local_body: str,
    constructor: str,
) -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot("SubClassOf(:A :B)", backend=provider_backend),
    )
    addition_source = cast(
        pyowl_core.OntologyView,
        _snapshot(local_body, backend=provider_backend),
    )
    overlay = pyowl_core.apply_delta(
        base,
        pyowl_core.OntologyDelta(
            add_axioms=cast(Any, set(addition_source.iter_axioms())),
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
    ingestion = report.provenance.ingestion
    assert ingestion.path == "scalar-native"
    assert ingestion.reason is not None
    assert (
        f"{constructor} root annotations require no anonymous individuals "
        "or local scope remap"
    ) in ingestion.reason
    assert ingestion.counters.get("native_compiled_edges", 0) == 0
    assert ingestion.counters["encoded_buffer_count"] == 0


@pytest.mark.parametrize(
    ("local_body", "reason"),
    [
        (
            "SubClassOf(:B :C) SubClassOf(ObjectUnionOf(:D :E) :F)",
            "bounded local-overlay emitting segment requires only named "
            "SubClassOf, ClassAssertion, or ObjectPropertyAssertion roots",
        ),
        (
            "ClassAssertion(:B :i) ClassAssertion(ObjectSomeValuesFrom(:p :C) :j)",
            "bounded local-overlay emitting segment requires only named "
            "SubClassOf, ClassAssertion, or ObjectPropertyAssertion roots",
        ),
        (
            "ClassAssertion(:B :i) ClassAssertion(:C _:anonymous)",
            "bounded local-overlay emitting segment requires only named "
            "SubClassOf, ClassAssertion, or ObjectPropertyAssertion roots",
        ),
        (
            "SubClassOf(:B :C) ClassAssertion(:D :i) Declaration(Class(:E))",
            "bounded local-overlay emitting segment requires only named "
            "SubClassOf, ClassAssertion, or ObjectPropertyAssertion roots",
        ),
        (
            "SubClassOf(:B :C) "
            "ClassAssertion(ObjectSomeValuesFrom(:p :E) :i) "
            "ObjectPropertyAssertion(:q :i :j)",
            "bounded local-overlay emitting segment requires only named "
            "SubClassOf, ClassAssertion, or ObjectPropertyAssertion roots",
        ),
        (
            "SubClassOf(:B :C) ClassAssertion(:D :i) "
            "SubObjectPropertyOf(:p :q)",
            "bounded local-overlay emitting segment requires only named "
            "SubClassOf, ClassAssertion, or ObjectPropertyAssertion roots",
        ),
        (
            "ObjectPropertyDomain(:p :A) ObjectPropertyDomain(:p :B)",
            "bounded local-overlay emitting segment requires only named "
            "SubClassOf, ClassAssertion, or ObjectPropertyAssertion roots",
        ),
        (
            "ObjectPropertyDomain(:p :A) ObjectPropertyRange(:q :B)",
            "bounded two-root local overlay requires its domain and range to use "
            "the same named object property",
        ),
        (
            'ObjectPropertyDomain(Annotation(:label "x") :p :A) '
            "ObjectPropertyRange(:p :B)",
            "bounded local-overlay ObjectPropertyDomain root must be unannotated",
        ),
        (
            "EquivalentClasses(:A :B)",
            "bounded local-overlay EquivalentClasses root requires an ignored "
            "complete direct projection",
        ),
        (
            "EquivalentClasses(:A ObjectIntersectionOf(:B :C))",
            "bounded local-overlay EquivalentClasses root requires an ignored "
            "complete direct projection",
        ),
        (
            'EquivalentClasses(Annotation(:label "x") :A '
            "ObjectComplementOf(:B))",
            "bounded local-overlay EquivalentClasses root must be unannotated",
        ),
        (
            "EquivalentClasses(:A ObjectOneOf(_:anonymous))",
            "bounded local-overlay ignored EquivalentClasses root requires no "
            "anonymous individuals or local scope remap",
        ),
        (
            "EquivalentClasses(:A ObjectSomeValuesFrom(:p :B) "
            "ObjectComplementOf(:C) ObjectHasSelf(:q))",
            "bounded local-overlay EquivalentClasses root requires a canonical "
            "binary or ternary ignored class-expression set",
        ),
        (
            'ObjectPropertyDomain(Annotation(:label "x") '
            "ObjectInverseOf(:p) :A)",
            "bounded local-overlay ObjectPropertyDomain root must be unannotated",
        ),
        (
            'ObjectPropertyRange(Annotation(:label "x") :p '
            "ObjectComplementOf(:A))",
            "bounded local-overlay ObjectPropertyRange root must be unannotated",
        ),
        (
            "ObjectPropertyDomain(:p ObjectOneOf(_:anonymous))",
            "bounded local-overlay ignored ObjectPropertyDomain root requires no "
            "anonymous individuals or local scope remap",
        ),
        (
            "ObjectPropertyRange(:p ObjectOneOf(_:anonymous))",
            "bounded local-overlay ignored ObjectPropertyRange root requires no "
            "anonymous individuals or local scope remap",
        ),
        (
            'SubObjectPropertyOf(Annotation(:label "x") '
            "ObjectPropertyChain(:p :q) :r)",
            "bounded local-overlay SubObjectPropertyOf root must be unannotated",
        ),
        (
            'SubObjectPropertyOf(Annotation(:label "x") :p :q)',
            "bounded local-overlay SubObjectPropertyOf root must be unannotated",
        ),
        (
            'InverseObjectProperties(Annotation(:label "x") :p :q)',
            "bounded local-overlay InverseObjectProperties root must be unannotated",
        ),
        (
            'AnnotationAssertion(Annotation(:label "x") '
            '<urn:meta> <urn:subject> "value")',
            "bounded local-overlay AnnotationAssertion root must be unannotated",
        ),
        (
            'AnnotationAssertion(<urn:meta> _:anonymous "value")',
            "bounded local-overlay AnnotationAssertion root requires no anonymous "
            "individuals or local scope remap",
        ),
        (
            'SubClassOf(Annotation(:label "x") :B ObjectSomeValuesFrom('
            ":p ObjectIntersectionOf(:C :D)))",
            "bounded local-overlay SubClassOf root must be unannotated",
        ),
        (
            "ClassAssertion(:B _:anonymous)",
            "bounded local-overlay ignored ClassAssertion root requires no anonymous "
            "individuals or local scope remap",
        ),
        (
            'ClassAssertion(Annotation(:label "x") '
            "ObjectSomeValuesFrom(:p :B) :i)",
            "bounded local-overlay ClassAssertion root must be unannotated",
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
            'SubDataPropertyOf(Annotation(:label "x") :dp :dq)',
            "bounded local-overlay SubDataPropertyOf root must be unannotated",
        ),
        (
            'EquivalentDataProperties(Annotation(:label "x") :dp :dq)',
            "bounded local-overlay EquivalentDataProperties root must be unannotated",
        ),
        (
            'DisjointDataProperties(Annotation(:label "x") :dp :dq)',
            "bounded local-overlay DisjointDataProperties root must be unannotated",
        ),
        (
            'DataPropertyDomain(Annotation(:label "x") :dp :Domain)',
            "bounded local-overlay DataPropertyDomain root must be unannotated",
        ),
        (
            "DataPropertyRange(Annotation(:label \"x\") :dp "
            "<http://www.w3.org/2001/XMLSchema#string>)",
            "bounded local-overlay DataPropertyRange root must be unannotated",
        ),
        (
            'FunctionalDataProperty(Annotation(:label "x") :dp)',
            "bounded local-overlay FunctionalDataProperty root must be unannotated",
        ),
        (
            "DatatypeDefinition(Annotation(:label \"x\") :custom "
            "<http://www.w3.org/2001/XMLSchema#string>)",
            "bounded local-overlay DatatypeDefinition root must be unannotated",
        ),
        (
            'HasKey(Annotation(:label "x") :KeyClass (:op) (:dp))',
            "bounded local-overlay HasKey root must be unannotated",
        ),
        (
            'EquivalentObjectProperties(Annotation(:label "x") :p :q)',
            "bounded local-overlay EquivalentObjectProperties root must be unannotated",
        ),
        (
            'DisjointObjectProperties(Annotation(:label "x") :p :q)',
            "bounded local-overlay DisjointObjectProperties root must be unannotated",
        ),
        (
            'FunctionalObjectProperty(Annotation(:label "x") :p)',
            "bounded local-overlay FunctionalObjectProperty root must be unannotated",
        ),
        (
            'InverseFunctionalObjectProperty(Annotation(:label "x") :p)',
            "bounded local-overlay InverseFunctionalObjectProperty root must be unannotated",
        ),
        (
            'ReflexiveObjectProperty(Annotation(:label "x") :p)',
            "bounded local-overlay ReflexiveObjectProperty root must be unannotated",
        ),
        (
            'IrreflexiveObjectProperty(Annotation(:label "x") :p)',
            "bounded local-overlay IrreflexiveObjectProperty root must be unannotated",
        ),
        (
            'SymmetricObjectProperty(Annotation(:label "x") :p)',
            "bounded local-overlay SymmetricObjectProperty root must be unannotated",
        ),
        (
            'AsymmetricObjectProperty(Annotation(:label "x") :p)',
            "bounded local-overlay AsymmetricObjectProperty root must be unannotated",
        ),
        (
            'TransitiveObjectProperty(Annotation(:label "x") :p)',
            "bounded local-overlay TransitiveObjectProperty root must be unannotated",
        ),
        (
            "EquivalentObjectProperties(:p :q :r :s)",
            "bounded local-overlay EquivalentObjectProperties root requires a canonical "
            "binary or ternary object-property-expression set",
        ),
        (
            "DisjointObjectProperties(:p :q :r :s)",
            "bounded local-overlay DisjointObjectProperties root requires a canonical "
            "binary or ternary object-property-expression set",
        ),
        (
            'SubAnnotationPropertyOf(Annotation(:label "x") :ap :aq)',
            "bounded local-overlay SubAnnotationPropertyOf root must be unannotated",
        ),
        (
            'AnnotationPropertyDomain(Annotation(:label "x") :ap <urn:domain>)',
            "bounded local-overlay AnnotationPropertyDomain root must be unannotated",
        ),
        (
            'AnnotationPropertyRange(Annotation(:label "x") :ap <urn:range>)',
            "bounded local-overlay AnnotationPropertyRange root must be unannotated",
        ),
        (
            'DisjointClasses(Annotation(:label "x") :A :B)',
            "bounded local-overlay DisjointClasses root must be unannotated",
        ),
        (
            "DisjointClasses(:A :B :C :D)",
            "bounded local-overlay DisjointClasses root requires a canonical binary "
            "or ternary class-expression set",
        ),
        (
            'DisjointUnion(Annotation(:label "x") :Defined :A :B)',
            "bounded local-overlay DisjointUnion root must be unannotated",
        ),
        (
            "DisjointUnion(:Defined :A :B :C :D)",
            "bounded local-overlay DisjointUnion root requires a canonical binary or "
            "ternary class-expression set",
        ),
        (
            'SameIndividual(Annotation(:label "x") :i :j)',
            "bounded local-overlay SameIndividual root must be unannotated",
        ),
        (
            "SameIndividual(:i _:anonymous)",
            "bounded local-overlay SameIndividual root requires named individuals",
        ),
        (
            "SameIndividual(:i :j :k :l)",
            "bounded local-overlay SameIndividual root requires a canonical binary or "
            "ternary named-individual set",
        ),
        (
            'DifferentIndividuals(Annotation(:label "x") :i :j)',
            "bounded local-overlay DifferentIndividuals root must be unannotated",
        ),
        (
            "DifferentIndividuals(:i _:anonymous)",
            "bounded local-overlay DifferentIndividuals root requires named individuals",
        ),
        (
            "DifferentIndividuals(:i :j :k :l)",
            "bounded local-overlay DifferentIndividuals root requires a canonical binary "
            "or ternary named-individual set",
        ),
        (
            'Declaration(Annotation(:label "x") Class(:D))',
            "bounded local-overlay Declaration root must be unannotated",
        ),
    ],
    ids=[
        "ignored-taxonomy-local-subclasses",
        "named-and-complex-local-class-assertions",
        "named-and-anonymous-local-class-assertions",
        "three-roots-with-declaration",
        "three-roots-with-complex-class-assertion",
        "three-roots-with-state-mutating-role",
        "two-local-domains",
        "two-local-properties",
        "annotated-local-domain-range-pair",
        "projecting-pair-local-equivalent-classes",
        "projecting-aggregate-local-equivalent-classes",
        "annotated-ignored-local-equivalent-classes",
        "anonymous-ignored-local-equivalent-classes",
        "oversized-ignored-local-equivalent-classes",
        "annotated-ignored-local-object-property-domain",
        "annotated-ignored-local-object-property-range",
        "anonymous-ignored-local-object-property-domain",
        "anonymous-ignored-local-object-property-range",
        "annotated-local-object-property-chain",
        "annotated-local-sub-object-property",
        "annotated-local-inverse-object-properties",
        "annotated-local-annotation-assertion",
        "anonymous-local-annotation-assertion",
        "annotated-complex-local-restriction-filler",
        "anonymous-local-class-assertion",
        "annotated-complex-local-class-assertion",
        "anonymous-local-object-property-assertion",
        "annotated-local-negative-object-property-assertion",
        "anonymous-local-negative-object-property-assertion",
        "annotated-local-data-property-assertion",
        "anonymous-local-data-property-assertion",
        "annotated-local-negative-data-property-assertion",
        "anonymous-local-negative-data-property-assertion",
        "annotated-local-sub-data-property-axiom",
        "annotated-local-equivalent-data-properties",
        "annotated-local-disjoint-data-properties",
        "annotated-local-data-property-domain",
        "annotated-local-data-property-range",
        "annotated-local-functional-data-property",
        "annotated-local-datatype-definition",
        "annotated-local-has-key",
        "annotated-local-equivalent-object-properties",
        "annotated-local-disjoint-object-properties",
        "annotated-local-functional-object-property",
        "annotated-local-inverse-functional-object-property",
        "annotated-local-reflexive-object-property",
        "annotated-local-irreflexive-object-property",
        "annotated-local-symmetric-object-property",
        "annotated-local-asymmetric-object-property",
        "annotated-local-transitive-object-property",
        "oversized-local-equivalent-object-properties",
        "oversized-local-disjoint-object-properties",
        "annotated-local-sub-annotation-property",
        "annotated-local-annotation-property-domain",
        "annotated-local-annotation-property-range",
        "annotated-local-disjoint-classes",
        "oversized-local-disjoint-classes",
        "annotated-local-disjoint-union",
        "oversized-local-disjoint-union",
        "annotated-local-same-individual",
        "anonymous-local-same-individual",
        "oversized-local-same-individual",
        "annotated-local-different-individuals",
        "anonymous-local-different-individuals",
        "oversized-local-different-individuals",
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


@pytest.mark.parametrize(
    ("local_body", "reason"),
    [
        (
            'Annotation(Annotation(<urn:nested> "x") <urn:meta> "value")',
            "bounded local-overlay ontology Annotation root must have no nested "
            "annotations",
        ),
        (
            "Annotation(<urn:meta> _:anonymous)",
            "bounded local-overlay ontology Annotation root requires no anonymous "
            "individuals or local scope remap",
        ),
    ],
    ids=["nested-ontology-annotation", "anonymous-ontology-annotation"],
)
def test_hidden_iterator_keeps_unsupported_local_ontology_annotation_on_fallback(
    local_body: str,
    reason: str,
) -> None:
    base = cast(
        pyowl_core.OntologyView,
        _snapshot(
            "Declaration(Class(:A)) Declaration(Class(:B)) SubClassOf(:A :B)"
        ),
    )
    addition_source = cast(pyowl_core.OntologyView, _snapshot(local_body))
    annotations = set(cast(Any, addition_source).ontology_annotations())
    assert len(annotations) == 1
    overlay = pyowl_core.apply_delta(
        base,
        pyowl_core.OntologyDelta(
            add_ontology_annotations=cast(Any, annotations),
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
