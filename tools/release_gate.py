#!/usr/bin/env python3
"""Aggregate locally provable release checks without masking external gates."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

if __package__:
    from .audit_release import audit_artifact, release_legal_payloads
    from .audit_runtime import audit as audit_runtime
    from .check_core_compatibility import release_evidence_errors
    from .check_dependency_dag import check_dependency_dag
    from .generate_supply_chain import generate
    from .hash_artifacts import verify_manifest_content
    from .release_support import (
        read_stable_regular_file,
        read_toml,
        release_artifacts,
    )
else:
    from audit_release import audit_artifact, release_legal_payloads
    from audit_runtime import audit as audit_runtime
    from check_core_compatibility import release_evidence_errors
    from check_dependency_dag import check_dependency_dag
    from generate_supply_chain import generate
    from hash_artifacts import verify_manifest_content
    from release_support import read_stable_regular_file, read_toml, release_artifacts


def _check(
    name: str,
    passed: bool,
    detail: str,
    *,
    evidence: dict[str, object] | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {"name": name, "passed": passed, "detail": detail}
    if evidence is not None:
        result["evidence"] = evidence
    return result


def _core_compatibility(root: Path, metadata: dict[str, object]) -> tuple[bool, str]:
    try:
        document = json.loads(
            (root / "release/core-compatibility.json").read_text(encoding="utf-8")
        )
        commit = str(document["tested_source"]["commit"])
        tree = str(document["tested_source"]["tree"])
        constraint = str(document["dependency_constraint"])
        fixture = document["consumer_fixture"]
        golden = json.loads(
            (root / "src/pyowl2vec_star_projector/conformance_data/goldens.json").read_text(
                encoding="utf-8"
            )
        )
        dependencies = metadata["project"]["dependencies"]
    except (KeyError, OSError, TypeError, ValueError) as error:
        return False, f"invalid core compatibility evidence: {type(error).__name__}"
    if document.get("schema") != "pyowl-projector.core-compatibility/1":
        return False, "unsupported core compatibility evidence schema"
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        return False, "tested core source commit is not a full Git object ID"
    if re.fullmatch(r"[0-9a-f]{40}", tree) is None:
        return False, "tested core source tree is not a full Git object ID"
    release_errors = release_evidence_errors(document, commit)
    if release_errors:
        return False, release_errors[0]
    if constraint not in dependencies:
        return False, f"project metadata does not retain {constraint}"
    expected_fields = (
        "sha256",
        "structural_fingerprint",
        "logical_fingerprint",
        "signature_fingerprint",
    )
    if any(fixture.get(name) != golden["fixture"].get(name) for name in expected_fields):
        return False, "consumer fixture fingerprints differ from core compatibility evidence"
    expected_edges = {case["case_id"]: case["canonical_edges_sha256"] for case in golden["cases"]}
    if fixture.get("edge_digests") != expected_edges:
        return False, "consumer edge digests differ from core compatibility evidence"
    workflow_paths = (
        root / ".github/workflows/ci.yml",
        root / ".github/workflows/native.yml",
        root / ".github/workflows/packaging.yml",
    )
    checkout = re.compile(
        r"repository:\s*OAEI-ML/pyOWLCore\s*\n"
        r"\s*ref:\s*([0-9a-f]{40})\s*\n"
        r"\s*path:\s*\.deps/pyowl-core"
    )
    references: list[str] = []
    occurrences = 0
    for path in workflow_paths:
        text = path.read_text(encoding="utf-8")
        occurrences += text.count("repository: OAEI-ML/pyOWLCore")
        references.extend(checkout.findall(text))
    if not references or len(references) != occurrences or set(references) != {commit}:
        return False, "one or more pyOWLCore source-checkout lanes are unpinned or mismatched"
    return True, f"{len(references)} source-checkout lane(s) pinned to {commit}"


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


def _checkout_identity(root: Path) -> tuple[dict[str, object], list[str]]:
    errors: list[str] = []
    try:
        commit = _git_output(root, "rev-parse", "--verify", "HEAD")
        tree = _git_output(root, "rev-parse", "--verify", "HEAD^{tree}")
        status = _git_output(root, "status", "--porcelain=v1", "--untracked-files=no")
    except ValueError as error:
        return {}, [f"cannot resolve release checkout identity: {error}"]
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        errors.append(f"release checkout commit is not an exact Git object ID: {commit!r}")
    if re.fullmatch(r"[0-9a-f]{40}", tree) is None:
        errors.append(f"release checkout tree is not an exact Git object ID: {tree!r}")
    clean = not status
    if not clean:
        errors.append("release checkout has tracked worktree or index changes")
    return {
        "commit": commit,
        "tree": tree,
        "tracked_worktree_clean": clean,
    }, errors


def _evidence_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return path.name


def _artifact_inventory(
    directory: Path,
) -> tuple[list[Path], dict[str, tuple[int, int, int, int, int, int]]]:
    artifacts = release_artifacts(directory)
    identities = {}
    for path in artifacts:
        value = path.lstat()
        identities[path.name] = (
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )
    return artifacts, identities


def _revalidate_payload(path: Path, payload: bytes, label: str) -> str | None:
    try:
        current = read_stable_regular_file(path, label=label)
    except ValueError as error:
        return str(error)
    if current != payload:
        return f"{label} changed during evidence verification"
    return None


def _artifact_checks(
    root: Path,
    artifact_directory: Path,
    audit_report_path: Path | None,
    version: str,
) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []
    artifact_set_errors: list[str] = []
    try:
        artifacts, initial_inventory = _artifact_inventory(artifact_directory)
    except (OSError, ValueError) as error:
        artifacts = []
        initial_inventory = {}
        artifact_set_errors.append(str(error))
    if not artifacts and not artifact_set_errors:
        artifact_set_errors.append("release artifact set is empty")
    try:
        legal_payloads = release_legal_payloads(root)
    except ValueError as error:
        legal_payloads = None
        artifact_set_errors.append(str(error))
    reports = [
        audit_artifact(
            path,
            expected_version=version,
            expected_legal_payloads=legal_payloads,
        )
        for path in artifacts
    ]
    kinds = {str(report["kind"]) for report in reports if report["passed"]}
    audit_errors = [
        f"{report['artifact']}: {report['errors']}" for report in reports if not report["passed"]
    ]
    checks.append(
        _check(
            "artifact-audit",
            bool(reports) and not audit_errors,
            "; ".join(audit_errors) if audit_errors else f"audited {len(reports)} artifact(s)",
        )
    )
    required_kinds = {"sdist", "universal-wheel"}
    checks.append(
        _check(
            "fallback-artifact-set",
            required_kinds <= kinds,
            f"found kinds: {sorted(kinds)}",
        )
    )

    manifest_path = artifact_directory / "SHA256SUMS"
    manifest_errors: list[str] = []
    manifest_captured = False
    try:
        manifest_payload = read_stable_regular_file(manifest_path, label="SHA256SUMS")
    except ValueError as error:
        manifest_payload = b""
        manifest_errors.append(str(error))
    else:
        manifest_captured = True
        try:
            manifest_errors.extend(verify_manifest_content(artifact_directory, manifest_payload))
        except (OSError, ValueError) as error:
            manifest_errors.append(str(error))
        expected_manifest = "".join(
            f"{report['sha256']}  {report['artifact']}\n" for report in reports
        ).encode("utf-8")
        if manifest_payload != expected_manifest:
            manifest_errors.append(
                "SHA256SUMS does not exactly bind the current audited artifact set"
            )

    report_errors: list[str] = []
    audit_payload = b""
    audit_captured = False
    if audit_report_path is None:
        report_errors.append("release audit report path was not provided")
    else:
        try:
            audit_payload = read_stable_regular_file(
                audit_report_path,
                label="release audit report",
            )
        except ValueError as error:
            report_errors.append(str(error))
        else:
            audit_captured = True
            try:
                audit_document = json.loads(audit_payload)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                report_errors.append(f"release audit report is not valid JSON: {error}")
            else:
                expected_document = {
                    "schema": "pyowl-projector.release-audit/1",
                    "version": version,
                    "artifacts": reports,
                }
                if audit_document != expected_document:
                    report_errors.append(
                        "release audit report does not exactly match the current artifact audit"
                    )

    checkout, checkout_errors = _checkout_identity(root)
    artifact_subjects = [
        {
            "name": report["artifact"],
            "kind": report["kind"],
            "sha256": report["sha256"],
            "bytes": report["bytes"],
            "members": report["members"],
        }
        for report in reports
    ]
    try:
        _, final_inventory = _artifact_inventory(artifact_directory)
    except (OSError, ValueError) as error:
        artifact_set_errors.append(str(error))
    else:
        if final_inventory != initial_inventory:
            artifact_set_errors.append(
                "release artifact set or file identities changed during evidence verification"
            )
    if manifest_captured:
        changed = _revalidate_payload(manifest_path, manifest_payload, "SHA256SUMS")
        if changed is not None:
            manifest_errors.append(changed)
    if audit_captured and audit_report_path is not None:
        changed = _revalidate_payload(
            audit_report_path,
            audit_payload,
            "release audit report",
        )
        if changed is not None:
            report_errors.append(changed)

    binding = {
        "schema": "pyowl-projector.artifact-binding/1",
        "scope": "verified-subject-digests-and-checkout-context",
        "checkout_context": checkout,
        "artifacts": artifact_subjects,
        "sha256_manifest": {
            "path": _evidence_path(manifest_path, root),
            "bytes": len(manifest_payload),
            "sha256": hashlib.sha256(manifest_payload).hexdigest(),
        },
        "release_audit": {
            "path": (
                _evidence_path(audit_report_path, root) if audit_report_path is not None else None
            ),
            "bytes": len(audit_payload),
            "sha256": hashlib.sha256(audit_payload).hexdigest(),
        },
    }
    binding_errors = [
        *artifact_set_errors,
        *manifest_errors,
        *report_errors,
        *checkout_errors,
    ]
    checks.append(
        _check(
            "artifact-evidence-binding",
            not binding_errors,
            "; ".join(binding_errors)
            if binding_errors
            else (
                f"verified {len(artifact_subjects)} artifact subject(s) alongside checkout "
                f"context {checkout['commit']} tree {checkout['tree']}; derivation requires "
                "the signed build attestation"
            ),
            evidence=binding,
        )
    )
    return checks


def local_checks(
    root: Path,
    artifact_directory: Path | None,
    audit_report_path: Path | None = None,
) -> list[dict[str, object]]:
    metadata = read_toml(root / "pyproject.toml")
    version = str(metadata["project"]["version"])
    checks: list[dict[str, object]] = []
    version_source = (root / "src/pyowl2vec_star_projector/_version.py").read_text(encoding="utf-8")
    checks.append(
        _check(
            "version-consistency",
            f'__version__ = "{version}"' in version_source,
            f"project and import version must both be {version}",
        )
    )
    cargo_version = str(read_toml(root / "native/Cargo.toml")["package"]["version"])
    expected_cargo = re.sub(r"rc(\d+)$", r"-rc.\1", version)
    checks.append(
        _check(
            "native-version-consistency",
            cargo_version == expected_cargo,
            f"Cargo {cargo_version}; expected {expected_cargo}",
        )
    )
    required_docs = (
        "CHANGELOG.md",
        "RELEASING.md",
        "docs/compatibility.md",
        "docs/migration.md",
        "release/fallback-build-requirements.txt",
        "release/native-build-requirements.txt",
        "release/external-gates.json",
        "release/core-compatibility.json",
        "release/build-provenance.json",
        "release/license-inventory.json",
        "release/sbom/native-build.cdx.json",
        "release/sbom/runtime.cdx.json",
        "reports/p6/consumer-conformance.md",
    )
    missing_docs = [name for name in required_docs if not (root / name).is_file()]
    checks.append(
        _check("release-documentation", not missing_docs, f"missing: {missing_docs or 'none'}")
    )
    core_compatible, core_detail = _core_compatibility(root, metadata)
    checks.append(_check("core-compatibility-pin", core_compatible, core_detail))
    runtime_errors = audit_runtime(root)
    checks.append(
        _check(
            "java-free-runtime-boundary",
            not runtime_errors,
            "; ".join(runtime_errors) if runtime_errors else "no forbidden imports or metadata",
        )
    )
    dag_errors = check_dependency_dag(root)
    checks.append(
        _check(
            "projector-dependency-dag",
            not dag_errors,
            "; ".join(dag_errors) if dag_errors else "no reverse consumer dependencies",
        )
    )
    generated = generate(root)
    stale = [
        str(path.relative_to(root))
        for path, content in generated.items()
        if not path.is_file() or path.read_bytes() != content
    ]
    checks.append(_check("supply-chain-current", not stale, f"stale: {stale or 'none'}"))
    if artifact_directory is not None:
        checks.extend(_artifact_checks(root, artifact_directory, audit_report_path, version))
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--audit-report", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--include-external",
        action="store_true",
        help="make unresolved authenticated/hosted gates release-blocking",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    artifact_directory = args.artifacts.resolve() if args.artifacts else None
    if artifact_directory is not None and args.audit_report is None:
        parser.error("--audit-report is required with --artifacts")
    if artifact_directory is None and args.audit_report is not None:
        parser.error("--audit-report requires --artifacts")
    audit_report_path = args.audit_report.resolve() if args.audit_report else None
    checks = local_checks(root, artifact_directory, audit_report_path)
    external = json.loads((root / "release/external-gates.json").read_text(encoding="utf-8"))
    external_blocked = [
        gate for gate in external["gates"] if gate["status"] not in ("passed", "not-applicable")
    ]
    report = {
        "schema": "pyowl-projector.release-gate/1",
        "version": read_toml(root / "pyproject.toml")["project"]["version"],
        "local_checks": checks,
        "external_gates": external["gates"],
        "local_passed": all(bool(check["passed"]) for check in checks),
        "release_passed": all(bool(check["passed"]) for check in checks) and not external_blocked,
    }
    artifact_binding = next(
        (check["evidence"] for check in checks if check["name"] == "artifact-evidence-binding"),
        None,
    )
    if artifact_binding is not None:
        report["artifact_binding"] = artifact_binding
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    if not report["local_passed"]:
        return 1
    return 2 if args.include_external and external_blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
