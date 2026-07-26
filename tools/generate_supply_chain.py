#!/usr/bin/env python3
"""Generate deterministic CycloneDX SBOMs and the machine-readable license inventory."""

from __future__ import annotations

import argparse
import hashlib
import re
import uuid
from pathlib import Path
from typing import Any

if __package__:
    from .release_support import canonical_json, read_stable_regular_file, write_if_changed
else:
    from release_support import canonical_json, read_stable_regular_file, write_if_changed

_NAMESPACE = uuid.UUID("2a582b0c-c26a-45a9-8a1f-9a2783ca1fef")
_BUILD_INPUT_PATHS = (
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
)


def _component(
    name: str,
    version: str,
    license_expression: str,
    purl: str,
    *,
    component_type: str = "library",
    hashes: list[dict[str, str]] | None = None,
    properties: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "type": component_type,
        "bom-ref": purl,
        "name": name,
        "version": version,
        "licenses": [{"expression": license_expression}],
        "purl": purl,
    }
    if hashes:
        result["hashes"] = hashes
    if properties:
        result["properties"] = properties
    return result


def _bom(
    version: str, scope: str, root: dict[str, object], components: list[dict[str, object]]
) -> dict[str, object]:
    root_ref = str(root["bom-ref"])
    component_refs = [str(component["bom-ref"]) for component in components]
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{uuid.uuid5(_NAMESPACE, f'{version}:{scope}')}",
        "version": 1,
        "metadata": {
            "tools": {
                "components": [
                    {
                        "type": "application",
                        "name": "pyowl-projector-supply-chain-generator",
                        "version": "1",
                    }
                ]
            },
            "component": root,
            "properties": [{"name": "pyowl-projector:scope", "value": scope}],
        },
        "components": components,
        "dependencies": [
            {"ref": root_ref, "dependsOn": component_refs},
            *({"ref": reference, "dependsOn": []} for reference in component_refs),
        ],
    }


def _cargo_licenses(text: str) -> dict[tuple[str, str], str]:
    result: dict[tuple[str, str], str] = {}
    pattern = re.compile(r"^\| ([^|]+) \| ([^|]+) \| ([^|]+) \|$")
    for line in text.splitlines():
        match = pattern.match(line)
        if not match or match.group(1).strip() == "Crate":
            continue
        name, version, expression = (part.strip() for part in match.groups())
        if set(version) == {"-"}:
            continue
        result[(name, version)] = expression
    return result


def _capture_build_inputs(root: Path) -> dict[str, bytes]:
    return {
        relative_path: read_stable_regular_file(
            root / relative_path,
            label=f"build provenance input {relative_path}",
        )
        for relative_path in _BUILD_INPUT_PATHS
    }


def _decode_build_input(payload: bytes, label: str) -> str:
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"build provenance: build input is not UTF-8: {label}") from error


def _load_build_toml(payload: bytes, label: str) -> dict[str, Any]:
    try:
        import tomllib
    except ImportError:  # pragma: no cover - Python 3.10
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError as error:  # pragma: no cover - actionable CLI failure
            raise RuntimeError(
                "Python 3.10 release tooling requires the dev extra (tomli)"
            ) from error
    try:
        loaded = tomllib.loads(_decode_build_input(payload, label))
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"build provenance: cannot parse TOML build input {label}") from error
    if not isinstance(loaded, dict):  # pragma: no cover - tomllib contract
        raise ValueError(f"build provenance: TOML build input is not a table: {label}")
    return loaded


def _workflow_pin(text: str, pattern: str, label: str) -> str:
    values = set(re.findall(pattern, text))
    if len(values) != 1:
        raise ValueError(f"build provenance: expected one unique {label}, got {sorted(values)!r}")
    return values.pop()


def _requirements(payload: bytes, label: str) -> list[str]:
    lines = []
    for raw_line in _decode_build_input(payload, label).splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#"):
            lines.append(line)
    if not lines:
        raise ValueError(f"build provenance: {label} contains no requirements")
    return lines


def _build_provenance(payloads: dict[str, bytes]) -> dict[str, object]:
    pyproject = _load_build_toml(payloads["pyproject.toml"], "pyproject.toml")
    cargo = _load_build_toml(payloads["native/Cargo.toml"], "native/Cargo.toml")
    project = pyproject.get("project")
    build_system = pyproject.get("build-system")
    cargo_package = cargo.get("package")
    if not isinstance(project, dict) or not isinstance(project.get("version"), str):
        raise ValueError("build provenance: pyproject.toml has no literal project version")
    if not isinstance(build_system, dict) or not isinstance(build_system.get("requires"), list):
        raise ValueError("build provenance: pyproject.toml has no build-system requirements")
    if not isinstance(cargo_package, dict) or not isinstance(
        cargo_package.get("rust-version"), str
    ):
        raise ValueError("build provenance: native/Cargo.toml has no literal rust-version")

    native_workflow = _decode_build_input(
        payloads[".github/workflows/native.yml"], ".github/workflows/native.yml"
    )
    packaging_workflows = "\n".join(
        _decode_build_input(payloads[path], path)
        for path in (
            ".github/workflows/packaging.yml",
            ".github/workflows/release-candidate.yml",
        )
    )
    rust_toolchain = _workflow_pin(
        native_workflow,
        r'(?m)^\s*toolchain:\s*"([0-9]+\.[0-9]+\.[0-9]+)"\s*$',
        "Rust release toolchain",
    )
    rust_sanitizer_toolchain = _workflow_pin(
        native_workflow,
        r'(?m)^\s*toolchain:\s*"(nightly-[0-9]{4}-[0-9]{2}-[0-9]{2})"\s*$',
        "Rust sanitizer toolchain",
    )
    rust_msrv = str(cargo_package["rust-version"])
    if not rust_toolchain.startswith(f"{rust_msrv}."):
        raise ValueError(
            "build provenance: Cargo rust-version "
            f"{rust_msrv!r} does not match workflow toolchain {rust_toolchain!r}"
        )
    cibuildwheel_revision = _workflow_pin(
        native_workflow,
        r"pypa/cibuildwheel@([0-9a-f]{40})",
        "cibuildwheel action revision",
    )
    source_date_epoch = _workflow_pin(
        packaging_workflows,
        r'echo "SOURCE_DATE_EPOCH=\$\((git log -1 --pretty=%ct)\)"',
        "SOURCE_DATE_EPOCH command",
    )
    inputs = {
        path: {
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        for path, payload in payloads.items()
    }
    return {
        "schema": "pyowl-projector.build-provenance/1",
        "scope": "deterministic-build-and-release-recipe",
        "distribution": "pyowl2vec-star-projector",
        "version": project["version"],
        "source_date_epoch": {
            "source": "release commit timestamp",
            "command": source_date_epoch,
        },
        "tools": {
            "cargo_manifest_rust_version": rust_msrv,
            "rust_toolchain": rust_toolchain,
            "rust_sanitizer_toolchain": rust_sanitizer_toolchain,
            "cibuildwheel_action": f"pypa/cibuildwheel@{cibuildwheel_revision}",
            "python_build_system": build_system["requires"],
            "python_fallback_requirements": _requirements(
                payloads["release/fallback-build-requirements.txt"],
                "release/fallback-build-requirements.txt",
            ),
            "python_native_requirements": _requirements(
                payloads["release/native-build-requirements.txt"],
                "release/native-build-requirements.txt",
            ),
        },
        "inputs": inputs,
    }


def build_provenance(root: Path) -> dict[str, object]:
    """Bind the deterministic build and release recipe to exact regular-file inputs."""
    return _build_provenance(_capture_build_inputs(root))


def generate(root: Path) -> dict[Path, bytes]:
    payloads = _capture_build_inputs(root)
    project = _load_build_toml(payloads["pyproject.toml"], "pyproject.toml")["project"]
    version = str(project["version"])
    root_ref = f"pkg:pypi/pyowl2vec-star-projector@{version}"
    runtime_root = _component(
        "pyowl2vec-star-projector",
        version,
        "Apache-2.0",
        root_ref,
        component_type="application",
    )
    core = _component(
        "pyowl-core",
        "0.1.x",
        "Apache-2.0",
        "pkg:pypi/pyowl-core@0.1.x",
        properties=[{"name": "pyowl-projector:version-constraint", "value": ">=0.1,<0.2"}],
    )
    runtime_bom = _bom(version, "runtime", runtime_root, [core])

    cargo_lock = _load_build_toml(payloads["native/Cargo.lock"], "native/Cargo.lock")
    license_map = _cargo_licenses(
        _decode_build_input(
            payloads["native/THIRD_PARTY_LICENSES.md"],
            "native/THIRD_PARTY_LICENSES.md",
        )
    )
    native_components: list[dict[str, object]] = []
    native_licenses: list[dict[str, str]] = []
    for package in sorted(cargo_lock["package"], key=lambda item: (item["name"], item["version"])):
        name = str(package["name"])
        crate_version = str(package["version"])
        if name == "pyowl2vec-star-projector-native":
            continue
        expression = license_map.get((name, crate_version))
        if expression is None:
            raise ValueError(f"license inventory missing Cargo package {name} {crate_version}")
        checksum = package.get("checksum")
        hashes = [{"alg": "SHA-256", "content": str(checksum)}] if checksum else None
        native_components.append(
            _component(
                name,
                crate_version,
                expression,
                f"pkg:cargo/{name}@{crate_version}",
                hashes=hashes,
                properties=[{"name": "pyowl-projector:scope", "value": "native-build-or-linked"}],
            )
        )
        native_licenses.append(
            {"name": name, "version": crate_version, "license": expression, "scope": "native"}
        )
    build_tools = [
        _component("build", "1.5.0", "MIT", "pkg:pypi/build@1.5.0"),
        _component(
            "packaging",
            "26.2",
            "Apache-2.0 OR BSD-2-Clause",
            "pkg:pypi/packaging@26.2",
        ),
        _component("pyproject-hooks", "1.2.0", "MIT", "pkg:pypi/pyproject-hooks@1.2.0"),
        _component(
            "tomli",
            "2.4.1",
            "MIT",
            "pkg:pypi/tomli@2.4.1",
            properties=[{"name": "pyowl-projector:environment", "value": "python<3.11"}],
        ),
        _component("setuptools", "83.0.0", "MIT", "pkg:pypi/setuptools@83.0.0"),
        _component("wheel", "0.46.3", "MIT", "pkg:pypi/wheel@0.46.3"),
        _component(
            "setuptools-rust",
            "1.13.0",
            "MIT",
            "pkg:pypi/setuptools-rust@1.13.0",
            properties=[{"name": "pyowl-projector:scope", "value": "explicit-native-only"}],
        ),
        _component(
            "semantic-version",
            "2.10.0",
            "BSD-2-Clause",
            "pkg:pypi/semantic-version@2.10.0",
            properties=[{"name": "pyowl-projector:scope", "value": "explicit-native-only"}],
        ),
    ]
    build_root = dict(runtime_root)
    build_root["bom-ref"] = f"{root_ref}?scope=native-build"
    native_bom = _bom(version, "native-build", build_root, [*build_tools, *native_components])

    license_inventory = {
        "schema": "pyowl-projector.license-inventory/1",
        "project": {
            "name": "pyowl2vec-star-projector",
            "version": version,
            "license": "Apache-2.0",
        },
        "runtime": [{"name": "pyowl-core", "version": ">=0.1,<0.2", "license": "Apache-2.0"}],
        "python_build": [
            {"name": "build", "version": "1.5.0", "license": "MIT"},
            {
                "name": "packaging",
                "version": "26.2",
                "license": "Apache-2.0 OR BSD-2-Clause",
            },
            {"name": "pyproject-hooks", "version": "1.2.0", "license": "MIT"},
            {
                "name": "tomli",
                "version": "2.4.1",
                "license": "MIT",
                "scope": "python<3.11",
            },
            {"name": "setuptools", "version": "83.0.0", "license": "MIT"},
            {"name": "wheel", "version": "0.46.3", "license": "MIT"},
            {
                "name": "setuptools-rust",
                "version": "1.13.0",
                "license": "MIT",
                "scope": "explicit-native-only",
            },
            {
                "name": "semantic-version",
                "version": "2.10.0",
                "license": "BSD-2-Clause",
                "scope": "explicit-native-only",
            },
        ],
        "native": native_licenses,
        "native_license_selection": {
            "dual_license_choice": "Apache-2.0",
            "base_text": "LICENSE",
            "additional_texts": ["LLVM-exception", "Unicode-3.0"],
            "bundle": "native/THIRD_PARTY_LICENSES.md",
        },
        "behavioral_references": [
            {
                "name": "mOWL OWL2VecStarProjector.scala",
                "commit": "d9935369144f9a618ece38b7b2a8f4293afe8c26",
                "blob": "a7f7584bbe687ae341cf0547bc0492ada87cf4b8",
                "license": "BSD-3-Clause",
                "shipped": "no",
            }
        ],
        "fixture_content": {
            "license": "CC0-1.0",
            "shipped": "consumer-conformance-only",
            "paths": ["pyowl2vec_star_projector/conformance_data/consumer.ofn"],
        },
        "java_components": [],
    }
    output = root / "release"
    return {
        output / "build-provenance.json": canonical_json(_build_provenance(payloads)),
        output / "sbom" / "runtime.cdx.json": canonical_json(runtime_bom),
        output / "sbom" / "native-build.cdx.json": canonical_json(native_bom),
        output / "license-inventory.json": canonical_json(license_inventory),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    outputs = generate(root)
    stale = [
        path
        for path, content in outputs.items()
        if not path.is_file() or path.read_bytes() != content
    ]
    if args.check:
        if stale:
            print("stale supply-chain outputs: " + ", ".join(str(path) for path in stale))
            return 1
        print("supply-chain outputs are current")
        return 0
    changed = [path for path, content in outputs.items() if write_if_changed(path, content)]
    print(f"updated {len(changed)} supply-chain file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
