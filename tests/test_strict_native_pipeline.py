from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pyowl_core
import pytest

import pyowl2vec_star_projector.api as api
import pyowl2vec_star_projector.encoded as encoded
import pyowl2vec_star_projector.native as native
from pyowl2vec_star_projector import Edge, ProjectionOptions, Projector, StreamingLimits
from pyowl2vec_star_projector.errors import (
    InvalidProjectionOptionsError,
    NativeBackendUnavailableError,
    ProjectionError,
    ProjectionResourceError,
    SnapshotCompatibilityError,
)

pytestmark = pytest.mark.skipif(
    not callable(getattr(pyowl_core, "native_validation_report", None)),
    reason="candidate core native validation capability is required",
)


def snapshot(*, backend=pyowl_core.BackendPreference.NATIVE):
    return pyowl_core.load_snapshot(
        b"Prefix(:=<urn:strict#>) Ontology(<urn:strict> "
        b"Declaration(Class(:A)) Declaration(Class(:B)) "
        b"SubClassOf(:A :B) "
        b'SubClassOf(Annotation(<urn:meta> "duplicate") :A :B) '
        b"SubClassOf(:B :C) "
        b'AnnotationAssertion(<http://www.w3.org/2000/01/rdf-schema#label> :A "caf\xc3\xa9") '
        b'AnnotationAssertion(<http://www.w3.org/2000/01/rdf-schema#label> :B "b"))',
        options=pyowl_core.LoadOptions(backend=backend, imports=pyowl_core.ImportPolicy.IGNORE),
    )


@pytest.mark.parametrize("buffer_edges", [1, 2, 7, 100])
@pytest.mark.parametrize("duplicates", ["preserve", "unique"])
@pytest.mark.parametrize("include_literals", [False, True])
def test_strict_public_native_pipeline_preserves_edges_and_semantic_report(
    buffer_edges, duplicates, include_literals, tmp_path
):
    view = snapshot()
    options = ProjectionOptions(
        backend="python", duplicates=duplicates, include_literals=include_literals
    )
    baseline = Projector()
    expected = list(baseline.iter_edges(view, options=options, buffer_edges=buffer_edges))
    projector = Projector()
    strict = replace(options, backend="auto", require_native_pipeline=True)
    with (
        patch.object(api, "prepare_streaming_compilation", side_effect=AssertionError("scalar")),
        patch.object(
            api, "prepare_encoded_subset_compilation", side_effect=AssertionError("indexed")
        ),
        patch.object(api, "iter_edge_policy", side_effect=AssertionError("Python canonical")),
        patch.object(
            encoded, "_validate_column_references", side_effect=AssertionError("Python validation")
        ),
        patch.object(
            native,
            "_acquire_root_encoded_lease",
            side_effect=AssertionError("redundant ROOT publication"),
        ),
    ):
        actual = list(
            projector.iter_edges(
                view,
                options=strict,
                buffer_edges=buffer_edges,
                temp_directory=tmp_path,
                streaming_limits=StreamingLimits(merge_fan_in=2, max_open_files=3),
            )
        )
    assert actual == expected
    assert projector.last_view is view
    assert projector.last_report.provenance.counts == baseline.last_report.provenance.counts
    assert projector.last_report.diagnostics == baseline.last_report.diagnostics
    counters = projector.last_report.provenance.ingestion.counters
    assert counters["native_validation_receipt"] is True
    assert counters["native_canonical_published_edges"] == len(actual)
    assert counters["native_canonical_peak_reserved_bytes"] <= 64 * 1024**2
    assert counters["native_canonical_sort_calls"] >= 1
    assert not list(tmp_path.iterdir())


def test_strict_rejects_incompatible_configuration_and_scalar_owner_before_compile():
    with pytest.raises(InvalidProjectionOptionsError, match="conflicts"):
        ProjectionOptions(backend="python", require_native_pipeline=True)
    with pytest.raises(InvalidProjectionOptionsError, match="canonical"):
        ProjectionOptions(order="encounter", require_native_pipeline=True)
    view = snapshot(backend=pyowl_core.BackendPreference.PYTHON)
    with patch.object(
        api, "prepare_native_encoded_compilation", side_effect=AssertionError("compiled")
    ):
        with pytest.raises(SnapshotCompatibilityError):
            Projector().project(view, options=ProjectionOptions(require_native_pipeline=True))


@pytest.mark.parametrize("limit", ["max_spill_bytes", "max_temporary_bytes", "native_buffer_bytes"])
def test_strict_resource_failure_cleans_outputs(limit, tmp_path):
    kwargs = {limit: 1 if limit == "native_buffer_bytes" else 0}
    projector = Projector()
    with pytest.raises(ProjectionResourceError):
        list(
            projector.iter_edges(
                snapshot(),
                options=ProjectionOptions(require_native_pipeline=True),
                buffer_edges=1,
                temp_directory=tmp_path,
                streaming_limits=StreamingLimits(**kwargs),
            )
        )
    assert projector.last_report is None
    assert not list(tmp_path.iterdir())


def test_final_edge_mutation_is_rejected_without_a_success_report(tmp_path):
    projector = Projector()

    def corrupt(edge):
        object.__setattr__(edge, "source", "changed")

    with patch.object(native, "_NATIVE_ENCODED_EDGE_ALLOCATION_PROBE", corrupt):
        with pytest.raises(ProjectionError) as failure:
            list(
                projector.iter_edges(
                    snapshot(),
                    options=ProjectionOptions(require_native_pipeline=True),
                    buffer_edges=1,
                    temp_directory=tmp_path,
                )
            )
    assert "canonical Edge allocation changed" in str(failure.value.__cause__)
    assert projector.last_report is None
    assert not list(tmp_path.iterdir())


def test_native_spill_format_remains_readable_by_existing_reader(tmp_path):
    # Stop immediately before public output to inspect an actual native run.
    from pyowl2vec_star_projector.streaming import _Run, _RunReader

    view = snapshot()
    options = ProjectionOptions(backend="native", include_literals=True)
    ingestion = encoded.select_ingestion(
        view, selected_backend="native", native_features=frozenset({encoded.ENCODED_NATIVE_FEATURE})
    )
    compilation, reason = native.prepare_native_encoded_compilation(
        view,
        ingestion.lease,
        options,
        batch_edges=1,
        max_total_edges=None,
        cancellation_token=None,
    )
    assert reason is None
    output = compilation.compiler._kernel.canonicalize_batches(
        str(tmp_path), 1, 1024**2, 2, 3, 2**63 - 1, 2**63 - 1, False
    )
    try:
        import struct

        for path in tmp_path.iterdir():
            content = path.read_bytes()
            _, count, payload, _ = struct.unpack(">15sQQ32s", content[:63])
            reader = _RunReader(_Run(Path(path), count, payload, len(content)))
            try:
                edges = list(reader)
            finally:
                reader.close()
            assert edges == sorted(edges, key=Edge.canonical_key)
    finally:
        output.close()
        compilation.batches.close()
    assert not list(tmp_path.iterdir())


def test_imported_class_membership_keeps_root_only_annotation_selection(tmp_path):
    label = b"<http://www.w3.org/2000/01/rdf-schema#label>"
    root = (
        b"Ontology(<urn:root> Import(<urn:leaf>) "
        b"AnnotationAssertion(" + label + b' <urn:L> "root-on-imported-class"))'
    )
    leaf = (
        b"Ontology(<urn:leaf> Declaration(Class(<urn:L>)) "
        b"SubClassOf(<urn:L> <urn:A>) AnnotationAssertion(" + label + b' <urn:L> "import-only"))'
    )
    view = pyowl_core.load_snapshot(
        root,
        options=pyowl_core.LoadOptions(
            backend=pyowl_core.BackendPreference.NATIVE,
            imports=pyowl_core.ImportPolicy.RESOLVE_LOCAL,
        ),
        resolver=pyowl_core.MappingResolver({"urn:leaf": leaf}),
    )
    assert not pyowl_core.encoded_scopes_equivalent(
        view, pyowl_core.AxiomScope.ROOT, pyowl_core.AxiomScope.CLOSURE
    )
    baseline = Projector().project(
        view, options=ProjectionOptions(backend="python", include_literals=True)
    )
    with patch.object(
        encoded, "_validate_column_references", side_effect=AssertionError("Python validation")
    ):
        actual = list(
            Projector().iter_edges(
                view,
                options=ProjectionOptions(require_native_pipeline=True, include_literals=True),
                buffer_edges=1,
                temp_directory=tmp_path,
            )
        )
    assert actual == baseline
    assert any(edge.destination == "root-on-imported-class" for edge in actual)
    assert all(edge.destination != "import-only" for edge in actual)


@pytest.mark.parametrize("bidirectional", [False, True])
@pytest.mark.parametrize("duplicates", ["preserve", "unique"])
def test_public_strict_asserted_taxonomy_matches_reference(bidirectional, duplicates, tmp_path):
    from pyowl2vec_star_projector import iter_taxonomy_edges, project_taxonomy

    root = (
        b"Prefix(:=<urn:tax#>) Ontology(<urn:tax> Import(<urn:leaf>) "
        b"SubClassOf(:A :B) SubClassOf(:A ObjectSomeValuesFrom(:p :B)) "
        b'AnnotationAssertion(<http://www.w3.org/2000/01/rdf-schema#label> :A "label"))'
    )
    leaf = b"Ontology(<urn:leaf> SubClassOf(<urn:tax#B> <urn:tax#C>))"
    view = pyowl_core.load_snapshot(
        root,
        options=pyowl_core.LoadOptions(
            backend=pyowl_core.BackendPreference.NATIVE,
            imports=pyowl_core.ImportPolicy.RESOLVE_LOCAL,
        ),
        resolver=pyowl_core.MappingResolver({"urn:leaf": leaf}),
    )
    expected = Projector().project_taxonomy(
        view, backend="python", bidirectional=bidirectional, duplicates=duplicates
    )
    kwargs = dict(
        require_native_pipeline=True,
        bidirectional=bidirectional,
        duplicates=duplicates,
        buffer_edges=1,
        temp_directory=tmp_path,
    )
    with (
        patch.object(api, "iter_edge_policy", side_effect=AssertionError("Python canonical")),
        patch.object(api, "prepare_streaming_compilation", side_effect=AssertionError("scalar")),
        patch.object(
            encoded, "_validate_column_references", side_effect=AssertionError("Python columns")
        ),
    ):
        assert Projector().project_taxonomy(view, **kwargs) == expected
        assert list(Projector().iter_taxonomy_edges(view, **kwargs)) == expected
        assert project_taxonomy(view, **kwargs) == expected
        assert list(iter_taxonomy_edges(view, **kwargs)) == expected
    assert len(expected) == (4 if bidirectional else 2)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("duplicates", ["preserve", "unique"])
def test_native_canonical_digest_and_artifact_payload_match(duplicates, tmp_path):
    import io

    view = snapshot()
    options = ProjectionOptions(backend="python", include_literals=True, duplicates=duplicates)
    strict = replace(options, backend="auto", require_native_pipeline=True)
    baseline = Projector().canonical_digest(view, options=options, buffer_edges=1)
    actual = Projector().canonical_digest(view, options=strict, buffer_edges=1)
    assert (actual.sha256, actual.edge_count, actual.duplicate_count) == (
        baseline.sha256,
        baseline.edge_count,
        baseline.duplicate_count,
    )
    old = io.BytesIO()
    new = io.BytesIO()
    Projector().write_artifact(view, old, options=options, buffer_edges=1, temp_directory=tmp_path)
    result = Projector().write_artifact(
        view, new, options=strict, buffer_edges=1, temp_directory=tmp_path
    )
    assert result.canonical_edges_sha256 == baseline.sha256
    assert old.getvalue().splitlines()[1:] == new.getvalue().splitlines()[1:]
    assert not list(tmp_path.iterdir())


def test_sink_failure_and_late_cancellation_clean_native_runs(tmp_path):
    class Token:
        cancelled = False

        def check(self):
            if self.cancelled:
                raise RuntimeError("fixture cancellation")

    for cancel in [False, True]:
        token = Token()
        projector = Projector()

        def sink(batch, cancel=cancel, token=token):
            if cancel:
                token.cancelled = True
            else:
                raise RuntimeError("fixture sink failure")

        with pytest.raises(RuntimeError, match="fixture"):
            projector.project_to_sink(
                snapshot(),
                sink,
                options=ProjectionOptions(require_native_pipeline=True),
                batch_size=1,
                buffer_edges=1,
                temp_directory=tmp_path,
                cancellation_token=token,
            )
        assert projector.last_report is None
        assert not list(tmp_path.iterdir())


def test_public_strict_preflight_rejects_old_binaries_without_loading(monkeypatch):
    from pyowl2vec_star_projector import (
        NativeBackendUnavailableError,
        require_native_pipeline_support,
    )

    require_native_pipeline_support()
    monkeypatch.setattr(pyowl_core, "load_snapshot", lambda *args, **kwargs: pytest.fail("parsed"))
    monkeypatch.setattr(pyowl_core, "native_validation_available", lambda: False)
    with pytest.raises(NativeBackendUnavailableError, match="core binary"):
        require_native_pipeline_support()


@pytest.mark.parametrize("taxonomy", [False, True])
@pytest.mark.parametrize("failure", ["decline", "unsupported", "unavailable"])
def test_strict_compiler_unavailable_preserves_error_category(taxonomy, failure):
    view = snapshot()
    projector = Projector()

    def fail(*args, **kwargs):
        if failure == "unsupported":
            raise native.NativeEncodedDirectUnsupported("fixture unsupported")
        if failure == "unavailable":
            raise NativeBackendUnavailableError("fixture unavailable")
        return None, "fixture decline"

    with (
        patch.object(api, "prepare_native_encoded_compilation", fail),
        patch.object(api, "prepare_streaming_compilation", side_effect=AssertionError("scalar")),
        patch.object(
            api, "prepare_encoded_subset_compilation", side_effect=AssertionError("indexed")
        ),
        pytest.raises(NativeBackendUnavailableError, match="fixture"),
    ):
        if taxonomy:
            projector.project_taxonomy(view, require_native_pipeline=True)
        else:
            projector.project(view, options=ProjectionOptions(require_native_pipeline=True))
    assert projector.last_report is None
