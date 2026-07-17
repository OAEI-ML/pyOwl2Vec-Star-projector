#!/usr/bin/env python3
"""Audit release archives for metadata, fallback, provenance, and Java boundaries."""

from __future__ import annotations

import argparse
import json
import re
import tarfile
import zipfile
from email.parser import BytesParser
from email.policy import default
from pathlib import Path

if __package__:
    from .release_support import (
        FORBIDDEN_BINARY_SUFFIXES,
        FORBIDDEN_PATH_PARTS,
        archive_members,
        read_toml,
        release_artifacts,
        sha256_file,
    )
else:
    from release_support import (
        FORBIDDEN_BINARY_SUFFIXES,
        FORBIDDEN_PATH_PARTS,
        archive_members,
        read_toml,
        release_artifacts,
        sha256_file,
    )

_NATIVE_SUFFIXES = (".dll", ".dylib", ".pyd", ".so")
_JAVA_DEPENDENCIES = frozenset(
    {
        "deeponto",
        "exact-om",
        "jpype",
        "jpype1",
        "mowl",
        "oaei-bioml-eval",
        "owlapi",
        "pyelk",
        "pyhermit",
        "robot",
    }
)
_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_FORBIDDEN_NATIVE_MARKERS = (
    b"JNI_CreateJavaVM",
    b"JNI_GetCreatedJavaVMs",
    b"JNIEnv",
    b"JavaVM",
    b"libjvm",
    b"jvm.dll",
)


def audit_artifact(path: Path, *, expected_version: str) -> dict[str, object]:
    errors: list[str] = []
    try:
        members = dict(archive_members(path))
    except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile) as error:
        kind = "invalid-wheel" if path.suffix == ".whl" else "invalid-sdist"
        return {
            "artifact": path.name,
            "kind": kind,
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
            "members": 0,
            "errors": [f"archive could not be read safely: {error}"],
            "passed": False,
        }
    lowered = {name.lower(): content for name, content in members.items()}
    names = tuple(lowered)
    for name in names:
        wrapped = f"/{name}/"
        if name.endswith(FORBIDDEN_BINARY_SUFFIXES):
            errors.append(f"forbidden Java-family binary: {name}")
        if any(part in wrapped for part in FORBIDDEN_PATH_PARTS):
            errors.append(f"quarantined path shipped: {name}")

    if path.suffix == ".whl":
        kind = _audit_wheel(path, lowered, expected_version, errors)
    elif path.name.endswith(".tar.gz"):
        kind = _audit_sdist(lowered, expected_version, errors)
    else:  # pragma: no cover - filtered by CLI
        kind = "unknown"
        errors.append("unsupported artifact suffix")

    _required_conformance_kit(lowered, errors)

    return {
        "artifact": path.name,
        "kind": kind,
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "members": len(members),
        "errors": errors,
        "passed": not errors,
    }


def _audit_wheel(
    path: Path,
    members: dict[str, bytes],
    expected_version: str,
    errors: list[str],
) -> str:
    metadata_name = _one(members, ".dist-info/metadata", errors)
    wheel_name = _one(members, ".dist-info/wheel", errors)
    if metadata_name:
        _audit_metadata(members[metadata_name], expected_version, errors)
    wheel_text = members[wheel_name].decode("utf-8") if wheel_name else ""
    native_members = [name for name in members if name.endswith(_NATIVE_SUFFIXES)]
    pure_name = path.name.endswith("-py3-none-any.whl")
    if pure_name:
        if native_members:
            errors.append("universal fallback wheel contains native binaries")
        if "root-is-purelib: true" not in wheel_text.lower():
            errors.append("universal fallback wheel is not Root-Is-Purelib")
        if "tag: py3-none-any" not in wheel_text.lower():
            errors.append("universal fallback wheel lacks py3-none-any tag")
        kind = "universal-wheel"
    else:
        if not any("/_native" in f"/{name}" for name in native_members):
            errors.append("platform wheel does not contain the native extension")
        if "root-is-purelib: false" not in wheel_text.lower():
            errors.append("platform wheel unexpectedly claims Root-Is-Purelib")
        kind = "native-wheel"
        _audit_native_payloads(native_members, members, errors)
    _required_license_basenames(members, errors)
    return kind


def _audit_sdist(members: dict[str, bytes], expected_version: str, errors: list[str]) -> str:
    root_metadata = [
        name for name in members if name.endswith("/pkg-info") and len(Path(name).parts) == 2
    ]
    metadata_name = root_metadata[0] if len(root_metadata) == 1 else None
    if metadata_name is None:
        errors.append(f"expected one root PKG-INFO, found {len(root_metadata)}")
    if metadata_name:
        _audit_metadata(members[metadata_name], expected_version, errors)
    required = (
        "_build_backend.py",
        "license",
        "notice",
        "third_party_notices.md",
        "native/cargo.lock",
        "native/cargo.toml",
        "native/third_party_licenses.md",
        "releasing.md",
    )
    for suffix in required:
        if not any(name == suffix or name.endswith(f"/{suffix}") for name in members):
            errors.append(f"sdist missing required release source: {suffix}")
    return "sdist"


def _audit_metadata(content: bytes, expected_version: str, errors: list[str]) -> None:
    metadata = BytesParser(policy=default).parsebytes(content)
    if metadata["Name"] != "pyowl2vec-star-projector":
        errors.append(f"unexpected distribution name: {metadata['Name']!r}")
    if metadata["Version"] != expected_version:
        errors.append(f"metadata version {metadata['Version']!r} != {expected_version!r}")
    if metadata["Requires-Python"] != ">=3.10":
        errors.append(f"unexpected Requires-Python: {metadata['Requires-Python']!r}")
    requirements = metadata.get_all("Requires-Dist", [])
    base = [item for item in requirements if "extra ==" not in item]
    if len(base) != 1 or not re.match(r"^pyowl-core\s*<0\.2,>=0\.1$", base[0]):
        errors.append(f"unexpected base dependencies: {base!r}")
    for requirement in requirements:
        match = _REQUIREMENT_NAME.match(requirement)
        if match is None:
            errors.append(f"invalid dependency requirement: {requirement!r}")
            continue
        dependency = re.sub(r"[-_.]+", "-", match.group(1)).lower()
        if dependency in _JAVA_DEPENDENCIES:
            errors.append(f"Java-facing dependency or extra present: {dependency}")


def _audit_native_payloads(
    names: list[str],
    members: dict[str, bytes],
    errors: list[str],
) -> None:
    for name in names:
        payload = members[name]
        found = [marker for marker in _FORBIDDEN_NATIVE_MARKERS if marker in payload]
        markers = [
            marker.decode("ascii")
            for marker in found
            if not any(marker != other and marker in other for other in found)
        ]
        if markers:
            errors.append(
                f"native extension contains JVM/JNI marker(s) {', '.join(markers)}: {name}"
            )


def _required_license_basenames(members: dict[str, bytes], errors: list[str]) -> None:
    basenames = {Path(name).name.lower() for name in members}
    for required in ("license", "notice", "third_party_notices.md", "third_party_licenses.md"):
        if required not in basenames:
            errors.append(f"wheel missing license/provenance file: {required}")


def _required_conformance_kit(members: dict[str, bytes], errors: list[str]) -> None:
    for required in (
        "conformance_data/consumer.ofn",
        "conformance_data/goldens.json",
        "conformance_data/license",
    ):
        if not any(name.endswith(required) for name in members):
            errors.append(f"artifact missing consumer conformance resource: {required}")


def _one(members: dict[str, bytes], suffix: str, errors: list[str]) -> str | None:
    found = [name for name in members if name.endswith(suffix)]
    if len(found) != 1:
        errors.append(f"expected one *{suffix}, found {len(found)}")
        return None
    return found[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifacts", type=Path)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    version = str(read_toml(root / "pyproject.toml")["project"]["version"])
    artifacts = release_artifacts(args.artifacts.resolve())
    if not artifacts:
        parser.error(f"no wheel or sdist artifacts in {args.artifacts}")
    results = [audit_artifact(path, expected_version=version) for path in artifacts]
    report = {"schema": "pyowl-projector.release-audit/1", "version": version, "artifacts": results}
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if all(bool(result["passed"]) for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
