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
        assert sample["consumer_surface"] == "iterator"
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
    assert result["configuration"]["private_native_surface"] == "iterator"
    assert result["source_revisions"] == {"projector": "1" * 40, "core": "2" * 40}
    assert result["production_acceptance"]["private_candidate_is_public"] is False
    blockers = result["production_acceptance"]["known_private_candidate_blockers"]
    assert not any("fully-materialized" in blocker for blocker in blockers)
    assert any("mmap-overlay-composite" in blocker for blocker in blockers)

    native_artifact = result["runtime_binding"]["projector"]["native_extension"]
    assert native_artifact["available"] is True
    assert len(native_artifact["sha256"]) == 64
    assert native_artifact["encoded_direct_kernel_version"] == 74
    assert native_artifact["features"] == ["abi3-py310", "bounded-batches"]

    samples = result["samples"]
    for sample, evidence in zip(samples, result["acceptance_evidence"], strict=True):
        assert sample["execution_surface"] == "private-native-candidate"
        assert sample["consumer_surface"] == "iterator"
        assert sample["consumer_metrics"] == {
            "first_edge_observable": True,
            "surface": "iterator",
        }
        assert len(sample["consumer_metrics_sha256"]) == 64
        assert sample["ingestion"]["path"] == "encoded-native"
        assert sample["counters"]["native_batch_edges"] == 1
        assert sample["counters"]["native_compiled_edges"] == sample["edge_count"] == 1
        assert sample["counters"]["native_output_vector_edges"] == 0
        assert sample["counters"]["native_peak_buffered_edges"] == 1
        assert len(sample["counter_ledger_sha256"]) == 64
        assert evidence["acceptance_ready"] is False
        assert evidence["private_candidate_boundary_ready"] is True
        assert evidence["private_candidate_evidence_ready"] is evidence["installed_artifacts_bound"]
        assert evidence["missing_private_candidate_counters"] == []
        assert evidence["private_candidate_counter_violations"] == {}
        assert evidence["source_revisions_bound"] is True
    assert len(samples) == 2


@pytest.mark.skipif(not NATIVE_AVAILABLE, reason="native extension is unavailable")
@pytest.mark.parametrize(
    ("surface", "order", "first_edge_observable"),
    [
        ("sink", "encounter", True),
        ("digest", "canonical", False),
        ("artifact", "canonical", False),
    ],
)
def test_private_stream_surfaces_are_measured_and_bound_without_public_acceptance(
    tmp_path: Path,
    surface: str,
    order: str,
    first_edge_observable: bool,
) -> None:
    result = benchmark_encoded_compiler.run(
        _ontology(tmp_path),
        document_format="functional",
        load_backend="python",
        projector_backend="native",
        order=order,
        duplicates="preserve",
        include_literals=False,
        repetitions=1,
        warmups=0,
        buffer_edges=1,
        require_encoded_native=False,
        private_native_candidate=True,
        private_native_surface=surface,
        projector_revision="1" * 40,
        core_revision="2" * 40,
    )

    assert result["configuration"]["execution_surface"] == "private-native-candidate"
    assert result["configuration"]["private_native_surface"] == surface
    sample = result["samples"][0]
    evidence = result["acceptance_evidence"][0]
    assert sample["execution_surface"] == "private-native-candidate"
    assert sample["consumer_surface"] == surface
    assert sample["consumer_metrics"]["surface"] == surface
    assert sample["consumer_metrics"]["first_edge_observable"] is first_edge_observable
    assert len(sample["consumer_metrics_sha256"]) == 64
    assert sample["ingestion"]["path"] == "encoded-native"
    assert sample["edge_count"] == sample["counters"]["native_compiled_edges"] == 1
    assert len(sample["edge_sha256"]) == 64
    assert evidence["acceptance_ready"] is False
    assert evidence["private_candidate_boundary_ready"] is True
    assert evidence["private_candidate_evidence_ready"] is evidence["installed_artifacts_bound"]
    assert evidence["missing_private_candidate_counters"] == []
    assert evidence["private_candidate_counter_violations"] == {}
    if surface == "sink":
        assert sample["first_edge_seconds"] is not None
        assert sample["consumer_metrics"]["batch_count"] == 1
        assert sample["consumer_metrics"]["peak_batch_edges"] == 1
    elif surface == "digest":
        assert sample["first_edge_seconds"] is None
        assert sample["consumer_metrics"]["canonical_edges_sha256"] == sample["edge_sha256"]
    else:
        assert sample["first_edge_seconds"] is None
        assert sample["consumer_metrics"]["canonical_edges_sha256"] == sample["edge_sha256"]
        assert len(sample["consumer_metrics"]["artifact_sha256"]) == 64
        assert sample["consumer_metrics"]["bytes_written"] > 0


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
        (
            {
                "private_native_candidate": True,
                "private_native_surface": "digest",
            },
            "require order='canonical'",
        ),
        (
            {"private_native_surface": "sink"},
            "requires private_native_candidate=True",
        ),
        (
            {
                "private_native_candidate": True,
                "private_native_surface": "unknown",
            },
            "must be one of",
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
    assert (
        checkpoint["build_environment"]["wheel_used"]
        != checkpoint["build_environment"]["wheel_pinned"]
    )

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


def test_installed_annotation_provenance_checkpoint_is_fail_closed_and_nonpublic() -> None:
    path = (
        Path(__file__).parents[1]
        / "reports"
        / "p7"
        / "evidence"
        / "installed-annotation-provenance-checkpoint.json"
    )
    checkpoint = json.loads(path.read_text(encoding="utf-8"))

    assert checkpoint["schema"] == (
        "pyowl-projector.p7-installed-annotation-provenance-checkpoint/1"
    )
    assert checkpoint["status"] == "checkpoint-only"
    assert checkpoint["acceptance_complete"] is False
    assert checkpoint["public_acceptance_ready"] is False
    assert checkpoint["performance_claim"] is False
    assert all(len(revision) == 40 for revision in checkpoint["source_revisions"].values())

    construction = checkpoint["checkpoint_wheel_construction"]
    assert construction["kind"] == "assembled-correctness-checkpoint"
    assert construction["source_fallback_wheel_built_from_exact_projector_archive"] is True
    assert construction["native_binary_transplanted_from_prior_installed_checkpoint"] is True
    assert construction["native_tree_matches_donor_revision"] is True
    assert construction["rust_source_build_available"] is False
    assert construction["release_artifact"] is False

    public = checkpoint["public_contract"]
    assert public["encoded_direct_kernel_version"] == 29
    assert public["native_features"] == ["abi3-py310", "bounded-batches"]
    assert public["encoded_structural_compiler_advertised"] is False

    imported = checkpoint["imported_closure"]
    visible = imported["visible_annotations"]
    assert imported["provider"] == "python"
    assert visible["scalar_parity"] is True
    assert visible["imported_label_suppressed"] is True
    assert visible["ingestion_path"] == "scalar-native"
    assert visible["fallback_reason"].startswith(
        "private native direct batches do not bind root-scoped annotation provenance"
    )
    assert ["urn:root#A", "rdfs:label", "root"] in visible["edges"]
    assert not any(edge[-1] == "leaf" for edge in visible["edges"])
    limited = imported["visible_annotations_at_scalar_edge_limit"]
    assert limited["scalar_parity"] is True
    assert limited["native_closure_limit_error_avoided"] is True
    assert limited["edge_count"] == limited["edge_limit"] == visible["edge_count"]
    assert limited["edges_sha256"] == visible["edges_sha256"]
    assert limited["ingestion_path"] == "scalar-native"
    hidden = imported["hidden_annotations"]
    assert hidden["scalar_parity"] is True
    assert hidden["ingestion_path"] == "encoded-native"
    assert hidden["fallback_reason"] is None

    annotation_free = checkpoint["annotation_free_imported_closure"]
    assert annotation_free["scalar_parity"] is True
    assert annotation_free["root_provenance_preflight_required"] is False
    assert annotation_free["ingestion_path"] == "encoded-native"
    assert annotation_free["fallback_reason"] is None

    single = checkpoint["single_document_visible_annotations"]
    assert single["python_provider"]["closure_exporter_count"] > 1
    assert single["native_provider"]["closure_exporter_count"] == 1
    assert single["python_provider"]["edges_sha256"] == single["native_provider"]["edges_sha256"]
    assert all(lane["ingestion_path"] == "encoded-native" for lane in single.values())
    assert all(lane["scalar_parity"] is True for lane in single.values())

    verification = checkpoint["source_verification"]
    assert verification["root_provenance_precedes_native_edge_limit"] is True
    assert verification["annotation_free_imports_skip_root_preflight"] is True

    for artifact in checkpoint["artifacts"].values():
        assert len(artifact["sha256"]) == 64
    assert checkpoint["known_blockers"]
    assert checkpoint["limitations"]
