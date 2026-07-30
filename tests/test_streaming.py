from __future__ import annotations

import gc
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import warnings
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pyowl_core import CancellationSource, OperationCancelledError
from pyowl_core.model import IRI, Class, EntityKind, SubClassOf

from pyowl2vec_star_projector import (
    BATCH_SINK_PROTOCOL_VERSION,
    Edge,
    ProjectionOptions,
    ProjectionResourceError,
    Projector,
    SpillMetrics,
    StreamingLimits,
    probe_native_backend,
)
from pyowl2vec_star_projector.artifact import json_record
from pyowl2vec_star_projector.streaming import (
    _restrict_owner_file_permissions,
    _SpillWorkspace,
    iter_edge_policy,
)

from .support.core_views import Capabilities, ConformingView, fixture_view


def _canonical_options(**changes: Any) -> ProjectionOptions:
    return ProjectionOptions(backend="python", order="canonical", **changes)


def test_private_file_permissions_tolerate_platform_without_fchmod(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(os, "fchmod")
    _restrict_owner_file_permissions(-1)


def test_external_sort_is_invariant_across_buffers_fan_in_and_duplicate_policy(
    tmp_path: Path,
) -> None:
    view = fixture_view("domain-range")
    for duplicates in ("preserve", "unique"):
        expected = Projector().project(
            view,
            options=_canonical_options(duplicates=duplicates),
        )
        for buffer_edges, fan_in in ((1, 2), (2, 3), (7, 4), (100, 8)):
            parent = tmp_path / f"{duplicates}-{buffer_edges}-{fan_in}"
            parent.mkdir()
            projector = Projector()
            actual = list(
                projector.iter_edges(
                    view,
                    options=_canonical_options(duplicates=duplicates),
                    buffer_edges=buffer_edges,
                    temp_directory=parent,
                    streaming_limits=StreamingLimits(
                        merge_fan_in=fan_in,
                        max_open_files=fan_in + 1,
                    ),
                )
            )
            assert actual == expected
            assert list(parent.iterdir()) == []
            assert projector.last_spill_metrics.runs_created >= 1
            if buffer_edges == 1:
                assert projector.last_spill_metrics.merge_passes >= 1


def test_native_and_python_use_identical_external_bytes_when_native_available(
    tmp_path: Path,
) -> None:
    if not probe_native_backend().available:
        pytest.skip("optional native extension is unavailable")
    view = fixture_view("kitchen-sink")
    outputs: dict[str, bytes] = {}
    for backend in ("python", "native"):
        destination = io.BytesIO()
        Projector().write_artifact(
            view,
            destination,
            options=ProjectionOptions(backend=backend),
            buffer_edges=2,
            temp_directory=tmp_path,
            streaming_limits=StreamingLimits(merge_fan_in=2, max_open_files=3),
        )
        outputs[backend] = destination.getvalue()
        assert list(tmp_path.iterdir()) == []
    assert outputs["python"] == outputs["native"]


def test_artifact_bytes_exclude_backend_fallback_execution_state(tmp_path: Path) -> None:
    view = fixture_view("domain-range")
    outputs: dict[str, bytes] = {}
    for backend in ("python", "auto"):
        destination = io.BytesIO()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = Projector().write_artifact(
                view,
                destination,
                options=ProjectionOptions(backend=backend),
                buffer_edges=2,
                temp_directory=tmp_path,
            )
        outputs[backend] = destination.getvalue()
        metadata = json.loads(outputs[backend].splitlines()[0])
        assert metadata["counts"]["warnings"] == 0
        assert "NATIVE_BACKEND_FALLBACK" not in metadata["warning_summary"]
        if backend == "auto":
            assert result.report.provenance.counts.warnings == 1
    assert outputs["auto"] == outputs["python"]

    wire_view = ConformingView(view.documents, wire_verified=True)
    wire_destination = io.BytesIO()
    Projector().write_artifact(
        wire_view,
        wire_destination,
        options=ProjectionOptions(backend="python"),
        buffer_edges=2,
        temp_directory=tmp_path,
    )
    assert wire_destination.getvalue() == outputs["python"]


def test_encounter_iterator_reaches_first_edge_without_full_traversal(tmp_path: Path) -> None:
    axioms = tuple(
        SubClassOf(
            Class(IRI(f"urn:stream:A{index:05d}")),
            Class(IRI(f"urn:stream:B{index:05d}")),
        )
        for index in range(500)
    )

    class LazyView:
        capabilities = Capabilities()
        structural_fingerprint = "s"
        logical_fingerprint = "l"
        signature_fingerprint = "g"

        def __init__(self) -> None:
            self.visited = 0

        def iter_axioms(
            self,
            axiom_type: type[object] | None = None,
            *,
            scope: object = "closure",
        ) -> object:
            del scope

            def generate() -> object:
                for axiom in axioms:
                    if axiom_type is None or type(axiom) is axiom_type:
                        self.visited += 1
                        yield axiom

            return generate()

        def signature(
            self,
            kind: EntityKind | None = None,
            *,
            scope: object = "closure",
            include_builtins: bool = True,
        ) -> tuple[()]:
            del kind, scope, include_builtins
            return ()

    view = LazyView()
    iterator = Projector().iter_edges(
        view,
        options=ProjectionOptions(backend="python", order="encounter"),
        temp_directory=tmp_path,
    )
    assert next(iterator) == Edge("urn:stream:A00000", "http://subclassof", "urn:stream:B00000")
    assert view.visited == 1
    iterator.close()
    assert list(tmp_path.iterdir()) == []


def test_encounter_duplicate_index_spills_exactly_at_the_buffer_bound(tmp_path: Path) -> None:
    raw = [Edge(f"urn:s:{index % 7}", "urn:r", f"urn:d:{index % 5}") for index in range(80)]
    for duplicates in ("preserve", "unique"):
        statistics = SimpleNamespace(raw_edges=0, distinct_edges=0, duplicate_edges=0)
        metrics: list[SpillMetrics] = []
        actual = list(
            iter_edge_policy(
                raw,
                duplicates=duplicates,
                order="encounter",
                buffer_edges=3,
                temp_directory=tmp_path,
                limits=StreamingLimits(cancellation_check_interval=1),
                statistics=statistics,
                metrics_sink=metrics.append,
            )
        )
        expected = raw if duplicates == "preserve" else list(dict.fromkeys(raw))
        assert actual == expected
        assert statistics.raw_edges == len(raw)
        assert statistics.distinct_edges == len(set(raw))
        assert statistics.duplicate_edges == len(raw) - len(set(raw))
        assert metrics and metrics[0].peak_live_bytes > 0
        assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "limits, expected_limit",
    [
        (StreamingLimits(max_total_edges=2), "max_total_edges"),
        (StreamingLimits(max_spill_bytes=1), "max_spill_bytes"),
        (StreamingLimits(max_temporary_bytes=1), "max_temporary_bytes"),
    ],
)
def test_resource_limits_are_typed_and_cleanup(
    tmp_path: Path,
    limits: StreamingLimits,
    expected_limit: str,
) -> None:
    with pytest.raises(ProjectionResourceError) as raised:
        list(
            Projector().iter_edges(
                fixture_view("domain-range"),
                options=_canonical_options(),
                buffer_edges=2,
                temp_directory=tmp_path,
                streaming_limits=limits,
            )
        )
    assert raised.value.details["limit"] == expected_limit
    assert list(tmp_path.iterdir()) == []


def test_private_run_permissions_names_content_and_close_cleanup(tmp_path: Path) -> None:
    iterator = Projector().iter_edges(
        fixture_view("domain-range"),
        options=_canonical_options(),
        buffer_edges=2,
        temp_directory=tmp_path,
        streaming_limits=StreamingLimits(merge_fan_in=2, max_open_files=3),
    )
    assert next(iterator)
    sessions = list(tmp_path.iterdir())
    assert len(sessions) == 1
    session = sessions[0]
    assert stat.S_IMODE(session.stat().st_mode) == 0o700
    runs = list(session.iterdir())
    assert runs
    for run in runs:
        assert run.name.startswith("run-")
        assert stat.S_IMODE(run.stat().st_mode) == 0o600
        assert os.fsencode(tmp_path) not in run.read_bytes()
    iterator.close()
    assert list(tmp_path.iterdir()) == []

    collected = Projector().iter_edges(
        fixture_view("domain-range"),
        options=_canonical_options(),
        buffer_edges=2,
        temp_directory=tmp_path,
    )
    assert next(collected)
    del collected
    gc.collect()
    assert list(tmp_path.iterdir()) == []


def test_cancel_failure_disk_full_and_corrupt_run_all_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancellation = CancellationSource()
    iterator = Projector().iter_edges(
        fixture_view("domain-range"),
        options=_canonical_options(),
        buffer_edges=2,
        temp_directory=tmp_path,
        streaming_limits=StreamingLimits(
            merge_fan_in=2,
            max_open_files=3,
            cancellation_check_interval=1,
        ),
        cancellation_token=cancellation.token,
    )
    assert next(iterator)
    cancellation.cancel("P4 test")
    with pytest.raises(OperationCancelledError):
        next(iterator)
    assert list(tmp_path.iterdir()) == []

    def failing_edges() -> object:
        yield Edge("a", "r", "b")
        yield Edge("c", "r", "d")
        raise RuntimeError("injected backend failure")

    with pytest.raises(RuntimeError, match="injected backend"):
        list(
            iter_edge_policy(
                failing_edges(),
                duplicates="preserve",
                order="canonical",
                buffer_edges=1,
                temp_directory=tmp_path,
                limits=StreamingLimits(),
            )
        )
    assert list(tmp_path.iterdir()) == []

    import pyowl2vec_star_projector.streaming as streaming

    monkeypatch.setattr(streaming.shutil, "disk_usage", lambda path: SimpleNamespace(free=0))
    with pytest.raises(ProjectionResourceError) as disk_full:
        list(
            iter_edge_policy(
                [Edge("a", "r", "b")],
                duplicates="preserve",
                order="canonical",
                buffer_edges=1,
                temp_directory=tmp_path,
                limits=StreamingLimits(),
            )
        )
    assert disk_full.value.details["available"] == 0
    assert list(tmp_path.iterdir()) == []
    monkeypatch.undo()

    workspace = _SpillWorkspace(tmp_path, StreamingLimits())
    run = workspace.write_sorted_run(
        [Edge("a", "r", "b")],
        duplicates="preserve",
        cancellation_token=None,
    )
    with run.path.open("r+b") as stream:
        stream.seek(-1, os.SEEK_END)
        value = stream.read(1)
        stream.seek(-1, os.SEEK_END)
        stream.write(bytes([value[0] ^ 0xFF]))
    with pytest.raises(ProjectionResourceError, match="corrupt private run"):
        list(workspace.merge((run,), cancellation_token=None))
    workspace.cleanup()
    assert list(tmp_path.iterdir()) == []


def test_keyboard_interrupt_and_controlled_process_shutdown_cleanup(tmp_path: Path) -> None:
    def interrupted() -> object:
        yield Edge("a", "r", "b")
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        list(
            iter_edge_policy(
                interrupted(),
                duplicates="preserve",
                order="canonical",
                buffer_edges=1,
                temp_directory=tmp_path,
                limits=StreamingLimits(),
            )
        )
    assert list(tmp_path.iterdir()) == []

    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(sys.path)
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; from pyowl2vec_star_projector import Edge, StreamingLimits; "
                "from pyowl2vec_star_projector.streaming import iter_edge_policy; "
                "p=sys.argv[1]; it=iter_edge_policy([Edge('b','r','c'),Edge('a','r','d')],"
                "duplicates='preserve',order='canonical',buffer_edges=1,temp_directory=p,"
                "limits=StreamingLimits()); next(it); keep=it"
            ),
            str(tmp_path),
        ],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr
    assert list(tmp_path.iterdir()) == []


def test_batch_sink_v1_is_backpressured_and_sink_failure_cleans(tmp_path: Path) -> None:
    class Sink:
        protocol_version = BATCH_SINK_PROTOCOL_VERSION

        def __init__(self) -> None:
            self.batches: list[tuple[Edge, ...]] = []
            self.report: object | None = None

        def write_batch(self, batch: tuple[Edge, ...]) -> None:
            self.batches.append(batch)

        def finish(self, report: object) -> None:
            self.report = report

    sink = Sink()
    projector = Projector()
    report = projector.project_to_sink(
        fixture_view("domain-range"),
        sink,
        options=_canonical_options(),
        batch_size=3,
        buffer_edges=2,
        temp_directory=tmp_path,
    )
    assert sink.report is report
    assert sink.batches and all(0 < len(batch) <= 3 for batch in sink.batches)
    assert sum(map(len, sink.batches)) == report.provenance.counts.edges
    assert list(tmp_path.iterdir()) == []

    def fail(batch: tuple[Edge, ...]) -> None:
        del batch
        raise RuntimeError("sink failure")

    with pytest.raises(RuntimeError, match="sink failure"):
        Projector().project_to_sink(
            fixture_view("domain-range"),
            fail,
            options=_canonical_options(),
            batch_size=1,
            buffer_edges=2,
            temp_directory=tmp_path,
        )
    assert list(tmp_path.iterdir()) == []


def test_artifact_metadata_digest_and_path_are_portable_and_atomic(tmp_path: Path) -> None:
    spill = tmp_path / "spill"
    spill.mkdir()
    destination = io.BytesIO()
    projector = Projector()
    result = projector.write_artifact(
        fixture_view("domain-range"),
        destination,
        options=_canonical_options(),
        buffer_edges=2,
        temp_directory=spill,
        streaming_limits=StreamingLimits(merge_fan_in=2, max_open_files=3),
    )
    artifact = destination.getvalue()
    records = artifact.splitlines(keepends=True)
    metadata = json.loads(records[0])
    assert metadata["schema"] == "pyowl-projector.edge-list/1"
    assert metadata["counts"]["edges"] == len(records) - 1 == result.edge_count
    assert "timestamp" not in records[0].decode("utf-8").lower()
    assert hashlib.sha256(b"".join(records[1:])).hexdigest() == result.canonical_edges_sha256
    preimage_metadata = dict(metadata)
    preimage_metadata.pop("artifact_sha256")
    expected_artifact_digest = hashlib.sha256(
        json_record(preimage_metadata) + b"".join(records[1:])
    ).hexdigest()
    assert expected_artifact_digest == metadata["artifact_sha256"] == result.artifact_sha256
    assert artifact.endswith(b"\n")
    assert list(spill.iterdir()) == []

    target = tmp_path / "edges.jsonl"
    path_result = Projector().write_artifact(
        fixture_view("domain-range"),
        target,
        options=_canonical_options(),
        buffer_edges=3,
        temp_directory=spill,
    )
    assert target.read_bytes() == artifact
    assert path_result.artifact_sha256 == result.artifact_sha256
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert not list(tmp_path.glob(".pyowl2vec-artifact-*"))

    digest = Projector().canonical_digest(
        fixture_view("domain-range"),
        options=ProjectionOptions(backend="python", order="encounter"),
        buffer_edges=1,
        temp_directory=spill,
    )
    assert digest.sha256 == result.canonical_edges_sha256
    assert digest.edge_count == result.edge_count
    assert list(spill.iterdir()) == []

    class FailingWriter:
        def __init__(self) -> None:
            self.calls = 0

        def write(self, value: bytes) -> int:
            self.calls += 1
            if self.calls > 1:
                raise OSError("injected artifact failure")
            return len(value)

    with pytest.raises(ProjectionResourceError, match="artifact I/O failed"):
        Projector().write_artifact(
            fixture_view("domain-range"),
            FailingWriter(),  # type: ignore[arg-type]
            options=_canonical_options(),
            buffer_edges=2,
            temp_directory=spill,
        )
    assert list(spill.iterdir()) == []

    class ImpossibleProgressWriter:
        def write(self, value: bytes) -> int:
            return len(value) + 1

    with pytest.raises(ProjectionResourceError, match="artifact I/O failed"):
        Projector().write_artifact(
            fixture_view("domain-range"),
            ImpossibleProgressWriter(),  # type: ignore[arg-type]
            options=_canonical_options(),
            buffer_edges=2,
            temp_directory=spill,
        )

    class NoProgressWriter:
        def write(self, value: bytes) -> None:
            del value

    with pytest.raises(ProjectionResourceError, match="artifact I/O failed"):
        Projector().write_artifact(
            fixture_view("domain-range"),
            NoProgressWriter(),  # type: ignore[arg-type]
            options=_canonical_options(),
            buffer_edges=2,
            temp_directory=spill,
        )
    assert list(spill.iterdir()) == []
