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
MAX_RELEASE_ARTIFACT_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 10_000
MAX_ARCHIVE_MEMBER_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE_EXPANDED_BYTES = 512 * 1024 * 1024
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


def _stable_stat_snapshots(
    initial: os.stat_result,
    opened: os.stat_result,
    completed: os.stat_result,
    final: os.stat_result,
    reopened: os.stat_result | None,
    *,
    windows: bool,
) -> bool:
    if not windows:
        return (
            len(
                {
                    _stat_identity(initial),
                    _stat_identity(opened),
                    _stat_identity(completed),
                    _stat_identity(final),
                }
            )
            == 1
        )

    # CPython's current Windows path-stat fast path does not populate the
    # device and file-index fields that fstat obtains from an open handle, and
    # some metadata fields can differ between the two APIs. Compare path
    # observations independently, then bind the consumed handle to a second
    # handle opened from the final path.
    if _stat_identity(initial) != _stat_identity(final):
        return False
    if _stat_identity(opened) != _stat_identity(completed):
        return False
    return reopened is not None and _stat_identity(opened) == _stat_identity(reopened)


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
        reopened = None
        if os.name == "nt":
            with path.open("rb") as verification_stream:
                reopened = os.fstat(verification_stream.fileno())
    except OSError as error:
        raise ValueError(f"cannot read {label}: {error}") from error
    if (
        not _stable_stat_snapshots(
            initial,
            opened,
            completed,
            final,
            reopened,
            windows=os.name == "nt",
        )
        or not stat.S_ISREG(opened.st_mode)
        or position != opened.st_size
    ):
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
    if len(payload) > MAX_RELEASE_ARTIFACT_BYTES:
        raise ValueError(f"release artifact exceeds {MAX_RELEASE_ARTIFACT_BYTES} byte limit")
    seen: set[str] = set()
    seen_casefolded: set[str] = set()
    expanded = 0
    if path.suffix == ".whl":
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ARCHIVE_MEMBERS:
                raise ValueError(f"archive exceeds {MAX_ARCHIVE_MEMBERS} member limit")
            for info in sorted(entries, key=lambda item: item.filename):
                name = _safe_member(info.filename, directory=info.is_dir())
                _record_member(name, seen, seen_casefolded)
                if info.is_dir():
                    continue
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG)):
                    raise ValueError(f"unsupported wheel member type: {info.filename!r}")
                if info.flag_bits & 0x1:
                    raise ValueError(f"encrypted wheel member is unsupported: {info.filename!r}")
                expanded = _expanded_size(expanded, info.file_size, name)
                content = archive.read(info)
                if len(content) != info.file_size:
                    raise ValueError(f"archive member size changed while reading: {name!r}")
                yield name, content
        return
    if path.name.endswith(".tar.gz"):
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            entries: list[tarfile.TarInfo] = []
            for info in archive:
                entries.append(info)
                if len(entries) > MAX_ARCHIVE_MEMBERS:
                    raise ValueError(f"archive exceeds {MAX_ARCHIVE_MEMBERS} member limit")
            for info in sorted(entries, key=lambda item: item.name):
                name = _safe_member(info.name, directory=info.isdir())
                _record_member(name, seen, seen_casefolded)
                if info.isdir():
                    continue
                if not info.isfile():
                    raise ValueError(f"unsupported sdist member type: {info.name!r}")
                expanded = _expanded_size(expanded, info.size, name)
                stream = archive.extractfile(info)
                if stream is None:  # pragma: no cover - guarded by isfile
                    raise ValueError(f"cannot read archive member {info.name!r}")
                content = stream.read()
                if len(content) != info.size:
                    raise ValueError(f"archive member size changed while reading: {name!r}")
                yield name, content
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


def _expanded_size(current: int, size: int, name: str) -> int:
    if size < 0 or size > MAX_ARCHIVE_MEMBER_BYTES:
        raise ValueError(f"archive member {name!r} exceeds {MAX_ARCHIVE_MEMBER_BYTES} byte limit")
    expanded = current + size
    if expanded > MAX_ARCHIVE_EXPANDED_BYTES:
        raise ValueError(
            f"archive expanded content exceeds {MAX_ARCHIVE_EXPANDED_BYTES} byte limit"
        )
    return expanded


def _record_member(name: str, seen: set[str], seen_casefolded: set[str]) -> None:
    if name in seen:
        raise ValueError(f"duplicate archive member: {name!r}")
    folded = name.casefold()
    if folded in seen_casefolded:
        raise ValueError(f"case-insensitive duplicate archive member: {name!r}")
    seen.add(name)
    seen_casefolded.add(folded)


def _safe_member(name: str, *, directory: bool = False) -> str:
    if "\\" in name or "\x00" in name or any(ord(character) < 32 for character in name):
        raise ValueError(f"unsafe archive member: {name!r}")
    normalized = name[:-1] if directory and name.endswith("/") else name
    parts = normalized.split("/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or len(normalized.encode("utf-8")) > 1024
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in parts)
        or parts[0].endswith(":")
    ):
        raise ValueError(f"unsafe archive member: {name!r}")
    return normalized
