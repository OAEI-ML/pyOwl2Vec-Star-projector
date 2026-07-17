#!/usr/bin/env python3
"""Generate deterministic CycloneDX SBOMs and the machine-readable license inventory."""

from __future__ import annotations

import argparse
import re
import uuid
from pathlib import Path

if __package__:
    from .release_support import canonical_json, read_toml, write_if_changed
else:
    from release_support import canonical_json, read_toml, write_if_changed

_NAMESPACE = uuid.UUID("2a582b0c-c26a-45a9-8a1f-9a2783ca1fef")


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


def _cargo_licenses(path: Path) -> dict[tuple[str, str], str]:
    result: dict[tuple[str, str], str] = {}
    pattern = re.compile(r"^\| ([^|]+) \| ([^|]+) \| ([^|]+) \|$")
    for line in path.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if not match or match.group(1).strip() == "Crate":
            continue
        name, version, expression = (part.strip() for part in match.groups())
        if set(version) == {"-"}:
            continue
        result[(name, version)] = expression
    return result


def generate(root: Path) -> dict[Path, bytes]:
    project = read_toml(root / "pyproject.toml")["project"]
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

    cargo_lock = read_toml(root / "native" / "Cargo.lock")
    license_map = _cargo_licenses(root / "native" / "THIRD_PARTY_LICENSES.md")
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
        "fixture_content": {"license": "CC0-1.0", "shipped": "no"},
        "java_components": [],
    }
    output = root / "release"
    return {
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
