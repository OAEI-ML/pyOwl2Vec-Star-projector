from __future__ import annotations

import base64
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
from tools import (
    audit_release,
    check_core_compatibility,
    generate_supply_chain,
    release_gate,
    release_support,
)
from tools.audit_release import (
    _audit_metadata,
    _audit_native_payloads,
    _audit_sdist,
    _audit_wheel_legal_payloads,
    audit_artifact,
    release_legal_payloads,
)
from tools.generate_supply_chain import build_provenance, generate
from tools.hash_artifacts import create_manifest, verify_manifest
from tools.release_gate import local_checks
from tools.release_support import read_stable_regular_file, read_toml

ROOT = Path(__file__).resolve().parents[1]
ACTION = re.compile(r"(?m)^\s*-?\s*uses:\s+([^\s#]+)")
EXPECTED_PROVENANCE_INPUTS = {
    ".github/workflows/ci.yml",
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
    "release/core-compatibility.json",
    "release/native-build-requirements.txt",
    "setup.py",
    "tools/audit_release.py",
    "tools/audit_runtime.py",
    "tools/check_core_compatibility.py",
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


def _wheel_record_digest(payload: bytes) -> str:
    encoded = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=")
    return f"sha256={encoded.decode('ascii')}"


def _write_minimal_wheel(path: Path, *, tamper_after_record: bool = False) -> None:
    version = "0.1"
    dist_info = f"pyowl2vec_star_projector-{version}.dist-info"
    files = {
        "pyowl2vec_star_projector/__init__.py": b"",
        "pyowl2vec_star_projector/conformance_data/consumer.ofn": b"Ontology()",
        "pyowl2vec_star_projector/conformance_data/goldens.json": b"{}",
        "pyowl2vec_star_projector/conformance_data/LICENSE": b"CC0-1.0",
        f"{dist_info}/licenses/LICENSE": b"Apache-2.0",
        f"{dist_info}/licenses/NOTICE": b"notice",
        f"{dist_info}/licenses/THIRD_PARTY_NOTICES.md": b"notices",
        f"{dist_info}/licenses/native/THIRD_PARTY_LICENSES.md": b"native notices",
        f"{dist_info}/METADATA": (
            b"Metadata-Version: 2.4\n"
            b"Name: pyowl2vec-star-projector\n"
            b"Version: 0.1\n"
            b"Requires-Python: >=3.10\n"
            b"Requires-Dist: pyowl-core<0.2,>=0.1\n\n"
        ),
        f"{dist_info}/WHEEL": (
            b"Wheel-Version: 1.0\n"
            b"Generator: release-test\n"
            b"Root-Is-Purelib: true\n"
            b"Tag: py3-none-any\n\n"
        ),
    }
    record_name = f"{dist_info}/RECORD"
    record = "".join(
        f"{name},{_wheel_record_digest(payload)},{len(payload)}\n"
        for name, payload in sorted(files.items())
    )
    record += f"{record_name},,\n"
    files[record_name] = record.encode("utf-8")
    if tamper_after_record:
        files["pyowl2vec_star_projector/__init__.py"] = b"tampered"
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in sorted(files.items()):
            archive.writestr(name, payload)


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
    assert provenance["scope"] == "deterministic-build-and-release-recipe"
    assert provenance["distribution"] == "pyowl2vec-star-projector"
    assert provenance["version"] == "0.1.0rc1"
    assert provenance["source_date_epoch"] == {
        "source": "release commit timestamp",
        "command": "git log -1 --pretty=%ct",
    }
    assert provenance["tools"] == {
        "cargo_manifest_rust_version": "1.83",
        "rust_toolchain": "1.83.0",
        "rust_sanitizer_toolchain": "nightly-2025-01-15",
        "cibuildwheel_action": ("pypa/cibuildwheel@65b8265957fd86372d9689a0acdfd55813970d5d"),
        "offline_python_images": {
            "3.10": (
                "python:3.10-slim@sha256:"
                "e8d6cdadc17ce7146e1bb286e6093d58c8cf582659a558ad51cd103829655e72"
            ),
            "3.11": (
                "python:3.11-slim@sha256:"
                "00af38ae2ed311628970782e8a2d7f014d8909dbc63cb97bc0a158187f4db045"
            ),
            "3.12": (
                "python:3.12-slim@sha256:"
                "cab2dbf575e971934a81e4622f5aba17aa7929719bd7e31033a3a83b97fd0464"
            ),
            "3.13": (
                "python:3.13-slim@sha256:"
                "afe189875f1d2f9b45e287834fb9f2c273a5d59d354ae4050ab9affbf0a6ba06"
            ),
        },
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


def test_supply_chain_rejects_unreviewed_python_build_pin(tmp_path: Path) -> None:
    _copy_build_inputs(tmp_path)
    requirements = tmp_path / "release" / "fallback-build-requirements.txt"
    original = requirements.read_text(encoding="utf-8")
    requirements.write_text(
        original.replace("build==1.5.0", "build==9.9.9"),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Python build license review differs"):
        generate(tmp_path)


def test_supply_chain_rejects_extra_cargo_license_row(tmp_path: Path) -> None:
    _copy_build_inputs(tmp_path)
    inventory = tmp_path / "native" / "THIRD_PARTY_LICENSES.md"
    inventory.write_text(
        inventory.read_text(encoding="utf-8") + "\n| unreviewed-crate | 9.9.9 | MIT |\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Cargo license review differs from lock"):
        generate(tmp_path)


def test_supply_chain_requires_exact_cargo_checksums(tmp_path: Path) -> None:
    _copy_build_inputs(tmp_path)
    lock = tmp_path / "native" / "Cargo.lock"
    original = lock.read_text(encoding="utf-8")
    lock.write_text(
        original.replace(
            'checksum = "2304e00983f87ffb38b55b444b5e3b60a884b5d30c0fca7d82fe33449bbe55ea"\n',
            "",
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"heck 0\.5\.0 has no exact checksum"):
        generate(tmp_path)


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
        read_stable_regular_file(path, label="input.txt")


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


def test_rust_workflow_toolchains_are_immutable() -> None:
    workflow = (ROOT / ".github/workflows/native.yml").read_text(encoding="utf-8")
    steps = workflow.split("- uses: dtolnay/rust-toolchain@")[1:]
    assert len(steps) == 3
    observed = []
    for step in steps:
        block = step.split("\n      - ", 1)[0]
        toolchains = re.findall(r'(?m)^\s+toolchain:\s*"([^"]+)"\s*$', block)
        assert len(toolchains) == 1
        observed.extend(toolchains)
    assert observed == ["1.83.0", "1.83.0", "nightly-2025-01-15"]
    assert all(
        re.fullmatch(r"(?:[0-9]+\.[0-9]+\.[0-9]+|nightly-[0-9]{4}-[0-9]{2}-[0-9]{2})", item)
        for item in observed
    )


def test_native_workflow_runs_bounded_p7_contract_on_installed_wheel() -> None:
    workflow = (ROOT / ".github/workflows/native.yml").read_text(encoding="utf-8")
    condition = "matrix.os == 'ubuntu-latest' && matrix.python-version == '3.12'"
    assert workflow.count(condition) == 2
    assert "P7 installed-wheel encoded contract" in workflow
    for path in (
        "tests/test_native_encoded_foundation.py",
        "tests/test_private_native_encoded_integration.py",
        "tests/test_encoded_benchmark.py",
    ):
        assert path in workflow
    assert workflow.count('PYOWL2VEC_REQUIRE_NATIVE_TESTS: "1"') == 2
    assert "python -m tools.differential_encoded_native" in workflow
    assert "python -m tools.hostile_encoded_native" in workflow
    assert "--cases 32 --provider both --buffer-edges 7" in workflow
    assert "--sources 4 --provider both" in workflow
    assert "p7-generated-short.json" in workflow
    assert "p7-hostile-short.json" in workflow
    assert "p7-native-contract-linux-x86_64-py312" in workflow


def test_offline_smoke_images_are_platform_digest_pinned() -> None:
    workflow = (ROOT / ".github/workflows/packaging.yml").read_text(encoding="utf-8")
    images = generate_supply_chain._offline_python_images(workflow)
    assert set(images) == {"3.10", "3.11", "3.12", "3.13"}
    assert all(
        re.fullmatch(
            rf"python:{re.escape(version)}-slim@sha256:[0-9a-f]{{64}}",
            image,
        )
        for version, image in images.items()
    )
    assert "PYTHON_IMAGE: ${{ matrix.python.image }}" in workflow
    assert 'docker pull "$PYTHON_IMAGE"' in workflow
    assert "python:${{" not in workflow


def test_build_provenance_rejects_mutable_offline_smoke_image(tmp_path: Path) -> None:
    _copy_build_inputs(tmp_path)
    workflow = tmp_path / ".github/workflows/packaging.yml"
    original = workflow.read_text(encoding="utf-8")
    workflow.write_text(
        re.sub(
            r"python:3\.10-slim@sha256:[0-9a-f]{64}",
            "python:3.10-slim",
            original,
            count=1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="offline Python image matrix differs"):
        build_provenance(tmp_path)


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


def test_release_gate_exactly_binds_manifest_audit_and_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "example-0.1-py3-none-any.whl"
    artifact.write_bytes(b"not a zip archive")
    report = {
        "schema": "pyowl-projector.release-audit/1",
        "version": "0.1",
        "artifacts": [audit_artifact(artifact, expected_version="0.1")],
    }
    audit_path = tmp_path / "release-audit.json"
    audit_path.write_text(json.dumps(report), encoding="utf-8")
    (tmp_path / "SHA256SUMS").write_text(create_manifest(tmp_path), encoding="utf-8")
    checkout = {
        "commit": "1" * 40,
        "tree": "2" * 40,
        "tracked_worktree_clean": True,
    }
    monkeypatch.setattr(release_gate, "_checkout_identity", lambda root: (checkout, []))

    checks = release_gate._artifact_checks(ROOT, tmp_path, audit_path, "0.1")
    binding = next(check for check in checks if check["name"] == "artifact-evidence-binding")
    assert binding["passed"] is True
    assert binding["evidence"]["checkout_context"] == checkout
    assert binding["evidence"]["artifacts"] == [
        {
            "name": artifact.name,
            "kind": "invalid-wheel",
            "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "bytes": len(artifact.read_bytes()),
            "members": 0,
        }
    ]

    artifact.write_bytes(b"changed after audit")
    checks = release_gate._artifact_checks(ROOT, tmp_path, audit_path, "0.1")
    binding = next(check for check in checks if check["name"] == "artifact-evidence-binding")
    assert binding["passed"] is False
    assert "hash mismatch" in binding["detail"]
    assert "does not exactly match the current artifact audit" in binding["detail"]


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


def test_release_audit_rejects_sdist_links(tmp_path: Path) -> None:
    artifact = tmp_path / "example-0.1.tar.gz"
    with tarfile.open(artifact, "w:gz") as archive:
        link = tarfile.TarInfo("example-0.1/linked.py")
        link.type = tarfile.SYMTYPE
        link.linkname = "../outside.py"
        archive.addfile(link)
    report = audit_artifact(artifact, expected_version="0.1")
    assert report["errors"] == [
        "archive could not be read safely: unsupported sdist member type: 'example-0.1/linked.py'"
    ]


def test_release_audit_rejects_noncanonical_wheel_paths(tmp_path: Path) -> None:
    artifact = tmp_path / "example-0.1-py3-none-any.whl"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("example\\module.py", "unsafe")
    report = audit_artifact(artifact, expected_version="0.1")
    assert report["errors"] == [
        "archive could not be read safely: unsafe archive member: 'example\\\\module.py'"
    ]


def test_release_audit_enforces_expanded_member_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "example-0.1-py3-none-any.whl"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("example/module.py", "12345")
    monkeypatch.setattr(release_support, "MAX_ARCHIVE_MEMBER_BYTES", 4)
    report = audit_artifact(artifact, expected_version="0.1")
    assert report["errors"] == [
        "archive could not be read safely: archive member 'example/module.py' exceeds 4 byte limit"
    ]


def test_release_audit_binds_wheel_filename_tags_and_record(tmp_path: Path) -> None:
    artifact = tmp_path / "pyowl2vec_star_projector-0.1-py3-none-any.whl"
    _write_minimal_wheel(artifact)
    report = audit_artifact(artifact, expected_version="0.1")
    assert report["passed"] is True
    assert report["kind"] == "universal-wheel"
    assert report["errors"] == []

    mismatched = tmp_path / "other_project-0.1-py3-none-any.whl"
    mismatched.write_bytes(artifact.read_bytes())
    report = audit_artifact(mismatched, expected_version="0.1")
    assert (
        "wheel filename distribution 'other_project' is not "
        "'pyowl2vec_star_projector'" in report["errors"]
    )


def test_release_audit_rejects_wheel_record_payload_mismatch(tmp_path: Path) -> None:
    artifact = tmp_path / "pyowl2vec_star_projector-0.1-py3-none-any.whl"
    _write_minimal_wheel(artifact, tamper_after_record=True)
    report = audit_artifact(artifact, expected_version="0.1")
    assert report["passed"] is False
    assert report["errors"] == [
        "wheel RECORD hash mismatch for 'pyowl2vec_star_projector/__init__.py'",
        "wheel RECORD size mismatch for 'pyowl2vec_star_projector/__init__.py'",
    ]


def test_release_audit_requires_exact_source_controlled_legal_payloads() -> None:
    expected = release_legal_payloads(ROOT)
    dist_info = "pyowl2vec_star_projector-0.1.dist-info"
    members = {
        f"{dist_info}/licenses/LICENSE": expected["project_license"],
        f"{dist_info}/licenses/NOTICE": expected["project_notice"],
        (f"{dist_info}/licenses/THIRD_PARTY_NOTICES.md"): expected["third_party_notices"],
        (f"{dist_info}/licenses/native/THIRD_PARTY_LICENSES.md"): expected[
            "native_third_party_licenses"
        ],
        ("pyowl2vec_star_projector/conformance_data/LICENSE"): expected["conformance_license"],
    }
    errors: list[str] = []
    _audit_wheel_legal_payloads(members, dist_info, expected, errors)
    assert errors == []

    members[f"{dist_info}/licenses/NOTICE"] = b"changed"
    members["unreviewed/LICENSE"] = b"unknown"
    errors = []
    _audit_wheel_legal_payloads(members, dist_info, expected, errors)
    assert errors == [
        "artifact contains unreviewed legal payloads: ['unreviewed/LICENSE']",
        "artifact legal payload differs from source-controlled bytes: "
        "pyowl2vec_star_projector-0.1.dist-info/licenses/NOTICE",
    ]


def test_release_audit_binds_sdist_filename_and_root() -> None:
    version = "0.1"
    root = f"pyowl2vec_star_projector-{version}"
    metadata = (
        b"Metadata-Version: 2.4\n"
        b"Name: pyowl2vec-star-projector\n"
        b"Version: 0.1\n"
        b"Requires-Python: >=3.10\n"
        b"Requires-Dist: pyowl-core<0.2,>=0.1\n\n"
    )
    members = {
        f"{root}/pkg-info": metadata,
        **{
            f"{root}/{name}": b"present"
            for name in (
                "_build_backend.py",
                "license",
                "notice",
                "third_party_notices.md",
                "native/cargo.lock",
                "native/cargo.toml",
                "native/third_party_licenses.md",
                "releasing.md",
                "src/pyowl2vec_star_projector/conformance_data/license",
            )
        },
    }
    errors: list[str] = []
    assert _audit_sdist(Path("other-0.1.tar.gz"), members, version, errors) == "sdist"
    assert errors == ["sdist filename 'other-0.1.tar.gz' != 'pyowl2vec_star_projector-0.1.tar.gz'"]


def test_release_audit_hashes_exact_bytes_even_if_path_is_swapped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "example-0.1-py3-none-any.whl"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("example/module.py", "original")
    original = artifact.read_bytes()
    original_archive_members = audit_release.archive_members

    def swap_after_snapshot(path: Path, *, payload: bytes | None = None):
        path.write_bytes(b"replacement")
        return original_archive_members(path, payload=payload)

    monkeypatch.setattr(audit_release, "archive_members", swap_after_snapshot)
    report = audit_artifact(artifact, expected_version="0.1")
    assert report["sha256"] == hashlib.sha256(original).hexdigest()
    assert report["bytes"] == len(original)
    assert "release artifact changed during audit" in report["errors"]
    assert report["passed"] is False


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
    assert compatibility["tested_source"]["commit"] == ("af9bdb0b9178766b5f15806fb6a2f00b05e00e22")
    assert compatibility["native_ontology_redesign"] == {
        "commit": "af9bdb0b9178766b5f15806fb6a2f00b05e00e22",
        "classification": "behavior-preserving-native-ontology-redesign",
        "workpackages": ["WP14", "WP15", "WP16", "WP17", "WP18"],
        "summary": (
            "The settled WP14-WP18 APIs and implementations include native contracts, retained "
            "storage, streaming ingestion, native views, wire integration, and locally executable "
            "release/performance checks while preserving the Projector consumer fixture "
            "fingerprints and canonical edge bytes."
        ),
    }
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


def test_core_checkout_guard_rejects_wrong_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = "a" * 40

    def git_output(_root: Path, *arguments: str) -> str:
        if arguments[0] == "rev-parse":
            return "b" * 40
        return ""

    monkeypatch.setattr(check_core_compatibility, "_git_output", git_output)
    imported = tmp_path / "src/pyowl_core/__init__.py"
    assert check_core_compatibility._checkout_errors(tmp_path, expected, imported) == [
        f"pyOWLCore checkout is {'b' * 40}, expected exact commit {expected}",
    ]


def test_core_checkout_guard_rejects_unrelated_module_inside_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = "a" * 40

    def git_output(_root: Path, *_arguments: str) -> str:
        return expected if _arguments[0] == "rev-parse" else ""

    monkeypatch.setattr(check_core_compatibility, "_git_output", git_output)
    imported = tmp_path / "src/unrelated/__init__.py"
    assert check_core_compatibility._checkout_errors(tmp_path, expected, imported) == [
        f"imported pyowl_core from {imported.resolve()}, expected "
        f"{(tmp_path / 'src/pyowl_core/__init__.py').resolve()}"
    ]


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
