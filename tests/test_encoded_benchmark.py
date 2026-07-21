"""Contract checks for the P7 load-excluded benchmark harness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks import benchmark_encoded_compiler
from pyowl2vec_star_projector import probe_native_backend

NATIVE_AVAILABLE = probe_native_backend().available


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
        assert sample["execution_surface"] == "public"
        assert sample["ingestion"]["path"] == "scalar-python"
        assert not any(sample["core_operation_delta"].values())
        assert evidence["acceptance_ready"] is False
        assert evidence["private_candidate_boundary_ready"] is False
        assert evidence["private_candidate_evidence_ready"] is False
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


@pytest.mark.skipif(not NATIVE_AVAILABLE, reason="native extension is unavailable")
def test_private_candidate_records_bound_counter_evidence_without_public_acceptance(
    tmp_path: Path,
) -> None:
    result = benchmark_encoded_compiler.run(
        _ontology(tmp_path),
        document_format="functional",
        load_backend="python",
        projector_backend="native",
        order="encounter",
        duplicates="preserve",
        include_literals=False,
        repetitions=2,
        warmups=1,
        buffer_edges=1,
        require_encoded_native=False,
        private_native_candidate=True,
        projector_revision="1" * 40,
        core_revision="2" * 40,
    )

    assert result["configuration"]["execution_surface"] == "private-native-candidate"
    assert result["source_revisions"] == {"projector": "1" * 40, "core": "2" * 40}
    assert result["production_acceptance"]["private_candidate_is_public"] is False
    blockers = result["production_acceptance"]["known_private_candidate_blockers"]
    assert any("fully-materialized" in blocker for blocker in blockers)
    assert any("mmap-overlay-composite" in blocker for blocker in blockers)

    native_artifact = result["runtime_binding"]["projector"]["native_extension"]
    assert native_artifact["available"] is True
    assert len(native_artifact["sha256"]) == 64
    assert native_artifact["encoded_direct_kernel_version"] == 29
    assert native_artifact["features"] == ["abi3-py310", "bounded-batches"]

    samples = result["samples"]
    for sample, evidence in zip(samples, result["acceptance_evidence"], strict=True):
        assert sample["execution_surface"] == "private-native-candidate"
        assert sample["ingestion"]["path"] == "encoded-native"
        assert sample["counters"]["native_batch_edges"] == 1
        assert sample["counters"]["native_output_vector_edges"] == sample["edge_count"] == 1
        assert len(sample["counter_ledger_sha256"]) == 64
        assert evidence["acceptance_ready"] is False
        assert evidence["private_candidate_boundary_ready"] is True
        assert evidence["private_candidate_evidence_ready"] is evidence["installed_artifacts_bound"]
        assert evidence["missing_private_candidate_counters"] == []
        assert evidence["private_candidate_counter_violations"] == {}
        assert evidence["source_revisions_bound"] is True
    assert len(samples) == 2


def test_private_candidate_cannot_use_public_acceptance_gate(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cannot satisfy the public"):
        benchmark_encoded_compiler.run(
            _ontology(tmp_path),
            document_format="functional",
            load_backend="python",
            projector_backend="native",
            order="encounter",
            duplicates="preserve",
            include_literals=False,
            repetitions=1,
            warmups=0,
            buffer_edges=1,
            require_encoded_native=True,
            private_native_candidate=True,
        )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        (
            {"projector_backend": "python", "private_native_candidate": True},
            "requires projector_backend='native'",
        ),
        (
            {"require_private_native_candidate": True},
            "requires private_native_candidate=True",
        ),
        ({"projector_revision": "abc"}, "full lowercase 40-character Git SHA"),
    ],
)
def test_private_evidence_configuration_fails_before_loading(
    changes: dict[str, object],
    message: str,
) -> None:
    arguments: dict[str, object] = {
        "document_format": "functional",
        "load_backend": "python",
        "projector_backend": "native",
        "order": "encounter",
        "duplicates": "preserve",
        "include_literals": False,
        "repetitions": 1,
        "warmups": 0,
        "buffer_edges": 1,
        "require_encoded_native": False,
    }
    arguments.update(changes)
    with pytest.raises(ValueError, match=message):
        benchmark_encoded_compiler.run(Path("does-not-exist.ofn"), **arguments)  # type: ignore[arg-type]


def test_installed_private_checkpoint_is_hash_bound_and_explicitly_nonpublic() -> None:
    path = (
        Path(__file__).parents[1]
        / "reports"
        / "p7"
        / "evidence"
        / "installed-private-checkpoint.json"
    )
    checkpoint = json.loads(path.read_text(encoding="utf-8"))

    assert checkpoint["schema"] == "pyowl-projector.p7-installed-private-checkpoint/1"
    assert checkpoint["status"] == "checkpoint-only"
    assert checkpoint["acceptance_complete"] is False
    assert checkpoint["public_acceptance_ready"] is False
    assert checkpoint["private_candidate_is_public"] is False
    assert all(len(revision) == 40 for revision in checkpoint["source_revisions"].values())

    projector = checkpoint["artifacts"]["installed_projector"]
    assert projector["encoded_direct_kernel_version"] == 29
    assert projector["native_features"] == ["abi3-py310", "bounded-batches"]
    assert len(projector["native_sha256"]) == 64
    assert checkpoint["build_environment"]["dependency_check_skipped"] is True
    assert checkpoint["build_environment"]["wheel_used"] != checkpoint["build_environment"][
        "wheel_pinned"
    ]

    contract = checkpoint["exact_contract"]
    assert contract["edge_count"] == contract["counters"]["native_output_vector_edges"] == 11
    assert contract["counters"]["encoded_zero_copy_buffers"] == 11
    assert contract["counters"]["encoded_staging_copy_bytes"] == 0
    assert contract["counters"]["native_batch_edges"] == 3
    assert contract["counters"]["native_boundary_calls"] == 5
    assert not any(contract["core_operation_delta"].values())

    assert set(checkpoint["lanes"]) == {"python_provider", "native_provider"}
    for lane in checkpoint["lanes"].values():
        assert lane["acceptance_ready"] is False
        assert lane["private_candidate_boundary_ready"] is True
        assert lane["private_candidate_evidence_ready"] is True
        assert lane["installed_artifacts_bound"] is True
        assert lane["source_revisions_bound"] is True
        assert lane["edge_count"] == contract["edge_count"]
        assert lane["edge_sha256"] == contract["edge_sha256"]
        assert len(lane["raw_evidence_sha256"]) == 64

    assert checkpoint["pre_fix_observation"]["encoded_direct_kernel_version"] == 28
    assert checkpoint["pre_fix_observation"]["private_candidate_evidence_ready"] is False
    assert checkpoint["known_blockers"]
    assert checkpoint["limitations"]
