"""Lazy Python bridge to the optional bounded-batch native edge engine."""

from __future__ import annotations

import importlib
from collections.abc import Iterable, Iterator
from typing import Any, Protocol, cast

from .backend import native_runtime_policy_reason
from .compiler import Compilation, CompileStatistics
from .errors import (
    NativeBackendUnavailableError,
    ProjectionError,
    ProjectionResourceError,
)
from .model import Edge
from .options import DuplicatePolicy, EdgeOrder

NATIVE_API_VERSION = 1


class _Processor(Protocol):
    stats: tuple[int, int, int]
    drained: bool

    def push_batch(self, batch: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]: ...

    def finish(self) -> None: ...

    def drain_batch(self, max_items: int) -> list[tuple[str, str, str]]: ...

    def cancel(self) -> None: ...


def load_native_module() -> Any:
    """Load and validate the private extension only at native dispatch."""
    policy_reason = native_runtime_policy_reason()
    if policy_reason is not None:
        raise NativeBackendUnavailableError(policy_reason)
    try:
        module = importlib.import_module("pyowl2vec_star_projector._native")
    except MemoryError:
        raise
    except Exception as error:
        raise NativeBackendUnavailableError(
            "native projector extension could not be loaded",
            details={"cause": type(error).__name__},
        ) from error
    try:
        actual = getattr(module, "NATIVE_API_VERSION", None)
        processor = getattr(module, "EdgeBatchProcessor", None)
    except MemoryError:
        raise
    except Exception as error:
        raise NativeBackendUnavailableError(
            "native projector extension metadata could not be read",
            details={"cause": type(error).__name__},
        ) from error
    if type(actual) is not int or actual != NATIVE_API_VERSION:
        raise NativeBackendUnavailableError(
            "native projector API is incompatible",
            details={
                "expected_native_api": NATIVE_API_VERSION,
                "actual_native_api": actual if type(actual) is int else -1,
            },
        )
    if not callable(processor):
        raise NativeBackendUnavailableError("native projector extension is incomplete")
    return module


def native_implementation_version() -> str:
    module = load_native_module()
    try:
        version = getattr(module, "__version__", None)
    except MemoryError:
        raise
    except Exception as error:
        raise NativeBackendUnavailableError(
            "native projector version metadata could not be read",
            details={"cause": type(error).__name__},
        ) from error
    if not isinstance(version, str) or not version:
        raise NativeBackendUnavailableError("native projector version metadata is invalid")
    return version


def iter_native_compilation(
    compilation: Compilation,
    *,
    batch_edges: int,
) -> Iterator[Edge]:
    yield from iter_native_policy(
        compilation.iter_raw_edges(),
        duplicates=compilation.options.duplicates,
        order=compilation.options.order,
        batch_edges=batch_edges,
        statistics=compilation.statistics,
    )


def iter_native_passthrough(
    edges: Iterable[Edge],
    *,
    batch_edges: int,
) -> Iterator[Edge]:
    """Transfer raw edges through native code without global native storage.

    P4 owns ordering and duplicate policy so every native processor is limited
    to one bounded batch.  A one-edge first batch preserves low time-to-first-
    edge; later batches use the configured transfer size.
    """
    source = iter(edges)
    try:
        try:
            first = next(source)
        except StopIteration:
            return
        yield from iter_native_policy(
            (first,),
            duplicates="preserve",
            order="encounter",
            batch_edges=1,
        )
        batch: list[Edge] = []
        for edge in source:
            batch.append(edge)
            if len(batch) == batch_edges:
                yield from iter_native_policy(
                    batch,
                    duplicates="preserve",
                    order="encounter",
                    batch_edges=batch_edges,
                )
                batch = []
        if batch:
            yield from iter_native_policy(
                batch,
                duplicates="preserve",
                order="encounter",
                batch_edges=batch_edges,
            )
    finally:
        close = getattr(source, "close", None)
        if callable(close):
            close()


def iter_native_policy(
    edges: Iterable[Edge],
    *,
    duplicates: DuplicatePolicy,
    order: EdgeOrder,
    batch_edges: int,
    statistics: CompileStatistics | None = None,
) -> Iterator[Edge]:
    """Apply edge policies through bounded transfers and release on cancellation."""
    module = load_native_module()
    try:
        processor = cast(_Processor, module.EdgeBatchProcessor(order, duplicates))
    except (MemoryError, OverflowError) as error:
        raise _resource_error(error) from error
    except Exception as error:
        raise _execution_error(error) from error

    completed = False
    batch: list[tuple[str, str, str]] = []
    try:
        for edge in edges:
            batch.append(edge.as_tuple())
            if len(batch) == batch_edges:
                yield from _push(processor, batch)
                batch = []
        if batch:
            yield from _push(processor, batch)
        _call(processor.finish)
        while not processor.drained:
            for value in _call(lambda: processor.drain_batch(batch_edges)):
                yield Edge(*value)
        _copy_statistics(processor, statistics)
        completed = True
    finally:
        if not completed:
            try:
                processor.cancel()
            except Exception:
                # Cancellation cleanup cannot replace the consumer's exception.
                pass


def _push(processor: _Processor, batch: list[tuple[str, str, str]]) -> Iterator[Edge]:
    for value in _call(lambda: processor.push_batch(batch)):
        yield Edge(*value)


def _call(operation: Any) -> Any:
    try:
        return operation()
    except (MemoryError, OverflowError) as error:
        raise _resource_error(error) from error
    except ProjectionError:
        raise
    except Exception as error:
        raise _execution_error(error) from error


def _resource_error(error: BaseException) -> ProjectionResourceError:
    return ProjectionResourceError(
        "native projector exhausted its configured edge resources",
        details={"native_exception": type(error).__name__},
    )


def _execution_error(error: BaseException) -> ProjectionError:
    return ProjectionError(
        "native projector execution failed",
        details={"native_exception": type(error).__name__},
    )


def _copy_statistics(
    processor: _Processor,
    statistics: CompileStatistics | None,
) -> None:
    if statistics is None:
        return
    raw_edges, distinct_edges, duplicate_edges = processor.stats
    statistics.raw_edges = raw_edges
    statistics.distinct_edges = distinct_edges
    statistics.duplicate_edges = duplicate_edges


__all__ = [
    "NATIVE_API_VERSION",
    "iter_native_compilation",
    "iter_native_passthrough",
    "iter_native_policy",
    "load_native_module",
    "native_implementation_version",
]
