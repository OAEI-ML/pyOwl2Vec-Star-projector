#!/usr/bin/env python3
"""Audit release archives for metadata, fallback, provenance, and Java boundaries."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
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
        read_stable_regular_file,
        read_toml,
        release_artifacts,
    )
else:
    from release_support import (
        FORBIDDEN_BINARY_SUFFIXES,
        FORBIDDEN_PATH_PARTS,
        archive_members,
        read_stable_regular_file,
        read_toml,
        release_artifacts,
    )

_NATIVE_SUFFIXES = (".dll", ".dylib", ".pyd", ".so")
_WHEEL_FILENAME = re.compile(
    r"(?P<distribution>[A-Za-z0-9_]+)-(?P<version>[A-Za-z0-9_.!+]+)"
    r"(?:-(?P<build>[0-9][A-Za-z0-9_.]*))?"
    r"-(?P<python>[A-Za-z0-9_.]+)-(?P<abi>[A-Za-z0-9_.]+)"
    r"-(?P<platform>[A-Za-z0-9_.]+)\.whl"
)
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


def _artifact_identity(path: Path) -> tuple[int, int, int, int, int, int]:
    value = path.lstat()
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _identity_error(
    path: Path,
    initial: tuple[int, int, int, int, int, int],
) -> str | None:
    try:
        final = _artifact_identity(path)
    except OSError as error:
        return f"release artifact disappeared during audit: {error}"
    if final != initial:
        return "release artifact changed during audit"
    return None


def audit_artifact(path: Path, *, expected_version: str) -> dict[str, object]:
    errors: list[str] = []
    payload: bytes | None = None
    initial_identity: tuple[int, int, int, int, int, int] | None = None
    try:
        initial_identity = _artifact_identity(path)
        payload = read_stable_regular_file(path, label=f"release artifact {path.name}")
        members = dict(archive_members(path, payload=payload))
    except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile) as error:
        kind = "invalid-wheel" if path.suffix == ".whl" else "invalid-sdist"
        if initial_identity is not None:
            changed = _identity_error(path, initial_identity)
            if changed is not None:
                errors.append(changed)
        return {
            "artifact": path.name,
            "kind": kind,
            "sha256": hashlib.sha256(payload).hexdigest() if payload is not None else None,
            "bytes": len(payload) if payload is not None else None,
            "members": 0,
            "errors": [f"archive could not be read safely: {error}", *errors],
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
        kind = _audit_wheel(path, members, expected_version, errors)
    elif path.name.endswith(".tar.gz"):
        kind = _audit_sdist(path, lowered, expected_version, errors)
    else:  # pragma: no cover - filtered by CLI
        kind = "unknown"
        errors.append("unsupported artifact suffix")

    _required_conformance_kit(lowered, errors)
    changed = _identity_error(path, initial_identity)
    if changed is not None:
        errors.append(changed)

    return {
        "artifact": path.name,
        "kind": kind,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
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
    lowered = {name.lower(): content for name, content in members.items()}
    expected_root = f"pyowl2vec_star_projector-{expected_version}.dist-info"
    dist_info_roots = {name.split("/", 1)[0] for name in members if ".dist-info/" in name.lower()}
    if dist_info_roots != {expected_root}:
        errors.append(f"wheel dist-info roots {sorted(dist_info_roots)!r} != {[expected_root]!r}")
    metadata_name = f"{expected_root}/metadata"
    wheel_name = f"{expected_root}/wheel"
    record_name = f"{expected_root}/record"
    if metadata_name not in lowered:
        errors.append(f"wheel missing exact metadata path: {metadata_name}")
    else:
        _audit_metadata(lowered[metadata_name], expected_version, errors)
    if wheel_name not in lowered:
        errors.append(f"wheel missing exact WHEEL path: {wheel_name}")
        wheel_metadata = None
    else:
        wheel_metadata = BytesParser(policy=default).parsebytes(lowered[wheel_name])
    if record_name not in lowered:
        errors.append(f"wheel missing exact RECORD path: {record_name}")
    else:
        _audit_record(members, f"{expected_root}/RECORD", lowered[record_name], errors)

    match = _WHEEL_FILENAME.fullmatch(path.name)
    filename_tags: set[tuple[str, str, str]] = set()
    if match is None:
        errors.append(f"invalid wheel filename: {path.name!r}")
        kind = "invalid-wheel"
    else:
        if match.group("distribution") != "pyowl2vec_star_projector":
            errors.append(
                f"wheel filename distribution {match.group('distribution')!r} is not "
                "'pyowl2vec_star_projector'"
            )
        if match.group("version") != expected_version:
            errors.append(
                f"wheel filename version {match.group('version')!r} != {expected_version!r}"
            )
        if match.group("build") is not None:
            errors.append("wheel filename unexpectedly contains a build tag")
        filename_tags = {
            (python, abi, platform)
            for python in match.group("python").split(".")
            for abi in match.group("abi").split(".")
            for platform in match.group("platform").split(".")
        }
        kind = "universal-wheel" if filename_tags == {("py3", "none", "any")} else "native-wheel"

    wheel_tags = (
        set()
        if wheel_metadata is None
        else {
            tuple(value.split("-", 2))
            for value in wheel_metadata.get_all("Tag", [])
            if len(value.split("-", 2)) == 3
        }
    )
    if wheel_tags != filename_tags:
        errors.append(
            f"WHEEL tags {sorted(wheel_tags)!r} do not exactly match filename tags "
            f"{sorted(filename_tags)!r}"
        )
    root_is_pure = None if wheel_metadata is None else wheel_metadata.get("Root-Is-Purelib")
    native_members = [name for name in members if name.endswith(_NATIVE_SUFFIXES)]
    if kind == "universal-wheel":
        if native_members:
            errors.append("universal fallback wheel contains native binaries")
        if root_is_pure != "true":
            errors.append("universal fallback wheel is not Root-Is-Purelib")
    elif kind == "native-wheel":
        if any(python != "cp310" or abi != "abi3" for python, abi, _ in filename_tags):
            errors.append("native wheel must use only the cp310-abi3 interpreter/ABI tag")
        unsupported_platforms = sorted(
            platform for _, _, platform in filename_tags if not _supported_native_platform(platform)
        )
        if unsupported_platforms:
            errors.append(f"native wheel has unsupported platform tags: {unsupported_platforms}")
        if not any("/_native" in f"/{name}" for name in native_members):
            errors.append("platform wheel does not contain the native extension")
        if root_is_pure != "false":
            errors.append("platform wheel unexpectedly claims Root-Is-Purelib")
        _audit_native_payloads(native_members, members, errors)
    _required_license_basenames(members, errors)
    return kind


def _supported_native_platform(platform: str) -> bool:
    return (
        (platform.startswith("manylinux") and platform.endswith(("_x86_64", "_aarch64")))
        or (platform.startswith("macosx") and platform.endswith(("_x86_64", "_arm64")))
        or platform == "win_amd64"
    )


def _audit_record(
    members: dict[str, bytes],
    record_name: str,
    content: bytes,
    errors: list[str],
) -> None:
    try:
        text = content.decode("utf-8")
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except (UnicodeDecodeError, csv.Error) as error:
        errors.append(f"wheel RECORD is invalid: {error}")
        return
    records: dict[str, tuple[str, str]] = {}
    for number, row in enumerate(rows, 1):
        if len(row) != 3:
            errors.append(f"wheel RECORD row {number} does not have exactly three fields")
            continue
        name, digest, size = row
        if name in records:
            errors.append(f"wheel RECORD repeats path: {name!r}")
            continue
        records[name] = (digest, size)
    missing = sorted(members.keys() - records.keys())
    extra = sorted(records.keys() - members.keys())
    if missing:
        errors.append(f"wheel RECORD is missing members: {missing}")
    if extra:
        errors.append(f"wheel RECORD names absent members: {extra}")
    for name in sorted(members.keys() & records.keys()):
        digest, size = records[name]
        if name == record_name:
            if digest or size:
                errors.append("wheel RECORD must leave its own hash and size empty")
            continue
        expected_digest = "sha256=" + base64.urlsafe_b64encode(
            hashlib.sha256(members[name]).digest()
        ).rstrip(b"=").decode("ascii")
        if digest != expected_digest:
            errors.append(f"wheel RECORD hash mismatch for {name!r}")
        if size != str(len(members[name])):
            errors.append(f"wheel RECORD size mismatch for {name!r}")


def _audit_sdist(
    path: Path,
    members: dict[str, bytes],
    expected_version: str,
    errors: list[str],
) -> str:
    expected_root = f"pyowl2vec_star_projector-{expected_version}"
    expected_filename = f"{expected_root}.tar.gz"
    if path.name != expected_filename:
        errors.append(f"sdist filename {path.name!r} != {expected_filename!r}")
    roots = {name.split("/", 1)[0] for name in members}
    if roots != {expected_root}:
        errors.append(f"sdist roots {sorted(roots)!r} != {[expected_root]!r}")
    metadata_name = f"{expected_root}/pkg-info"
    if metadata_name not in members:
        errors.append(f"sdist missing exact root PKG-INFO: {metadata_name}")
    else:
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
