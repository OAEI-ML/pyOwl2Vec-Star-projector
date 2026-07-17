from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from pyowl2vec_star_projector import (
    Edge,
    NativeBackendFallbackWarning,
    ProjectionError,
    ProjectionOptions,
    ProjectionResourceError,
    Projector,
    probe_native_backend,
)
from pyowl2vec_star_projector import backend as backend_module
from pyowl2vec_star_projector.compiler import CompileStatistics
from pyowl2vec_star_projector.native import (
    iter_native_policy,
    load_native_module,
)

from .support.core_views import GOLDENS, fixture_view

INVENTORY = json.loads(
    (Path(__file__).parent / "fixtures" / "oracle" / "inventory.json").read_text("utf-8")
)
NATIVE_AVAILABLE = probe_native_backend().available
NATIVE_REQUIRED = os.environ.get("PYOWL2VEC_REQUIRE_NATIVE_TESTS") == "1"

if NATIVE_REQUIRED and not NATIVE_AVAILABLE:
    raise RuntimeError("PYOWL2VEC_REQUIRE_NATIVE_TESTS=1 but the native extension is unavailable")

pytestmark = pytest.mark.skipif(
    not NATIVE_AVAILABLE,
    reason="optional native extension is not installed",
)


def _outcome(view: object, options: ProjectionOptions) -> tuple[object, object]:
    projector = Projector()
    try:
        edges = projector.project(view, options=options)
    except ProjectionError as error:
        return (
            "error",
            (type(error), error.code, str(error), dict(error.details)),
        )
    assert projector.last_report is not None
    return "ok", (edges, projector.last_report.to_dict())


def _semantic_report(report: dict[str, object]) -> dict[str, object]:
    normalized = json.loads(json.dumps(report))
    provenance = normalized["provenance"]
    provenance.pop("selected_backend")
    provenance.pop("native_implementation_version")
    provenance["options"].pop("backend")
    return normalized


@pytest.mark.parametrize(
    "entry",
    [entry for entry in INVENTORY["fixtures"] if entry["id"] != "imports-missing"],
    ids=lambda entry: entry["id"],
)
def test_native_matches_python_for_every_fixture_policy(entry: dict[str, Any]) -> None:
    golden = json.loads((GOLDENS / f"{entry['id']}.json").read_text("utf-8"))
    for case in golden["cases"].values():
        flags = case["options"]
        for duplicates in ("preserve", "unique"):
            for order in ("canonical", "encounter"):
                common = {
                    "bidirectional_taxonomy": flags["bidirectional_taxonomy"],
                    "only_taxonomy": flags["only_taxonomy"],
                    "include_literals": flags["include_literals"],
                    "duplicates": duplicates,
                    "order": order,
                }
                python = _outcome(
                    fixture_view(entry["id"], entry["document"]),
                    ProjectionOptions(backend="python", **common),
                )
                native = _outcome(
                    fixture_view(entry["id"], entry["document"]),
                    ProjectionOptions(backend="native", **common),
                )
                assert python[0] == native[0]
                if python[0] == "error":
                    assert python[1] == native[1]
                    continue
                python_edges, python_report = python[1]
                native_edges, native_report = native[1]
                assert python_edges == native_edges
                assert _semantic_report(python_report) == _semantic_report(native_report)
                assert native_report["provenance"]["selected_backend"] == "native"
                assert native_report["provenance"]["native_implementation_version"]


@pytest.mark.parametrize("session", INVENTORY["sessions"], ids=lambda entry: entry["id"])
def test_native_matches_python_stateful_sessions(session: dict[str, Any]) -> None:
    golden = json.loads((GOLDENS / f"{session['id']}.json").read_text("utf-8"))
    for case in golden["cases"].values():
        flags = case["options"]
        python = Projector()
        native = Projector()
        python_options = ProjectionOptions(
            backend="python", compatibility_state="scala-instance", **flags
        )
        native_options = ProjectionOptions(
            backend="native", compatibility_state="scala-instance", **flags
        )
        for document in session["documents"]:
            fixture_id = Path(document).stem
            python_edges = python.project(
                fixture_view(fixture_id, document), options=python_options
            )
            native_edges = native.project(
                fixture_view(fixture_id, document), options=native_options
            )
            assert python_edges == native_edges
            assert python.last_report is not None and native.last_report is not None
            assert _semantic_report(python.last_report.to_dict()) == _semantic_report(
                native.last_report.to_dict()
            )


def test_generated_edge_policies_are_batch_size_invariant() -> None:
    alphabet = ["a", "z", "\x00", "\n", "é", "e\u0301", "東", "🦉"]
    for seed in range(40):
        randomizer = random.Random(seed)
        raw = [
            Edge(
                randomizer.choice(alphabet) + str(randomizer.randrange(7)),
                randomizer.choice(alphabet) + str(randomizer.randrange(5)),
                randomizer.choice(alphabet) + str(randomizer.randrange(9)),
            )
            for _ in range(randomizer.randrange(0, 220))
        ]
        for duplicates in ("preserve", "unique"):
            expected = raw if duplicates == "preserve" else list(dict.fromkeys(raw))
            for order in ("encounter", "canonical"):
                ordered = (
                    sorted(expected, key=Edge.canonical_key) if order == "canonical" else expected
                )
                for batch_edges in (1, 2, 7, 64, 1_000):
                    stats = CompileStatistics()
                    actual = list(
                        iter_native_policy(
                            iter(raw),
                            duplicates=duplicates,
                            order=order,
                            batch_edges=batch_edges,
                            statistics=stats,
                        )
                    )
                    assert actual == ordered
                    assert stats.raw_edges == len(raw)
                    assert stats.distinct_edges == len(set(raw))
                    assert stats.duplicate_edges == len(raw) - len(set(raw))


def test_native_taxonomy_matches_all_python_policies() -> None:
    view = fixture_view("domain-range")
    for bidirectional in (False, True):
        for duplicates in ("preserve", "unique"):
            for order in ("encounter", "canonical"):
                expected = Projector().project_taxonomy(
                    view,
                    bidirectional=bidirectional,
                    duplicates=duplicates,
                    order=order,
                    backend="python",
                )
                actual = Projector().project_taxonomy(
                    view,
                    bidirectional=bidirectional,
                    duplicates=duplicates,
                    order=order,
                    backend="native",
                )
                assert actual == expected


def test_native_preserves_view_identity_and_fingerprints() -> None:
    view = fixture_view("kitchen-sink")
    before = (
        view.structural_fingerprint,
        view.logical_fingerprint,
        view.signature_fingerprint,
    )
    projector = Projector()
    assert projector.project(view, options=ProjectionOptions(backend="native"))
    assert projector.last_view is view
    assert before == (
        view.structural_fingerprint,
        view.logical_fingerprint,
        view.signature_fingerprint,
    )


def test_auto_keeps_experimental_native_opt_in_and_warns_once() -> None:
    backend_module._fallback_warning_emitted = False
    view = fixture_view("equivalence-ordering")
    with pytest.warns(NativeBackendFallbackWarning) as caught:
        first = Projector()
        first.project(view, options=ProjectionOptions(backend="auto"))
        Projector().project(view, options=ProjectionOptions(backend="auto"))
    assert len(caught) == 1
    assert "backend='python'" in str(caught[0].message)
    assert first.last_report is not None
    assert first.last_report.provenance.selected_backend == "python"
    assert first.last_report.provenance.counts.warnings == 1


def test_native_projector_is_reentrant_across_threads() -> None:
    view = fixture_view("rbox-collisions")
    options = ProjectionOptions(backend="native", compatibility_state="isolated")
    expected = Projector().project(view, options=options)
    projector = Projector()
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: projector.project(view, options=options), range(16)))
    assert all(result == expected for result in results)


def test_iterator_close_cancels_and_releases_native_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pyowl2vec_star_projector.native as bridge

    real = load_native_module()
    processors: list[Any] = []

    class CapturingModule:
        @staticmethod
        def EdgeBatchProcessor(order: str, duplicates: str) -> Any:
            processor = real.EdgeBatchProcessor(order, duplicates)
            processors.append(processor)
            return processor

    monkeypatch.setattr(bridge, "load_native_module", lambda: CapturingModule)
    iterator = iter_native_policy(
        (Edge(str(index), "r", "x") for index in range(20)),
        duplicates="preserve",
        order="canonical",
        batch_edges=3,
    )
    assert next(iterator)
    iterator.close()
    assert processors and processors[0].cancelled
    assert processors[0].drained


def test_panics_and_allocation_failures_are_typed_at_python_boundary() -> None:
    import pyowl2vec_star_projector.native as bridge

    module = load_native_module()
    panic_processor = module.EdgeBatchProcessor("encounter", "preserve")
    with pytest.raises(ProjectionError) as panic:
        bridge._call(panic_processor.test_injected_panic)
    assert panic.value.details["native_exception"] == "RuntimeError"

    limited = module.EdgeBatchProcessor("encounter", "preserve", 1)
    with pytest.raises(ProjectionResourceError) as allocation:
        bridge._call(lambda: limited.push_batch([("a", "r", "b"), ("c", "r", "d")]))
    assert allocation.value.details["native_exception"] == "MemoryError"
    assert limited.stats == (0, 0, 0)


def test_unfinished_processor_is_safe_during_interpreter_shutdown() -> None:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(sys.path)
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from pyowl2vec_star_projector._native import EdgeBatchProcessor; "
                "p=EdgeBatchProcessor('canonical','preserve'); "
                "p.push_batch([('a','r','b')])"
            ),
        ],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr


def test_native_required_ci_mode_cannot_silently_skip() -> None:
    if NATIVE_REQUIRED:
        assert probe_native_backend().available
