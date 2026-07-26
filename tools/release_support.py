"""Shared deterministic helpers for release tooling."""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import tarfile
import zipfile
from collections.abc import Callable, Iterator
from io import BufferedReader
from pathlib import Path, PurePosixPath
from typing import Any, TypeVar

ARCHIVE_SUFFIXES = (".whl", ".tar.gz")
FORBIDDEN_BINARY_SUFFIXES = (".class", ".ear", ".jar", ".war")
FORBIDDEN_PATH_PARTS = (
    "/native/target/",
    "/tests/fixtures/oracle/",
    "/tests/goldens/",
    "/tools/java-oracle/",
)
_T = TypeVar("_T")


def canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _consume_stable_regular_file(
    path: Path,
    *,
    label: str,
    consume: Callable[[BufferedReader], _T],
) -> _T:
    try:
        initial = path.lstat()
    except OSError as error:
        raise ValueError(f"cannot inspect {label}: {error}") from error
    if not stat.S_ISREG(initial.st_mode):
        raise ValueError(f"{label} must be a regular non-symlink file")
    try:
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            result = consume(stream)
            position = stream.tell()
            completed = os.fstat(stream.fileno())
        final = path.lstat()
    except OSError as error:
        raise ValueError(f"cannot read {label}: {error}") from error
    identities = {
        _stat_identity(initial),
        _stat_identity(opened),
        _stat_identity(completed),
        _stat_identity(final),
    }
    if len(identities) != 1 or not stat.S_ISREG(opened.st_mode) or position != opened.st_size:
        raise ValueError(f"{label} changed while reading")
    return result


def read_stable_regular_file(path: Path, *, label: str) -> bytes:
    payload = _consume_stable_regular_file(path, label=label, consume=lambda stream: stream.read())
    return payload


def sha256_file(path: Path) -> str:
    def consume(stream: BufferedReader) -> str:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        return digest.hexdigest()

    return _consume_stable_regular_file(
        path, label=f"release artifact {path.name}", consume=consume
    )


def release_artifacts(directory: Path) -> list[Path]:
    artifacts: list[Path] = []
    for path in directory.iterdir():
        if not path.name.endswith(ARCHIVE_SUFFIXES):
            continue
        try:
            identity = path.lstat()
        except OSError as error:
            raise ValueError(f"cannot inspect release artifact {path.name}: {error}") from error
        if not stat.S_ISREG(identity.st_mode):
            raise ValueError(f"release artifact must be a regular non-symlink file: {path.name}")
        artifacts.append(path)
    return sorted(artifacts)


def archive_members(path: Path, *, payload: bytes | None = None) -> Iterator[tuple[str, bytes]]:
    """Yield safe normalized archive member names and bytes."""
    if payload is None:
        payload = read_stable_regular_file(path, label=f"release artifact {path.name}")
    seen: set[str] = set()
    if path.suffix == ".whl":
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
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
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
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
