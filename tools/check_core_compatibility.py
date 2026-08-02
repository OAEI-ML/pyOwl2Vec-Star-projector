#!/usr/bin/env python3
"""Verify the exact pyOWLCore checkout and its public consumer contract."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

if __package__:
    from .release_support import read_stable_regular_file
else:
    from release_support import read_stable_regular_file

_RELEASE_ONLY_CLASSIFICATION = "behavior-preserving-release-evidence-only"
_PRODUCTION_RELEASE_CLASSIFICATION = "production-release"
_RUNTIME_SOURCE_PREFIXES = ("src/", "native/")
_RUNTIME_SOURCE_FILES = frozenset({"pyproject.toml", "setup.py", "_build_backend.py"})
_COMPARATOR_ONLY_PREFIXES = (
    "benchmarks/comparators/",
    "tests/benchmark/comparators/",
)


def _version_pair(value: object) -> list[int] | None:
    if (
        isinstance(value, tuple)
        and len(value) == 2
        and all(type(item) is int and item >= 0 for item in value)
    ):
        return list(value)
    return None


def _git_output(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(root), *arguments),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown Git error"
        raise ValueError(detail)
    return completed.stdout.strip()


def _checkout_errors(
    core_root: Path,
    expected_commit: str,
    expected_tree: str,
    imported_module: Path,
) -> list[str]:
    errors: list[str] = []
    try:
        actual_commit = _git_output(core_root, "rev-parse", "--verify", "HEAD^{commit}")
        actual_tree = _git_output(core_root, "rev-parse", "--verify", "HEAD^{tree}")
        status = _git_output(
            core_root,
            "status",
            "--porcelain=v1",
            "--untracked-files=no",
            "--",
            "src",
            "native",
            "pyproject.toml",
            "setup.py",
            "_build_backend.py",
        )
    except ValueError as error:
        return [f"cannot inspect pyOWLCore checkout: {error}"]
    if actual_commit != expected_commit:
        errors.append(
            f"pyOWLCore checkout is {actual_commit}, expected exact commit {expected_commit}"
        )
    if actual_tree != expected_tree:
        errors.append(f"pyOWLCore tree is {actual_tree}, expected exact tree {expected_tree}")
    if status:
        errors.append("pyOWLCore checkout has tracked runtime-source changes")
    resolved_module = imported_module.resolve()
    expected_module = (core_root / "src/pyowl_core/__init__.py").resolve()
    if resolved_module != expected_module:
        errors.append(f"imported pyowl_core from {resolved_module}, expected {expected_module}")
    return errors


def _load_evidence(root: Path) -> dict[str, Any]:
    path = root / "release/core-compatibility.json"
    try:
        document = json.loads(read_stable_regular_file(path, label="core compatibility evidence"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError(f"cannot parse core compatibility evidence: {error}") from error
    if not isinstance(document, dict):
        raise ValueError("core compatibility evidence is not a JSON object")
    if document.get("schema") != "pyowl-projector.core-compatibility/1":
        raise ValueError("unsupported core compatibility evidence schema")
    return document


def release_evidence_errors(
    evidence: dict[str, Any],
    implementation_commit: str,
) -> list[str]:
    """Validate the separately recorded release-evidence-only core revision."""
    source = evidence.get("release_evidence_source")
    if not isinstance(source, dict):
        return ["core release-evidence source is not an object"]
    release_commit = source.get("commit")
    if not isinstance(release_commit, str) or re.fullmatch(r"[0-9a-f]{40}", release_commit) is None:
        return ["core release-evidence source is not an exact 40-character commit"]
    errors: list[str] = []
    classification = source.get("classification")
    if classification == _PRODUCTION_RELEASE_CLASSIFICATION:
        if release_commit != implementation_commit:
            errors.append("core production release source differs from the tested implementation")
        if source.get("implementation_commit") != implementation_commit:
            errors.append("core release-evidence source names a different implementation revision")
        if source.get("runtime_source_changed") is not False:
            errors.append("core production release source does not describe the tested tree")
        if source.get("changed_paths") != []:
            errors.append("core production release source lists changes beyond the tested tree")
        summary = source.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            errors.append("core release-evidence source has no summary")
        return errors
    if release_commit == implementation_commit:
        errors.append("core release-evidence revision does not follow the implementation revision")
    if source.get("implementation_commit") != implementation_commit:
        errors.append("core release-evidence source names a different implementation revision")
    if classification != _RELEASE_ONLY_CLASSIFICATION:
        errors.append("core release-evidence source has an unsupported classification")
    if source.get("runtime_source_changed") is not False:
        errors.append("core release-evidence source does not preserve runtime sources")
    changed_paths = source.get("changed_paths")
    if (
        not isinstance(changed_paths, list)
        or not changed_paths
        or not all(isinstance(path, str) and path for path in changed_paths)
    ):
        errors.append("core release-evidence changed paths are not a nonempty string list")
    else:
        if changed_paths != sorted(set(changed_paths)):
            errors.append("core release-evidence changed paths are not sorted and unique")
        if any(
            path in _RUNTIME_SOURCE_FILES or path.startswith(_RUNTIME_SOURCE_PREFIXES)
            for path in changed_paths
        ):
            errors.append("core release-evidence revision lists a runtime-source change")
    summary = source.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        errors.append("core release-evidence source has no summary")
    return errors


def release_evidence_checkout_errors(
    evidence: dict[str, Any],
    implementation_commit: str,
    core_root: Path,
) -> list[str]:
    """Verify the declared successor against the core repository object graph."""
    if release_evidence_errors(evidence, implementation_commit):
        return ["cannot inspect invalid core release-evidence metadata"]
    source = evidence["release_evidence_source"]
    release_commit = source["commit"]
    changed_paths = source["changed_paths"]
    try:
        _git_output(core_root, "cat-file", "-e", f"{implementation_commit}^{{commit}}")
        _git_output(core_root, "cat-file", "-e", f"{release_commit}^{{commit}}")
    except ValueError as error:
        return [f"cannot resolve core release-evidence Git objects: {error}"]
    if source["classification"] == _PRODUCTION_RELEASE_CLASSIFICATION:
        return []

    errors: list[str] = []
    try:
        _git_output(
            core_root,
            "merge-base",
            "--is-ancestor",
            implementation_commit,
            release_commit,
        )
    except ValueError:
        errors.append(
            "core release-evidence revision is not a descendant of the implementation revision"
        )

    try:
        parent_row = _git_output(
            core_root,
            "rev-list",
            "--parents",
            "-n",
            "1",
            release_commit,
        ).split()
    except ValueError as error:
        errors.append(f"cannot inspect core release-evidence parent: {error}")
    else:
        if parent_row != [release_commit, implementation_commit]:
            errors.append(
                "core release-evidence revision is not the direct implementation successor"
            )

    try:
        actual_paths = _git_output(
            core_root,
            "diff",
            "--name-only",
            "--diff-filter=ACDMRTUXB",
            implementation_commit,
            release_commit,
            "--",
        ).splitlines()
    except ValueError as error:
        errors.append(f"cannot inspect core release-evidence diff: {error}")
    else:
        if actual_paths != changed_paths:
            errors.append("core release-evidence actual changed paths differ from its declaration")
        if any(
            path in _RUNTIME_SOURCE_FILES or path.startswith(_RUNTIME_SOURCE_PREFIXES)
            for path in actual_paths
        ):
            errors.append("core release-evidence actual diff changes runtime sources")
        if any(not path.startswith(_COMPARATOR_ONLY_PREFIXES) for path in actual_paths):
            errors.append("core release-evidence actual diff is not comparator-only")
    return errors


def compatibility_errors(root: Path, core_root: Path) -> list[str]:
    """Return exact-source and public-conformance failures."""
    try:
        import pyowl_core

        from pyowl2vec_star_projector import (
            CORE_ADAPTER_PROTOCOL_VERSION,
            CORE_API_VERSION,
            CORE_MODEL_SCHEMA_VERSION,
            CORE_WIRE_FORMAT_VERSION,
            consumer_conformance_cases,
            consumer_conformance_fixture,
            consumer_conformance_fixture_metadata,
            verify_consumer_conformance,
        )
        from pyowl2vec_star_projector.encoded import (
            ENCODED_DESCRIPTOR_SHA256,
            ENCODED_SCHEMA_NAME,
            ENCODED_SCHEMA_VERSION,
        )
    except ImportError as error:
        return [f"cannot import public consumer APIs: {error}"]
    try:
        evidence = _load_evidence(root)
        source = evidence["tested_source"]
        expected_commit = source["commit"]
        expected_tree = source["tree"]
        expected_version = source["version"]
        expected_contract = evidence["public_contract"]
        fixture_evidence = evidence["consumer_fixture"]
    except (KeyError, TypeError, ValueError) as error:
        return [f"invalid core compatibility evidence: {error}"]
    if (
        not isinstance(expected_commit, str)
        or re.fullmatch(r"[0-9a-f]{40}", expected_commit) is None
    ):
        return ["tested pyOWLCore source is not an exact 40-character commit"]
    if not isinstance(expected_version, str):
        return ["tested pyOWLCore version is not a string"]
    if not isinstance(expected_tree, str) or re.fullmatch(r"[0-9a-f]{40}", expected_tree) is None:
        return ["tested pyOWLCore tree is not an exact 40-character object ID"]
    if not isinstance(expected_contract, dict):
        return ["tested pyOWLCore public contract is not an object"]
    if not isinstance(fixture_evidence, dict):
        return ["consumer fixture evidence is not an object"]

    errors = release_evidence_errors(evidence, expected_commit)
    if not errors:
        errors.extend(
            release_evidence_checkout_errors(
                evidence,
                expected_commit,
                core_root,
            )
        )
    module_file = getattr(pyowl_core, "__file__", None)
    if not isinstance(module_file, str):
        errors.append("imported pyowl_core has no filesystem source")
    else:
        errors.extend(
            _checkout_errors(
                core_root,
                expected_commit,
                expected_tree,
                Path(module_file),
            )
        )
    observed_version = getattr(pyowl_core, "__version__", None)
    if observed_version != expected_version:
        errors.append(
            f"imported pyowl_core version is {observed_version!r}, expected {expected_version!r}"
        )

    descriptor_digest = getattr(pyowl_core, "ENCODED_STRUCTURAL_DESCRIPTOR_SHA256_V2", None)
    observed_contract = {
        "api_version": _version_pair(getattr(pyowl_core, "API_VERSION", None)),
        "adapter_protocol_version": getattr(pyowl_core, "ADAPTER_PROTOCOL_VERSION", None),
        "model_schema_version": getattr(pyowl_core, "MODEL_SCHEMA_VERSION", None),
        "wire_format_version": _version_pair(getattr(pyowl_core, "WIRE_FORMAT_VERSION", None)),
        "encoded_schema_name": getattr(
            pyowl_core,
            "ENCODED_STRUCTURAL_SCHEMA_NAME_V2",
            None,
        ),
        "encoded_schema_version": getattr(
            pyowl_core,
            "ENCODED_STRUCTURAL_SCHEMA_VERSION_V2",
            None,
        ),
        "encoded_descriptor_sha256": (
            descriptor_digest.hex() if isinstance(descriptor_digest, bytes) else None
        ),
    }
    if observed_contract != expected_contract:
        errors.append("imported pyowl_core public contract differs from compatibility evidence")
    projector_contract = {
        "api_version": list(CORE_API_VERSION),
        "adapter_protocol_version": CORE_ADAPTER_PROTOCOL_VERSION,
        "model_schema_version": CORE_MODEL_SCHEMA_VERSION,
        "wire_format_version": list(CORE_WIRE_FORMAT_VERSION),
        "encoded_schema_name": ENCODED_SCHEMA_NAME,
        "encoded_schema_version": ENCODED_SCHEMA_VERSION,
        "encoded_descriptor_sha256": ENCODED_DESCRIPTOR_SHA256.hex(),
    }
    if projector_contract != expected_contract:
        errors.append("projector compatibility constants differ from core compatibility evidence")

    metadata = consumer_conformance_fixture_metadata()
    for field in (
        "sha256",
        "structural_fingerprint",
        "logical_fingerprint",
        "signature_fingerprint",
    ):
        if fixture_evidence.get(field) != getattr(metadata, field):
            errors.append(f"consumer fixture {field} differs from compatibility evidence")
    expected_edges = fixture_evidence.get("edge_digests")
    cases = consumer_conformance_cases()
    observed_edges = {case.case_id: case.canonical_edges_sha256 for case in cases}
    if expected_edges != observed_edges:
        errors.append("consumer edge digests differ from compatibility evidence")

    try:
        snapshot = pyowl_core.load_snapshot(
            consumer_conformance_fixture(),
            document_iri=metadata.document_iri,
            options=pyowl_core.LoadOptions(
                backend=pyowl_core.BackendPreference.PYTHON,
                format=pyowl_core.DocumentFormat.FUNCTIONAL,
            ),
        )
        for case in cases:
            result = verify_consumer_conformance(
                snapshot,
                case_id=case.case_id,
                backend="python",
            )
            if result.canonical_edges_sha256 != case.canonical_edges_sha256:
                errors.append(f"public conformance digest differs for {case.case_id}")
    except Exception as error:
        errors.append(
            f"pyOWLCore public consumer conformance failed: {type(error).__name__}: {error}"
        )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--core-root", type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    core_root = args.core_root.resolve()
    errors = compatibility_errors(root, core_root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("pyOWLCore exact source and public consumer conformance passed: 3 case(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
