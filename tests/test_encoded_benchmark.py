"""Contract checks for the P7 load-excluded benchmark harness."""

from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks import benchmark_encoded_compiler


def _ontology(tmp_path: Path) -> Path:
    path = tmp_path / "benchmark.ofn"
    path.write_bytes(
        b"Prefix(:=<urn:benchmark#>) Ontology(Declaration(Class(:A)) "
        b"Declaration(Class(:B)) SubClassOf(:A :B))"
    )
    return path


def test_scalar_smoke_preserves_identity_and_reports_path_rejection(
    tmp_path: Path,
) -> None:
    result = benchmark_encoded_compiler.run(
        _ontology(tmp_path),
        document_format="functional",
        load_backend="python",
        projector_backend="python",
        order="encounter",
        duplicates="preserve",
        include_literals=False,
        repetitions=2,
        warmups=1,
        buffer_edges=1,
        require_encoded_native=False,
    )

    assert result["schema"] == "pyowl-projector.encoded-compiler-benchmark/1"
    assert result["identity"] == {"projector_retained_input": True}
    samples = result["samples"]
    assert len(samples) == 2
    assert {sample["edge_count"] for sample in samples} == {1}
    assert len({sample["edge_sha256"] for sample in samples}) == 1
    for sample, evidence in zip(samples, result["acceptance_evidence"], strict=True):
        assert sample["ingestion"]["path"] == "scalar-python"
        assert not any(sample["core_operation_delta"].values())
        assert evidence["acceptance_ready"] is False
        assert evidence["missing_public_zero_counters"] == []
        assert evidence["nonzero_forbidden_counters"] == {}


def test_required_encoded_mode_rejects_scalar_selection(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="acceptance evidence"):
        benchmark_encoded_compiler.run(
            _ontology(tmp_path),
            document_format="functional",
            load_backend="python",
            projector_backend="python",
            order="encounter",
            duplicates="preserve",
            include_literals=False,
            repetitions=1,
            warmups=0,
            buffer_edges=1,
            require_encoded_native=True,
        )
