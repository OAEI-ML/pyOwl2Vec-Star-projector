"""Shared deterministic helpers for release tooling."""

from __future__ import annotations

import hashlib
import json
import tarfile
import zipfile
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Any

ARCHIVE_SUFFIXES = (".whl", ".tar.gz")
FORBIDDEN_BINARY_SUFFIXES = (".class", ".ear", ".jar", ".war")
FORBIDDEN_PATH_PARTS = (
    "/native/target/",
    "/tests/fixtures/oracle/",
    "/tests/goldens/",
    "/tools/java-oracle/",
)


def canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def release_artifacts(directory: Path) -> list[Path]:
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.name.endswith(ARCHIVE_SUFFIXES)
    )


def archive_members(path: Path) -> Iterator[tuple[str, bytes]]:
    """Yield safe normalized archive member names and bytes."""
    seen: set[str] = set()
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            for info in sorted(archive.infolist(), key=lambda item: item.filename):
                if info.is_dir():
                    continue
                name = _safe_member(info.filename)
                if name in seen:
                    raise ValueError(f"duplicate archive member: {name!r}")
                seen.add(name)
                yield name, archive.read(info)
        return
    if path.name.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as archive:
            for info in sorted(archive.getmembers(), key=lambda item: item.name):
                if not info.isfile():
                    continue
                name = _safe_member(info.name)
                if name in seen:
                    raise ValueError(f"duplicate archive member: {name!r}")
                seen.add(name)
                stream = archive.extractfile(info)
                if stream is None:  # pragma: no cover - guarded by isfile
                    raise ValueError(f"cannot read archive member {info.name!r}")
                yield name, stream.read()
        return
    raise ValueError(f"unsupported release artifact: {path}")


def normalized_members(path: Path) -> dict[str, str]:
    """Return content hashes with the sdist's generated root directory removed."""
    members = list(archive_members(path))
    strip_root = path.name.endswith(".tar.gz")
    result: dict[str, str] = {}
    for name, content in members:
        parts = PurePosixPath(name).parts
        normalized = "/".join(parts[1:]) if strip_root and len(parts) > 1 else name
        result[normalized] = hashlib.sha256(content).hexdigest()
    return result


def read_toml(path: Path) -> dict[str, Any]:
    try:
        import tomllib
    except ImportError:  # pragma: no cover - Python 3.10
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError as error:  # pragma: no cover - actionable CLI failure
            raise RuntimeError(
                "Python 3.10 release tooling requires the dev extra (tomli)"
            ) from error
    with path.open("rb") as stream:
        return tomllib.load(stream)


def write_if_changed(path: Path, content: bytes) -> bool:
    if path.is_file() and path.read_bytes() == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return True


def _safe_member(name: str) -> str:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe archive member: {name!r}")
    return str(path)
