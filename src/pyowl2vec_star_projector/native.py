"""Lazy Python bridge to the optional bounded-batch native edge engine."""

from __future__ import annotations

import hashlib
import importlib
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol, cast

from .backend import native_runtime_policy_reason
from .compiler import Compilation, CompileStatistics
from .encoded import EncodedStructuralLease
from .errors import (
    NativeBackendUnavailableError,
    ProjectionError,
    ProjectionResourceError,
    SnapshotCompatibilityError,
)
from .model import Edge
from .options import DuplicatePolicy, EdgeOrder

NATIVE_API_VERSION = 1
ENCODED_DIRECT_KERNEL_VERSION = 2
ENCODED_DIRECT_BUFFER_ORDER = (
    "root_kinds",
    "root_ids",
    "node_tags",
    "node_field_offsets",
    "field_kinds",
    "field_values",
    "field_lengths",
    "item_kinds",
    "item_values",
    "item_lengths",
    "scalar_bytes",
)


class _Processor(Protocol):
    stats: tuple[int, int, int]
    drained: bool

    def push_batch(self, batch: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]: ...

    def finish(self) -> None: ...

    def drain_batch(self, max_items: int) -> list[tuple[str, str, str]]: ...

    def cancel(self) -> None: ...


class NativeEncodedDirectUnsupported(Exception):
    """A valid public shape is outside the private Rust foundation."""


class NativeEncodedDirectCancelled(Exception):
    """The private Rust foundation was cancelled before publishing output."""


@dataclass(frozen=True, slots=True)
class NativeEncodedDirectStatistics:
    roots: int
    nodes: int
    declarations: int
    subclasses: int
    equivalents: int
    class_assertions: int
    edges: int
    buffer_bytes: int

    def __post_init__(self) -> None:
        for value in (
            self.roots,
            self.nodes,
            self.declarations,
            self.subclasses,
            self.equivalents,
            self.class_assertions,
            self.edges,
            self.buffer_bytes,
        ):
            if type(value) is not int or value < 0:
                raise ProjectionError("native encoded compiler returned invalid statistics")

    @property
    def ingestion_counters(self) -> Mapping[str, int | bool]:
        """Return the auditable facts for this exact private boundary call."""

        return MappingProxyType(
            {
                "encoded_buffer_bytes": self.buffer_bytes,
                "encoded_buffer_count": len(ENCODED_DIRECT_BUFFER_ORDER),
                "encoded_compiler_gil_released": True,
                "encoded_detached_buffer_count": len(ENCODED_DIRECT_BUFFER_ORDER),
                "encoded_indexed_buffer_count": 0,
                "encoded_staging_copy_bytes": 0,
                "encoded_zero_copy_buffers": len(ENCODED_DIRECT_BUFFER_ORDER),
                "native_boundary_calls": 1,
                "per_row_ffi_calls": 0,
                "structural_copy_bytes": 0,
            }
        )


@dataclass(slots=True)
class NativeEncodedDirectCompiler:
    """Owner-retaining Python handle for the private one-shot Rust compiler."""

    lease: EncodedStructuralLease
    _kernel: Any
    _module: Any

    @property
    def state(self) -> str:
        value = getattr(self._kernel, "state", None)
        if not isinstance(value, str):
            raise ProjectionError("native encoded compiler returned invalid state")
        return value

    @property
    def retained_buffer_count(self) -> int:
        value = getattr(self._kernel, "retained_buffer_count", None)
        if type(value) is not int or value < 0:
            raise ProjectionError("native encoded compiler returned invalid buffer count")
        return value

    def compile_batch(
        self,
        *,
        bidirectional: bool,
        max_edges: int,
        max_iri_bytes: int,
        asserted_taxonomy_only: bool = False,
    ) -> tuple[list[Edge], NativeEncodedDirectStatistics]:
        if type(bidirectional) is not bool:
            raise TypeError("bidirectional must be bool")
        if type(asserted_taxonomy_only) is not bool:
            raise TypeError("asserted_taxonomy_only must be bool")
        if type(max_edges) is not int or max_edges < 1:
            raise ValueError("max_edges must be a positive int")
        if type(max_iri_bytes) is not int or max_iri_bytes < 1:
            raise ValueError("max_iri_bytes must be a positive int")
        try:
            raw_edges, raw_stats = self._kernel.compile_batch(
                bidirectional,
                max_edges,
                max_iri_bytes,
                asserted_taxonomy_only,
            )
        except MemoryError as error:
            raise _resource_error(error) from error
        except self._module.EncodedDirectUnsupportedError as error:
            raise NativeEncodedDirectUnsupported(str(error)) from error
        except self._module.EncodedDirectBufferError as error:
            raise SnapshotCompatibilityError(str(error)) from error
        except self._module.EncodedDirectCancelledError as error:
            raise NativeEncodedDirectCancelled(str(error)) from error
        except ProjectionError:
            raise
        except Exception as error:
            raise _execution_error(error) from error

        if type(raw_edges) is not list or type(raw_stats) is not tuple or len(raw_stats) != 8:
            raise ProjectionError("native encoded compiler returned an invalid batch envelope")
        try:
            statistics = NativeEncodedDirectStatistics(*raw_stats)
            edges = [Edge(*value) for value in raw_edges]
        except (MemoryError, OverflowError) as error:
            raise _resource_error(error) from error
        except ProjectionError:
            raise
        except Exception as error:
            raise ProjectionError(
                "native encoded compiler returned an invalid edge batch"
            ) from error
        if statistics.edges != len(edges) or statistics.buffer_bytes != sum(
            buffer.nbytes for buffer in self.lease.buffers.values()
        ):
            raise ProjectionError(
                "native encoded compiler statistics do not match its retained input"
            )
        return edges, statistics

    def cancel(self) -> bool:
        try:
            result = self._kernel.cancel()
        except Exception as error:
            raise _execution_error(error) from error
        if type(result) is not bool:
            raise ProjectionError("native encoded compiler returned an invalid cancellation result")
        return result


def prepare_native_encoded_direct(
    lease: EncodedStructuralLease,
) -> NativeEncodedDirectCompiler:
    """Bind one validated public lease to the unadvertised Rust foundation.

    No memoryview is copied.  The Rust constructor accepts only exact, full
    immutable-``bytes`` exporters.  Mmap, sliced, and other valid exporters are
    deliberately reported as unsupported until the abi3-safe design expands.
    """

    if type(lease) is not EncodedStructuralLease:
        raise TypeError("lease must be EncodedStructuralLease")
    if lease.owner is not getattr(lease.encoded_view, "owner", None):
        raise SnapshotCompatibilityError("encoded lease lost its exact owner identity")
    try:
        descriptor_sha256 = bytes.fromhex(lease.descriptor_sha256)
    except (TypeError, ValueError) as error:
        raise SnapshotCompatibilityError("encoded lease descriptor digest is invalid") from error
    if len(descriptor_sha256) != 32:
        raise SnapshotCompatibilityError("encoded lease descriptor digest is invalid")
    try:
        descriptor = cast(Any, lease.encoded_view).descriptor
    except Exception as error:
        raise SnapshotCompatibilityError("encoded view descriptor is not readable") from error
    if type(descriptor) is not bytes or not descriptor:
        raise SnapshotCompatibilityError(
            "encoded view descriptor must be nonempty exact immutable bytes"
        )
    if hashlib.sha256(descriptor).digest() != descriptor_sha256:
        raise SnapshotCompatibilityError(
            "encoded view descriptor digest differs from its validated lease"
        )

    module = load_native_module()
    try:
        version = getattr(module, "ENCODED_DIRECT_KERNEL_VERSION", None)
        order = getattr(module, "ENCODED_DIRECT_BUFFER_ORDER", None)
        compiler = getattr(module, "EncodedDirectCompiler", None)
        unsupported = getattr(module, "EncodedDirectUnsupportedError", None)
        buffer_error = getattr(module, "EncodedDirectBufferError", None)
        cancelled = getattr(module, "EncodedDirectCancelledError", None)
    except Exception as error:
        raise NativeBackendUnavailableError(
            "native encoded foundation metadata could not be read",
            details={"cause": type(error).__name__},
        ) from error
    if version != ENCODED_DIRECT_KERNEL_VERSION:
        raise NativeBackendUnavailableError("native encoded foundation version is incompatible")
    actual_order = tuple(order) if isinstance(order, (tuple, list)) else ()
    if actual_order != ENCODED_DIRECT_BUFFER_ORDER:
        raise NativeBackendUnavailableError(
            "native encoded foundation buffer order is incompatible"
        )
    exceptions = (unsupported, buffer_error, cancelled)
    if not callable(compiler) or not all(
        isinstance(value, type) and issubclass(value, Exception) for value in exceptions
    ):
        raise NativeBackendUnavailableError("native encoded foundation is incomplete")
    unsupported_type = cast(type[Exception], unsupported)
    buffer_error_type = cast(type[Exception], buffer_error)
    try:
        kernel = compiler(lease.encoded_view, lease.owner, descriptor_sha256)
    except unsupported_type as error:
        raise NativeEncodedDirectUnsupported(str(error)) from error
    except buffer_error_type as error:
        raise SnapshotCompatibilityError(str(error)) from error
    except MemoryError as error:
        raise _resource_error(error) from error
    except Exception as error:
        raise _execution_error(error) from error
    return NativeEncodedDirectCompiler(lease, kernel, module)


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
    version, _features = native_runtime_metadata()
    return version


def native_runtime_metadata() -> tuple[str, frozenset[str]]:
    """Return validated execution metadata from one extension import."""
    module = load_native_module()
    try:
        version = getattr(module, "__version__", None)
        raw_features = getattr(module, "FEATURES", ())
    except MemoryError:
        raise
    except Exception as error:
        raise NativeBackendUnavailableError(
            "native projector runtime metadata could not be read",
            details={"cause": type(error).__name__},
        ) from error
    if not isinstance(version, str) or not version:
        raise NativeBackendUnavailableError("native projector version metadata is invalid")
    if not isinstance(raw_features, (tuple, list, frozenset)) or not all(
        isinstance(item, str) and item for item in raw_features
    ):
        raise NativeBackendUnavailableError("native projector feature metadata is invalid")
    return version, frozenset(raw_features)


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
    "ENCODED_DIRECT_BUFFER_ORDER",
    "ENCODED_DIRECT_KERNEL_VERSION",
    "NATIVE_API_VERSION",
    "NativeEncodedDirectCancelled",
    "NativeEncodedDirectCompiler",
    "NativeEncodedDirectStatistics",
    "NativeEncodedDirectUnsupported",
    "iter_native_compilation",
    "iter_native_passthrough",
    "iter_native_policy",
    "load_native_module",
    "native_implementation_version",
    "native_runtime_metadata",
    "prepare_native_encoded_direct",
]
