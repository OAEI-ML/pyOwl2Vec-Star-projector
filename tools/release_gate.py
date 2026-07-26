#!/usr/bin/env python3
"""Aggregate locally provable release checks without masking external gates."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

if __package__:
    from .audit_release import audit_artifact
    from .audit_runtime import audit as audit_runtime
    from .check_dependency_dag import check_dependency_dag
    from .generate_supply_chain import generate
    from .release_support import read_toml, release_artifacts
else:
    from audit_release import audit_artifact
    from audit_runtime import audit as audit_runtime
    from check_dependency_dag import check_dependency_dag
    from generate_supply_chain import generate
    from release_support import read_toml, release_artifacts


def _check(name: str, passed: bool, detail: str) -> dict[str, object]:
    return {"name": name, "passed": passed, "detail": detail}


def _core_compatibility(root: Path, metadata: dict[str, object]) -> tuple[bool, str]:
    try:
        document = json.loads(
            (root / "release/core-compatibility.json").read_text(encoding="utf-8")
        )
        commit = str(document["tested_source"]["commit"])
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


def local_checks(root: Path, artifact_directory: Path | None) -> list[dict[str, object]]:
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
        artifacts = release_artifacts(artifact_directory)
        reports = [audit_artifact(path, expected_version=version) for path in artifacts]
        kinds = {str(report["kind"]) for report in reports if report["passed"]}
        errors = [
            f"{report['artifact']}: {report['errors']}"
            for report in reports
            if not report["passed"]
        ]
        checks.append(
            _check(
                "artifact-audit",
                bool(reports) and not errors,
                "; ".join(errors) if errors else f"audited {len(reports)} artifact(s)",
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
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--include-external",
        action="store_true",
        help="make unresolved authenticated/hosted gates release-blocking",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    artifact_directory = args.artifacts.resolve() if args.artifacts else None
    checks = local_checks(root, artifact_directory)
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
