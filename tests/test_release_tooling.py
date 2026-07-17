from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

import _build_backend
from tools.audit_release import _audit_metadata, _audit_native_payloads
from tools.generate_supply_chain import generate
from tools.hash_artifacts import create_manifest, verify_manifest
from tools.release_gate import local_checks
from tools.release_support import read_toml

ROOT = Path(__file__).resolve().parents[1]


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


def test_external_release_gates_are_never_silently_presented_as_passed() -> None:
    document = json.loads((ROOT / "release/external-gates.json").read_text(encoding="utf-8"))
    gates = document["gates"]
    assert {gate["id"] for gate in gates} >= {
        "distribution-name-ownership",
        "hosted-platform-matrix",
        "private-index-selection",
        "signed-provenance",
    }
    assert all(gate["status"] == "blocked" for gate in gates)
    workflow = (ROOT / ".github/workflows/release-candidate.yml").read_text(encoding="utf-8")
    assert "pypi" not in workflow.lower()
    assert "twine upload" not in workflow.lower()


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
