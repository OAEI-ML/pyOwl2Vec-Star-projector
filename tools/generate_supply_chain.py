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
_PROJECT_NAME = "pyowl2vec-star-projector"
_PROJECT_LICENSE = "Apache-2.0"
_RUNTIME_DEPENDENCY = "pyowl-core>=0.1,<0.2"
_CRATES_IO_SOURCE = "registry+https://github.com/rust-lang/crates.io-index"
_PINNED_REQUIREMENT = re.compile(
    r"([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9][A-Za-z0-9.!+_-]*)"
    r"(?:\s*;\s*(.+))?"
)
_PYTHON_BUILD_LICENSES = {
    ("build", "1.5.0"): "MIT",
    ("packaging", "26.2"): "Apache-2.0 OR BSD-2-Clause",
    ("pyproject-hooks", "1.2.0"): "MIT",
    ("tomli", "2.4.1"): "MIT",
    ("setuptools", "83.0.0"): "MIT",
    ("wheel", "0.46.3"): "MIT",
    ("setuptools-rust", "1.13.0"): "MIT",
    ("semantic-version", "2.10.0"): "BSD-2-Clause",
}
_BUILD_INPUT_PATHS = (
    ".gitattributes",
    ".github/workflows/ci.yml",
    ".github/workflows/native.yml",
    ".github/workflows/packaging.yml",
    ".github/workflows/release-candidate.yml",
    ".github/workflows/release.yml",
    "MANIFEST.in",
    "_build_backend.py",
    "native/Cargo.lock",
    "native/Cargo.toml",
    "native/THIRD_PARTY_LICENSES.md",
    "native/build.rs",
    "pyproject.toml",
    "release/fallback-build-requirements.txt",
    "release/core-compatibility.json",
    "release/owner-release-authorization-0.1.1.md",
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
        if not name.strip("-") and not version.strip("-:"):
            continue
        key = (name, version)
        if key in result:
            raise ValueError(f"duplicate Cargo license inventory row {name} {version}")
        result[key] = expression
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


def _offline_python_images(text: str) -> dict[str, str]:
    rows = re.findall(
        r'(?m)^\s*-\s+version:\s*"([0-9]+\.[0-9]+)"\s*$'
        r"\n\s+image:\s*(python:([0-9]+\.[0-9]+)-slim@sha256:[0-9a-f]{64})\s*$",
        text,
    )
    images: dict[str, str] = {}
    for version, image, image_version in rows:
        if version != image_version:
            raise ValueError(
                "build provenance: offline Python image version mismatch "
                f"{version!r} != {image_version!r}"
            )
        if version in images:
            raise ValueError(f"build provenance: duplicate offline Python image for {version}")
        images[version] = image
    expected = {"3.10", "3.11", "3.12", "3.13"}
    if set(images) != expected:
        raise ValueError(
            "build provenance: offline Python image matrix differs; "
            f"expected={sorted(expected)}, observed={sorted(images)}"
        )
    return dict(sorted(images.items()))


def _requirements(payload: bytes, label: str) -> list[str]:
    lines = []
    for raw_line in _decode_build_input(payload, label).splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#"):
            lines.append(line)
    if not lines:
        raise ValueError(f"build provenance: {label} contains no requirements")
    return lines


def _normalize_python_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _pinned_requirements(
    lines: list[str],
    *,
    label: str,
) -> list[tuple[str, str, str | None]]:
    parsed: list[tuple[str, str, str | None]] = []
    seen: set[str] = set()
    for line in lines:
        match = _PINNED_REQUIREMENT.fullmatch(line)
        if match is None:
            raise ValueError(f"supply chain: {label} has non-exact requirement {line!r}")
        name = _normalize_python_name(match.group(1))
        version = match.group(2)
        marker = match.group(3)
        if name in seen:
            raise ValueError(f"supply chain: {label} repeats Python package {name}")
        seen.add(name)
        parsed.append((name, version, marker))
    return parsed


def _python_build_requirements(
    payloads: dict[str, bytes],
    pyproject: dict[str, Any],
) -> tuple[list[tuple[str, str, str | None, bool]], list[str], list[str]]:
    fallback_lines = _requirements(
        payloads["release/fallback-build-requirements.txt"],
        "release/fallback-build-requirements.txt",
    )
    native_lines = _requirements(
        payloads["release/native-build-requirements.txt"],
        "release/native-build-requirements.txt",
    )
    expected_include = "-r fallback-build-requirements.txt"
    if not native_lines or native_lines[0] != expected_include:
        raise ValueError(
            f"supply chain: native build requirements must begin with {expected_include!r}"
        )
    fallback = _pinned_requirements(
        fallback_lines,
        label="fallback build requirements",
    )
    native_only = _pinned_requirements(
        native_lines[1:],
        label="native build requirements",
    )
    combined = [(name, version, marker, False) for name, version, marker in fallback] + [
        (name, version, marker, True) for name, version, marker in native_only
    ]
    keys = {(name, version) for name, version, _, _ in combined}
    reviewed = set(_PYTHON_BUILD_LICENSES)
    if keys != reviewed:
        missing = sorted(reviewed - keys)
        extra = sorted(keys - reviewed)
        raise ValueError(
            f"supply chain: Python build license review differs; missing={missing}, extra={extra}"
        )
    names = [name for name, _, _, _ in combined]
    if len(names) != len(set(names)):
        raise ValueError("supply chain: Python build requirement names are not unique")

    build_system = pyproject.get("build-system")
    if not isinstance(build_system, dict) or build_system.get("requires") != [
        "setuptools==83.0.0",
        "wheel==0.46.3",
    ]:
        raise ValueError("supply chain: pyproject build-system pins differ from reviewed locks")
    project = pyproject.get("project")
    optional = project.get("optional-dependencies") if isinstance(project, dict) else None
    if not isinstance(optional, dict) or optional.get("native-build") != [
        "setuptools-rust==1.13.0"
    ]:
        raise ValueError("supply chain: native-build extra differs from reviewed lock")
    return combined, fallback_lines, native_lines


def _python_build_records(
    requirements: list[tuple[str, str, str | None, bool]],
) -> tuple[list[dict[str, object]], list[dict[str, str]]]:
    components: list[dict[str, object]] = []
    inventory: list[dict[str, str]] = []
    for name, version, marker, native_only in requirements:
        properties: list[dict[str, str]] = []
        if marker is not None:
            properties.append({"name": "pyowl-projector:environment-marker", "value": marker})
        if native_only:
            properties.append({"name": "pyowl-projector:scope", "value": "explicit-native-only"})
        license_expression = _PYTHON_BUILD_LICENSES[(name, version)]
        components.append(
            _component(
                name,
                version,
                license_expression,
                f"pkg:pypi/{name}@{version}",
                properties=properties or None,
            )
        )
        record = {
            "name": name,
            "version": version,
            "license": license_expression,
        }
        if marker is not None:
            record["environment_marker"] = marker
        if native_only:
            record["scope"] = "explicit-native-only"
        inventory.append(record)
    return components, inventory


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
    _, fallback_requirements, native_requirements = _python_build_requirements(
        payloads,
        pyproject,
    )

    native_workflow = _decode_build_input(
        payloads[".github/workflows/native.yml"], ".github/workflows/native.yml"
    )
    packaging_workflow = _decode_build_input(
        payloads[".github/workflows/packaging.yml"],
        ".github/workflows/packaging.yml",
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
    offline_python_images = _offline_python_images(packaging_workflow)
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
            "offline_python_images": offline_python_images,
            "python_build_system": build_system["requires"],
            "python_fallback_requirements": fallback_requirements,
            "python_native_requirements": native_requirements,
        },
        "inputs": inputs,
    }


def build_provenance(root: Path) -> dict[str, object]:
    """Bind the deterministic build and release recipe to exact regular-file inputs."""
    return _build_provenance(_capture_build_inputs(root))


def generate(root: Path) -> dict[Path, bytes]:
    payloads = _capture_build_inputs(root)
    pyproject = _load_build_toml(payloads["pyproject.toml"], "pyproject.toml")
    project = pyproject.get("project")
    if not isinstance(project, dict):
        raise ValueError("supply chain: pyproject.toml has no project table")
    if (
        project.get("name") != _PROJECT_NAME
        or project.get("license") != _PROJECT_LICENSE
        or project.get("dependencies") != [_RUNTIME_DEPENDENCY]
    ):
        raise ValueError(
            "supply chain: project identity, license, or runtime dependency boundary changed"
        )
    version = str(project["version"])
    python_requirements, _, _ = _python_build_requirements(payloads, pyproject)
    build_tools, python_build_inventory = _python_build_records(python_requirements)
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
    cargo_manifest = _load_build_toml(
        payloads["native/Cargo.toml"],
        "native/Cargo.toml",
    )
    cargo_package = cargo_manifest.get("package")
    if not isinstance(cargo_package, dict):
        raise ValueError("supply chain: native/Cargo.toml has no package table")
    locked_packages = cargo_lock.get("package")
    if not isinstance(locked_packages, list):
        raise ValueError("supply chain: native/Cargo.lock has no package inventory")
    license_map = _cargo_licenses(
        _decode_build_input(
            payloads["native/THIRD_PARTY_LICENSES.md"],
            "native/THIRD_PARTY_LICENSES.md",
        )
    )
    root_package_key = (
        str(cargo_package.get("name")),
        str(cargo_package.get("version")),
    )
    locked_keys: list[tuple[str, str]] = []
    for package in locked_packages:
        if not isinstance(package, dict):
            raise ValueError("supply chain: Cargo.lock package entry is not a table")
        locked_keys.append((str(package.get("name")), str(package.get("version"))))
    if len(locked_keys) != len(set(locked_keys)):
        raise ValueError("supply chain: Cargo.lock contains duplicate package identities")
    if locked_keys.count(root_package_key) != 1:
        raise ValueError(
            f"supply chain: Cargo.lock must contain exact native root {root_package_key!r}"
        )
    external_keys = set(locked_keys) - {root_package_key}
    missing_licenses = sorted(external_keys - license_map.keys())
    extra_licenses = sorted(license_map.keys() - external_keys)
    if missing_licenses or extra_licenses:
        raise ValueError(
            "supply chain: Cargo license review differs from lock; "
            f"missing={missing_licenses}, extra={extra_licenses}"
        )
    native_components: list[dict[str, object]] = []
    native_licenses: list[dict[str, str]] = []
    for package in sorted(locked_packages, key=lambda item: (item["name"], item["version"])):
        name = str(package["name"])
        crate_version = str(package["version"])
        if (name, crate_version) == root_package_key:
            continue
        expression = license_map[(name, crate_version)]
        source = package.get("source")
        checksum = package.get("checksum")
        if source != _CRATES_IO_SOURCE:
            raise ValueError(
                f"supply chain: Cargo package {name} {crate_version} has unreviewed source "
                f"{source!r}"
            )
        if not isinstance(checksum, str) or re.fullmatch(r"[0-9a-f]{64}", checksum) is None:
            raise ValueError(
                f"supply chain: Cargo package {name} {crate_version} has no exact checksum"
            )
        native_components.append(
            _component(
                name,
                crate_version,
                expression,
                f"pkg:cargo/{name}@{crate_version}",
                hashes=[{"alg": "SHA-256", "content": checksum}],
                properties=[{"name": "pyowl-projector:scope", "value": "native-build-or-linked"}],
            )
        )
        native_licenses.append(
            {"name": name, "version": crate_version, "license": expression, "scope": "native"}
        )
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
        "python_build": python_build_inventory,
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
