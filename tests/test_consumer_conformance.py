from __future__ import annotations

import json
import os
from pathlib import Path

import pyowl_core
import pytest

from pyowl2vec_star_projector import (
    CONSUMER_CONFORMANCE_SCHEMA,
    ConsumerConformanceError,
    SnapshotProviderProbe,
    consumer_conformance_cases,
    consumer_conformance_fixture,
    consumer_conformance_fixture_metadata,
    probe_native_backend,
    verify_consumer_conformance,
)

ROOT = Path(__file__).resolve().parents[1]


def _snapshot() -> pyowl_core.OntologySnapshot:
    metadata = consumer_conformance_fixture_metadata()
    return pyowl_core.load_snapshot(
        consumer_conformance_fixture(),
        document_iri=metadata.document_iri,
        options=pyowl_core.LoadOptions(
            backend=pyowl_core.BackendPreference.PYTHON,
            format=pyowl_core.DocumentFormat.FUNCTIONAL,
        ),
    )


def test_packaged_fixture_and_goldens_are_self_consistent() -> None:
    metadata = consumer_conformance_fixture_metadata()
    assert metadata.resource == "consumer.ofn"
    assert len(consumer_conformance_fixture()) > 100
    cases = consumer_conformance_cases()
    assert tuple(case.case_id for case in cases) == (
        "exact-owl2vec",
        "exact-owl2vec-literals",
        "exact-taxonomy",
    )
    assert all(case.canonical_edges_sha256 for case in cases)
    benchmark = json.loads(
        (ROOT / "reports/p6/evidence/consumer-handoff.json").read_text(encoding="utf-8")
    )
    assert benchmark["passed"] is True
    assert benchmark["fixture_sha256"] == metadata.sha256
    assert benchmark["provider_calls_per_projection"] == 1
    assert benchmark["source_accesses"] == 0


@pytest.mark.parametrize("case_id", [case.case_id for case in consumer_conformance_cases()])
def test_python_consumer_conformance_preserves_one_shared_snapshot(case_id: str) -> None:
    snapshot = _snapshot()
    marker = object()
    result = verify_consumer_conformance(
        snapshot,
        case_id=case_id,
        backend="python",
        identity_probes={"consumer-lazy-view": lambda: marker},
    )
    record = result.to_dict()
    assert record["schema"] == CONSUMER_CONFORMANCE_SCHEMA
    assert record["passed"] is True
    assert record["provider_calls"] == 1
    assert record["source_accesses"] == 0
    assert record["snapshot_identity_preserved"] is True
    assert result.core_before == result.core_after
    assert result.axiom_count_before == result.axiom_count_after == 13
    assert result.signature_count_before == result.signature_count_after == 8
    assert result.preserved_identity_probes == ("consumer-lazy-view",)
    if case_id == "exact-taxonomy":
        assert result.report is None
    else:
        assert result.report is not None
        assert result.report.provenance.source_kind == "provider"
        assert result.report.provenance.selected_backend == "python"


@pytest.mark.skipif(
    not probe_native_backend().available,
    reason="optional native extension is not installed",
)
@pytest.mark.parametrize("case_id", [case.case_id for case in consumer_conformance_cases()])
def test_native_consumer_conformance_is_identical(case_id: str) -> None:
    result = verify_consumer_conformance(_snapshot(), case_id=case_id, backend="native")
    assert result.canonical_edges_sha256 == next(
        case.canonical_edges_sha256
        for case in consumer_conformance_cases()
        if case.case_id == case_id
    )
    if result.report is not None:
        assert result.report.provenance.selected_backend == "native"


def test_provider_probe_turns_path_or_stream_fallback_into_typed_failure() -> None:
    probe = SnapshotProviderProbe(object())
    assert probe.owl_snapshot() is probe.snapshot
    with pytest.raises(ConsumerConformanceError, match="shared snapshot"):
        os.fspath(probe)
    with pytest.raises(ConsumerConformanceError, match="shared snapshot"):
        probe.read()
    assert probe.provider_calls == 1
    assert probe.source_accesses == 2


def test_wrong_fixture_and_changed_consumer_identity_fail_explicitly() -> None:
    wrong = pyowl_core.load_snapshot(
        b"Ontology(<urn:wrong> Declaration(Class(<urn:wrong#A>)))",
        document_iri="urn:wrong",
        options=pyowl_core.LoadOptions(
            backend=pyowl_core.BackendPreference.PYTHON,
            format=pyowl_core.DocumentFormat.FUNCTIONAL,
        ),
    )
    with pytest.raises(ConsumerConformanceError, match="consumer conformance failed"):
        verify_consumer_conformance(wrong)

    values = iter((object(), object()))
    with pytest.raises(ConsumerConformanceError, match="identity probe 'changing' changed"):
        verify_consumer_conformance(
            _snapshot(),
            identity_probes={"changing": lambda: next(values)},
        )


def test_unknown_case_and_backend_fail_before_projection() -> None:
    snapshot = _snapshot()
    with pytest.raises(ConsumerConformanceError, match="unknown consumer"):
        verify_consumer_conformance(snapshot, case_id="latest")
    with pytest.raises(ConsumerConformanceError, match="unsupported conformance backend"):
        verify_consumer_conformance(snapshot, backend="gpu")  # type: ignore[arg-type]
