"""Bounded edge delivery and private external canonical sorting.

The spill format in this module is deliberately private.  It carries only edge
strings, uses random file names and owner-only permissions, and is removed when
an iterator is exhausted, closed, cancelled, or collected.
"""

from __future__ import annotations

import atexit
import hashlib
import heapq
import os
import shutil
import sqlite3
import struct
import tempfile
import threading
import weakref
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

from .errors import InvalidProjectionOptionsError, ProjectionResourceError
from .model import Edge
from .options import DuplicatePolicy, EdgeOrder

_RUN_MAGIC = b"PYOWL2VEC-RUN\x00\x01"
_RUN_HEADER = struct.Struct(">15sQQ32s")
_FIELD_LENGTHS = struct.Struct(">III")
_MAX_FIELD_BYTES = (1 << 32) - 1


def _restrict_owner_file_permissions(descriptor: int) -> None:
    """Apply POSIX owner-only permissions when the platform exposes fchmod."""
    fchmod = getattr(os, "fchmod", None)
    if fchmod is not None:
        fchmod(descriptor, 0o600)


class CancellationTokenLike(Protocol):
    """Structural subset of the core cancellation token used by projection."""

    def check(self) -> None: ...


class StatisticsLike(Protocol):
    raw_edges: int
    distinct_edges: int
    duplicate_edges: int


@dataclass(frozen=True, slots=True)
class StreamingLimits:
    """Resource bounds for one edge iterator.

    ``max_temporary_bytes`` bounds simultaneously live private files.
    ``max_spill_bytes`` bounds all bytes written, including merge passes.
    The defaults limit file descriptors while leaving corpus-dependent byte and
    edge limits to the caller.
    """

    merge_fan_in: int = 32
    max_open_files: int = 64
    max_total_edges: int | None = None
    max_spill_bytes: int | None = None
    max_temporary_bytes: int | None = None
    cancellation_check_interval: int = 4_096

    def __post_init__(self) -> None:
        _positive_int("merge_fan_in", self.merge_fan_in, minimum=2)
        _positive_int("max_open_files", self.max_open_files, minimum=3)
        _positive_int("cancellation_check_interval", self.cancellation_check_interval)
        if self.merge_fan_in + 1 > self.max_open_files:
            raise InvalidProjectionOptionsError(
                "merge_fan_in plus one output file must not exceed max_open_files",
                details={
                    "merge_fan_in": self.merge_fan_in,
                    "max_open_files": self.max_open_files,
                },
            )
        _optional_nonnegative_int("max_total_edges", self.max_total_edges)
        _optional_nonnegative_int("max_spill_bytes", self.max_spill_bytes)
        _optional_nonnegative_int("max_temporary_bytes", self.max_temporary_bytes)


@dataclass(frozen=True, slots=True)
class SpillMetrics:
    """Non-path operational evidence for one canonical stream."""

    runs_created: int
    merge_passes: int
    peak_live_bytes: int
    total_spill_bytes: int


@dataclass(frozen=True, slots=True)
class _Run:
    path: Path
    edge_count: int
    payload_bytes: int
    size_bytes: int


_WORKSPACES: weakref.WeakSet[_SpillWorkspace] = weakref.WeakSet()
_WORKSPACES_LOCK = threading.Lock()


def _cleanup_workspaces() -> None:
    with _WORKSPACES_LOCK:
        workspaces = tuple(_WORKSPACES)
    for workspace in workspaces:
        workspace.cleanup()


atexit.register(_cleanup_workspaces)


class _SpillWorkspace:
    """One private directory with centrally accounted temporary resources."""

    def __init__(self, parent: os.PathLike[str] | str | None, limits: StreamingLimits) -> None:
        self.limits = limits
        self.live_bytes = 0
        self.peak_live_bytes = 0
        self.total_written = 0
        self.runs_created = 0
        self.merge_passes = 0
        self._runs: dict[Path, int] = {}
        self._cleaned = False
        selected = None if parent is None else Path(parent)
        if selected is not None and not selected.is_dir():
            raise ProjectionResourceError(
                "projection temporary directory is not an existing directory",
                details={"stage": "temporary-directory"},
            )
        try:
            self.directory = Path(tempfile.mkdtemp(prefix="pyowl2vec-", dir=selected))
            os.chmod(self.directory, 0o700)
        except OSError as error:
            raise _os_resource_error("temporary-directory", error) from error
        try:
            self._available_estimate = shutil.disk_usage(self.directory).free
        except OSError as error:
            shutil.rmtree(self.directory, ignore_errors=True)
            raise _os_resource_error("temporary-directory", error) from error
        with _WORKSPACES_LOCK:
            _WORKSPACES.add(self)

    def write_sorted_run(
        self,
        edges: list[Edge],
        *,
        duplicates: DuplicatePolicy,
        cancellation_token: CancellationTokenLike | None,
    ) -> _Run:
        edges.sort(key=Edge.canonical_key)
        values: Iterable[Edge] = edges
        if duplicates == "unique":
            values = _coalesce_sorted(values)
        return self._write_run(values, cancellation_token=cancellation_token)

    def write_merged_run(
        self,
        runs: tuple[_Run, ...],
        *,
        duplicates: DuplicatePolicy,
        cancellation_token: CancellationTokenLike | None,
    ) -> _Run:
        if len(runs) > self.limits.merge_fan_in:
            raise AssertionError("merge group exceeds validated fan-in")
        values = self.merge(runs, cancellation_token=cancellation_token)
        if duplicates == "unique":
            values = _coalesce_sorted(values)
        try:
            created = self._write_run(values, cancellation_token=cancellation_token)
        finally:
            close = getattr(values, "close", None)
            if callable(close):
                close()
        for run in runs:
            self.delete(run)
        return created

    def _write_run(
        self,
        edges: Iterable[Edge],
        *,
        cancellation_token: CancellationTokenLike | None,
    ) -> _Run:
        descriptor: int | None = None
        path: Path | None = None
        accounted = 0
        try:
            descriptor, name = tempfile.mkstemp(prefix="run-", suffix=".bin", dir=self.directory)
            path = Path(name)
            _restrict_owner_file_permissions(descriptor)
            with os.fdopen(descriptor, "w+b", buffering=1024 * 1024) as stream:
                descriptor = None
                self._grow(_RUN_HEADER.size, stage="run-header")
                accounted += _RUN_HEADER.size
                stream.write(b"\x00" * _RUN_HEADER.size)
                digest = hashlib.sha256()
                count = 0
                payload_bytes = 0
                for edge in edges:
                    if count % self.limits.cancellation_check_interval == 0:
                        _check_cancel(cancellation_token)
                    record = _encode_edge(edge)
                    self._grow(len(record), stage="run-write")
                    accounted += len(record)
                    stream.write(record)
                    digest.update(record)
                    payload_bytes += len(record)
                    count += 1
                _check_cancel(cancellation_token)
                stream.seek(0)
                stream.write(
                    _RUN_HEADER.pack(
                        _RUN_MAGIC,
                        count,
                        payload_bytes,
                        digest.digest(),
                    )
                )
                stream.flush()
            assert path is not None
            run = _Run(path, count, payload_bytes, accounted)
            self._runs[path] = accounted
            self.runs_created += 1
            return run
        except ProjectionResourceError:
            self._forget_partial(path, accounted)
            raise
        except (OSError, MemoryError, OverflowError) as error:
            self._forget_partial(path, accounted)
            if isinstance(error, OSError):
                raise _os_resource_error("run-write", error) from error
            raise ProjectionResourceError(
                "projection could not allocate its bounded run buffer",
                details={"stage": "run-write", "cause": type(error).__name__},
            ) from error
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def merge(
        self,
        runs: tuple[_Run, ...],
        *,
        cancellation_token: CancellationTokenLike | None,
    ) -> Iterator[Edge]:
        readers: list[_RunReader] = []
        heap: list[tuple[tuple[bytes, bytes, bytes], int, Edge]] = []
        try:
            for index, run in enumerate(runs):
                reader = _RunReader(run)
                readers.append(reader)
                try:
                    edge = next(reader)
                except StopIteration:
                    continue
                heapq.heappush(heap, (edge.canonical_key(), index, edge))
            emitted = 0
            while heap:
                if emitted % self.limits.cancellation_check_interval == 0:
                    _check_cancel(cancellation_token)
                _, index, edge = heapq.heappop(heap)
                emitted += 1
                yield edge
                try:
                    successor = next(readers[index])
                except StopIteration:
                    continue
                heapq.heappush(heap, (successor.canonical_key(), index, successor))
            _check_cancel(cancellation_token)
        finally:
            for reader in readers:
                reader.close()

    def reduce(
        self,
        runs: list[_Run],
        *,
        duplicates: DuplicatePolicy,
        cancellation_token: CancellationTokenLike | None,
    ) -> tuple[_Run, ...]:
        current = runs
        while len(current) > self.limits.merge_fan_in:
            self.merge_passes += 1
            following: list[_Run] = []
            for offset in range(0, len(current), self.limits.merge_fan_in):
                group = tuple(current[offset : offset + self.limits.merge_fan_in])
                if len(group) == 1:
                    following.append(group[0])
                else:
                    following.append(
                        self.write_merged_run(
                            group,
                            duplicates=duplicates,
                            cancellation_token=cancellation_token,
                        )
                    )
            current = following
        return tuple(current)

    def delete(self, run: _Run) -> None:
        size = self._runs.pop(run.path, 0)
        try:
            run.path.unlink(missing_ok=True)
        except OSError:
            # A later workspace cleanup retries.  Caller-owned paths are never touched.
            if size:
                self._runs[run.path] = size
            return
        self.live_bytes = max(0, self.live_bytes - size)
        self._available_estimate += size

    def cleanup(self) -> None:
        if self._cleaned:
            return
        for path in tuple(self._runs):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        self._runs.clear()
        self.live_bytes = 0
        try:
            shutil.rmtree(self.directory)
        except OSError:
            pass
        if self.directory.exists():
            return
        self._cleaned = True
        with _WORKSPACES_LOCK:
            _WORKSPACES.discard(self)

    def metrics(self) -> SpillMetrics:
        return SpillMetrics(
            runs_created=self.runs_created,
            merge_passes=self.merge_passes,
            peak_live_bytes=self.peak_live_bytes,
            total_spill_bytes=self.total_written,
        )

    def _grow(self, amount: int, *, stage: str) -> None:
        new_live = self.live_bytes + amount
        new_total = self.total_written + amount
        temporary_limit = self.limits.max_temporary_bytes
        if temporary_limit is not None and new_live > temporary_limit:
            raise _limit_error("max_temporary_bytes", new_live, temporary_limit, stage=stage)
        spill_limit = self.limits.max_spill_bytes
        if spill_limit is not None and new_total > spill_limit:
            raise _limit_error("max_spill_bytes", new_total, spill_limit, stage=stage)
        available = self._available_estimate
        if amount > available:
            try:
                available = shutil.disk_usage(self.directory).free
            except OSError as error:
                raise _os_resource_error(stage, error) from error
            self._available_estimate = available
        if amount > available:
            raise ProjectionResourceError(
                "projection temporary space is exhausted",
                details={"stage": stage, "required": amount, "available": available},
            )
        self.live_bytes = new_live
        self.total_written = new_total
        self.peak_live_bytes = max(self.peak_live_bytes, new_live)
        self._available_estimate -= amount

    def _forget_partial(self, path: Path | None, accounted: int) -> None:
        self.live_bytes = max(0, self.live_bytes - accounted)
        self._available_estimate += accounted
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


class _RunReader:
    def __init__(self, run: _Run) -> None:
        self.run = run
        self._stream: BinaryIO | None = None
        self._count = 0
        self._payload_bytes = 0
        self._digest = hashlib.sha256()
        self._verified = False
        try:
            stream = run.path.open("rb", buffering=1024 * 1024)
            self._stream = stream
            header = _read_exact(stream, _RUN_HEADER.size)
            magic, count, payload_bytes, digest = _RUN_HEADER.unpack(header)
            file_size = run.path.stat().st_size
        except (OSError, EOFError, struct.error) as error:
            self.close()
            raise _corrupt_run(error) from error
        if (
            magic != _RUN_MAGIC
            or count != run.edge_count
            or payload_bytes != run.payload_bytes
            or digest == b"\x00" * 32
            or file_size != _RUN_HEADER.size + payload_bytes
        ):
            self.close()
            raise _corrupt_run(ValueError("invalid run header"))
        self._expected_count = count
        self._expected_payload_bytes = payload_bytes
        self._expected_digest = digest

    def __iter__(self) -> _RunReader:
        return self

    def __next__(self) -> Edge:
        if self._count == self._expected_count:
            self._verify()
            raise StopIteration
        stream = self._stream
        if stream is None:
            raise StopIteration
        try:
            lengths_bytes = _read_exact(stream, _FIELD_LENGTHS.size)
            lengths = _FIELD_LENGTHS.unpack(lengths_bytes)
            remaining = self._expected_payload_bytes - self._payload_bytes
            if _FIELD_LENGTHS.size + sum(lengths) > remaining:
                raise EOFError("run record lengths exceed the declared payload")
            fields = tuple(_read_exact(stream, length) for length in lengths)
            record = lengths_bytes + b"".join(fields)
            values = tuple(value.decode("utf-8") for value in fields)
        except (OSError, EOFError, UnicodeDecodeError, struct.error) as error:
            raise _corrupt_run(error) from error
        self._digest.update(record)
        self._payload_bytes += len(record)
        self._count += 1
        return Edge(*values)

    def _verify(self) -> None:
        if self._verified:
            return
        stream = self._stream
        if stream is None:
            raise _corrupt_run(ValueError("run closed before verification"))
        try:
            tail = stream.read(1)
        except OSError as error:
            raise _corrupt_run(error) from error
        if (
            tail
            or self._payload_bytes != self._expected_payload_bytes
            or self._digest.digest() != self._expected_digest
        ):
            raise _corrupt_run(ValueError("run checksum mismatch"))
        self._verified = True

    def close(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is not None:
            stream.close()


class _DistinctIndex:
    """Exact encounter-order membership with a bounded in-memory front."""

    def __init__(
        self,
        *,
        memory_edges: int,
        temp_directory: os.PathLike[str] | str | None,
        limits: StreamingLimits,
    ) -> None:
        self.memory_edges = memory_edges
        self.temp_directory = temp_directory
        self.limits = limits
        self.memory: set[Edge] = set()
        self.connection: sqlite3.Connection | None = None
        self.workspace: _SpillWorkspace | None = None
        self.database_path: Path | None = None
        self.count = 0
        self.peak_bytes = 0

    def add(self, edge: Edge) -> bool:
        connection = self.connection
        if connection is None:
            if edge in self.memory:
                return False
            if len(self.memory) < self.memory_edges:
                self.memory.add(edge)
                self.count += 1
                return True
            self._spill()
            connection = self.connection
            assert connection is not None
        try:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO seen(edge_key) VALUES (?)",
                (sqlite3.Binary(_encode_edge(edge)),),
            )
        except sqlite3.Error as error:
            raise _sqlite_resource_error(error) from error
        inserted = cursor.rowcount == 1
        if inserted:
            self.count += 1
        if self.count % self.limits.cancellation_check_interval == 0:
            self._check_size()
        return inserted

    def metrics(self) -> SpillMetrics:
        if self.connection is not None:
            self._check_size()
        return SpillMetrics(
            runs_created=0,
            merge_passes=0,
            peak_live_bytes=self.peak_bytes,
            total_spill_bytes=self.peak_bytes,
        )

    def close(self) -> None:
        connection = self.connection
        self.connection = None
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass
        workspace = self.workspace
        self.workspace = None
        if workspace is not None:
            workspace.cleanup()
        self.database_path = None
        self.memory.clear()

    def _spill(self) -> None:
        workspace = _SpillWorkspace(self.temp_directory, self.limits)
        descriptor: int | None = None
        path: Path | None = None
        connection: sqlite3.Connection | None = None
        try:
            descriptor, name = tempfile.mkstemp(
                prefix="seen-", suffix=".sqlite3", dir=workspace.directory
            )
            _restrict_owner_file_permissions(descriptor)
            os.close(descriptor)
            descriptor = None
            path = Path(name)
            connection = sqlite3.connect(path)
            connection.execute("PRAGMA journal_mode=OFF")
            connection.execute("PRAGMA synchronous=OFF")
            connection.execute("PRAGMA temp_store=FILE")
            connection.execute("PRAGMA mmap_size=0")
            connection.execute("PRAGMA cache_size=-2048")
            connection.execute("CREATE TABLE seen(edge_key BLOB PRIMARY KEY) WITHOUT ROWID")
            connection.executemany(
                "INSERT INTO seen(edge_key) VALUES (?)",
                ((sqlite3.Binary(_encode_edge(edge)),) for edge in self.memory),
            )
        except (OSError, sqlite3.Error) as error:
            if descriptor is not None:
                os.close(descriptor)
            if connection is not None:
                connection.close()
            workspace.cleanup()
            if isinstance(error, sqlite3.Error):
                raise _sqlite_resource_error(error) from error
            raise _os_resource_error("encounter-distinct-index", error) from error
        self.workspace = workspace
        assert path is not None
        self.database_path = path
        assert connection is not None
        self.connection = connection
        self.memory.clear()
        self._check_size()

    def _check_size(self) -> None:
        path = self.database_path
        if path is None:
            return
        try:
            size = path.stat().st_size
        except OSError as error:
            raise _os_resource_error("encounter-distinct-index", error) from error
        self.peak_bytes = max(self.peak_bytes, size)
        for name, allowed in (
            ("max_temporary_bytes", self.limits.max_temporary_bytes),
            ("max_spill_bytes", self.limits.max_spill_bytes),
        ):
            if allowed is not None and size > allowed:
                raise _limit_error(name, size, allowed, stage="encounter-distinct-index")


def iter_edge_policy(
    edges: Iterable[Edge],
    *,
    duplicates: DuplicatePolicy,
    order: EdgeOrder,
    buffer_edges: int,
    temp_directory: os.PathLike[str] | str | None,
    limits: StreamingLimits,
    statistics: StatisticsLike | None = None,
    cancellation_token: CancellationTokenLike | None = None,
    metrics_sink: Callable[[SpillMetrics], object] | None = None,
) -> Iterator[Edge]:
    """Apply deterministic policies with bounded canonical spill and cleanup."""
    _positive_int("buffer_edges", buffer_edges)
    _validate_cancellation_token(cancellation_token)
    if order == "encounter":
        yield from _iter_encounter(
            edges,
            duplicates=duplicates,
            buffer_edges=buffer_edges,
            temp_directory=temp_directory,
            limits=limits,
            statistics=statistics,
            cancellation_token=cancellation_token,
            metrics_sink=metrics_sink,
        )
        return
    yield from _iter_canonical(
        edges,
        duplicates=duplicates,
        buffer_edges=buffer_edges,
        temp_directory=temp_directory,
        limits=limits,
        statistics=statistics,
        cancellation_token=cancellation_token,
        metrics_sink=metrics_sink,
    )


def _iter_encounter(
    edges: Iterable[Edge],
    *,
    duplicates: DuplicatePolicy,
    buffer_edges: int,
    temp_directory: os.PathLike[str] | str | None,
    limits: StreamingLimits,
    statistics: StatisticsLike | None,
    cancellation_token: CancellationTokenLike | None,
    metrics_sink: Callable[[SpillMetrics], object] | None,
) -> Iterator[Edge]:
    source = iter(edges)
    raw_count = 0
    distinct = _DistinctIndex(
        memory_edges=buffer_edges,
        temp_directory=temp_directory,
        limits=limits,
    )
    try:
        _check_cancel(cancellation_token)
        for edge in source:
            raw_count += 1
            _enforce_edge_limit(raw_count, limits)
            if raw_count % limits.cancellation_check_interval == 0:
                _check_cancel(cancellation_token)
            duplicate = not distinct.add(edge)
            if duplicates == "preserve" or not duplicate:
                yield edge
        _check_cancel(cancellation_token)
        _set_statistics(statistics, raw_count, distinct.count)
    except (MemoryError, OverflowError) as error:
        raise _allocation_resource_error("encounter", error) from error
    finally:
        _close_iterator(source)
        try:
            metrics = distinct.metrics()
        finally:
            distinct.close()
        if metrics_sink is not None:
            metrics_sink(metrics)


def _iter_canonical(
    edges: Iterable[Edge],
    *,
    duplicates: DuplicatePolicy,
    buffer_edges: int,
    temp_directory: os.PathLike[str] | str | None,
    limits: StreamingLimits,
    statistics: StatisticsLike | None,
    cancellation_token: CancellationTokenLike | None,
    metrics_sink: Callable[[SpillMetrics], object] | None,
) -> Iterator[Edge]:
    workspace = _SpillWorkspace(temp_directory, limits)
    source = iter(edges)
    runs: list[_Run] = []
    raw_count = 0
    try:
        _check_cancel(cancellation_token)
        buffer: list[Edge] = []
        for edge in source:
            raw_count += 1
            _enforce_edge_limit(raw_count, limits)
            if raw_count % limits.cancellation_check_interval == 0:
                _check_cancel(cancellation_token)
            buffer.append(edge)
            if len(buffer) == buffer_edges:
                runs.append(
                    workspace.write_sorted_run(
                        buffer,
                        duplicates=duplicates,
                        cancellation_token=cancellation_token,
                    )
                )
                buffer = []
        if buffer:
            runs.append(
                workspace.write_sorted_run(
                    buffer,
                    duplicates=duplicates,
                    cancellation_token=cancellation_token,
                )
            )
        _close_iterator(source)
        source = iter(())
        if not runs:
            _set_statistics(statistics, 0, 0)
            return
        final_runs = workspace.reduce(
            runs,
            duplicates=duplicates,
            cancellation_token=cancellation_token,
        )
        merged = workspace.merge(final_runs, cancellation_token=cancellation_token)
        distinct_count = 0
        sentinel = object()
        last: Edge | object = sentinel
        try:
            for edge in merged:
                is_distinct = last is sentinel or edge != last
                if is_distinct:
                    distinct_count += 1
                if duplicates == "preserve" or is_distinct:
                    yield edge
                last = edge
        finally:
            _close_iterator(merged)
        _set_statistics(statistics, raw_count, distinct_count)
    except (MemoryError, OverflowError) as error:
        raise _allocation_resource_error("canonical", error) from error
    finally:
        _close_iterator(source)
        metrics = workspace.metrics()
        workspace.cleanup()
        if metrics_sink is not None:
            metrics_sink(metrics)


def _encode_edge(edge: Edge) -> bytes:
    values = tuple(value.encode("utf-8") for value in edge.as_tuple())
    if any(len(value) > _MAX_FIELD_BYTES for value in values):
        raise ProjectionResourceError(
            "an edge field exceeds the private run-format bound",
            details={"stage": "run-encode", "max_field_bytes": _MAX_FIELD_BYTES},
        )
    return _FIELD_LENGTHS.pack(*(len(value) for value in values)) + b"".join(values)


def _coalesce_sorted(edges: Iterable[Edge]) -> Iterator[Edge]:
    sentinel = object()
    last: Edge | object = sentinel
    source = iter(edges)
    try:
        for edge in source:
            if last is sentinel or edge != last:
                yield edge
                last = edge
    finally:
        _close_iterator(source)


def _read_exact(stream: BinaryIO, amount: int) -> bytes:
    value = stream.read(amount)
    if len(value) != amount:
        raise EOFError("truncated run")
    return value


def _check_cancel(token: CancellationTokenLike | None) -> None:
    if token is not None:
        token.check()


def _validate_cancellation_token(token: CancellationTokenLike | None) -> None:
    if token is not None and not callable(getattr(token, "check", None)):
        raise InvalidProjectionOptionsError("cancellation_token must expose check() or be None")


def _enforce_edge_limit(observed: int, limits: StreamingLimits) -> None:
    allowed = limits.max_total_edges
    if allowed is not None and observed > allowed:
        raise _limit_error("max_total_edges", observed, allowed, stage="edge-compile")


def _set_statistics(
    statistics: StatisticsLike | None,
    raw_count: int,
    distinct_count: int,
) -> None:
    if statistics is None:
        return
    statistics.raw_edges = raw_count
    statistics.distinct_edges = distinct_count
    statistics.duplicate_edges = raw_count - distinct_count


def _close_iterator(iterator: object) -> None:
    close = getattr(iterator, "close", None)
    if callable(close):
        close()


def _positive_int(name: str, value: object, *, minimum: int = 1) -> None:
    if type(value) is not int or value < minimum:
        raise InvalidProjectionOptionsError(f"{name} must be an int >= {minimum}")


def _optional_nonnegative_int(name: str, value: object) -> None:
    if value is not None and (type(value) is not int or value < 0):
        raise InvalidProjectionOptionsError(f"{name} must be a non-negative int or None")


def _limit_error(name: str, observed: int, allowed: int, *, stage: str) -> ProjectionResourceError:
    return ProjectionResourceError(
        f"projection exceeded {name}",
        details={"limit": name, "observed": observed, "allowed": allowed, "stage": stage},
    )


def _os_resource_error(stage: str, error: OSError) -> ProjectionResourceError:
    return ProjectionResourceError(
        "projection temporary I/O failed",
        details={
            "stage": stage,
            "cause": type(error).__name__,
            "errno": -1 if error.errno is None else error.errno,
        },
    )


def _corrupt_run(error: BaseException) -> ProjectionResourceError:
    return ProjectionResourceError(
        "projection encountered a corrupt private run",
        details={"stage": "run-read", "cause": type(error).__name__},
    )


def _sqlite_resource_error(error: sqlite3.Error) -> ProjectionResourceError:
    return ProjectionResourceError(
        "encounter duplicate-index I/O failed",
        details={
            "stage": "encounter-distinct-index",
            "cause": type(error).__name__,
            "sqlite_error": str(getattr(error, "sqlite_errorname", "unknown")),
        },
    )


def _allocation_resource_error(stage: str, error: BaseException) -> ProjectionResourceError:
    return ProjectionResourceError(
        "projection could not allocate its bounded streaming state",
        details={"stage": stage, "cause": type(error).__name__},
    )


__all__ = [
    "CancellationTokenLike",
    "SpillMetrics",
    "StreamingLimits",
    "iter_edge_policy",
]
