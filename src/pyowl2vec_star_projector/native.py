"""Lazy Python bridge to the optional bounded-batch native edge engine."""

from __future__ import annotations

import hashlib
import importlib
import sys
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol, cast

from .backend import native_runtime_policy_reason
from .compiler import Compilation, CompileStatistics, RoleState
from .diagnostics import ProjectionDiagnostic
from .encoded import (
    EncodedStructuralLease,
    _acquire_root_encoded_lease,
    _resolve_private_empty_overlay_aliases,
)
from .errors import (
    NativeBackendUnavailableError,
    ProjectionError,
    ProjectionResourceError,
    SnapshotCompatibilityError,
    UnsupportedAxiomShapeError,
)
from .model import Edge
from .options import DuplicatePolicy, EdgeOrder, ProjectionOptions
from .streaming import CancellationTokenLike

NATIVE_API_VERSION = 1
ENCODED_DIRECT_KERNEL_VERSION = 46
_PROJECTOR_EDGE_TYPE = Edge
_NATIVE_ENCODED_EDGE_ALLOCATION_PROBE: Callable[[Edge], object] | None = None
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
_TAG_ANNOTATION_ASSERTION = 120


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
    anonymous_individuals: int
    ontology_annotations: int
    swrl_rules: int
    declarations: int
    subclasses: int
    restriction_subclasses: int
    ignored_subclasses: int
    equivalents: int
    aggregate_equivalents: int
    equivalent_base_edges: int
    ignored_equivalents: int
    disjoint_classes: int
    disjoint_unions: int
    has_keys: int
    same_individuals: int
    different_individuals: int
    class_assertions: int
    ignored_class_assertions: int
    object_property_assertions: int
    negative_object_property_assertions: int
    sub_object_properties: int
    object_property_chains: int
    equivalent_object_properties: int
    disjoint_object_properties: int
    inverse_object_properties: int
    functional_object_properties: int
    inverse_functional_object_properties: int
    reflexive_object_properties: int
    irreflexive_object_properties: int
    symmetric_object_properties: int
    asymmetric_object_properties: int
    transitive_object_properties: int
    sub_data_properties: int
    equivalent_data_properties: int
    disjoint_data_properties: int
    data_property_domains: int
    data_property_ranges: int
    functional_data_properties: int
    datatype_definitions: int
    data_property_assertions: int
    negative_data_property_assertions: int
    annotation_assertions: int
    selected_annotation_assertions: int
    sub_annotation_properties: int
    annotation_property_domains: int
    annotation_property_ranges: int
    annotation_edges: int
    non_string_literal_renderings: int
    skipped_axioms: int
    object_property_domains: int
    object_property_ranges: int
    ignored_object_property_domains: int
    ignored_object_property_ranges: int
    domain_range_edges: int
    role_expansion_edges: int
    edges: int
    buffer_bytes: int
    root_provenance_buffer_bytes: int

    def __post_init__(self) -> None:
        for value in (
            self.roots,
            self.nodes,
            self.anonymous_individuals,
            self.ontology_annotations,
            self.swrl_rules,
            self.declarations,
            self.subclasses,
            self.restriction_subclasses,
            self.ignored_subclasses,
            self.equivalents,
            self.aggregate_equivalents,
            self.equivalent_base_edges,
            self.ignored_equivalents,
            self.disjoint_classes,
            self.disjoint_unions,
            self.has_keys,
            self.same_individuals,
            self.different_individuals,
            self.class_assertions,
            self.ignored_class_assertions,
            self.object_property_assertions,
            self.negative_object_property_assertions,
            self.sub_object_properties,
            self.object_property_chains,
            self.equivalent_object_properties,
            self.disjoint_object_properties,
            self.inverse_object_properties,
            self.functional_object_properties,
            self.inverse_functional_object_properties,
            self.reflexive_object_properties,
            self.irreflexive_object_properties,
            self.symmetric_object_properties,
            self.asymmetric_object_properties,
            self.transitive_object_properties,
            self.sub_data_properties,
            self.equivalent_data_properties,
            self.disjoint_data_properties,
            self.data_property_domains,
            self.data_property_ranges,
            self.functional_data_properties,
            self.datatype_definitions,
            self.data_property_assertions,
            self.negative_data_property_assertions,
            self.annotation_assertions,
            self.selected_annotation_assertions,
            self.sub_annotation_properties,
            self.annotation_property_domains,
            self.annotation_property_ranges,
            self.annotation_edges,
            self.non_string_literal_renderings,
            self.skipped_axioms,
            self.object_property_domains,
            self.object_property_ranges,
            self.ignored_object_property_domains,
            self.ignored_object_property_ranges,
            self.domain_range_edges,
            self.role_expansion_edges,
            self.edges,
            self.buffer_bytes,
            self.root_provenance_buffer_bytes,
        ):
            if type(value) is not int or value < 0:
                raise ProjectionError("native encoded compiler returned invalid statistics")

    @property
    def ingestion_counters(self) -> Mapping[str, int | bool]:
        """Return the auditable facts for this exact private boundary call."""

        retained_buffer_count = len(ENCODED_DIRECT_BUFFER_ORDER) * (
            1 + int(self.root_provenance_buffer_bytes != 0)
        )
        return MappingProxyType(
            {
                "encoded_buffer_bytes": self.buffer_bytes + self.root_provenance_buffer_bytes,
                "encoded_buffer_count": retained_buffer_count,
                "encoded_compiler_gil_released": True,
                "encoded_detached_buffer_count": retained_buffer_count,
                "encoded_indexed_buffer_count": 0,
                "encoded_staging_copy_bytes": 0,
                "encoded_zero_copy_buffers": retained_buffer_count,
                "native_boundary_calls": 1,
                "per_row_ffi_calls": 0,
                "structural_copy_bytes": 0,
            }
        )


# Preserve canonical identities while tests or embedding code replace the callable factories.
_NATIVE_ENCODED_DIRECT_STATISTICS_TYPE = NativeEncodedDirectStatistics
_NATIVE_ENCODED_STATISTICS_ALLOCATION_PROBE: (
    Callable[[NativeEncodedDirectStatistics], object] | None
) = None


def _native_skipped_counts(
    statistics: NativeEncodedDirectStatistics,
) -> tuple[tuple[str, int], ...]:
    """Return exact grouped scalar skip counts in diagnostic constructor order."""

    return tuple(
        sorted(
            (
                ("AnnotationPropertyDomain", statistics.annotation_property_domains),
                ("AnnotationPropertyRange", statistics.annotation_property_ranges),
                ("AsymmetricObjectProperty", statistics.asymmetric_object_properties),
                ("DataPropertyAssertion", statistics.data_property_assertions),
                ("DataPropertyDomain", statistics.data_property_domains),
                ("DataPropertyRange", statistics.data_property_ranges),
                ("DatatypeDefinition", statistics.datatype_definitions),
                ("DifferentIndividuals", statistics.different_individuals),
                ("DisjointClasses", statistics.disjoint_classes),
                ("DisjointDataProperties", statistics.disjoint_data_properties),
                ("DisjointObjectProperties", statistics.disjoint_object_properties),
                ("DisjointUnion", statistics.disjoint_unions),
                ("EquivalentDataProperties", statistics.equivalent_data_properties),
                ("EquivalentObjectProperties", statistics.equivalent_object_properties),
                ("FunctionalDataProperty", statistics.functional_data_properties),
                ("FunctionalObjectProperty", statistics.functional_object_properties),
                ("HasKey", statistics.has_keys),
                (
                    "InverseFunctionalObjectProperty",
                    statistics.inverse_functional_object_properties,
                ),
                ("IrreflexiveObjectProperty", statistics.irreflexive_object_properties),
                (
                    "NegativeDataPropertyAssertion",
                    statistics.negative_data_property_assertions,
                ),
                (
                    "NegativeObjectPropertyAssertion",
                    statistics.negative_object_property_assertions,
                ),
                ("ReflexiveObjectProperty", statistics.reflexive_object_properties),
                ("SameIndividual", statistics.same_individuals),
                ("SubAnnotationPropertyOf", statistics.sub_annotation_properties),
                ("SubDataPropertyOf", statistics.sub_data_properties),
                ("SymmetricObjectProperty", statistics.symmetric_object_properties),
                ("TransitiveObjectProperty", statistics.transitive_object_properties),
            ),
            key=lambda item: item[0],
        )
    )


def _native_ignored_counts(
    statistics: NativeEncodedDirectStatistics,
    options: ProjectionOptions,
) -> tuple[tuple[str, int], ...]:
    """Return exact grouped scalar ignores that carry diagnostics."""

    return (
        (
            "AnnotationAssertion",
            (
                statistics.selected_annotation_assertions - statistics.annotation_edges
                if options.include_literals
                else 0
            ),
        ),
        ("ClassAssertion", statistics.ignored_class_assertions),
        ("EquivalentClasses", statistics.ignored_equivalents),
        ("ObjectPropertyDomain", statistics.ignored_object_property_domains),
        ("ObjectPropertyRange", statistics.ignored_object_property_ranges),
        (
            "SubClassOf",
            statistics.ignored_subclasses
            + (statistics.restriction_subclasses if options.only_taxonomy else 0),
        ),
    )


@dataclass(frozen=True, slots=True)
class NativeEncodedDirectRoleState:
    """Private retained role maps for explicit Scala-instance parity."""

    _kernel: Any
    _module: Any

    @property
    def in_use(self) -> bool:
        value = getattr(self._kernel, "in_use", None)
        if type(value) is not bool:
            raise ProjectionError("native encoded role state returned invalid use status")
        return value

    @property
    def subrole_property_count(self) -> int:
        value = getattr(self._kernel, "subrole_property_count", None)
        if type(value) is not int or value < 0:
            raise ProjectionError("native encoded role state returned invalid subrole count")
        return value

    @property
    def inverse_property_count(self) -> int:
        value = getattr(self._kernel, "inverse_property_count", None)
        if type(value) is not int or value < 0:
            raise ProjectionError("native encoded role state returned invalid inverse count")
        return value

    def snapshot(self) -> RoleState:
        """Copy the retained native maps into the scalar-compatible lifecycle shape."""

        raw = _call_encoded_direct(self._module, self._kernel.snapshot)
        if type(raw) is not tuple or len(raw) != 2:
            raise ProjectionError("native encoded role state returned an invalid snapshot")
        raw_subroles, raw_inverses = raw
        if type(raw_subroles) is not list or type(raw_inverses) is not list:
            raise ProjectionError("native encoded role state returned an invalid snapshot")
        subroles: dict[str, tuple[str, ...]] = {}
        for row in raw_subroles:
            if type(row) is not tuple or len(row) != 2:
                raise ProjectionError("native encoded role state returned an invalid subrole row")
            property_iri, raw_values = row
            if (
                type(property_iri) is not str
                or type(raw_values) is not list
                or not all(type(value) is str for value in raw_values)
                or property_iri in subroles
            ):
                raise ProjectionError("native encoded role state returned an invalid subrole row")
            subroles[property_iri] = tuple(raw_values)
        inverses: dict[str, str] = {}
        for row in raw_inverses:
            if type(row) is not tuple or len(row) != 2:
                raise ProjectionError("native encoded role state returned an invalid inverse row")
            property_iri, inverse_iri = row
            if (
                type(property_iri) is not str
                or type(inverse_iri) is not str
                or property_iri in inverses
            ):
                raise ProjectionError("native encoded role state returned an invalid inverse row")
            inverses[property_iri] = inverse_iri
        return RoleState(subroles, inverses)


@dataclass(slots=True)
class NativeEncodedDirectCompiler:
    """Owner-retaining Python handle for the private one-shot Rust compiler."""

    lease: EncodedStructuralLease
    root_annotation_lease: EncodedStructuralLease | None
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

    @property
    def coarse_chunk_edges(self) -> int:
        return _native_nonnegative_int(
            self._kernel,
            "coarse_chunk_edges",
            "coarse chunk-edge limit",
        )

    @property
    def batch_intermediate_list_edges(self) -> int:
        return _native_nonnegative_int(
            self._kernel,
            "batch_intermediate_list_edges",
            "batch intermediate-list edge count",
        )

    @property
    def coarse_output_chunks(self) -> int:
        return _native_nonnegative_int(
            self._kernel,
            "coarse_output_chunks",
            "coarse output-chunk count",
        )

    @property
    def coarse_output_vector_edges(self) -> int:
        return _native_nonnegative_int(
            self._kernel,
            "coarse_output_vector_edges",
            "coarse output-vector edge count",
        )

    @property
    def coarse_intermediate_list_edges(self) -> int:
        return _native_nonnegative_int(
            self._kernel,
            "coarse_intermediate_list_edges",
            "coarse intermediate-list edge count",
        )

    @property
    def peak_buffered_coarse_edges(self) -> int:
        return _native_nonnegative_int(
            self._kernel,
            "peak_buffered_coarse_edges",
            "peak buffered coarse-edge count",
        )

    def compile_batch(
        self,
        *,
        bidirectional: bool,
        max_edges: int,
        max_iri_bytes: int,
        asserted_taxonomy_only: bool = False,
        only_taxonomy: bool = False,
        include_literals: bool = False,
        role_state: NativeEncodedDirectRoleState | None = None,
    ) -> tuple[list[Edge], NativeEncodedDirectStatistics]:
        if type(bidirectional) is not bool:
            raise TypeError("bidirectional must be bool")
        if type(asserted_taxonomy_only) is not bool:
            raise TypeError("asserted_taxonomy_only must be bool")
        if type(only_taxonomy) is not bool:
            raise TypeError("only_taxonomy must be bool")
        if type(include_literals) is not bool:
            raise TypeError("include_literals must be bool")
        if type(max_edges) is not int or max_edges < 1:
            raise ValueError("max_edges must be a positive int")
        if type(max_iri_bytes) is not int or max_iri_bytes < 1:
            raise ValueError("max_iri_bytes must be a positive int")
        if role_state is not None and type(role_state) is not NativeEncodedDirectRoleState:
            raise TypeError("role_state must be NativeEncodedDirectRoleState or None")
        if role_state is not None and role_state._module is not self._module:
            raise ProjectionError("native encoded role state belongs to another native module")
        expected_buffer_bytes = sum(buffer.nbytes for buffer in self.lease.buffers.values())
        expected_root_bytes = (
            0
            if self.root_annotation_lease is None
            else sum(buffer.nbytes for buffer in self.root_annotation_lease.buffers.values())
        )
        raw_edges, raw_stats = _call_encoded_direct(
            self._module,
            lambda: self._kernel.compile_batch(
                bidirectional,
                max_edges,
                max_iri_bytes,
                Edge,
                _PROJECTOR_EDGE_TYPE,
                _NATIVE_ENCODED_EDGE_ALLOCATION_PROBE,
                NativeEncodedDirectStatistics,
                _NATIVE_ENCODED_DIRECT_STATISTICS_TYPE,
                _NATIVE_ENCODED_STATISTICS_ALLOCATION_PROBE,
                asserted_taxonomy_only,
                only_taxonomy,
                include_literals,
                None if role_state is None else role_state._kernel,
            ),
        )

        if (
            type(raw_edges) is not list
            or type(raw_stats) is not _NATIVE_ENCODED_DIRECT_STATISTICS_TYPE
            or not all(type(value) is _PROJECTOR_EDGE_TYPE for value in raw_edges)
        ):
            raise ProjectionError("native encoded compiler returned an invalid batch envelope")
        edges = cast(list[Edge], raw_edges)
        statistics = raw_stats
        if (
            statistics.edges != len(edges)
            or statistics.buffer_bytes != expected_buffer_bytes
            or statistics.root_provenance_buffer_bytes != expected_root_bytes
        ):
            raise ProjectionError(
                "native encoded compiler statistics do not match its retained input"
            )
        return edges, statistics

    def iter_batches(
        self,
        *,
        bidirectional: bool,
        max_edges: int,
        max_iri_bytes: int,
        batch_edges: int,
        asserted_taxonomy_only: bool = False,
        only_taxonomy: bool = False,
        include_literals: bool = False,
        role_state: NativeEncodedDirectRoleState | None = None,
    ) -> NativeEncodedDirectBatchIterator:
        """Compile once and drain private caller-bounded native edge batches."""

        if type(bidirectional) is not bool:
            raise TypeError("bidirectional must be bool")
        if type(asserted_taxonomy_only) is not bool:
            raise TypeError("asserted_taxonomy_only must be bool")
        if type(only_taxonomy) is not bool:
            raise TypeError("only_taxonomy must be bool")
        if type(include_literals) is not bool:
            raise TypeError("include_literals must be bool")
        if type(max_edges) is not int or max_edges < 1:
            raise ValueError("max_edges must be a positive int")
        if type(max_iri_bytes) is not int or max_iri_bytes < 1:
            raise ValueError("max_iri_bytes must be a positive int")
        if type(batch_edges) is not int or batch_edges < 1:
            raise ValueError("batch_edges must be a positive int")
        if role_state is not None and type(role_state) is not NativeEncodedDirectRoleState:
            raise TypeError("role_state must be NativeEncodedDirectRoleState or None")
        if role_state is not None and role_state._module is not self._module:
            raise ProjectionError("native encoded role state belongs to another native module")
        expected_buffer_bytes = sum(buffer.nbytes for buffer in self.lease.buffers.values())
        expected_root_bytes = (
            0
            if self.root_annotation_lease is None
            else sum(buffer.nbytes for buffer in self.root_annotation_lease.buffers.values())
        )

        raw_batches = _call_encoded_direct(
            self._module,
            lambda: self._kernel.compile_batches(
                bidirectional,
                max_edges,
                max_iri_bytes,
                batch_edges,
                self,
                NativeEncodedDirectStatistics,
                _NATIVE_ENCODED_DIRECT_STATISTICS_TYPE,
                _NATIVE_ENCODED_STATISTICS_ALLOCATION_PROBE,
                NativeEncodedDirectBatchIterator,
                _NATIVE_ENCODED_DIRECT_BATCH_ITERATOR_TYPE,
                _NATIVE_ENCODED_ITERATOR_ALLOCATION_PROBE,
                asserted_taxonomy_only,
                only_taxonomy,
                include_literals,
                None if role_state is None else role_state._kernel,
            ),
        )
        try:
            if type(raw_batches) is not _NATIVE_ENCODED_DIRECT_BATCH_ITERATOR_TYPE:
                raise ProjectionError(
                    "native encoded compiler returned an invalid streaming envelope"
                )
            statistics = raw_batches.statistics
            if (
                type(statistics) is not _NATIVE_ENCODED_DIRECT_STATISTICS_TYPE
                or raw_batches._compiler is not self
                or raw_batches.batch_edges != batch_edges
                or raw_batches.yielded_edges != 0
                or statistics.buffer_bytes != expected_buffer_bytes
                or statistics.root_provenance_buffer_bytes != expected_root_bytes
            ):
                raise ProjectionError(
                    "native encoded compiler statistics do not match its retained input"
                )
            return raw_batches
        except Exception:
            _close_encoded_batches_quietly(self)
            raise

    def compile_to_sink(
        self,
        sink: object,
        *,
        bidirectional: bool,
        max_edges: int,
        max_iri_bytes: int,
        batch_edges: int,
        asserted_taxonomy_only: bool = False,
        only_taxonomy: bool = False,
        include_literals: bool = False,
        role_state: NativeEncodedDirectRoleState | None = None,
    ) -> NativeEncodedDirectStatistics:
        """Push private native batches to one synchronous batch sink."""

        candidate = getattr(sink, "write_batch", None)
        writer: Callable[[tuple[Edge, ...]], object]
        if callable(candidate):
            writer = cast(Callable[[tuple[Edge, ...]], object], candidate)
        elif callable(sink):
            writer = cast(Callable[[tuple[Edge, ...]], object], sink)
        else:
            raise TypeError("sink must be callable or expose write_batch(batch)")
        batches = self.iter_batches(
            bidirectional=bidirectional,
            max_edges=max_edges,
            max_iri_bytes=max_iri_bytes,
            batch_edges=batch_edges,
            asserted_taxonomy_only=asserted_taxonomy_only,
            only_taxonomy=only_taxonomy,
            include_literals=include_literals,
            role_state=role_state,
        )
        try:
            for batch in batches:
                writer(batch)
        finally:
            batches.close()
        return batches.statistics

    def cancel(self) -> bool:
        try:
            result = self._kernel.cancel()
        except Exception as error:
            raise _execution_error(error) from error
        if type(result) is not bool:
            raise ProjectionError("native encoded compiler returned an invalid cancellation result")
        return result


@dataclass(slots=True)
class NativeEncodedDirectBatchIterator(Iterator[tuple[Edge, ...]]):
    """Private batch iterator backed by a resumable bounded native cursor."""

    _compiler: NativeEncodedDirectCompiler | None
    statistics: NativeEncodedDirectStatistics
    batch_edges: int
    _yielded_edges: int = 0
    _boundary_calls: int = 1
    _edge_batches: int = 0
    _peak_buffered_edges: int = 0
    _terminal_state: str = "active"

    def __iter__(self) -> NativeEncodedDirectBatchIterator:
        return self

    def __next__(self) -> tuple[Edge, ...]:
        compiler = self._compiler
        if compiler is None:
            raise StopIteration
        if self._yielded_edges == self.statistics.edges:
            self._finish_exhausted(compiler)
            raise StopIteration
        raw_batch = _call_encoded_direct(
            compiler._module,
            lambda: compiler._kernel.next_batch(
                Edge,
                _PROJECTOR_EDGE_TYPE,
                _NATIVE_ENCODED_EDGE_ALLOCATION_PROBE,
            ),
        )
        try:
            if (
                type(raw_batch) is not tuple
                or not raw_batch
                or len(raw_batch) > self.batch_edges
                or not all(type(value) is _PROJECTOR_EDGE_TYPE for value in raw_batch)
            ):
                raise ProjectionError("native encoded compiler returned an invalid bounded batch")
            batch = cast(tuple[Edge, ...], raw_batch)
        except (MemoryError, OverflowError) as error:
            self.close()
            raise _resource_error(error) from error
        except ProjectionError:
            self.close()
            raise
        except Exception as error:
            self.close()
            raise ProjectionError(
                "native encoded compiler returned an invalid edge batch"
            ) from error
        next_count = self._yielded_edges + len(batch)
        if next_count > self.statistics.edges:
            self.close()
            raise ProjectionError("native encoded batch output exceeds its compiled edge count")
        self._yielded_edges = next_count
        if self._yielded_edges == self.statistics.edges:
            self._finish_exhausted(compiler)
        return batch

    @property
    def state(self) -> str:
        compiler = self._compiler
        if compiler is None:
            return self._terminal_state
        value = getattr(compiler._kernel, "batch_state", None)
        if value not in {"active", "exhausted", "cancelled"}:
            raise ProjectionError("native encoded batch iterator returned invalid state")
        return cast(str, value)

    @property
    def yielded_edges(self) -> int:
        return self._yielded_edges

    @property
    def boundary_calls(self) -> int:
        compiler = self._compiler
        if compiler is not None:
            return _native_nonnegative_int(
                compiler._kernel,
                "batch_boundary_calls",
                "boundary-call count",
            )
        return self._boundary_calls

    @property
    def edge_batches(self) -> int:
        compiler = self._compiler
        if compiler is not None:
            return _native_nonnegative_int(
                compiler._kernel,
                "emitted_edge_batches",
                "edge-batch count",
            )
        return self._edge_batches

    @property
    def remaining_edges(self) -> int:
        compiler = self._compiler
        if compiler is None:
            return 0
        return _native_nonnegative_int(
            compiler._kernel,
            "remaining_batch_edges",
            "remaining-edge count",
        )

    @property
    def peak_buffered_edges(self) -> int:
        compiler = self._compiler
        if compiler is not None:
            return _native_nonnegative_int(
                compiler._kernel,
                "peak_buffered_batch_edges",
                "peak buffered-edge count",
            )
        return self._peak_buffered_edges

    @property
    def ingestion_counters(self) -> Mapping[str, int]:
        """Return batch-boundary facts without counting metadata getters."""

        return MappingProxyType(
            {
                "configured_batch_edges": self.batch_edges,
                "native_boundary_calls": self.boundary_calls,
                "native_edge_batches": self.edge_batches,
                "native_peak_buffered_edges": self.peak_buffered_edges,
                "per_row_ffi_calls": 0,
                "published_edges": self._yielded_edges,
            }
        )

    def close(self) -> bool:
        """Cancel and release all not-yet-published native edges."""

        compiler = self._compiler
        if compiler is None:
            return False
        result = _call_encoded_direct(compiler._module, compiler._kernel.close_batches)
        if type(result) is not bool:
            raise ProjectionError("native encoded batch iterator returned invalid close status")
        self._capture_counters(compiler)
        state = getattr(compiler._kernel, "batch_state", None)
        if state not in {"exhausted", "cancelled"}:
            raise ProjectionError("native encoded batch iterator did not close")
        self._terminal_state = cast(str, state)
        self._compiler = None
        return result

    cancel = close

    def _finish_exhausted(self, compiler: NativeEncodedDirectCompiler) -> None:
        if getattr(compiler._kernel, "batch_state", None) != "exhausted":
            self.close()
            raise ProjectionError("native encoded batch iterator ended before native exhaustion")
        if (
            _native_nonnegative_int(
                compiler._kernel,
                "remaining_batch_edges",
                "remaining-edge count",
            )
            != 0
        ):
            self.close()
            raise ProjectionError("native encoded batch iterator retained edges after exhaustion")
        self._capture_counters(compiler)
        self._terminal_state = "exhausted"
        self._compiler = None

    def _capture_counters(self, compiler: NativeEncodedDirectCompiler) -> None:
        self._boundary_calls = _native_nonnegative_int(
            compiler._kernel,
            "batch_boundary_calls",
            "boundary-call count",
        )
        self._edge_batches = _native_nonnegative_int(
            compiler._kernel,
            "emitted_edge_batches",
            "edge-batch count",
        )
        self._peak_buffered_edges = _native_nonnegative_int(
            compiler._kernel,
            "peak_buffered_batch_edges",
            "peak buffered-edge count",
        )

    def __del__(self) -> None:
        compiler = self._compiler
        if compiler is None:
            return
        try:
            compiler._kernel.close_batches()
        except Exception:
            pass
        self._compiler = None


# Match a replaceable iterator factory against the import-time canonical type.
_NATIVE_ENCODED_DIRECT_BATCH_ITERATOR_TYPE = NativeEncodedDirectBatchIterator
_NATIVE_ENCODED_ITERATOR_ALLOCATION_PROBE: (
    Callable[[NativeEncodedDirectBatchIterator], object] | None
) = None


@dataclass(slots=True)
class NativeEncodedDirectCompilation:
    """Hidden production-adjacent adapter for the exact named-edge slice."""

    view: object
    lease: EncodedStructuralLease
    container_leases: tuple[EncodedStructuralLease, ...]
    root_annotation_lease: EncodedStructuralLease | None
    options: ProjectionOptions
    batches: NativeEncodedDirectBatchIterator
    native_statistics: NativeEncodedDirectStatistics
    statistics: CompileStatistics
    role_state: NativeEncodedDirectRoleState | None = None

    @property
    def diagnostics(self) -> tuple[ProjectionDiagnostic, ...]:
        diagnostics: list[ProjectionDiagnostic] = [
            ProjectionDiagnostic(
                code="MOWL_IGNORED_SHAPE",
                message="constructor does not emit an edge in the pinned profile",
                count=count,
                constructor=constructor,
            )
            for constructor, count in _native_ignored_counts(
                self.native_statistics,
                self.options,
            )
            if count
        ]
        if self.native_statistics.non_string_literal_renderings:
            diagnostics.append(
                ProjectionDiagnostic(
                    code="MOWL_NON_STRING_LITERAL_RENDERING",
                    message="pinned mOWL rendering preserves malformed datatype syntax",
                    severity="warning",
                    count=self.native_statistics.non_string_literal_renderings,
                    constructor="Literal",
                )
            )
        diagnostics.extend(
            ProjectionDiagnostic(
                code="MOWL_SKIPPED_AXIOM",
                message="axiom category is not visited by the pinned profile",
                count=count,
                constructor=constructor,
            )
            for constructor, count in _native_skipped_counts(self.native_statistics)
            if count
        )
        return tuple(diagnostics)

    def prepare_role_state(self) -> None:
        if self.options.compatibility_state == "scala-instance" and self.role_state is None:
            raise ProjectionError(
                "hidden native compilation lost its retained Scala-instance state"
            )

    @property
    def ingestion_counters(self) -> Mapping[str, int | bool]:
        retained_leases = (
            self.container_leases
            + (self.lease,)
            + (() if self.root_annotation_lease is None else (self.root_annotation_lease,))
        )
        retained_buffer_count = sum(len(lease.buffers) for lease in retained_leases)
        detached_buffer_count = len(self.lease.buffers) + (
            0 if self.root_annotation_lease is None else len(self.root_annotation_lease.buffers)
        )
        retained_buffer_bytes = sum(
            buffer.nbytes for lease in retained_leases for buffer in lease.buffers.values()
        )
        retained_subroles = 0 if self.role_state is None else self.role_state.subrole_property_count
        retained_inverses = 0 if self.role_state is None else self.role_state.inverse_property_count
        return MappingProxyType(
            {
                "base_flattening_bytes": 0,
                "encoded_buffer_bytes": retained_buffer_bytes,
                "encoded_buffer_count": retained_buffer_count,
                "encoded_compiler_gil_released": True,
                "encoded_detached_buffer_count": detached_buffer_count,
                "encoded_indexed_buffer_count": 0,
                "encoded_posting_bytes": 0,
                "encoded_referenced_view_count": len(self.container_leases)
                + int(self.root_annotation_lease is not None),
                "encoded_segment_count": sum(len(lease.segments) for lease in retained_leases),
                "encoded_staging_copy_bytes": 0,
                "encoded_zero_copy_buffers": retained_buffer_count,
                "materialized_scalar_rows": 0,
                "native_batch_edges": self.batches.batch_edges,
                "native_boundary_calls": self.batches.boundary_calls,
                "native_compiled_edges": self.native_statistics.edges,
                "native_edge_batches": self.batches.edge_batches,
                "native_output_vector_edges": 0,
                "native_peak_buffered_edges": self.batches.peak_buffered_edges,
                "native_retained_inverse_properties": retained_inverses,
                "native_retained_subrole_properties": retained_subroles,
                "parser_calls": 0,
                "per_row_ffi_calls": 0,
                "resolver_calls": 0,
                "scalar_axiom_materializations": 0,
                "scalar_term_materializations": 0,
                "structural_copy_bytes": 0,
                "wire_decoder_calls": 0,
                "wire_encoder_calls": 0,
            }
        )

    def iter_raw_edges(
        self,
        cancellation_token: CancellationTokenLike | None = None,
    ) -> Iterator[Edge]:
        try:
            for batch in self.batches:
                if cancellation_token is not None:
                    cancellation_token.check()
                yield from batch
        finally:
            self.batches.close()


def prepare_native_encoded_compilation(
    view: object,
    lease: EncodedStructuralLease,
    options: ProjectionOptions,
    *,
    batch_edges: int,
    max_total_edges: int | None,
    cancellation_token: CancellationTokenLike | None,
    role_state: NativeEncodedDirectRoleState | None = None,
) -> tuple[NativeEncodedDirectCompilation | None, str | None]:
    """Prepare the hidden exact named-edge seam or request whole-call fallback."""

    if options.compatibility_state == "scala-instance" and role_state is None:
        return None, "private native direct batches do not bind Scala-instance state"
    if options.compatibility_state == "isolated" and role_state is not None:
        raise ProjectionError("isolated native compilation received retained Scala-instance state")
    if cancellation_token is not None:
        cancellation_token.check()
    container_leases: tuple[EncodedStructuralLease, ...] = ()
    resolved_aliases = _resolve_private_empty_overlay_aliases(lease)
    if resolved_aliases is not None:
        lease, container_leases = resolved_aliases
    root_annotation_lease: EncodedStructuralLease | None = None
    if options.include_literals and _lease_contains_annotation_assertions(lease):
        if container_leases:
            return (
                None,
                "private native empty-overlay alias does not support "
                "root-scoped annotation provenance",
            )
        root_annotation_lease, annotation_fallback_reason = _native_annotation_provenance_selection(
            view,
            lease,
        )
        if annotation_fallback_reason is not None:
            return None, annotation_fallback_reason
    compiler = prepare_native_encoded_direct(
        lease,
        root_annotation_lease=root_annotation_lease,
    )
    maximum_edges = sys.maxsize if max_total_edges is None else max(1, max_total_edges)
    batches = compiler.iter_batches(
        bidirectional=options.bidirectional_taxonomy,
        max_edges=maximum_edges,
        max_iri_bytes=sys.maxsize,
        batch_edges=batch_edges,
        only_taxonomy=options.only_taxonomy,
        include_literals=options.include_literals,
        role_state=role_state,
    )
    try:
        if cancellation_token is not None:
            cancellation_token.check()
        native_statistics = batches.statistics
        direct_subclasses = native_statistics.subclasses - (
            native_statistics.restriction_subclasses + native_statistics.ignored_subclasses
        )
        taxonomy_edges = direct_subclasses * (2 if options.bidirectional_taxonomy else 1)
        restriction_edges = 0 if options.only_taxonomy else native_statistics.restriction_subclasses
        expected_annotation_edges = native_statistics.annotation_edges
        skipped_roots = sum(
            count for _constructor, count in _native_skipped_counts(native_statistics)
        )
        expected_edges = (
            taxonomy_edges
            + restriction_edges
            + native_statistics.equivalent_base_edges
            + (native_statistics.class_assertions - native_statistics.ignored_class_assertions)
            + native_statistics.object_property_assertions
            + native_statistics.domain_range_edges
            + native_statistics.role_expansion_edges
            + expected_annotation_edges
        )
        admitted_roots = (
            native_statistics.declarations
            + native_statistics.subclasses
            + native_statistics.equivalents
            + native_statistics.class_assertions
            + native_statistics.object_property_assertions
            + native_statistics.object_property_domains
            + native_statistics.object_property_ranges
            + native_statistics.sub_object_properties
            + native_statistics.inverse_object_properties
            + native_statistics.annotation_assertions
            + native_statistics.ontology_annotations
            + native_statistics.swrl_rules
            + skipped_roots
        )
        exact_named_edges = (
            native_statistics.roots == admitted_roots
            and native_statistics.restriction_subclasses + native_statistics.ignored_subclasses
            <= native_statistics.subclasses
            and native_statistics.aggregate_equivalents <= native_statistics.equivalents
            and native_statistics.equivalent_base_edges <= native_statistics.edges
            and native_statistics.ignored_class_assertions <= native_statistics.class_assertions
            and native_statistics.object_property_chains <= native_statistics.sub_object_properties
            and native_statistics.selected_annotation_assertions
            <= native_statistics.annotation_assertions
            and native_statistics.annotation_edges
            <= native_statistics.selected_annotation_assertions
            and (options.include_literals or native_statistics.annotation_edges == 0)
            and native_statistics.non_string_literal_renderings <= expected_annotation_edges
            and native_statistics.ignored_object_property_domains
            <= native_statistics.object_property_domains
            and native_statistics.ignored_object_property_ranges
            <= native_statistics.object_property_ranges
            and native_statistics.domain_range_edges
            <= (
                native_statistics.object_property_domains
                - native_statistics.ignored_object_property_domains
            )
            * (
                native_statistics.object_property_ranges
                - native_statistics.ignored_object_property_ranges
            )
            and native_statistics.edges == expected_edges
            and native_statistics.skipped_axioms == skipped_roots
        )
        if not exact_named_edges:
            batches.close()
            return (
                None,
                "private native batch integration requires exact root partitions, base-edge "
                "totals, role expansion, diagnostics, and skipped or silent ledgers",
            )
        return (
            NativeEncodedDirectCompilation(
                view=view,
                lease=lease,
                container_leases=container_leases,
                root_annotation_lease=root_annotation_lease,
                options=options,
                batches=batches,
                native_statistics=native_statistics,
                statistics=CompileStatistics(
                    ignored_shapes=sum(
                        count
                        for _constructor, count in _native_ignored_counts(
                            native_statistics,
                            options,
                        )
                    )
                    + native_statistics.object_property_chains,
                    skipped_axioms=native_statistics.skipped_axioms,
                ),
                role_state=role_state,
            ),
            None,
        )
    except Exception:
        batches.close()
        raise


def _lease_contains_annotation_assertions(lease: EncodedStructuralLease) -> bool:
    """Inspect validated root/tag columns without materializing or copying rows."""

    root_ids = lease.buffers["root_ids"]
    node_tags = lease.buffers["node_tags"]
    for offset in range(0, root_ids.nbytes, 4):
        node_id = (
            root_ids[offset]
            | root_ids[offset + 1] << 8
            | root_ids[offset + 2] << 16
            | root_ids[offset + 3] << 24
        )
        tag_offset = (node_id - 1) * 2
        tag = node_tags[tag_offset] | node_tags[tag_offset + 1] << 8
        if tag == _TAG_ANNOTATION_ASSERTION:
            return True
    return False


def _native_annotation_provenance_selection(
    view: object,
    closure_lease: EncodedStructuralLease,
) -> tuple[EncodedStructuralLease | None, str | None]:
    """Select a native root table or explain why provenance must fall back.

    A byte-identical root selection needs no auxiliary table.  An unequal exact
    direct selection is retained by kernel v30 and joined to canonical closure
    annotation identities before counting or publishing edges.
    """

    root_lease = _acquire_root_encoded_lease(view, closure_lease)
    if root_lease is None:
        return None, "core view does not support root-scoped native annotation provenance"
    try:
        root_compiler = prepare_native_encoded_direct(root_lease)
    except NativeEncodedDirectUnsupported:
        return None, "root-scoped native annotation provenance is not exact-direct"
    del root_compiler

    for name in ENCODED_DIRECT_BUFFER_ORDER:
        closure_buffer = closure_lease.buffers[name]
        root_buffer = root_lease.buffers[name]
        if closure_buffer.nbytes != root_buffer.nbytes or closure_buffer != root_buffer:
            return root_lease, None
    return None, None


def prepare_native_encoded_role_state() -> NativeEncodedDirectRoleState:
    """Create one unadvertised retained role-state handle for ordered calls."""

    module = load_native_module()
    try:
        version = getattr(module, "ENCODED_DIRECT_KERNEL_VERSION", None)
        factory = getattr(module, "EncodedDirectRoleState", None)
    except Exception as error:
        raise NativeBackendUnavailableError(
            "native encoded role-state metadata could not be read",
            details={"cause": type(error).__name__},
        ) from error
    if version != ENCODED_DIRECT_KERNEL_VERSION:
        raise NativeBackendUnavailableError("native encoded foundation version is incompatible")
    if not callable(factory):
        raise NativeBackendUnavailableError("native encoded role-state foundation is incomplete")
    try:
        kernel = factory()
    except MemoryError as error:
        raise _resource_error(error) from error
    except Exception as error:
        raise _execution_error(error) from error
    return NativeEncodedDirectRoleState(kernel, module)


def _validated_direct_descriptor_digest(lease: EncodedStructuralLease) -> bytes:
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
    return descriptor_sha256


def prepare_native_encoded_direct(
    lease: EncodedStructuralLease,
    *,
    root_annotation_lease: EncodedStructuralLease | None = None,
) -> NativeEncodedDirectCompiler:
    """Bind validated public leases to the unadvertised Rust foundation.

    No memoryview is copied.  The Rust constructor accepts exact full immutable-``bytes``
    exporters or the canonical eleven-column packed layout over one such exporter.  Arbitrary
    slices, mmap, and other valid exporters are deliberately reported as unsupported until the
    abi3-safe design expands.  An optional independent root table is retained for the native
    annotation-provenance join.
    """

    descriptor_sha256 = _validated_direct_descriptor_digest(lease)
    root_descriptor_sha256: bytes | None = None
    if root_annotation_lease is not None:
        root_descriptor_sha256 = _validated_direct_descriptor_digest(root_annotation_lease)
        if root_annotation_lease.owner is not lease.owner:
            raise SnapshotCompatibilityError(
                "encoded root annotation lease belongs to another closure owner"
            )

    module = load_native_module()
    try:
        version = getattr(module, "ENCODED_DIRECT_KERNEL_VERSION", None)
        order = getattr(module, "ENCODED_DIRECT_BUFFER_ORDER", None)
        compiler = getattr(module, "EncodedDirectCompiler", None)
        role_state_factory = getattr(module, "EncodedDirectRoleState", None)
        unsupported = getattr(module, "EncodedDirectUnsupportedError", None)
        buffer_error = getattr(module, "EncodedDirectBufferError", None)
        cancelled = getattr(module, "EncodedDirectCancelledError", None)
        reference_error = getattr(module, "EncodedDirectReferenceError", None)
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
    exceptions = (unsupported, buffer_error, cancelled, reference_error)
    if (
        not callable(compiler)
        or not callable(role_state_factory)
        or not all(isinstance(value, type) and issubclass(value, Exception) for value in exceptions)
    ):
        raise NativeBackendUnavailableError("native encoded foundation is incomplete")
    unsupported_type = cast(type[Exception], unsupported)
    buffer_error_type = cast(type[Exception], buffer_error)
    try:
        if root_annotation_lease is None:
            kernel = compiler(lease.encoded_view, lease.owner, descriptor_sha256)
        else:
            kernel = compiler(
                lease.encoded_view,
                lease.owner,
                descriptor_sha256,
                root_annotation_lease.encoded_view,
                root_annotation_lease.owner,
                root_descriptor_sha256,
            )
    except unsupported_type as error:
        raise NativeEncodedDirectUnsupported(str(error)) from error
    except buffer_error_type as error:
        raise SnapshotCompatibilityError(str(error)) from error
    except MemoryError as error:
        raise _resource_error(error) from error
    except Exception as error:
        raise _execution_error(error) from error
    return NativeEncodedDirectCompiler(lease, root_annotation_lease, kernel, module)


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


def _call_encoded_direct(module: Any, operation: Callable[[], Any]) -> Any:
    try:
        return operation()
    except MemoryError as error:
        raise _resource_error(error) from error
    except module.EncodedDirectUnsupportedError as error:
        raise NativeEncodedDirectUnsupported(str(error)) from error
    except module.EncodedDirectReferenceError as error:
        raise UnsupportedAxiomShapeError(
            str(error),
            details={
                "constructor": "ObjectInverseOf",
                "reference_error": "java.lang.ClassCastException",
            },
        ) from error
    except module.EncodedDirectBufferError as error:
        raise SnapshotCompatibilityError(str(error)) from error
    except module.EncodedDirectCancelledError as error:
        raise NativeEncodedDirectCancelled(str(error)) from error
    except ProjectionError:
        raise
    except Exception as error:
        raise _execution_error(error) from error


def _close_encoded_batches_quietly(compiler: NativeEncodedDirectCompiler) -> None:
    try:
        compiler._kernel.close_batches()
    except Exception:
        pass


def _native_nonnegative_int(kernel: Any, attribute: str, label: str) -> int:
    value = getattr(kernel, attribute, None)
    if type(value) is not int or value < 0:
        raise ProjectionError(f"native encoded batch iterator returned invalid {label}")
    return value


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
    "NativeEncodedDirectBatchIterator",
    "NativeEncodedDirectCancelled",
    "NativeEncodedDirectCompiler",
    "NativeEncodedDirectRoleState",
    "NativeEncodedDirectStatistics",
    "NativeEncodedDirectUnsupported",
    "iter_native_compilation",
    "iter_native_passthrough",
    "iter_native_policy",
    "load_native_module",
    "native_implementation_version",
    "native_runtime_metadata",
    "prepare_native_encoded_direct",
    "prepare_native_encoded_role_state",
]
