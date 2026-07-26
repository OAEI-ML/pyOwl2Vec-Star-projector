from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import tarfile
import warnings
import zipfile
from pathlib import Path

import pytest

import _build_backend
from tools import generate_supply_chain
from tools.audit_release import _audit_metadata, _audit_native_payloads, audit_artifact
from tools.generate_supply_chain import build_provenance, generate
from tools.hash_artifacts import create_manifest, verify_manifest
from tools.release_gate import local_checks
from tools.release_support import read_toml

ROOT = Path(__file__).resolve().parents[1]
ACTION = re.compile(r"(?m)^\s*-?\s*uses:\s+([^\s#]+)")
EXPECTED_PROVENANCE_INPUTS = {
    ".github/workflows/native.yml",
    ".github/workflows/packaging.yml",
    ".github/workflows/release-candidate.yml",
    "MANIFEST.in",
    "_build_backend.py",
    "native/Cargo.lock",
    "native/Cargo.toml",
    "native/THIRD_PARTY_LICENSES.md",
    "native/build.rs",
    "pyproject.toml",
    "release/fallback-build-requirements.txt",
    "release/native-build-requirements.txt",
    "setup.py",
    "tools/audit_release.py",
    "tools/audit_runtime.py",
    "tools/check_dependency_dag.py",
    "tools/compare_artifacts.py",
    "tools/generate_supply_chain.py",
    "tools/hash_artifacts.py",
    "tools/installed_smoke.py",
    "tools/release_gate.py",
    "tools/release_support.py",
}


def _copy_build_inputs(target: Path) -> None:
    for relative_path in generate_supply_chain._BUILD_INPUT_PATHS:
        destination = target / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative_path, destination)


def test_conditional_build_requirement_never_leaks_to_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYOWL2VEC_BUILD_NATIVE", "1")
    assert _build_backend.get_requires_for_build_wheel() == ["setuptools-rust==1.13.0"]
    monkeypatch.setenv("PYOWL2VEC_BUILD_NATIVE", "invalid")
    with pytest.raises(RuntimeError, match="auto, 0, or 1"):
        _build_backend.get_requires_for_build_wheel()


def test_version_and_generated_supply_chain_are_consistent() -> None:
    version = read_toml(ROOT / "pyproject.toml")["project"]["version"]
    assert version == "0.1.0rc1"
    for path, expected in generate(ROOT).items():
        assert path.read_bytes() == expected
    inventory = json.loads((ROOT / "release/license-inventory.json").read_text(encoding="utf-8"))
    assert inventory["project"]["version"] == version
    assert inventory["java_components"] == []
    assert len(inventory["native"]) == 14


def test_build_provenance_binds_exact_toolchain_and_inputs() -> None:
    provenance = build_provenance(ROOT)
    assert provenance["schema"] == "pyowl-projector.build-provenance/1"
    assert provenance["distribution"] == "pyowl2vec-star-projector"
    assert provenance["version"] == "0.1.0rc1"
    assert provenance["source_date_epoch"] == {
        "source": "release commit timestamp",
        "command": "git log -1 --pretty=%ct",
    }
    assert provenance["tools"] == {
        "cargo_manifest_rust_version": "1.83",
        "rust_toolchain": "1.83.0",
        "cibuildwheel_action": ("pypa/cibuildwheel@65b8265957fd86372d9689a0acdfd55813970d5d"),
        "python_build_system": ["setuptools==83.0.0", "wheel==0.46.3"],
        "python_fallback_requirements": [
            "build==1.5.0",
            "packaging==26.2",
            "pyproject-hooks==1.2.0",
            'tomli==2.4.1 ; python_version < "3.11"',
            "setuptools==83.0.0",
            "wheel==0.46.3",
        ],
        "python_native_requirements": [
            "-r fallback-build-requirements.txt",
            "setuptools-rust==1.13.0",
            "semantic-version==2.10.0",
        ],
    }
    inputs = provenance["inputs"]
    assert set(inputs) == EXPECTED_PROVENANCE_INPUTS
    for relative_path in EXPECTED_PROVENANCE_INPUTS:
        payload = (ROOT / relative_path).read_bytes()
        assert inputs[relative_path] == {
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }


def test_build_provenance_parses_and_hashes_same_captured_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _copy_build_inputs(tmp_path)
    workflow = tmp_path / ".github" / "workflows" / "native.yml"
    original = workflow.read_bytes()
    replacement = original.replace(b'toolchain: "1.83.0"', b'toolchain: "9.99.0"')
    assert replacement != original
    original_pin = generate_supply_chain._workflow_pin
    mutated = False

    def mutate_after_capture(text: str, pattern: str, label: str) -> str:
        nonlocal mutated
        if not mutated:
            workflow.write_bytes(replacement)
            mutated = True
        return original_pin(text, pattern, label)

    monkeypatch.setattr(generate_supply_chain, "_workflow_pin", mutate_after_capture)
    provenance = build_provenance(tmp_path)
    assert mutated
    assert provenance["tools"]["rust_toolchain"] == "1.83.0"
    assert provenance["inputs"][".github/workflows/native.yml"] == {
        "bytes": len(original),
        "sha256": hashlib.sha256(original).hexdigest(),
    }
    assert workflow.read_bytes() == replacement


def test_build_provenance_rejects_symlinked_inputs(tmp_path: Path) -> None:
    _copy_build_inputs(tmp_path)
    workflow = tmp_path / ".github" / "workflows" / "native.yml"
    target = tmp_path / "captured-native.yml"
    workflow.replace(target)
    try:
        workflow.symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(ValueError, match="regular non-symlink file"):
        build_provenance(tmp_path)


def test_stable_build_input_reader_rejects_concurrent_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "input.txt"
    path.write_bytes(b"captured")
    original_lstat = Path.lstat
    inspections = 0

    def mutate_before_final_identity(selected: Path):
        nonlocal inspections
        if selected == path:
            inspections += 1
            if inspections == 2:
                selected.write_bytes(b"changed-after-read")
        return original_lstat(selected)

    monkeypatch.setattr(Path, "lstat", mutate_before_final_identity)
    with pytest.raises(ValueError, match="changed while reading"):
        generate_supply_chain._read_stable_regular_file(path, label="input.txt")


def test_external_release_gates_are_never_silently_presented_as_passed() -> None:
    document = json.loads((ROOT / "release/external-gates.json").read_text(encoding="utf-8"))
    gates = document["gates"]
    assert {gate["id"] for gate in gates} >= {
        "pyowl-core-release",
        "distribution-name-ownership",
        "hosted-platform-matrix",
        "private-index-selection",
        "signed-provenance",
    }
    assert all(gate["status"] == "blocked" for gate in gates)
    corpora = next(gate for gate in gates if gate["id"] == "release-corpora")
    assert "cannot yet be loaded" not in corpora["reason"]
    assert corpora["completed_local_evidence"] == {
        "report": "reports/p4/streaming.md",
        "corpus": "OAEI Bio-ML NCIT source",
        "source_sha256": "379a37f47c0c8e7c30397769358cca955140d16b2797a1cc75da4b1fc2b354eb",
        "axioms": 243099,
        "edges": 42103,
        "canonical_edge_record_sha256": (
            "b0c1186bd4004bc2a288593c1b5783d568e44f58723fedd7ba6d9c1eb6a20914"
        ),
    }
    workflow = (ROOT / ".github/workflows/release-candidate.yml").read_text(encoding="utf-8")
    assert "pypi" not in workflow.lower()
    assert "twine upload" not in workflow.lower()


def test_external_workflow_actions_are_pinned_to_exact_commits() -> None:
    observed: list[str] = []
    for workflow in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        for action in ACTION.findall(workflow.read_text(encoding="utf-8")):
            if action.startswith("./"):
                continue
            observed.append(action)
            name, separator, revision = action.rpartition("@")
            assert separator and name
            assert re.fullmatch(r"[0-9a-f]{40}", revision), (
                f"{workflow.relative_to(ROOT)} has mutable external action {action}"
            )
    assert observed


def test_hash_manifest_detects_tampering(tmp_path: Path) -> None:
    artifact = tmp_path / "example-1-py3-none-any.whl"
    artifact.write_bytes(b"first")
    manifest = tmp_path / "SHA256SUMS"
    manifest.write_text(create_manifest(tmp_path), encoding="utf-8")
    assert verify_manifest(tmp_path, manifest) == []
    artifact.write_bytes(b"second")
    assert verify_manifest(tmp_path, manifest) == [
        "line 1: hash mismatch for example-1-py3-none-any.whl"
    ]


def test_hash_manifest_is_complete_unique_and_path_safe(tmp_path: Path) -> None:
    first = tmp_path / "first-1-py3-none-any.whl"
    second = tmp_path / "second-1.tar.gz"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    digest = hashlib.sha256(first.read_bytes()).hexdigest()
    manifest = tmp_path / "SHA256SUMS"
    manifest.write_text(
        f"{digest}  first-1-py3-none-any.whl\n"
        f"{digest}  first-1-py3-none-any.whl\n"
        f"{'0' * 64}  ../outside.whl\n",
        encoding="utf-8",
    )
    assert verify_manifest(tmp_path, manifest) == [
        "line 2: duplicate artifact record for first-1-py3-none-any.whl",
        "line 3: unsafe artifact name '../outside.whl'",
        "manifest missing release artifact second-1.tar.gz",
    ]


def test_release_audit_rejects_duplicate_normalized_archive_members(tmp_path: Path) -> None:
    artifact = tmp_path / "example-0.1-py3-none-any.whl"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(artifact, "w") as archive:
            archive.writestr("example/module.py", "first")
            archive.writestr("example/module.py", "second")
    report = audit_artifact(artifact, expected_version="0.1")
    assert report["passed"] is False
    assert report["kind"] == "invalid-wheel"
    assert report["errors"] == [
        "archive could not be read safely: duplicate archive member: 'example/module.py'"
    ]


def test_sdist_header_normalization_is_byte_reproducible(tmp_path: Path) -> None:
    artifacts: list[Path] = []
    for index, timestamp in enumerate((100, 200)):
        path = tmp_path / f"source-{index}.tar.gz"
        with tarfile.open(path, "w:gz") as archive:
            directory = tarfile.TarInfo("package")
            directory.type = tarfile.DIRTYPE
            directory.mode = 0o700 if index else 0o755
            directory.mtime = timestamp
            archive.addfile(directory)
            content = b"stable\n"
            info = tarfile.TarInfo("package/file.txt")
            info.size = len(content)
            info.mtime = timestamp
            info.uid = index + 10
            archive.addfile(info, io.BytesIO(content))
        _build_backend._normalize_sdist(path, 1234567890)
        artifacts.append(path)
    hashes = {hashlib.sha256(path.read_bytes()).hexdigest() for path in artifacts}
    assert len(hashes) == 1


def test_static_local_release_checks_pass() -> None:
    failures = [check for check in local_checks(ROOT, None) if not check["passed"]]
    assert failures == []


def test_core_compatibility_transition_preserves_semantic_digests() -> None:
    compatibility = json.loads(
        (ROOT / "release/core-compatibility.json").read_text(encoding="utf-8")
    )
    fixture = compatibility["consumer_fixture"]
    assert compatibility["tested_source"]["commit"] == ("6df155e3ef83588352dbfd11bc4b15bdc0fa9c4e")
    assert (
        compatibility["previous_source"]["structural_fingerprint"]
        != (fixture["structural_fingerprint"])
    )
    assert compatibility["semantic_change"] is False
    goldens = json.loads(
        (ROOT / "src/pyowl2vec_star_projector/conformance_data/goldens.json").read_text(
            encoding="utf-8"
        )
    )
    assert fixture["logical_fingerprint"] == goldens["fixture"]["logical_fingerprint"]
    assert fixture["signature_fingerprint"] == goldens["fixture"]["signature_fingerprint"]
    assert fixture["edge_digests"] == {
        case["case_id"]: case["canonical_edges_sha256"] for case in goldens["cases"]
    }


def test_native_release_audit_rejects_jvm_symbols() -> None:
    errors: list[str] = []
    _audit_native_payloads(
        ["package/_native.abi3.so"],
        {"package/_native.abi3.so": b"binary\x00JNI_CreateJavaVM\x00"},
        errors,
    )
    assert errors == [
        "native extension contains JVM/JNI marker(s) JNI_CreateJavaVM: package/_native.abi3.so"
    ]


def test_release_metadata_audit_covers_optional_java_dependencies_by_exact_name() -> None:
    metadata = b"""\
Name: pyowl2vec-star-projector
Version: 0.1.0rc1
Requires-Python: >=3.10
Requires-Dist: pyowl-core<0.2,>=0.1
Requires-Dist: mOWL; extra == 'reasoning'
Requires-Dist: robotframework; extra == 'testing'

"""
    errors: list[str] = []
    _audit_metadata(metadata, "0.1.0rc1", errors)
    assert errors == ["Java-facing dependency or extra present: mowl"]
