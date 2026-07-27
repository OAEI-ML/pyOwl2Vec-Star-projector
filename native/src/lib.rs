//! Bounded native kernels for the optional Python accelerator.
//!
//! The established engine receives owned edge batches from Python and applies
//! exact multiplicity/order policy.  P7 additionally owns a deliberately narrow
//! structural-columns v1 compiler for a bounded family of class, role, ABox,
//! annotation, and skipped logical/data-property axioms.  That private compiler
//! retains the public view and immutable `bytes` exporters, validates the whole
//! slice, and borrows them only during a GIL-released call.  The advertised
//! feature ledger remains unchanged until the complete compiler and acceptance
//! matrix exist.

#![deny(unsafe_op_in_unsafe_fn)]
#![deny(clippy::undocumented_unsafe_blocks)]

mod encoded_direct;

use std::collections::{HashMap, HashSet};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::atomic::{AtomicBool, AtomicU8, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

#[cfg(test)]
use encoded_direct::compile_direct_with_retained_role_state;
use encoded_direct::{
    prepare_direct_batches_uncommitted, prepare_direct_batches_with_retained_role_state,
    prepare_dynamic_composite_batches_uncommitted,
    prepare_dynamic_composite_batches_with_root_uncommitted,
    prepare_single_overlay_delta_batches_uncommitted, AnonymousScopeMapChain, DirectColumns,
    DirectCompileOptions, DirectCompileStats, DirectEdge, KernelError, OwnedRoleSnapshot,
    OwnedRoleState, PreparedDirectBatches, BUFFER_COUNT, BUFFER_NAMES, STATE_CANCELLED,
    STATE_FAILED, STATE_FINISHED, STATE_IDLE, STATE_RUNNING,
};
use pyo3::create_exception;
use pyo3::exceptions::{PyMemoryError, PyRuntimeError, PyValueError};
use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::pybacked::PyBackedBytes;
use pyo3::types::{
    PyBytes, PyInt, PyList, PyMapping, PyMemoryView, PySlice, PyString, PyTuple, PyType,
    PyTypeMethods,
};
use pyo3::IntoPyObjectExt;

const NATIVE_API_VERSION: u32 = 1;
const ENCODED_DIRECT_KERNEL_VERSION: u32 = 128;
const GENERAL_BUFFER_STABLE_ABI_MINIMUM: &str = "abi3-py311";
const COARSE_OUTPUT_CHUNK_EDGES: usize = 256;
const ENCODED_SCHEMA_NAME: &str = "pyowl-core/structural-columns";
const ENCODED_SCHEMA_VERSION: usize = 1;
const ENCODED_MODEL_SCHEMA: usize = 1;
const DIRECT_SEGMENT: usize = 1;
const OVERLAY_BASE_SEGMENT: usize = 2;
const OVERLAY_DELTA_SEGMENT: usize = 3;
const COMPOSITE_MEMBER_SEGMENT: usize = 4;
const COMPOSITE_BRIDGE_SEGMENT: usize = 5;
const POSTINGS_ALL: usize = 0;
const POSTINGS_INCLUDE: usize = 1;
const POSTINGS_EXCLUDE: usize = 2;
const ENCODED_DESCRIPTOR_SHA256: [u8; 32] = [
    0x9a, 0xd2, 0x9d, 0xb6, 0xa7, 0xe6, 0x16, 0xf6, 0x5c, 0xea, 0x29, 0x57, 0xbc, 0x5b, 0xa8, 0xd1,
    0xf9, 0xb9, 0x9e, 0xf0, 0xeb, 0x1f, 0xe1, 0x43, 0x2c, 0x09, 0xbe, 0x25, 0x78, 0x62, 0x67, 0xb5,
];

create_exception!(_native, EncodedDirectUnsupportedError, PyValueError);
create_exception!(_native, EncodedDirectBufferError, PyValueError);
create_exception!(_native, EncodedDirectCancelledError, PyRuntimeError);
create_exception!(_native, EncodedDirectReferenceError, PyValueError);

type EncodedDirectBatch = (Py<PyList>, Py<PyAny>);

#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
struct Edge {
    source: String,
    relation: String,
    destination: String,
}

impl From<(String, String, String)> for Edge {
    fn from(value: (String, String, String)) -> Self {
        Self {
            source: value.0,
            relation: value.1,
            destination: value.2,
        }
    }
}

impl From<Edge> for (String, String, String) {
    fn from(value: Edge) -> Self {
        (value.source, value.relation, value.destination)
    }
}

impl Edge {
    fn try_clone(&self) -> PyResult<Self> {
        fn clone_string(value: &str) -> PyResult<String> {
            let mut cloned = String::new();
            cloned
                .try_reserve_exact(value.len())
                .map_err(|_| PyMemoryError::new_err("native edge-string allocation failed"))?;
            cloned.push_str(value);
            Ok(cloned)
        }

        Ok(Self {
            source: clone_string(&self.source)?,
            relation: clone_string(&self.relation)?,
            destination: clone_string(&self.destination)?,
        })
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum OutputOrder {
    Canonical,
    Encounter,
}

impl OutputOrder {
    fn parse(value: &str) -> PyResult<Self> {
        match value {
            "canonical" => Ok(Self::Canonical),
            "encounter" => Ok(Self::Encounter),
            _ => Err(PyValueError::new_err(
                "order must be 'canonical' or 'encounter'",
            )),
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum DuplicatePolicy {
    Preserve,
    Unique,
}

impl DuplicatePolicy {
    fn parse(value: &str) -> PyResult<Self> {
        match value {
            "preserve" => Ok(Self::Preserve),
            "unique" => Ok(Self::Unique),
            _ => Err(PyValueError::new_err(
                "duplicates must be 'preserve' or 'unique'",
            )),
        }
    }
}

#[derive(Debug)]
struct EdgeEngine {
    order: OutputOrder,
    duplicates: DuplicatePolicy,
    seen: HashSet<Edge>,
    canonical: Vec<Edge>,
    raw_edges: usize,
    duplicate_edges: usize,
    finished: bool,
    cancelled: bool,
    max_edges: Option<usize>,
}

impl EdgeEngine {
    fn new(order: OutputOrder, duplicates: DuplicatePolicy, max_edges: Option<usize>) -> Self {
        Self {
            order,
            duplicates,
            seen: HashSet::new(),
            canonical: Vec::new(),
            raw_edges: 0,
            duplicate_edges: 0,
            finished: false,
            cancelled: false,
            max_edges,
        }
    }

    fn push(&mut self, batch: Vec<Edge>) -> PyResult<Vec<Edge>> {
        self.ensure_active()?;
        let next_count = self
            .raw_edges
            .checked_add(batch.len())
            .ok_or_else(|| PyMemoryError::new_err("native edge counter overflow"))?;
        if self.max_edges.is_some_and(|limit| next_count > limit) {
            return Err(PyMemoryError::new_err(
                "native edge limit reached before batch allocation",
            ));
        }
        self.seen
            .try_reserve(batch.len())
            .map_err(|_| PyMemoryError::new_err("native distinct-edge allocation failed"))?;
        if self.order == OutputOrder::Canonical {
            self.canonical
                .try_reserve(batch.len())
                .map_err(|_| PyMemoryError::new_err("native canonical allocation failed"))?;
        }

        let mut output = Vec::new();
        if self.order == OutputOrder::Encounter {
            output
                .try_reserve(batch.len())
                .map_err(|_| PyMemoryError::new_err("native output allocation failed"))?;
        }

        for edge in batch {
            self.raw_edges += 1;
            let is_new = self.seen.insert(edge.try_clone()?);
            if !is_new {
                self.duplicate_edges += 1;
            }
            match self.order {
                OutputOrder::Canonical => {
                    if self.duplicates == DuplicatePolicy::Preserve || is_new {
                        self.canonical.push(edge);
                    }
                }
                OutputOrder::Encounter => {
                    if self.duplicates == DuplicatePolicy::Preserve || is_new {
                        output.push(edge);
                    }
                }
            }
        }
        Ok(output)
    }

    fn finish(&mut self) -> PyResult<()> {
        self.ensure_active()?;
        if self.order == OutputOrder::Canonical {
            self.canonical.sort_unstable();
            // Draining with pop() is O(1); reverse retains ascending output.
            self.canonical.reverse();
        }
        self.finished = true;
        Ok(())
    }

    fn drain(&mut self, max_items: usize) -> PyResult<Vec<Edge>> {
        if !self.finished {
            return Err(PyValueError::new_err(
                "finish() must be called before canonical output is drained",
            ));
        }
        if self.cancelled {
            return Err(PyValueError::new_err("native processor was cancelled"));
        }
        let amount = max_items.min(self.canonical.len());
        let mut output = Vec::new();
        output
            .try_reserve(amount)
            .map_err(|_| PyMemoryError::new_err("native drain allocation failed"))?;
        for _ in 0..amount {
            if let Some(edge) = self.canonical.pop() {
                output.push(edge);
            }
        }
        Ok(output)
    }

    fn cancel(&mut self) {
        self.cancelled = true;
        self.canonical.clear();
        self.seen.clear();
    }

    fn ensure_active(&self) -> PyResult<()> {
        if self.cancelled {
            return Err(PyValueError::new_err("native processor was cancelled"));
        }
        if self.finished {
            return Err(PyValueError::new_err(
                "native processor is already finished",
            ));
        }
        Ok(())
    }
}

fn guarded<T>(operation: impl FnOnce() -> PyResult<T>) -> PyResult<T> {
    match catch_unwind(AssertUnwindSafe(operation)) {
        Ok(result) => result,
        Err(_) => Err(PyRuntimeError::new_err(
            "panic contained at the native projector boundary",
        )),
    }
}

#[pyclass(module = "pyowl2vec_star_projector._native")]
struct EdgeBatchProcessor {
    engine: EdgeEngine,
}

#[pymethods]
impl EdgeBatchProcessor {
    #[new]
    #[pyo3(signature = (order, duplicates, max_edges=None))]
    fn new(order: &str, duplicates: &str, max_edges: Option<usize>) -> PyResult<Self> {
        Ok(Self {
            engine: EdgeEngine::new(
                OutputOrder::parse(order)?,
                DuplicatePolicy::parse(duplicates)?,
                max_edges,
            ),
        })
    }

    fn push_batch(
        &mut self,
        batch: Vec<(String, String, String)>,
    ) -> PyResult<Vec<(String, String, String)>> {
        guarded(|| {
            let mut converted = Vec::new();
            converted
                .try_reserve_exact(batch.len())
                .map_err(|_| PyMemoryError::new_err("native input-batch allocation failed"))?;
            converted.extend(batch.into_iter().map(Edge::from));
            let output = self.engine.push(converted)?;
            edges_into_tuples(output)
        })
    }

    fn finish(&mut self, py: Python<'_>) -> PyResult<()> {
        guarded(|| py.detach(|| self.engine.finish()))
    }

    fn drain_batch(&mut self, max_items: usize) -> PyResult<Vec<(String, String, String)>> {
        if max_items == 0 {
            return Err(PyValueError::new_err("max_items must be positive"));
        }
        guarded(|| {
            let output = self.engine.drain(max_items)?;
            edges_into_tuples(output)
        })
    }

    fn cancel(&mut self) {
        self.engine.cancel();
    }

    #[getter]
    fn stats(&self) -> (usize, usize, usize) {
        (
            self.engine.raw_edges,
            self.engine.seen.len(),
            self.engine.duplicate_edges,
        )
    }

    #[getter]
    fn finished(&self) -> bool {
        self.engine.finished
    }

    #[getter]
    fn cancelled(&self) -> bool {
        self.engine.cancelled
    }

    #[getter]
    fn drained(&self) -> bool {
        self.engine.finished && self.engine.canonical.is_empty()
    }

    /// Exercise the production panic guard without allowing a panic to cross Python.
    fn test_injected_panic(&mut self) -> PyResult<()> {
        guarded(|| panic!("injected native projector panic"))
    }
}

fn edges_into_tuples(edges: Vec<Edge>) -> PyResult<Vec<(String, String, String)>> {
    let mut output = Vec::new();
    output
        .try_reserve_exact(edges.len())
        .map_err(|_| PyMemoryError::new_err("native tuple-batch allocation failed"))?;
    output.extend(edges.into_iter().map(Into::into));
    Ok(output)
}

impl Drop for EdgeBatchProcessor {
    fn drop(&mut self) {
        self.engine.cancel();
    }
}

#[derive(Debug, Default)]
struct RetainedRoleState {
    roles: Mutex<OwnedRoleState>,
    in_use: AtomicBool,
}

#[derive(Debug)]
struct RetainedRoleUse {
    retained: Arc<RetainedRoleState>,
}

impl Drop for RetainedRoleUse {
    fn drop(&mut self) {
        self.retained.in_use.store(false, Ordering::Release);
    }
}

impl RetainedRoleState {
    fn claim(self: &Arc<Self>) -> PyResult<RetainedRoleUse> {
        if self
            .in_use
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .is_err()
        {
            return Err(PyValueError::new_err(
                "encoded direct role state is already in use",
            ));
        }
        Ok(RetainedRoleUse {
            retained: Arc::clone(self),
        })
    }

    #[cfg(test)]
    fn compile_claimed(
        &self,
        columns: DirectColumns<'_>,
        root_annotation_columns: Option<DirectColumns<'_>>,
        options: DirectCompileOptions,
        compiler_state: &AtomicU8,
    ) -> PyResult<(
        Vec<encoded_direct::DirectEdge>,
        encoded_direct::DirectCompileStats,
    )> {
        let mut roles = self.roles.lock().map_err(|_| {
            PyRuntimeError::new_err("encoded direct role state is permanently failed")
        })?;
        compile_direct_with_retained_role_state(
            columns,
            root_annotation_columns,
            options,
            compiler_state,
            Some(&mut roles),
        )
        .map_err(kernel_error)
    }

    fn prepare_batches_uncommitted_claimed(
        &self,
        columns: DirectColumns<'_>,
        root_annotation_columns: Option<DirectColumns<'_>>,
        options: DirectCompileOptions,
        compiler_state: &AtomicU8,
    ) -> PyResult<PreparedDirectBatches> {
        let roles = self.roles.lock().map_err(|_| {
            PyRuntimeError::new_err("encoded direct role state is permanently failed")
        })?;
        prepare_direct_batches_uncommitted(
            columns,
            root_annotation_columns,
            options,
            compiler_state,
            Some(&roles),
        )
        .map_err(kernel_error)
    }

    #[allow(clippy::too_many_arguments)]
    fn prepare_dynamic_batches_uncommitted_claimed(
        &self,
        columns: &[DirectColumns<'_>],
        root_columns: Option<&[DirectColumns<'_>]>,
        options: DirectCompileOptions,
        compiler_state: &AtomicU8,
        max_work: usize,
        max_workspace_bytes: usize,
    ) -> PyResult<PreparedDirectBatches> {
        let roles = self.roles.lock().map_err(|_| {
            PyRuntimeError::new_err("encoded direct role state is permanently failed")
        })?;
        prepare_dynamic_composite_batches_with_root_uncommitted(
            columns,
            root_columns,
            options,
            compiler_state,
            Some(&roles),
            max_work,
            max_workspace_bytes,
        )
        .map_err(kernel_error)
    }

    #[allow(clippy::too_many_arguments)]
    fn prepare_overlay_batches_uncommitted_claimed(
        &self,
        columns: DirectColumns<'_>,
        delta_columns: DirectColumns<'_>,
        options: DirectCompileOptions,
        compiler_state: &AtomicU8,
        max_work: usize,
        max_workspace_bytes: usize,
    ) -> PyResult<PreparedDirectBatches> {
        let roles = self.roles.lock().map_err(|_| {
            PyRuntimeError::new_err("encoded direct role state is permanently failed")
        })?;
        prepare_single_overlay_delta_batches_uncommitted(
            columns,
            delta_columns,
            options,
            compiler_state,
            Some(&roles),
            max_work,
            max_workspace_bytes,
        )
        .map_err(kernel_error)
    }
}

/// Private retained role maps for explicit Scala-instance compatibility calls.
///
/// Only normalized role IRI strings cross operation boundaries; ontology
/// owners and structural buffers remain owned by their one-shot compiler.
#[pyclass(module = "pyowl2vec_star_projector._native", frozen)]
struct EncodedDirectRoleState {
    retained: Arc<RetainedRoleState>,
}

#[pymethods]
impl EncodedDirectRoleState {
    #[new]
    fn new() -> Self {
        Self {
            retained: Arc::new(RetainedRoleState::default()),
        }
    }

    #[getter]
    fn in_use(&self) -> bool {
        self.retained.in_use.load(Ordering::Acquire)
    }

    #[getter]
    fn subrole_property_count(&self) -> PyResult<usize> {
        self.retained
            .roles
            .lock()
            .map(|roles| roles.subrole_count())
            .map_err(|_| PyRuntimeError::new_err("encoded direct role state is permanently failed"))
    }

    #[getter]
    fn inverse_property_count(&self) -> PyResult<usize> {
        self.retained
            .roles
            .lock()
            .map(|roles| roles.inverse_count())
            .map_err(|_| PyRuntimeError::new_err("encoded direct role state is permanently failed"))
    }

    fn snapshot(&self) -> PyResult<OwnedRoleSnapshot> {
        self.retained
            .roles
            .lock()
            .map_err(|_| {
                PyRuntimeError::new_err("encoded direct role state is permanently failed")
            })?
            .snapshot()
            .map_err(kernel_error)
    }
}

#[derive(Debug, Default)]
struct DirectBatchOutput {
    stream: Option<PreparedDirectBatches>,
    remaining_edges: usize,
    batch_edges: usize,
    boundary_calls: usize,
    edge_batches: usize,
    peak_buffered_edges: usize,
    prepared: bool,
    draining: bool,
    exhausted: bool,
    cancelled: bool,
}

/// Exporter-specific lifetime ownership for a validated direct buffer.
///
/// A future general-buffer backend belongs here once the package can use
/// `pyo3::buffer::PyUntypedBuffer`. PyO3 0.28.3 compiles that API out for the
/// current `abi3-py310` floor, so the only safe zero-copy storage today is an
/// exact immutable Python `bytes` owner.
enum RetainedDirectStorage {
    ExactImmutableBytes(PyBackedBytes),
}

struct RetainedDirectBuffer {
    storage: RetainedDirectStorage,
    start: usize,
    end: usize,
}

impl RetainedDirectBuffer {
    fn as_slice(&self) -> &[u8] {
        match &self.storage {
            RetainedDirectStorage::ExactImmutableBytes(exporter) => &exporter[self.start..self.end],
        }
    }
}

#[derive(Default)]
struct RetainedAnonymousScopeMaps {
    buffers: Vec<RetainedDirectBuffer>,
}

impl AnonymousScopeMapChain for RetainedAnonymousScopeMaps {
    fn scope_map_count(&self) -> usize {
        self.buffers.len()
    }

    fn scope_map_at(&self, index: usize) -> Option<&[u8]> {
        self.buffers.get(index).map(RetainedDirectBuffer::as_slice)
    }
}

impl DirectBatchOutput {
    fn install(&mut self, stream: PreparedDirectBatches, batch_edges: usize) {
        let remaining_edges = stream.remaining_edges();
        let exhausted = remaining_edges == 0;
        *self = Self {
            stream: if exhausted { None } else { Some(stream) },
            remaining_edges,
            batch_edges,
            boundary_calls: 1,
            edge_batches: 0,
            peak_buffered_edges: 0,
            prepared: true,
            draining: false,
            exhausted,
            cancelled: false,
        };
    }

    fn remaining_edges(&self) -> usize {
        if self.prepared && !self.cancelled && !self.exhausted {
            self.remaining_edges
        } else {
            0
        }
    }

    fn state(&self) -> &'static str {
        if !self.prepared {
            "absent"
        } else if self.cancelled {
            "cancelled"
        } else if self.exhausted {
            "exhausted"
        } else {
            "active"
        }
    }

    fn cancel(&mut self) -> bool {
        if !self.prepared || self.cancelled || self.exhausted {
            return false;
        }
        self.stream = None;
        self.remaining_edges = 0;
        self.cancelled = true;
        true
    }
}

/// One-shot private compiler for the current honest P7 Rust slice.
///
/// The object retains both the encoded view/owner and an owned reference to
/// every immutable bytes exporter.  `compile_batch` is consequently able to
/// lend stable slices to Rust while `Python::detach` releases the GIL.  It is
/// intentionally absent from `FEATURES` until the full P7 compiler is ready.
struct RetainedCompositeMember {
    _view: Py<PyAny>,
    _owner: Py<PyAny>,
    buffers: Option<Vec<RetainedDirectBuffer>>,
    included_root_ids: Option<RetainedDirectBuffer>,
    excluded_root_ids: Option<RetainedDirectBuffer>,
    anonymous_scope_maps: RetainedAnonymousScopeMaps,
    root: Option<RetainedCompositeRootMember>,
}

struct RetainedCompositeRootMember {
    _view: Py<PyAny>,
    _owner: Py<PyAny>,
    buffers: Vec<RetainedDirectBuffer>,
    included_root_ids: Option<RetainedDirectBuffer>,
    excluded_root_ids: Option<RetainedDirectBuffer>,
    anonymous_scope_maps: RetainedAnonymousScopeMaps,
}

struct DynamicCompositeColumns<'a> {
    closure: Vec<DirectColumns<'a>>,
    root: Option<Vec<DirectColumns<'a>>>,
}

#[pyclass(module = "pyowl2vec_star_projector._native", frozen)]
struct EncodedDirectCompiler {
    _encoded_view: Py<PyAny>,
    _owner: Py<PyAny>,
    buffers: Vec<RetainedDirectBuffer>,
    _overlay_delta_view: Option<Py<PyAny>>,
    _overlay_delta_owner: Option<Py<PyAny>>,
    overlay_delta_buffers: Option<Vec<RetainedDirectBuffer>>,
    _third_member_view: Option<Py<PyAny>>,
    _third_member_owner: Option<Py<PyAny>>,
    third_member_buffers: Option<Vec<RetainedDirectBuffer>>,
    _fourth_member_view: Option<Py<PyAny>>,
    _fourth_member_owner: Option<Py<PyAny>>,
    fourth_member_buffers: Option<Vec<RetainedDirectBuffer>>,
    _nested_member_view: Option<Py<PyAny>>,
    _nested_member_owner: Option<Py<PyAny>>,
    _merge_manifest_view: Option<Py<PyAny>>,
    _merge_manifest_owner: Option<Py<PyAny>>,
    _root_merge_manifest_view: Option<Py<PyAny>>,
    _root_merge_manifest_owner: Option<Py<PyAny>>,
    composite_members: Option<Vec<RetainedCompositeMember>>,
    canonical_merge_limits: Option<(usize, usize)>,
    included_root_ids: Option<RetainedDirectBuffer>,
    excluded_root_ids: Option<RetainedDirectBuffer>,
    right_excluded_root_ids: Option<RetainedDirectBuffer>,
    third_excluded_root_ids: Option<RetainedDirectBuffer>,
    fourth_excluded_root_ids: Option<RetainedDirectBuffer>,
    anonymous_scope_map: Option<RetainedDirectBuffer>,
    right_anonymous_scope_map: Option<RetainedDirectBuffer>,
    _root_annotation_view: Option<Py<PyAny>>,
    _root_annotation_owner: Option<Py<PyAny>>,
    root_annotation_buffers: Option<Vec<RetainedDirectBuffer>>,
    state: AtomicU8,
    coarse_output_chunks: AtomicUsize,
    coarse_peak_buffered_edges: AtomicUsize,
    batch_output: Mutex<DirectBatchOutput>,
}

impl EncodedDirectCompiler {
    fn begin(&self) -> PyResult<()> {
        match self.state.compare_exchange(
            STATE_IDLE,
            STATE_RUNNING,
            Ordering::AcqRel,
            Ordering::Acquire,
        ) {
            Ok(_) => Ok(()),
            Err(STATE_CANCELLED) => Err(EncodedDirectCancelledError::new_err(
                "encoded direct compiler was cancelled",
            )),
            Err(STATE_RUNNING) => Err(PyValueError::new_err(
                "encoded direct compiler is already running",
            )),
            Err(STATE_FINISHED) => Err(PyValueError::new_err(
                "encoded direct compiler is already finished",
            )),
            Err(STATE_FAILED) => Err(PyValueError::new_err(
                "encoded direct compiler is permanently failed",
            )),
            Err(_) => Err(PyRuntimeError::new_err(
                "encoded direct compiler entered an invalid state",
            )),
        }
    }

    fn finish_result<T>(&self, result: PyResult<T>) -> PyResult<T> {
        match result {
            Ok(value) => match self.state.compare_exchange(
                STATE_RUNNING,
                STATE_FINISHED,
                Ordering::AcqRel,
                Ordering::Acquire,
            ) {
                Ok(_) => Ok(value),
                Err(STATE_CANCELLED) => Err(EncodedDirectCancelledError::new_err(
                    "encoded direct compiler was cancelled",
                )),
                Err(_) => Err(PyRuntimeError::new_err(
                    "encoded direct compiler changed state during compilation",
                )),
            },
            Err(error) => match self.state.compare_exchange(
                STATE_RUNNING,
                STATE_FAILED,
                Ordering::AcqRel,
                Ordering::Acquire,
            ) {
                Ok(_) => Err(error),
                Err(STATE_CANCELLED) => Err(EncodedDirectCancelledError::new_err(
                    "encoded direct compiler was cancelled",
                )),
                Err(_) => Err(error),
            },
        }
    }

    fn retained_base_columns<'a>(
        &'a self,
        base_columns: DirectColumns<'a>,
    ) -> PyResult<DirectColumns<'a>> {
        if let Some(members) = self.composite_members.as_ref() {
            let first = members.first().ok_or_else(|| {
                PyRuntimeError::new_err("encoded dynamic composite lost its base member")
            })?;
            if first.buffers.is_some() {
                return Err(PyRuntimeError::new_err(
                    "encoded dynamic composite duplicated its base buffers",
                ));
            }
            let included_root_ids = first
                .included_root_ids
                .as_ref()
                .map_or(&[][..], RetainedDirectBuffer::as_slice);
            let excluded_root_ids = first
                .excluded_root_ids
                .as_ref()
                .map_or(&[][..], RetainedDirectBuffer::as_slice);
            return Ok(base_columns
                .with_included_root_ids(included_root_ids)
                .with_excluded_root_ids(excluded_root_ids)
                .with_anonymous_scope_map_chain(&first.anonymous_scope_maps));
        }
        let included_root_ids = self
            .included_root_ids
            .as_ref()
            .map_or(&[][..], RetainedDirectBuffer::as_slice);
        let excluded_root_ids = self
            .excluded_root_ids
            .as_ref()
            .map_or(&[][..], RetainedDirectBuffer::as_slice);
        let anonymous_scope_map = self
            .anonymous_scope_map
            .as_ref()
            .map_or(&[][..], RetainedDirectBuffer::as_slice);
        Ok(base_columns
            .with_included_root_ids(included_root_ids)
            .with_excluded_root_ids(excluded_root_ids)
            .with_anonymous_scope_map(anonymous_scope_map))
    }

    fn dynamic_composite_columns<'a>(
        &'a self,
        base_columns: DirectColumns<'a>,
    ) -> PyResult<Option<DynamicCompositeColumns<'a>>> {
        let Some(members) = self.composite_members.as_ref() else {
            return Ok(None);
        };
        let mut columns = Vec::new();
        columns.try_reserve_exact(members.len()).map_err(|_| {
            PyMemoryError::new_err("encoded dynamic composite plan allocation failed")
        })?;
        for (index, member) in members.iter().enumerate() {
            let included_root_ids = member
                .included_root_ids
                .as_ref()
                .map_or(&[][..], RetainedDirectBuffer::as_slice);
            let excluded_root_ids = member
                .excluded_root_ids
                .as_ref()
                .map_or(&[][..], RetainedDirectBuffer::as_slice);
            let member_columns = if index == 0 {
                self.retained_base_columns(base_columns)?
            } else {
                let buffers = member.buffers.as_ref().ok_or_else(|| {
                    PyRuntimeError::new_err("encoded dynamic composite lost a member table")
                })?;
                let slices: [&[u8]; BUFFER_COUNT] =
                    std::array::from_fn(|buffer| buffers[buffer].as_slice());
                DirectColumns::from_ordered(slices)
            };
            columns.push(
                member_columns
                    .with_included_root_ids(included_root_ids)
                    .with_excluded_root_ids(excluded_root_ids)
                    .with_anonymous_scope_map_chain(&member.anonymous_scope_maps),
            );
        }
        let has_root = members.iter().any(|member| member.root.is_some());
        let root = if has_root {
            let mut root_columns = Vec::new();
            root_columns.try_reserve_exact(members.len()).map_err(|_| {
                PyMemoryError::new_err("encoded dynamic composite ROOT plan allocation failed")
            })?;
            for member in members {
                let root = member.root.as_ref().ok_or_else(|| {
                    PyRuntimeError::new_err("encoded dynamic composite lost one paired ROOT member")
                })?;
                let slices: [&[u8]; BUFFER_COUNT] =
                    std::array::from_fn(|buffer| root.buffers[buffer].as_slice());
                let included_root_ids = root
                    .included_root_ids
                    .as_ref()
                    .map_or(&[][..], RetainedDirectBuffer::as_slice);
                let excluded_root_ids = root
                    .excluded_root_ids
                    .as_ref()
                    .map_or(&[][..], RetainedDirectBuffer::as_slice);
                root_columns.push(
                    DirectColumns::from_ordered(slices)
                        .with_included_root_ids(included_root_ids)
                        .with_excluded_root_ids(excluded_root_ids)
                        .with_anonymous_scope_map_chain(&root.anonymous_scope_maps),
                );
            }
            Some(root_columns)
        } else {
            None
        };
        Ok(Some(DynamicCompositeColumns {
            closure: columns,
            root,
        }))
    }

    fn prepare_batches_owned(
        &self,
        py: Python<'_>,
        options: DirectCompileOptions,
        retained_role_state: Option<Arc<RetainedRoleState>>,
    ) -> PyResult<(
        PreparedDirectBatches,
        DirectCompileStats,
        Option<RetainedRoleUse>,
    )> {
        self.begin()?;
        let slices: [&[u8]; BUFFER_COUNT] =
            std::array::from_fn(|index| self.buffers[index].as_slice());
        let base_columns = DirectColumns::from_ordered(slices);
        let dynamic_composite_columns = self.dynamic_composite_columns(base_columns)?;
        let columns = self.retained_base_columns(base_columns)?;
        let overlay_delta_columns = self.overlay_delta_buffers.as_ref().map(|buffers| {
            let slices: [&[u8]; BUFFER_COUNT] =
                std::array::from_fn(|index| buffers[index].as_slice());
            let right_excluded_root_ids = self
                .right_excluded_root_ids
                .as_ref()
                .map_or(&[][..], RetainedDirectBuffer::as_slice);
            let right_anonymous_scope_map = if self._nested_member_view.is_some() {
                &[][..]
            } else {
                self.right_anonymous_scope_map
                    .as_ref()
                    .map_or(&[][..], RetainedDirectBuffer::as_slice)
            };
            DirectColumns::from_ordered(slices)
                .with_excluded_root_ids(right_excluded_root_ids)
                .with_anonymous_scope_map(right_anonymous_scope_map)
        });
        let third_member_columns = self.third_member_buffers.as_ref().map(|buffers| {
            let slices: [&[u8]; BUFFER_COUNT] =
                std::array::from_fn(|index| buffers[index].as_slice());
            let third_excluded_root_ids = self
                .third_excluded_root_ids
                .as_ref()
                .map_or(&[][..], RetainedDirectBuffer::as_slice);
            let third_anonymous_scope_map = if self._nested_member_view.is_some() {
                self.right_anonymous_scope_map
                    .as_ref()
                    .map_or(&[][..], RetainedDirectBuffer::as_slice)
            } else {
                &[][..]
            };
            DirectColumns::from_ordered(slices)
                .with_excluded_root_ids(third_excluded_root_ids)
                .with_anonymous_scope_map(third_anonymous_scope_map)
        });
        let fourth_member_columns = self.fourth_member_buffers.as_ref().map(|buffers| {
            let slices: [&[u8]; BUFFER_COUNT] =
                std::array::from_fn(|index| buffers[index].as_slice());
            let fourth_excluded_root_ids = self
                .fourth_excluded_root_ids
                .as_ref()
                .map_or(&[][..], RetainedDirectBuffer::as_slice);
            DirectColumns::from_ordered(slices).with_excluded_root_ids(fourth_excluded_root_ids)
        });
        let root_annotation_columns = self.root_annotation_buffers.as_ref().map(|buffers| {
            let slices: [&[u8]; BUFFER_COUNT] =
                std::array::from_fn(|index| buffers[index].as_slice());
            DirectColumns::from_ordered(slices)
        });
        let retained_role_use = match retained_role_state.as_ref() {
            Some(retained) => match retained.claim() {
                Ok(role_use) => Some(role_use),
                Err(error) => return self.finish_result(Err(error)),
            },
            None => None,
        };
        let result = guarded(|| {
            py.detach(|| {
                if let Some(composite_columns) = dynamic_composite_columns.as_ref() {
                    let (max_work, max_workspace_bytes) =
                        self.canonical_merge_limits.ok_or_else(|| {
                            PyRuntimeError::new_err(
                                "encoded dynamic composite lost its canonical limits",
                            )
                        })?;
                    if let Some(retained) = retained_role_state.as_ref() {
                        retained.prepare_dynamic_batches_uncommitted_claimed(
                            &composite_columns.closure,
                            composite_columns.root.as_deref(),
                            options,
                            &self.state,
                            max_work,
                            max_workspace_bytes,
                        )
                    } else {
                        prepare_dynamic_composite_batches_with_root_uncommitted(
                            &composite_columns.closure,
                            composite_columns.root.as_deref(),
                            options,
                            &self.state,
                            None,
                            max_work,
                            max_workspace_bytes,
                        )
                        .map_err(kernel_error)
                    }
                } else if let Some(delta_columns) = overlay_delta_columns {
                    let (max_work, max_workspace_bytes) =
                        self.canonical_merge_limits.ok_or_else(|| {
                            PyRuntimeError::new_err(
                                "encoded local-overlay compiler lost its canonical limits",
                            )
                        })?;
                    if self._merge_manifest_view.is_some() {
                        let member_count = 2
                            + usize::from(third_member_columns.is_some())
                            + usize::from(fourth_member_columns.is_some());
                        let composite_columns = [
                            columns,
                            delta_columns,
                            third_member_columns.unwrap_or(columns),
                            fourth_member_columns.unwrap_or(columns),
                        ];
                        if let Some(retained) = retained_role_state.as_ref() {
                            retained.prepare_dynamic_batches_uncommitted_claimed(
                                &composite_columns[..member_count],
                                None,
                                options,
                                &self.state,
                                max_work,
                                max_workspace_bytes,
                            )
                        } else {
                            prepare_dynamic_composite_batches_uncommitted(
                                &composite_columns[..member_count],
                                options,
                                &self.state,
                                None,
                                max_work,
                                max_workspace_bytes,
                            )
                            .map_err(kernel_error)
                        }
                    } else {
                        if let Some(retained) = retained_role_state.as_ref() {
                            retained.prepare_overlay_batches_uncommitted_claimed(
                                columns,
                                delta_columns,
                                options,
                                &self.state,
                                max_work,
                                max_workspace_bytes,
                            )
                        } else {
                            prepare_single_overlay_delta_batches_uncommitted(
                                columns,
                                delta_columns,
                                options,
                                &self.state,
                                None,
                                max_work,
                                max_workspace_bytes,
                            )
                            .map_err(kernel_error)
                        }
                    }
                } else if let Some(retained) = retained_role_state.as_ref() {
                    retained.prepare_batches_uncommitted_claimed(
                        columns,
                        root_annotation_columns,
                        options,
                        &self.state,
                    )
                } else {
                    prepare_direct_batches_with_retained_role_state(
                        columns,
                        root_annotation_columns,
                        options,
                        &self.state,
                        None,
                    )
                    .map_err(kernel_error)
                }
            })
        });
        let stream = match result {
            Ok(stream) => stream,
            Err(error) => return self.finish_result(Err(error)),
        };
        let statistics = stream.statistics();
        Ok((stream, statistics, retained_role_use))
    }

    fn cancel_batch_output(&self) -> PyResult<bool> {
        let mut output = self.batch_output.lock().map_err(|_| {
            PyRuntimeError::new_err("encoded direct batch output is permanently failed")
        })?;
        let cancelled = output.cancel();
        if cancelled {
            self.state.store(STATE_CANCELLED, Ordering::Release);
        }
        Ok(cancelled)
    }
}

#[pymethods]
impl EncodedDirectCompiler {
    #[new]
    #[allow(clippy::too_many_arguments)] // The private PyO3 ABI keeps retained inputs explicit.
    #[pyo3(signature = (
        encoded_view,
        expected_owner,
        descriptor_sha256,
        root_annotation_view=None,
        root_annotation_owner=None,
        root_annotation_descriptor_sha256=None,
        excluded_root_ids=None,
        overlay_delta_view=None,
        overlay_delta_owner=None,
        overlay_delta_descriptor_sha256=None,
        canonical_work_limit=None,
        canonical_workspace_limit=None,
        max_overlay_depth=None,
        merge_manifest_view=None,
        merge_manifest_owner=None,
        merge_manifest_descriptor_sha256=None,
        composite_members=None,
        root_merge_manifest_view=None,
        root_merge_manifest_owner=None,
        root_merge_manifest_descriptor_sha256=None,
        composite_root_members=None,
        included_root_ids=None,
        right_excluded_root_ids=None,
        third_member_view=None,
        third_member_owner=None,
        third_member_descriptor_sha256=None,
        third_excluded_root_ids=None,
        nested_member_view=None,
        nested_member_owner=None,
        nested_member_descriptor_sha256=None,
        fourth_member_view=None,
        fourth_member_owner=None,
        fourth_member_descriptor_sha256=None,
        fourth_excluded_root_ids=None,
        anonymous_scope_map=None,
        right_anonymous_scope_map=None,
    ))]
    fn new(
        encoded_view: &Bound<'_, PyAny>,
        expected_owner: &Bound<'_, PyAny>,
        descriptor_sha256: &Bound<'_, PyAny>,
        root_annotation_view: Option<&Bound<'_, PyAny>>,
        root_annotation_owner: Option<&Bound<'_, PyAny>>,
        root_annotation_descriptor_sha256: Option<&Bound<'_, PyAny>>,
        excluded_root_ids: Option<&Bound<'_, PyAny>>,
        overlay_delta_view: Option<&Bound<'_, PyAny>>,
        overlay_delta_owner: Option<&Bound<'_, PyAny>>,
        overlay_delta_descriptor_sha256: Option<&Bound<'_, PyAny>>,
        canonical_work_limit: Option<usize>,
        canonical_workspace_limit: Option<usize>,
        max_overlay_depth: Option<usize>,
        merge_manifest_view: Option<&Bound<'_, PyAny>>,
        merge_manifest_owner: Option<&Bound<'_, PyAny>>,
        merge_manifest_descriptor_sha256: Option<&Bound<'_, PyAny>>,
        composite_members: Option<&Bound<'_, PyAny>>,
        root_merge_manifest_view: Option<&Bound<'_, PyAny>>,
        root_merge_manifest_owner: Option<&Bound<'_, PyAny>>,
        root_merge_manifest_descriptor_sha256: Option<&Bound<'_, PyAny>>,
        composite_root_members: Option<&Bound<'_, PyAny>>,
        included_root_ids: Option<&Bound<'_, PyAny>>,
        right_excluded_root_ids: Option<&Bound<'_, PyAny>>,
        third_member_view: Option<&Bound<'_, PyAny>>,
        third_member_owner: Option<&Bound<'_, PyAny>>,
        third_member_descriptor_sha256: Option<&Bound<'_, PyAny>>,
        third_excluded_root_ids: Option<&Bound<'_, PyAny>>,
        nested_member_view: Option<&Bound<'_, PyAny>>,
        nested_member_owner: Option<&Bound<'_, PyAny>>,
        nested_member_descriptor_sha256: Option<&Bound<'_, PyAny>>,
        fourth_member_view: Option<&Bound<'_, PyAny>>,
        fourth_member_owner: Option<&Bound<'_, PyAny>>,
        fourth_member_descriptor_sha256: Option<&Bound<'_, PyAny>>,
        fourth_excluded_root_ids: Option<&Bound<'_, PyAny>>,
        anonymous_scope_map: Option<&Bound<'_, PyAny>>,
        right_anonymous_scope_map: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<Self> {
        let dynamic_composite_inputs = composite_members
            .map(parse_dynamic_composite_members)
            .transpose()?;
        let dynamic_composite_root_inputs = composite_root_members
            .map(parse_dynamic_composite_members)
            .transpose()?;
        let recursive_leaf_plan = dynamic_composite_inputs
            .as_deref()
            .is_some_and(|inputs| inputs.iter().any(|input| input.path.is_some()));
        if recursive_leaf_plan
            && dynamic_composite_inputs
                .as_deref()
                .is_some_and(|inputs| inputs.iter().any(|input| input.path.is_none()))
        {
            return Err(encoded_buffer_error(
                "encoded recursive leaf plan requires one exact path per retained table",
            ));
        }
        let recursive_max_overlay_depth = if recursive_leaf_plan {
            max_overlay_depth.ok_or_else(|| {
                encoded_buffer_error("encoded recursive leaf plan requires max_overlay_depth")
            })?
        } else {
            if max_overlay_depth.is_some() {
                return Err(encoded_buffer_error(
                    "max_overlay_depth requires an encoded recursive leaf plan",
                ));
            }
            0
        };
        let buffers = if recursive_leaf_plan {
            validate_encoded_view_header(encoded_view, expected_owner, descriptor_sha256)?;
            retained_structural_buffers(encoded_view)?
        } else {
            retained_direct_buffers(encoded_view, expected_owner, descriptor_sha256)?
        };
        let included_root_ids_view = included_root_ids;
        let included_root_ids = included_root_ids_view
            .map(|value| retained_exact_bytes_buffer(value, "included_root_ids"))
            .transpose()?;
        let excluded_root_ids_view = excluded_root_ids;
        let excluded_root_ids = excluded_root_ids_view
            .map(|value| retained_exact_bytes_buffer(value, "excluded_root_ids"))
            .transpose()?;
        let right_excluded_root_ids_view = right_excluded_root_ids;
        let right_excluded_root_ids = right_excluded_root_ids_view
            .map(|value| retained_exact_bytes_buffer(value, "right_excluded_root_ids"))
            .transpose()?;
        let third_excluded_root_ids_view = third_excluded_root_ids;
        let third_excluded_root_ids = third_excluded_root_ids_view
            .map(|value| retained_exact_bytes_buffer(value, "third_excluded_root_ids"))
            .transpose()?;
        let fourth_excluded_root_ids_view = fourth_excluded_root_ids;
        let fourth_excluded_root_ids = fourth_excluded_root_ids_view
            .map(|value| retained_exact_bytes_buffer(value, "fourth_excluded_root_ids"))
            .transpose()?;
        let anonymous_scope_map_view = anonymous_scope_map;
        let anonymous_scope_map = anonymous_scope_map_view
            .map(|value| retained_exact_bytes_buffer(value, "anonymous_scope_map"))
            .transpose()?;
        let right_anonymous_scope_map_view = right_anonymous_scope_map;
        let right_anonymous_scope_map = right_anonymous_scope_map_view
            .map(|value| retained_exact_bytes_buffer(value, "right_anonymous_scope_map"))
            .transpose()?;
        if anonymous_scope_map.is_some() != right_anonymous_scope_map.is_some() {
            return Err(encoded_buffer_error(
                "encoded scope-mapped composite requires both member scope maps",
            ));
        }
        if included_root_ids.is_some() && excluded_root_ids.is_some() {
            return Err(encoded_buffer_error(
                "encoded root selection cannot combine INCLUDE and EXCLUDE postings",
            ));
        }
        let root_annotation_buffers = match (
            root_annotation_view,
            root_annotation_owner,
            root_annotation_descriptor_sha256,
        ) {
            (None, None, None) => None,
            (Some(view), Some(owner), Some(digest)) => {
                Some(retained_direct_buffers(view, owner, digest)?)
            }
            _ => {
                return Err(encoded_buffer_error(
                    "encoded root annotation view, owner, and descriptor digest must be supplied together",
                ));
            }
        };
        let merge_manifest = match (
            merge_manifest_view,
            merge_manifest_owner,
            merge_manifest_descriptor_sha256,
        ) {
            (None, None, None) => None,
            (Some(view), Some(owner), Some(digest)) => Some((view, owner, digest)),
            _ => {
                return Err(encoded_buffer_error(
                    "encoded composite manifest view, owner, and descriptor digest must be supplied together",
                ));
            }
        };
        let root_merge_manifest = match (
            root_merge_manifest_view,
            root_merge_manifest_owner,
            root_merge_manifest_descriptor_sha256,
        ) {
            (None, None, None) => None,
            (Some(view), Some(owner), Some(digest)) => Some((view, owner, digest)),
            _ => {
                return Err(encoded_buffer_error(
                    "encoded composite ROOT manifest view, owner, and descriptor digest must be supplied together",
                ));
            }
        };
        let third_member = match (
            third_member_view,
            third_member_owner,
            third_member_descriptor_sha256,
        ) {
            (None, None, None) => None,
            (Some(view), Some(owner), Some(digest)) => Some((view, owner, digest)),
            _ => {
                return Err(encoded_buffer_error(
                    "encoded third member view, owner, and descriptor digest must be supplied together",
                ));
            }
        };
        let third_member_buffers = third_member
            .map(|(view, owner, digest)| retained_direct_buffers(view, owner, digest))
            .transpose()?;
        let fourth_member = match (
            fourth_member_view,
            fourth_member_owner,
            fourth_member_descriptor_sha256,
        ) {
            (None, None, None) => None,
            (Some(view), Some(owner), Some(digest)) => Some((view, owner, digest)),
            _ => {
                return Err(encoded_buffer_error(
                    "encoded fourth member view, owner, and descriptor digest must be supplied together",
                ));
            }
        };
        let fourth_member_buffers = fourth_member
            .map(|(view, owner, digest)| retained_direct_buffers(view, owner, digest))
            .transpose()?;
        let nested_member = match (
            nested_member_view,
            nested_member_owner,
            nested_member_descriptor_sha256,
        ) {
            (None, None, None) => None,
            (Some(view), Some(owner), Some(digest)) => Some((view, owner, digest)),
            _ => {
                return Err(encoded_buffer_error(
                    "encoded nested member view, owner, and descriptor digest must be supplied together",
                ));
            }
        };
        let dynamic_composite = if let Some(inputs) = dynamic_composite_inputs.as_ref() {
            if overlay_delta_view.is_some()
                || overlay_delta_owner.is_some()
                || overlay_delta_descriptor_sha256.is_some()
                || third_member.is_some()
                || fourth_member.is_some()
                || nested_member.is_some()
                || root_annotation_buffers.is_some()
                || included_root_ids.is_some()
                || excluded_root_ids.is_some()
                || right_excluded_root_ids.is_some()
                || third_excluded_root_ids.is_some()
                || fourth_excluded_root_ids.is_some()
                || anonymous_scope_map.is_some()
                || right_anonymous_scope_map.is_some()
            {
                return Err(encoded_buffer_error(
                    "encoded dynamic composite cannot combine fixed segmented inputs",
                ));
            }
            let (Some(max_work), Some(max_workspace_bytes)) =
                (canonical_work_limit, canonical_workspace_limit)
            else {
                return Err(encoded_buffer_error(
                    "encoded dynamic composite requires canonical limits",
                ));
            };
            if max_work == 0 || max_workspace_bytes == 0 {
                return Err(PyMemoryError::new_err(
                    "encoded dynamic composite canonical limits must be positive",
                ));
            }
            let Some((manifest, manifest_owner, manifest_digest)) = merge_manifest else {
                return Err(encoded_buffer_error(
                    "encoded dynamic composite requires an exact manifest",
                ));
            };
            if !inputs[0].view.is(encoded_view) || !inputs[0].owner.is(expected_owner) {
                return Err(encoded_buffer_error(
                    "encoded dynamic composite first member lost its base identity",
                ));
            }
            let mut bindings = Vec::new();
            bindings.try_reserve_exact(inputs.len()).map_err(|_| {
                PyMemoryError::new_err("encoded composite binding allocation failed")
            })?;
            for input in inputs {
                bindings.push((
                    &input.view,
                    &input.owner,
                    input.included_root_ids.as_ref(),
                    input.excluded_root_ids.as_ref(),
                    input.anonymous_scope_map.as_ref(),
                ));
            }
            let mut direct_validation_identities = HashSet::new();
            let closure_validation_work = if recursive_leaf_plan {
                validate_recursive_leaf_plan(
                    manifest,
                    manifest_owner,
                    manifest_digest,
                    inputs,
                    max_work,
                    recursive_max_overlay_depth,
                )?
            } else {
                validate_direct_member_composite_manifest(
                    manifest,
                    manifest_owner,
                    manifest_digest,
                    &bindings,
                    true,
                )?;
                let work = direct_composite_validation_work(
                    manifest,
                    inputs,
                    &mut direct_validation_identities,
                )?;
                if work > max_work {
                    return Err(PyMemoryError::new_err(
                        "encoded direct composite validation exceeds max_canonical_work",
                    ));
                }
                work
            };
            let root_inputs = dynamic_composite_root_inputs.as_deref();
            if let Some(root_inputs) = root_inputs {
                if root_inputs.len() != inputs.len() {
                    return Err(encoded_buffer_error(
                        "encoded composite ROOT plan must pair every closure member",
                    ));
                }
                let Some((root_manifest, root_manifest_owner, root_manifest_digest)) =
                    root_merge_manifest
                else {
                    return Err(encoded_buffer_error(
                        "encoded composite ROOT plan requires its exact manifest",
                    ));
                };
                let root_recursive_leaf_plan = root_inputs
                    .first()
                    .is_some_and(|input| input.path.is_some());
                if root_inputs
                    .iter()
                    .any(|input| input.path.is_some() != root_recursive_leaf_plan)
                    || root_recursive_leaf_plan != recursive_leaf_plan
                {
                    return Err(encoded_buffer_error(
                        "encoded composite ROOT rows use another path form",
                    ));
                }
                if recursive_leaf_plan {
                    let root_work_limit = max_work
                        .checked_sub(closure_validation_work)
                        .filter(|remaining| *remaining != 0)
                        .ok_or_else(|| {
                            PyMemoryError::new_err(
                                "encoded paired recursive validation exceeds max_canonical_work",
                            )
                        })?;
                    validate_recursive_root_plan(
                        manifest,
                        manifest_owner,
                        inputs,
                        root_manifest,
                        root_manifest_owner,
                        root_manifest_digest,
                        root_inputs,
                        root_work_limit,
                        recursive_max_overlay_depth,
                    )?;
                }
                let mut root_bindings = Vec::new();
                root_bindings
                    .try_reserve_exact(root_inputs.len())
                    .map_err(|_| {
                        PyMemoryError::new_err("encoded composite ROOT binding allocation failed")
                    })?;
                for input in root_inputs {
                    root_bindings.push((
                        &input.view,
                        &input.owner,
                        input.included_root_ids.as_ref(),
                        input.excluded_root_ids.as_ref(),
                        input.anonymous_scope_map.as_ref(),
                    ));
                }
                if !recursive_leaf_plan {
                    validate_direct_member_composite_manifest(
                        root_manifest,
                        root_manifest_owner,
                        root_manifest_digest,
                        &root_bindings,
                        true,
                    )?;
                    let root_validation_work = direct_composite_validation_work(
                        root_manifest,
                        root_inputs,
                        &mut direct_validation_identities,
                    )?;
                    let paired_validation_work = closure_validation_work
                        .checked_add(root_validation_work)
                        .ok_or_else(|| {
                            PyMemoryError::new_err(
                                "encoded paired direct validation-work counter overflow",
                            )
                        })?;
                    if paired_validation_work > max_work {
                        return Err(PyMemoryError::new_err(
                            "encoded paired direct validation exceeds max_canonical_work",
                        ));
                    }
                    validate_paired_composite_manifests(
                        manifest,
                        manifest_owner,
                        root_manifest,
                        root_manifest_owner,
                        inputs.len(),
                    )?;
                }
            } else if root_merge_manifest.is_some() {
                return Err(encoded_buffer_error(
                    "encoded composite ROOT manifest requires its member plan",
                ));
            }

            let mut retained = Vec::new();
            retained.try_reserve_exact(inputs.len()).map_err(|_| {
                PyMemoryError::new_err("encoded composite retention allocation failed")
            })?;
            for (index, input) in inputs.iter().enumerate() {
                if input.included_root_ids.is_some() && input.excluded_root_ids.is_some() {
                    return Err(encoded_buffer_error(
                        "encoded composite member cannot combine INCLUDE and EXCLUDE tables",
                    ));
                }
                let buffers = if index == 0 {
                    validate_encoded_view_header(
                        &input.view,
                        &input.owner,
                        &input.descriptor_sha256,
                    )?;
                    if !recursive_leaf_plan {
                        validate_direct_segment(&input.view, &input.owner)?;
                    }
                    None
                } else if recursive_leaf_plan {
                    validate_encoded_view_header(
                        &input.view,
                        &input.owner,
                        &input.descriptor_sha256,
                    )?;
                    Some(retained_structural_buffers(&input.view)?)
                } else {
                    Some(retained_direct_buffers(
                        &input.view,
                        &input.owner,
                        &input.descriptor_sha256,
                    )?)
                };
                let root = root_inputs
                    .map(|root_inputs| {
                        let root_input = &root_inputs[index];
                        if root_input.included_root_ids.is_some()
                            && root_input.excluded_root_ids.is_some()
                        {
                            return Err(encoded_buffer_error(
                                "encoded composite ROOT member cannot combine INCLUDE and EXCLUDE tables",
                            ));
                        }
                        let root_buffers = if recursive_leaf_plan {
                            validate_encoded_view_header(
                                &root_input.view,
                                &root_input.owner,
                                &root_input.descriptor_sha256,
                            )?;
                            retained_structural_buffers(&root_input.view)?
                        } else {
                            retained_direct_buffers(
                                &root_input.view,
                                &root_input.owner,
                                &root_input.descriptor_sha256,
                            )?
                        };
                        Ok(RetainedCompositeRootMember {
                            _view: root_input.view.clone().unbind(),
                            _owner: root_input.owner.clone().unbind(),
                            buffers: root_buffers,
                            included_root_ids: root_input
                                .included_root_ids
                                .as_ref()
                                .map(|value| {
                                    retained_exact_bytes_buffer(
                                        value,
                                        "composite ROOT included_root_ids",
                                    )
                                })
                                .transpose()?,
                            excluded_root_ids: root_input
                                .excluded_root_ids
                                .as_ref()
                                .map(|value| {
                                    retained_exact_bytes_buffer(
                                        value,
                                        "composite ROOT excluded_root_ids",
                                    )
                                })
                                .transpose()?,
                            anonymous_scope_maps: retain_dynamic_scope_maps(
                                root_input,
                                "composite ROOT anonymous_scope_map",
                            )?,
                        })
                    })
                    .transpose()?;
                retained.push(RetainedCompositeMember {
                    _view: input.view.clone().unbind(),
                    _owner: input.owner.clone().unbind(),
                    buffers,
                    included_root_ids: input
                        .included_root_ids
                        .as_ref()
                        .map(|value| {
                            retained_exact_bytes_buffer(value, "composite included_root_ids")
                        })
                        .transpose()?,
                    excluded_root_ids: input
                        .excluded_root_ids
                        .as_ref()
                        .map(|value| {
                            retained_exact_bytes_buffer(value, "composite excluded_root_ids")
                        })
                        .transpose()?,
                    anonymous_scope_maps: retain_dynamic_scope_maps(
                        input,
                        "composite anonymous_scope_map",
                    )?,
                    root,
                });
            }
            Some((retained, (max_work, max_workspace_bytes)))
        } else if dynamic_composite_root_inputs.is_some() || root_merge_manifest.is_some() {
            return Err(encoded_buffer_error(
                "encoded composite ROOT plan requires a closure composite plan",
            ));
        } else {
            None
        };
        let (composite_members, dynamic_composite_limits) = match dynamic_composite {
            Some((members, limits)) => (Some(members), Some(limits)),
            None => (None, None),
        };
        let (overlay_delta_buffers, canonical_merge_limits) = if let Some(limits) =
            dynamic_composite_limits
        {
            (None, Some(limits))
        } else {
            match (
                overlay_delta_view,
                overlay_delta_owner,
                overlay_delta_descriptor_sha256,
                canonical_work_limit,
                canonical_workspace_limit,
            ) {
                (None, None, None, None, None) => (None, None),
                (
                    Some(view),
                    Some(owner),
                    Some(digest),
                    Some(max_work),
                    Some(max_workspace_bytes),
                ) => {
                    if root_annotation_buffers.is_some() {
                        return Err(EncodedDirectUnsupportedError::new_err(
                            "bounded local-overlay compilation cannot combine root provenance",
                        ));
                    }
                    if max_work == 0 || max_workspace_bytes == 0 {
                        return Err(PyMemoryError::new_err(
                            "encoded local-overlay canonical limits must be positive",
                        ));
                    }
                    let buffers = if let Some((manifest, manifest_owner, manifest_digest)) =
                        merge_manifest
                    {
                        if let Some((nested_view, nested_owner, nested_digest)) = nested_member {
                            if !nested_view.is(view) || !nested_owner.is(owner) {
                                return Err(encoded_buffer_error(
                                    "encoded nested member lost its retained table identity",
                                ));
                            }
                            validate_encoded_view_header(nested_view, nested_owner, nested_digest)?;
                            let Some((third_view, third_owner, _)) = third_member else {
                                return Err(EncodedDirectUnsupportedError::new_err(
                                    "bounded nested-member composite requires one direct sibling",
                                ));
                            };
                            let buffers = retained_overlay_delta_buffers(
                                view,
                                owner,
                                digest,
                                encoded_view,
                                expected_owner,
                                excluded_root_ids_view,
                            )?;
                            if let Some((fourth_view, fourth_owner, _)) = fourth_member {
                                validate_nested_member_composite_manifest(
                                    manifest,
                                    manifest_owner,
                                    manifest_digest,
                                    nested_view,
                                    nested_owner,
                                    encoded_view,
                                    expected_owner,
                                    right_excluded_root_ids_view,
                                    &[
                                        (third_view, third_owner, third_excluded_root_ids_view),
                                        (fourth_view, fourth_owner, fourth_excluded_root_ids_view),
                                    ],
                                    anonymous_scope_map_view,
                                    right_anonymous_scope_map_view,
                                )?;
                            } else {
                                validate_nested_member_composite_manifest(
                                    manifest,
                                    manifest_owner,
                                    manifest_digest,
                                    nested_view,
                                    nested_owner,
                                    encoded_view,
                                    expected_owner,
                                    right_excluded_root_ids_view,
                                    &[(third_view, third_owner, third_excluded_root_ids_view)],
                                    anonymous_scope_map_view,
                                    right_anonymous_scope_map_view,
                                )?;
                            }
                            buffers
                        } else if let Some((third_view, third_owner, _)) = third_member {
                            let buffers = retained_direct_buffers(view, owner, digest)?;
                            if let Some((fourth_view, fourth_owner, _)) = fourth_member {
                                validate_direct_member_composite_manifest(
                                    manifest,
                                    manifest_owner,
                                    manifest_digest,
                                    &[
                                        (
                                            encoded_view,
                                            expected_owner,
                                            included_root_ids_view,
                                            excluded_root_ids_view,
                                            None,
                                        ),
                                        (view, owner, None, right_excluded_root_ids_view, None),
                                        (
                                            third_view,
                                            third_owner,
                                            None,
                                            third_excluded_root_ids_view,
                                            None,
                                        ),
                                        (
                                            fourth_view,
                                            fourth_owner,
                                            None,
                                            fourth_excluded_root_ids_view,
                                            None,
                                        ),
                                    ],
                                    false,
                                )?;
                            } else {
                                validate_direct_member_composite_manifest(
                                    manifest,
                                    manifest_owner,
                                    manifest_digest,
                                    &[
                                        (
                                            encoded_view,
                                            expected_owner,
                                            included_root_ids_view,
                                            excluded_root_ids_view,
                                            None,
                                        ),
                                        (view, owner, None, right_excluded_root_ids_view, None),
                                        (
                                            third_view,
                                            third_owner,
                                            None,
                                            third_excluded_root_ids_view,
                                            None,
                                        ),
                                    ],
                                    false,
                                )?;
                            }
                            buffers
                        } else {
                            let buffers = retained_direct_buffers(view, owner, digest)?;
                            validate_two_member_composite_manifest(
                                manifest,
                                manifest_owner,
                                manifest_digest,
                                encoded_view,
                                expected_owner,
                                view,
                                owner,
                                included_root_ids_view,
                                excluded_root_ids_view,
                                right_excluded_root_ids_view,
                                anonymous_scope_map_view,
                                right_anonymous_scope_map_view,
                            )?;
                            buffers
                        }
                    } else {
                        retained_overlay_delta_buffers(
                            view,
                            owner,
                            digest,
                            encoded_view,
                            expected_owner,
                            excluded_root_ids_view,
                        )?
                    };
                    (Some(buffers), Some((max_work, max_workspace_bytes)))
                }
                _ => {
                    return Err(encoded_buffer_error(
                    "encoded local-overlay view, owner, descriptor digest, and canonical limits must be supplied together",
                ));
                }
            }
        };
        if merge_manifest.is_some()
            && overlay_delta_buffers.is_none()
            && composite_members.is_none()
        {
            return Err(encoded_buffer_error(
                "encoded composite manifest requires two retained merge tables",
            ));
        }
        if third_member.is_some() && merge_manifest.is_none() {
            return Err(EncodedDirectUnsupportedError::new_err(
                "encoded third composite member requires an exact composite manifest",
            ));
        }
        if nested_member.is_some() && (third_member.is_none() || merge_manifest.is_none()) {
            return Err(EncodedDirectUnsupportedError::new_err(
                "encoded nested composite member requires three retained merge tables",
            ));
        }
        if fourth_member.is_some() && (third_member.is_none() || merge_manifest.is_none()) {
            return Err(EncodedDirectUnsupportedError::new_err(
                "encoded fourth composite member requires an exact composite manifest",
            ));
        }
        if third_member.is_none() && third_excluded_root_ids.is_some() {
            return Err(EncodedDirectUnsupportedError::new_err(
                "encoded third EXCLUDE selection requires an exact third composite member",
            ));
        }
        if fourth_member.is_none() && fourth_excluded_root_ids.is_some() {
            return Err(EncodedDirectUnsupportedError::new_err(
                "encoded fourth EXCLUDE selection requires an exact fourth composite member",
            ));
        }
        if included_root_ids.is_some() && merge_manifest.is_none() {
            return Err(EncodedDirectUnsupportedError::new_err(
                "encoded INCLUDE selection requires an exact composite manifest",
            ));
        }
        if right_excluded_root_ids.is_some() && merge_manifest.is_none() {
            return Err(EncodedDirectUnsupportedError::new_err(
                "encoded right EXCLUDE selection requires an exact composite manifest",
            ));
        }
        if included_root_ids.is_some()
            && (right_excluded_root_ids.is_some()
                || third_excluded_root_ids.is_some()
                || fourth_excluded_root_ids.is_some())
        {
            return Err(encoded_buffer_error(
                "encoded composite root selection cannot mix INCLUDE and EXCLUDE postings",
            ));
        }
        if nested_member.is_some() && included_root_ids.is_some() {
            return Err(encoded_buffer_error(
                "encoded nested composite member does not support outer INCLUDE",
            ));
        }
        if anonymous_scope_map.is_some() {
            let exact_two_member = merge_manifest.is_some()
                && third_member.is_none()
                && fourth_member.is_none()
                && nested_member.is_none();
            let exact_nested_member = merge_manifest.is_some()
                && third_member.is_some()
                && fourth_member.is_none()
                && nested_member.is_some();
            let exact_four_table_nested_member = merge_manifest.is_some()
                && third_member.is_some()
                && fourth_member.is_some()
                && nested_member.is_some();
            if !(exact_two_member || exact_nested_member || exact_four_table_nested_member)
                || included_root_ids.is_some()
                || (excluded_root_ids.is_some()
                    && !(exact_nested_member || exact_four_table_nested_member))
                || (right_excluded_root_ids.is_some()
                    && !(exact_nested_member || exact_four_table_nested_member))
                || (third_excluded_root_ids.is_some()
                    && !(exact_nested_member || exact_four_table_nested_member))
                || (fourth_excluded_root_ids.is_some() && !exact_four_table_nested_member)
            {
                return Err(EncodedDirectUnsupportedError::new_err(
                    "encoded anonymous scope remapping requires an exact two-member or bounded nested-member composite",
                ));
            }
        }
        Ok(Self {
            _encoded_view: encoded_view.clone().unbind(),
            _owner: expected_owner.clone().unbind(),
            buffers,
            _overlay_delta_view: overlay_delta_view.map(|value| value.clone().unbind()),
            _overlay_delta_owner: overlay_delta_owner.map(|value| value.clone().unbind()),
            overlay_delta_buffers,
            _third_member_view: third_member_view.map(|value| value.clone().unbind()),
            _third_member_owner: third_member_owner.map(|value| value.clone().unbind()),
            third_member_buffers,
            _fourth_member_view: fourth_member_view.map(|value| value.clone().unbind()),
            _fourth_member_owner: fourth_member_owner.map(|value| value.clone().unbind()),
            fourth_member_buffers,
            _nested_member_view: nested_member_view.map(|value| value.clone().unbind()),
            _nested_member_owner: nested_member_owner.map(|value| value.clone().unbind()),
            _merge_manifest_view: merge_manifest_view.map(|value| value.clone().unbind()),
            _merge_manifest_owner: merge_manifest_owner.map(|value| value.clone().unbind()),
            _root_merge_manifest_view: root_merge_manifest_view.map(|value| value.clone().unbind()),
            _root_merge_manifest_owner: root_merge_manifest_owner
                .map(|value| value.clone().unbind()),
            composite_members,
            canonical_merge_limits,
            included_root_ids,
            excluded_root_ids,
            right_excluded_root_ids,
            third_excluded_root_ids,
            fourth_excluded_root_ids,
            anonymous_scope_map,
            right_anonymous_scope_map,
            _root_annotation_view: root_annotation_view.map(|value| value.clone().unbind()),
            _root_annotation_owner: root_annotation_owner.map(|value| value.clone().unbind()),
            root_annotation_buffers,
            state: AtomicU8::new(STATE_IDLE),
            coarse_output_chunks: AtomicUsize::new(0),
            coarse_peak_buffered_edges: AtomicUsize::new(0),
            batch_output: Mutex::new(DirectBatchOutput::default()),
        })
    }

    /// Compile one materialized Python list through bounded native chunks.
    #[pyo3(signature = (
        bidirectional,
        max_edges,
        max_iri_bytes,
        edge_factory,
        edge_type,
        edge_allocation_probe,
        statistics_factory,
        statistics_type,
        statistics_allocation_probe,
        asserted_taxonomy_only=false,
        only_taxonomy=false,
        include_literals=false,
        role_state=None,
    ))]
    #[allow(clippy::too_many_arguments)] // The private PyO3 ABI keeps options explicit.
    fn compile_batch(
        &self,
        py: Python<'_>,
        bidirectional: bool,
        max_edges: usize,
        max_iri_bytes: usize,
        edge_factory: &Bound<'_, PyAny>,
        edge_type: &Bound<'_, PyAny>,
        edge_allocation_probe: Option<&Bound<'_, PyAny>>,
        statistics_factory: &Bound<'_, PyAny>,
        statistics_type: &Bound<'_, PyAny>,
        statistics_allocation_probe: Option<&Bound<'_, PyAny>>,
        asserted_taxonomy_only: bool,
        only_taxonomy: bool,
        include_literals: bool,
        role_state: Option<PyRef<'_, EncodedDirectRoleState>>,
    ) -> PyResult<EncodedDirectBatch> {
        if max_edges == 0 {
            return Err(PyValueError::new_err("max_edges must be positive"));
        }
        if max_iri_bytes == 0 {
            return Err(PyValueError::new_err("max_iri_bytes must be positive"));
        }
        let options = DirectCompileOptions {
            bidirectional,
            asserted_taxonomy_only,
            only_taxonomy,
            include_literals,
            max_edges,
            max_iri_bytes,
        };
        let retained_role_state = role_state.map(|value| Arc::clone(&value.retained));
        self.begin()?;
        let slices: [&[u8]; BUFFER_COUNT] =
            std::array::from_fn(|index| self.buffers[index].as_slice());
        let base_columns = DirectColumns::from_ordered(slices);
        let dynamic_composite_columns = self.dynamic_composite_columns(base_columns)?;
        let columns = self.retained_base_columns(base_columns)?;
        let overlay_delta_columns = self.overlay_delta_buffers.as_ref().map(|buffers| {
            let slices: [&[u8]; BUFFER_COUNT] =
                std::array::from_fn(|index| buffers[index].as_slice());
            let right_excluded_root_ids = self
                .right_excluded_root_ids
                .as_ref()
                .map_or(&[][..], RetainedDirectBuffer::as_slice);
            let right_anonymous_scope_map = if self._nested_member_view.is_some() {
                &[][..]
            } else {
                self.right_anonymous_scope_map
                    .as_ref()
                    .map_or(&[][..], RetainedDirectBuffer::as_slice)
            };
            DirectColumns::from_ordered(slices)
                .with_excluded_root_ids(right_excluded_root_ids)
                .with_anonymous_scope_map(right_anonymous_scope_map)
        });
        let third_member_columns = self.third_member_buffers.as_ref().map(|buffers| {
            let slices: [&[u8]; BUFFER_COUNT] =
                std::array::from_fn(|index| buffers[index].as_slice());
            let third_excluded_root_ids = self
                .third_excluded_root_ids
                .as_ref()
                .map_or(&[][..], RetainedDirectBuffer::as_slice);
            let third_anonymous_scope_map = if self._nested_member_view.is_some() {
                self.right_anonymous_scope_map
                    .as_ref()
                    .map_or(&[][..], RetainedDirectBuffer::as_slice)
            } else {
                &[][..]
            };
            DirectColumns::from_ordered(slices)
                .with_excluded_root_ids(third_excluded_root_ids)
                .with_anonymous_scope_map(third_anonymous_scope_map)
        });
        let fourth_member_columns = self.fourth_member_buffers.as_ref().map(|buffers| {
            let slices: [&[u8]; BUFFER_COUNT] =
                std::array::from_fn(|index| buffers[index].as_slice());
            let fourth_excluded_root_ids = self
                .fourth_excluded_root_ids
                .as_ref()
                .map_or(&[][..], RetainedDirectBuffer::as_slice);
            DirectColumns::from_ordered(slices).with_excluded_root_ids(fourth_excluded_root_ids)
        });
        let root_annotation_columns = self.root_annotation_buffers.as_ref().map(|buffers| {
            let slices: [&[u8]; BUFFER_COUNT] =
                std::array::from_fn(|index| buffers[index].as_slice());
            DirectColumns::from_ordered(slices)
        });
        let retained_role_use = match retained_role_state.as_ref() {
            Some(retained) => match retained.claim() {
                Ok(role_use) => Some(role_use),
                Err(error) => return self.finish_result(Err(error)),
            },
            None => None,
        };
        let result = guarded(|| {
            let edge_type = require_direct_edge_layout(
                py,
                edge_type,
                "encoded direct edge type has an incompatible native allocation layout",
            )?;
            require_canonical_factory(
                edge_factory,
                edge_type.as_any(),
                "encoded direct edge factory is not canonical",
            )?;
            let statistics_type = require_direct_statistics_layout(
                py,
                statistics_type,
                "encoded direct statistics type has an incompatible native allocation layout",
            )?;
            require_canonical_factory(
                statistics_factory,
                statistics_type.as_any(),
                "encoded direct statistics factory is not canonical",
            )?;
            let mut stream = py.detach(|| {
                if let Some(composite_columns) = dynamic_composite_columns.as_ref() {
                    let (max_work, max_workspace_bytes) =
                        self.canonical_merge_limits.ok_or_else(|| {
                            PyRuntimeError::new_err(
                                "encoded dynamic composite lost its canonical limits",
                            )
                        })?;
                    if let Some(retained) = retained_role_state.as_ref() {
                        retained.prepare_dynamic_batches_uncommitted_claimed(
                            &composite_columns.closure,
                            composite_columns.root.as_deref(),
                            options,
                            &self.state,
                            max_work,
                            max_workspace_bytes,
                        )
                    } else {
                        prepare_dynamic_composite_batches_with_root_uncommitted(
                            &composite_columns.closure,
                            composite_columns.root.as_deref(),
                            options,
                            &self.state,
                            None,
                            max_work,
                            max_workspace_bytes,
                        )
                        .map_err(kernel_error)
                    }
                } else if let Some(delta_columns) = overlay_delta_columns {
                    let (max_work, max_workspace_bytes) =
                        self.canonical_merge_limits.ok_or_else(|| {
                            PyRuntimeError::new_err(
                                "encoded local-overlay compiler lost its canonical limits",
                            )
                        })?;
                    if self._merge_manifest_view.is_some() {
                        let member_count = 2
                            + usize::from(third_member_columns.is_some())
                            + usize::from(fourth_member_columns.is_some());
                        let composite_columns = [
                            columns,
                            delta_columns,
                            third_member_columns.unwrap_or(columns),
                            fourth_member_columns.unwrap_or(columns),
                        ];
                        if let Some(retained) = retained_role_state.as_ref() {
                            retained.prepare_dynamic_batches_uncommitted_claimed(
                                &composite_columns[..member_count],
                                None,
                                options,
                                &self.state,
                                max_work,
                                max_workspace_bytes,
                            )
                        } else {
                            prepare_dynamic_composite_batches_uncommitted(
                                &composite_columns[..member_count],
                                options,
                                &self.state,
                                None,
                                max_work,
                                max_workspace_bytes,
                            )
                            .map_err(kernel_error)
                        }
                    } else {
                        if let Some(retained) = retained_role_state.as_ref() {
                            retained.prepare_overlay_batches_uncommitted_claimed(
                                columns,
                                delta_columns,
                                options,
                                &self.state,
                                max_work,
                                max_workspace_bytes,
                            )
                        } else {
                            prepare_single_overlay_delta_batches_uncommitted(
                                columns,
                                delta_columns,
                                options,
                                &self.state,
                                None,
                                max_work,
                                max_workspace_bytes,
                            )
                            .map_err(kernel_error)
                        }
                    }
                } else if let Some(retained) = retained_role_state.as_ref() {
                    retained.prepare_batches_uncommitted_claimed(
                        columns,
                        root_annotation_columns,
                        options,
                        &self.state,
                    )
                } else {
                    prepare_direct_batches_uncommitted(
                        columns,
                        root_annotation_columns,
                        options,
                        &self.state,
                        None,
                    )
                    .map_err(kernel_error)
                }
            })?;
            let statistics = stream.statistics();
            let output = PyList::empty(py);
            let mut output_chunks = 0_usize;
            let mut peak_buffered_edges = 0_usize;
            while stream.remaining_edges() != 0 {
                let (edges, cursor) = py
                    .detach(|| {
                        if let Some(composite) = dynamic_composite_columns.as_ref() {
                            stream.prepare_next_composite_batch(
                                &composite.closure,
                                &self.state,
                                COARSE_OUTPUT_CHUNK_EDGES,
                            )
                        } else {
                            stream.prepare_next_batch(
                                columns,
                                &self.state,
                                COARSE_OUTPUT_CHUNK_EDGES,
                            )
                        }
                    })
                    .map_err(kernel_error)?;
                let amount = edges.len();
                let mut values: Vec<Py<PyAny>> = Vec::new();
                values.try_reserve_exact(amount).map_err(|_| {
                    PyMemoryError::new_err("encoded coarse chunk allocation failed")
                })?;
                for edge in &edges {
                    let value = allocate_exact_edge(py, &edge_type, edge)?;
                    require_exact_edge_result(
                        value.bind(py),
                        edge_type.as_any(),
                        edge.source.as_str(),
                        edge.relation.as_str(),
                        edge.destination.as_str(),
                        "encoded direct edge allocation returned an invalid final object",
                    )?;
                    if let Some(probe) = edge_allocation_probe {
                        probe.call1((value.bind(py),))?;
                    }
                    values.push(value);
                }
                require_exact_edge_batch_results(
                    py,
                    &values,
                    edge_type.as_any(),
                    &edges,
                    "encoded direct edge allocation returned an invalid final object",
                )?;
                require_direct_edge_layout(
                    py,
                    edge_type.as_any(),
                    "encoded direct edge type changed during native allocation",
                )?;
                for value in values {
                    output.append(value)?;
                }
                // The Python list is still local. Commit cursor movement only
                // after the complete native chunk was appended successfully.
                stream.commit_cursor(cursor);
                output_chunks = output_chunks.checked_add(1).ok_or_else(|| {
                    PyMemoryError::new_err("encoded coarse chunk counter overflow")
                })?;
                peak_buffered_edges = peak_buffered_edges.max(amount);
            }
            require_canonical_factory(
                edge_factory,
                edge_type.as_any(),
                "encoded direct edge factory is not canonical",
            )?;
            let final_statistics = allocate_exact_statistics(py, &statistics_type, statistics)?;
            require_exact_statistics_result(
                final_statistics.bind(py),
                statistics_type.as_any(),
                statistics,
                "encoded direct statistics allocation returned an invalid final object",
            )?;
            if let Some(probe) = statistics_allocation_probe {
                probe.call1((final_statistics.bind(py),))?;
            }
            require_canonical_factory(
                statistics_factory,
                statistics_type.as_any(),
                "encoded direct statistics factory is not canonical",
            )?;
            require_exact_statistics_result(
                final_statistics.bind(py),
                statistics_type.as_any(),
                statistics,
                "encoded direct statistics allocation returned an invalid final object",
            )?;
            require_direct_statistics_layout(
                py,
                statistics_type.as_any(),
                "encoded direct statistics type changed during native allocation",
            )?;
            let statistics = final_statistics;
            let next_role_state =
                if retained_role_state.is_some() && !options.asserted_taxonomy_only {
                    Some(stream.try_clone_role_state().map_err(kernel_error)?)
                } else {
                    None
                };
            Ok((
                output.unbind(),
                statistics,
                next_role_state,
                output_chunks,
                peak_buffered_edges,
            ))
        });
        let (output, statistics, next_role_state, output_chunks, peak_buffered_edges) = match result
        {
            Ok(result) => result,
            Err(error) => return self.finish_result(Err(error)),
        };

        // All fallible output construction and retained-state cloning is now
        // complete. Holding the role mutex across the state transition makes
        // the retained commit indivisible from a successful coarse call.
        if let Some(next_role_state) = next_role_state {
            let Some(retained) = retained_role_state.as_ref() else {
                return self.finish_result(Err(PyRuntimeError::new_err(
                    "encoded coarse role-state transaction lost its owner",
                )));
            };
            let mut roles = match retained.roles.lock() {
                Ok(roles) => roles,
                Err(_) => {
                    return self.finish_result(Err(PyRuntimeError::new_err(
                        "encoded direct role state is permanently failed",
                    )));
                }
            };
            self.finish_result(Ok(()))?;
            *roles = next_role_state;
        } else {
            self.finish_result(Ok(()))?;
        }
        self.coarse_output_chunks
            .store(output_chunks, Ordering::Release);
        self.coarse_peak_buffered_edges
            .store(peak_buffered_edges, Ordering::Release);
        drop(retained_role_use);
        Ok((output, statistics))
    }

    /// Compile atomically, then retain a resumable cursor for bounded drains.
    #[pyo3(signature = (
        bidirectional,
        max_edges,
        max_iri_bytes,
        batch_edges,
        compiler_owner,
        statistics_factory,
        statistics_type,
        statistics_allocation_probe,
        iterator_factory,
        iterator_type,
        iterator_allocation_probe,
        asserted_taxonomy_only=false,
        only_taxonomy=false,
        include_literals=false,
        role_state=None,
    ))]
    #[allow(clippy::too_many_arguments)] // The private PyO3 ABI keeps options explicit.
    fn compile_batches(
        &self,
        py: Python<'_>,
        bidirectional: bool,
        max_edges: usize,
        max_iri_bytes: usize,
        batch_edges: usize,
        compiler_owner: &Bound<'_, PyAny>,
        statistics_factory: &Bound<'_, PyAny>,
        statistics_type: &Bound<'_, PyAny>,
        statistics_allocation_probe: Option<&Bound<'_, PyAny>>,
        iterator_factory: &Bound<'_, PyAny>,
        iterator_type: &Bound<'_, PyAny>,
        iterator_allocation_probe: Option<&Bound<'_, PyAny>>,
        asserted_taxonomy_only: bool,
        only_taxonomy: bool,
        include_literals: bool,
        role_state: Option<PyRef<'_, EncodedDirectRoleState>>,
    ) -> PyResult<Py<PyAny>> {
        if max_edges == 0 {
            return Err(PyValueError::new_err("max_edges must be positive"));
        }
        if max_iri_bytes == 0 {
            return Err(PyValueError::new_err("max_iri_bytes must be positive"));
        }
        if batch_edges == 0 {
            return Err(PyValueError::new_err("batch_edges must be positive"));
        }
        let options = DirectCompileOptions {
            bidirectional,
            asserted_taxonomy_only,
            only_taxonomy,
            include_literals,
            max_edges,
            max_iri_bytes,
        };
        let retained_role_state = role_state.map(|value| Arc::clone(&value.retained));
        let (stream, stats, retained_role_use) =
            self.prepare_batches_owned(py, options, retained_role_state.clone())?;
        let result = guarded(|| {
            let statistics_type = require_direct_statistics_layout(
                py,
                statistics_type,
                "encoded direct statistics type has an incompatible native allocation layout",
            )?;
            require_canonical_factory(
                statistics_factory,
                statistics_type.as_any(),
                "encoded direct statistics factory is not canonical",
            )?;
            let iterator_type = require_direct_iterator_layout(
                py,
                iterator_type,
                "encoded direct iterator type has an incompatible native allocation layout",
            )?;
            require_canonical_factory(
                iterator_factory,
                iterator_type.as_any(),
                "encoded direct iterator factory is not canonical",
            )?;
            let statistics = allocate_exact_statistics(py, &statistics_type, stats)?;
            require_exact_statistics_result(
                statistics.bind(py),
                statistics_type.as_any(),
                stats,
                "encoded direct statistics allocation returned an invalid final object",
            )?;
            if let Some(probe) = statistics_allocation_probe {
                probe.call1((statistics.bind(py),))?;
            }
            let iterator = allocate_exact_iterator(
                py,
                &iterator_type,
                compiler_owner,
                statistics.bind(py),
                batch_edges,
            )?;
            require_exact_iterator_result(
                iterator.bind(py),
                iterator_type.as_any(),
                compiler_owner,
                statistics.bind(py),
                batch_edges,
                "encoded direct iterator allocation returned an invalid final object",
            )?;
            if let Some(probe) = iterator_allocation_probe {
                probe.call1((iterator.bind(py),))?;
            }
            require_canonical_factory(
                iterator_factory,
                iterator_type.as_any(),
                "encoded direct iterator factory is not canonical",
            )?;
            require_exact_iterator_result(
                iterator.bind(py),
                iterator_type.as_any(),
                compiler_owner,
                statistics.bind(py),
                batch_edges,
                "encoded direct iterator allocation returned an invalid final object",
            )?;
            require_direct_iterator_layout(
                py,
                iterator_type.as_any(),
                "encoded direct iterator type changed during native allocation",
            )?;
            // The private allocation probes receive final objects and may
            // execute arbitrary Python. Revalidate the statistics after the
            // last probe and before any session or role publication.
            require_canonical_factory(
                statistics_factory,
                statistics_type.as_any(),
                "encoded direct statistics factory is not canonical",
            )?;
            require_exact_statistics_result(
                statistics.bind(py),
                statistics_type.as_any(),
                stats,
                "encoded direct statistics allocation returned an invalid final object",
            )?;
            require_direct_statistics_layout(
                py,
                statistics_type.as_any(),
                "encoded direct statistics type changed during native allocation",
            )?;
            let next_role_state =
                if retained_role_state.is_some() && !options.asserted_taxonomy_only {
                    Some(stream.try_clone_role_state().map_err(kernel_error)?)
                } else {
                    None
                };
            Ok((iterator, next_role_state))
        });
        let (iterator, next_role_state) = match result {
            Ok(result) => result,
            Err(error) => return self.finish_result(Err(error)),
        };
        let mut output = match self.batch_output.lock() {
            Ok(output) => output,
            Err(_) => {
                return self.finish_result(Err(PyRuntimeError::new_err(
                    "encoded direct batch output is permanently failed",
                )))
            }
        };
        if let Some(next_role_state) = next_role_state {
            let Some(retained) = retained_role_state.as_ref() else {
                return self.finish_result(Err(PyRuntimeError::new_err(
                    "encoded batch role-state transaction lost its owner",
                )));
            };
            let mut roles = match retained.roles.lock() {
                Ok(roles) => roles,
                Err(_) => {
                    return self.finish_result(Err(PyRuntimeError::new_err(
                        "encoded direct role state is permanently failed",
                    )))
                }
            };
            self.finish_result(Ok(()))?;
            output.install(stream, batch_edges);
            *roles = next_role_state;
        } else {
            self.finish_result(Ok(()))?;
            output.install(stream, batch_edges);
        }
        drop(retained_role_use);
        Ok(iterator)
    }

    /// Return one final Edge tuple; cursor movement commits afterwards.
    fn next_batch(
        &self,
        py: Python<'_>,
        edge_factory: &Bound<'_, PyAny>,
        edge_type: &Bound<'_, PyAny>,
        edge_allocation_probe: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<Py<PyTuple>> {
        guarded(|| {
            let edge_type = require_direct_edge_layout(
                py,
                edge_type,
                "encoded direct edge type has an incompatible native allocation layout",
            )?;
            require_canonical_factory(
                edge_factory,
                edge_type.as_any(),
                "encoded direct edge factory is not canonical",
            )?;
            let (mut stream, batch_edges, next_boundary_calls, next_edge_batches) = {
                let mut output = self.batch_output.lock().map_err(|_| {
                    PyRuntimeError::new_err("encoded direct batch output is permanently failed")
                })?;
                if !output.prepared {
                    return Err(PyValueError::new_err(
                        "encoded direct batch output has not been prepared",
                    ));
                }
                if output.cancelled {
                    return Err(EncodedDirectCancelledError::new_err(
                        "encoded direct batch output was cancelled",
                    ));
                }
                if output.exhausted {
                    return Ok(PyTuple::empty(py).unbind());
                }
                if output.draining {
                    return Err(PyValueError::new_err(
                        "encoded direct batch output is already draining",
                    ));
                }
                let next_boundary_calls =
                    output.boundary_calls.checked_add(1).ok_or_else(|| {
                        PyMemoryError::new_err("encoded direct boundary-call counter overflow")
                    })?;
                let next_edge_batches = output.edge_batches.checked_add(1).ok_or_else(|| {
                    PyMemoryError::new_err("encoded direct edge-batch counter overflow")
                })?;
                let stream = output.stream.take().ok_or_else(|| {
                    PyRuntimeError::new_err("encoded direct batch cursor is unavailable")
                })?;
                output.draining = true;
                (
                    stream,
                    output.batch_edges,
                    next_boundary_calls,
                    next_edge_batches,
                )
            };
            let slices: [&[u8]; BUFFER_COUNT] =
                std::array::from_fn(|index| self.buffers[index].as_slice());
            let columns = self.retained_base_columns(DirectColumns::from_ordered(slices))?;
            let dynamic_composite_columns = self.dynamic_composite_columns(columns)?;
            let prepared = py.detach(|| {
                if let Some(composite) = dynamic_composite_columns.as_ref() {
                    stream.prepare_next_composite_batch(
                        &composite.closure,
                        &self.state,
                        batch_edges,
                    )
                } else {
                    stream.prepare_next_batch(columns, &self.state, batch_edges)
                }
            });
            let prepared = prepared.map_err(kernel_error);
            let (edges, next_cursor) = match prepared {
                Ok(prepared) => prepared,
                Err(error) => {
                    let mut output = self.batch_output.lock().map_err(|_| {
                        PyRuntimeError::new_err("encoded direct batch output is permanently failed")
                    })?;
                    output.draining = false;
                    if output.cancelled {
                        return Err(EncodedDirectCancelledError::new_err(
                            "encoded direct batch output was cancelled",
                        ));
                    }
                    output.stream = Some(stream);
                    return Err(error);
                }
            };
            let amount = edges.len();
            let batch = (|| {
                let mut values: Vec<Py<PyAny>> = Vec::new();
                values.try_reserve_exact(amount).map_err(|_| {
                    PyMemoryError::new_err("encoded direct drain allocation failed")
                })?;
                for edge in &edges {
                    let value = allocate_exact_edge(py, &edge_type, edge)?;
                    require_exact_edge_result(
                        value.bind(py),
                        edge_type.as_any(),
                        edge.source.as_str(),
                        edge.relation.as_str(),
                        edge.destination.as_str(),
                        "encoded direct edge allocation returned an invalid final object",
                    )?;
                    if let Some(probe) = edge_allocation_probe {
                        probe.call1((value.bind(py),))?;
                    }
                    values.push(value);
                }
                require_canonical_factory(
                    edge_factory,
                    edge_type.as_any(),
                    "encoded direct edge factory is not canonical",
                )?;
                // A private test probe or allocation-triggered Python finalizer
                // may retain and mutate an earlier exact Edge. Validate the
                // complete batch as one transaction and require one final
                // object per edge.
                require_exact_edge_batch_results(
                    py,
                    &values,
                    edge_type.as_any(),
                    &edges,
                    "encoded direct edge allocation returned an invalid final object",
                )?;
                require_direct_edge_layout(
                    py,
                    edge_type.as_any(),
                    "encoded direct edge type changed during native allocation",
                )?;
                PyTuple::new(py, values).map(|values| values.unbind())
            })();
            let mut output = self.batch_output.lock().map_err(|_| {
                PyRuntimeError::new_err("encoded direct batch output is permanently failed")
            })?;
            output.draining = false;
            if output.cancelled {
                return Err(EncodedDirectCancelledError::new_err(
                    "encoded direct batch output was cancelled",
                ));
            }
            // Python allocation remains part of the cursor transaction. On
            // failure, restore the unchanged stream so the caller may retry.
            let batch = match batch {
                Ok(batch) => batch,
                Err(error) => {
                    output.stream = Some(stream);
                    return Err(error);
                }
            };
            stream.commit_cursor(next_cursor);
            let remaining_edges = stream.remaining_edges();
            let exhausted = remaining_edges == 0;
            output.boundary_calls = next_boundary_calls;
            output.edge_batches = next_edge_batches;
            output.peak_buffered_edges = output.peak_buffered_edges.max(amount);
            output.remaining_edges = remaining_edges;
            if exhausted {
                output.exhausted = true;
            } else {
                output.stream = Some(stream);
            }
            Ok(batch)
        })
    }

    /// Drop every not-yet-published edge.  Closing is idempotent.
    fn close_batches(&self) -> PyResult<bool> {
        self.cancel_batch_output()
    }

    #[getter]
    fn batch_state(&self) -> PyResult<&'static str> {
        self.batch_output
            .lock()
            .map(|output| output.state())
            .map_err(|_| {
                PyRuntimeError::new_err("encoded direct batch output is permanently failed")
            })
    }

    #[getter]
    fn remaining_batch_edges(&self) -> PyResult<usize> {
        self.batch_output
            .lock()
            .map(|output| output.remaining_edges())
            .map_err(|_| {
                PyRuntimeError::new_err("encoded direct batch output is permanently failed")
            })
    }

    #[getter]
    fn batch_boundary_calls(&self) -> PyResult<usize> {
        self.batch_output
            .lock()
            .map(|output| output.boundary_calls)
            .map_err(|_| {
                PyRuntimeError::new_err("encoded direct batch output is permanently failed")
            })
    }

    #[getter]
    fn emitted_edge_batches(&self) -> PyResult<usize> {
        self.batch_output
            .lock()
            .map(|output| output.edge_batches)
            .map_err(|_| {
                PyRuntimeError::new_err("encoded direct batch output is permanently failed")
            })
    }

    #[getter]
    fn peak_buffered_batch_edges(&self) -> PyResult<usize> {
        self.batch_output
            .lock()
            .map(|output| output.peak_buffered_edges)
            .map_err(|_| {
                PyRuntimeError::new_err("encoded direct batch output is permanently failed")
            })
    }

    #[getter]
    fn batch_intermediate_list_edges(&self) -> usize {
        0
    }

    #[getter]
    fn coarse_chunk_edges(&self) -> usize {
        COARSE_OUTPUT_CHUNK_EDGES
    }

    #[getter]
    fn coarse_output_chunks(&self) -> usize {
        self.coarse_output_chunks.load(Ordering::Acquire)
    }

    #[getter]
    fn coarse_output_vector_edges(&self) -> usize {
        0
    }

    #[getter]
    fn coarse_intermediate_list_edges(&self) -> usize {
        0
    }

    #[getter]
    fn peak_buffered_coarse_edges(&self) -> usize {
        self.coarse_peak_buffered_edges.load(Ordering::Acquire)
    }

    /// Cancel idle or detached work.  A racing successful result is discarded.
    fn cancel(&self) -> bool {
        loop {
            let state = self.state.load(Ordering::Acquire);
            match state {
                STATE_IDLE | STATE_RUNNING => {
                    if self
                        .state
                        .compare_exchange(
                            state,
                            STATE_CANCELLED,
                            Ordering::AcqRel,
                            Ordering::Acquire,
                        )
                        .is_ok()
                    {
                        return true;
                    }
                }
                _ => return false,
            }
        }
    }

    #[getter]
    fn state(&self) -> &'static str {
        match self.state.load(Ordering::Acquire) {
            STATE_IDLE => "idle",
            STATE_RUNNING => "running",
            STATE_FINISHED => "finished",
            STATE_CANCELLED => "cancelled",
            STATE_FAILED => "failed",
            _ => "invalid",
        }
    }

    #[getter]
    fn retained_buffer_count(&self) -> usize {
        let composite_buffers = self.composite_members.as_ref().map_or(0, |members| {
            members
                .iter()
                .map(|member| {
                    member.buffers.as_ref().map_or(0, Vec::len)
                        + usize::from(member.included_root_ids.is_some())
                        + usize::from(member.excluded_root_ids.is_some())
                        + member.anonymous_scope_maps.buffers.len()
                        + member.root.as_ref().map_or(0, |root| {
                            root.buffers.len()
                                + usize::from(root.included_root_ids.is_some())
                                + usize::from(root.excluded_root_ids.is_some())
                                + root.anonymous_scope_maps.buffers.len()
                        })
                })
                .sum()
        });
        self.buffers.len()
            + self.overlay_delta_buffers.as_ref().map_or(0, Vec::len)
            + self.third_member_buffers.as_ref().map_or(0, Vec::len)
            + self.fourth_member_buffers.as_ref().map_or(0, Vec::len)
            + composite_buffers
            + self.root_annotation_buffers.as_ref().map_or(0, Vec::len)
            + usize::from(self.included_root_ids.is_some())
            + usize::from(self.excluded_root_ids.is_some())
            + usize::from(self.right_excluded_root_ids.is_some())
            + usize::from(self.third_excluded_root_ids.is_some())
            + usize::from(self.fourth_excluded_root_ids.is_some())
            + usize::from(self.anonymous_scope_map.is_some())
            + usize::from(self.right_anonymous_scope_map.is_some())
    }

    /// Deterministic test hook proving another Python thread can cancel while
    /// this object's native work runs with the GIL released.
    fn test_wait_for_cancel(&self, py: Python<'_>, max_yields: usize) -> PyResult<()> {
        if max_yields == 0 {
            return Err(PyValueError::new_err("max_yields must be positive"));
        }
        self.begin()?;
        let result = guarded(|| {
            py.detach(|| {
                for _ in 0..max_yields {
                    if self.state.load(Ordering::Acquire) == STATE_CANCELLED {
                        return Err(EncodedDirectCancelledError::new_err(
                            "encoded direct compiler was cancelled",
                        ));
                    }
                    std::thread::yield_now();
                }
                Err(PyRuntimeError::new_err(
                    "test wait expired before cancellation",
                ))
            })
        });
        self.finish_result(result)
    }
}

const DIRECT_EDGE_FIELDS: [&str; 3] = ["source", "relation", "destination"];
const DIRECT_ITERATOR_FIELDS: [&str; 8] = [
    "_compiler",
    "statistics",
    "batch_edges",
    "_yielded_edges",
    "_boundary_calls",
    "_edge_batches",
    "_peak_buffered_edges",
    "_terminal_state",
];

fn require_direct_edge_layout<'py>(
    py: Python<'py>,
    expected_type: &Bound<'py, PyAny>,
    message: &'static str,
) -> PyResult<Bound<'py, PyType>> {
    require_object_slots_layout(py, expected_type, &DIRECT_EDGE_FIELDS, message)
}

fn require_direct_statistics_layout<'py>(
    py: Python<'py>,
    expected_type: &Bound<'py, PyAny>,
    message: &'static str,
) -> PyResult<Bound<'py, PyType>> {
    require_object_slots_layout(py, expected_type, &DIRECT_STATISTICS_FIELDS, message)
}

fn require_direct_iterator_layout<'py>(
    py: Python<'py>,
    expected_type: &Bound<'py, PyAny>,
    message: &'static str,
) -> PyResult<Bound<'py, PyType>> {
    let iterator_base = py
        .import("collections.abc")
        .and_then(|module| module.getattr("Iterator"))
        .map_err(|_| PyValueError::new_err(message))?;
    require_direct_slots_layout(
        py,
        expected_type,
        &iterator_base,
        &DIRECT_ITERATOR_FIELDS,
        message,
    )
}

fn require_object_slots_layout<'py, const N: usize>(
    py: Python<'py>,
    expected_type: &Bound<'py, PyAny>,
    expected_fields: &[&str; N],
    message: &'static str,
) -> PyResult<Bound<'py, PyType>> {
    let object_type = py
        .import("builtins")
        .and_then(|module| module.getattr("object"))
        .map_err(|_| PyValueError::new_err(message))?;
    require_direct_slots_layout(py, expected_type, &object_type, expected_fields, message)
}

fn require_direct_slots_layout<'py, const N: usize>(
    py: Python<'py>,
    expected_type: &Bound<'py, PyAny>,
    expected_base: &Bound<'py, PyAny>,
    expected_fields: &[&str; N],
    message: &'static str,
) -> PyResult<Bound<'py, PyType>> {
    let direct_type = expected_type
        .cast::<PyType>()
        .map_err(|_| PyValueError::new_err(message))?
        .to_owned();
    let object_type = py
        .import("builtins")
        .and_then(|module| module.getattr("object"))
        .map_err(|_| PyValueError::new_err(message))?;
    let base = direct_type
        .getattr("__base__")
        .map_err(|_| PyValueError::new_err(message))?;
    let direct_new = direct_type
        .getattr("__new__")
        .map_err(|_| PyValueError::new_err(message))?;
    let object_new = object_type
        .getattr("__new__")
        .map_err(|_| PyValueError::new_err(message))?;
    let base_new = expected_base
        .getattr("__new__")
        .map_err(|_| PyValueError::new_err(message))?;
    if !base.is(expected_base) || !base_new.is(&object_new) || !direct_new.is(&object_new) {
        return Err(PyValueError::new_err(message));
    }
    for name in [
        "__basicsize__",
        "__itemsize__",
        "__dictoffset__",
        "__weakrefoffset__",
    ] {
        let expected = object_type
            .getattr(name)
            .and_then(|value| value.extract::<isize>())
            .map_err(|_| PyValueError::new_err(message))?;
        let observed = expected_base
            .getattr(name)
            .and_then(|value| value.extract::<isize>())
            .map_err(|_| PyValueError::new_err(message))?;
        if observed != expected {
            return Err(PyValueError::new_err(message));
        }
    }

    let slots = direct_type
        .getattr("__slots__")
        .map_err(|_| PyValueError::new_err(message))?;
    let slots = slots
        .cast_into::<PyTuple>()
        .map_err(|_| PyValueError::new_err(message))?;
    if slots.len() != expected_fields.len() {
        return Err(PyValueError::new_err(message));
    }
    let member_descriptor_type = py
        .import("types")
        .and_then(|module| module.getattr("MemberDescriptorType"))
        .map_err(|_| PyValueError::new_err(message))?;
    for (index, expected) in expected_fields.iter().enumerate() {
        let observed = slots
            .get_item(index)
            .and_then(|value| value.extract::<String>())
            .map_err(|_| PyValueError::new_err(message))?;
        let descriptor = direct_type
            .getattr(*expected)
            .map_err(|_| PyValueError::new_err(message))?;
        if observed != *expected || !descriptor.is_exact_instance(&member_descriptor_type) {
            return Err(PyValueError::new_err(message));
        }
    }
    for name in ["__dictoffset__", "__weakrefoffset__"] {
        let offset = direct_type
            .getattr(name)
            .and_then(|value| value.extract::<isize>())
            .map_err(|_| PyValueError::new_err(message))?;
        if offset != 0 {
            return Err(PyValueError::new_err(message));
        }
    }
    Ok(direct_type)
}

fn allocate_exact_slotted_instance<'py>(
    py: Python<'py>,
    direct_type: &Bound<'py, PyType>,
) -> PyResult<Bound<'py, PyAny>> {
    // SAFETY: `direct_type` was checked above to be a live Python type whose
    // exact supported base has object-sized, zero-offset instance layout and
    // whose `__new__` is `object.__new__`. `PyType_GenericAlloc` is its
    // stable-ABI generic allocator and returns one owned reference or sets a
    // Python exception.
    unsafe {
        Bound::<PyAny>::from_owned_ptr_or_err(
            py,
            ffi::PyType_GenericAlloc(direct_type.as_type_ptr(), 0),
        )
    }
}

fn set_exact_slot(
    value: &Bound<'_, PyAny>,
    name: &Bound<'_, PyString>,
    field: &Bound<'_, PyAny>,
) -> PyResult<()> {
    // SAFETY: all three pointers are live Python objects under the same held
    // GIL. Layout preflight proved `name` resolves to a canonical member
    // descriptor; GenericSetAttr performs descriptor assignment and reference
    // counting without invoking the frozen dataclass's `__setattr__`.
    let result =
        unsafe { ffi::PyObject_GenericSetAttr(value.as_ptr(), name.as_ptr(), field.as_ptr()) };
    if result == 0 {
        Ok(())
    } else {
        Err(PyErr::fetch(value.py()))
    }
}

fn allocate_exact_edge(
    py: Python<'_>,
    edge_type: &Bound<'_, PyType>,
    edge: &DirectEdge,
) -> PyResult<Py<PyAny>> {
    let value = allocate_exact_slotted_instance(py, edge_type)?;
    for (name, field) in [
        (pyo3::intern!(py, "source"), edge.source.as_str()),
        (pyo3::intern!(py, "relation"), edge.relation.as_str()),
        (pyo3::intern!(py, "destination"), edge.destination.as_str()),
    ] {
        let field = PyString::new(py, field);
        set_exact_slot(&value, name, field.as_any())?;
    }
    Ok(value.unbind())
}

fn require_canonical_factory<'py>(
    factory: &Bound<'py, PyAny>,
    expected_type: &Bound<'py, PyAny>,
    message: &'static str,
) -> PyResult<()> {
    if factory.is(expected_type) {
        Ok(())
    } else {
        Err(PyValueError::new_err(message))
    }
}

fn require_exact_factory_result<'py>(
    value: &Bound<'py, PyAny>,
    expected_type: &Bound<'py, PyAny>,
    message: &'static str,
) -> PyResult<()> {
    if value.is_exact_instance(expected_type) {
        Ok(())
    } else {
        Err(PyValueError::new_err(message))
    }
}

fn require_exact_edge_result<'py>(
    value: &Bound<'py, PyAny>,
    expected_type: &Bound<'py, PyAny>,
    source: &str,
    relation: &str,
    destination: &str,
    message: &'static str,
) -> PyResult<()> {
    require_exact_factory_result(value, expected_type, message)?;
    for (name, expected) in [
        ("source", source),
        ("relation", relation),
        ("destination", destination),
    ] {
        let observed = value
            .getattr(name)
            .map_err(|_| PyValueError::new_err(message))?;
        if !observed.is_exact_instance_of::<PyString>() {
            return Err(PyValueError::new_err(message));
        }
        let observed = observed
            .cast::<PyString>()
            .map_err(|_| PyValueError::new_err(message))?
            .to_str()
            .map_err(|_| PyValueError::new_err(message))?;
        if observed != expected {
            return Err(PyValueError::new_err(message));
        }
    }
    Ok(())
}

fn require_exact_edge_batch_results(
    py: Python<'_>,
    values: &[Py<PyAny>],
    expected_type: &Bound<'_, PyAny>,
    expected: &[DirectEdge],
    message: &'static str,
) -> PyResult<()> {
    if values.len() != expected.len() {
        return Err(PyValueError::new_err(message));
    }
    let mut identities = HashSet::new();
    identities.try_reserve(values.len()).map_err(|_| {
        PyMemoryError::new_err("encoded direct edge identity validation allocation failed")
    })?;
    for (value, edge) in values.iter().zip(expected) {
        let value = value.bind(py);
        if !identities.insert(value.as_ptr()) {
            return Err(PyValueError::new_err(message));
        }
        require_exact_edge_result(
            value,
            expected_type,
            edge.source.as_str(),
            edge.relation.as_str(),
            edge.destination.as_str(),
            message,
        )?;
    }
    Ok(())
}

fn require_exact_statistics_result<'py>(
    value: &Bound<'py, PyAny>,
    expected_type: &Bound<'py, PyAny>,
    expected: DirectCompileStats,
    message: &'static str,
) -> PyResult<()> {
    require_exact_factory_result(value, expected_type, message)?;
    for (name, expected) in DIRECT_STATISTICS_FIELDS
        .into_iter()
        .zip(direct_statistics_values(expected))
    {
        let observed = value
            .getattr(name)
            .map_err(|_| PyValueError::new_err(message))?;
        if !observed.is_exact_instance_of::<PyInt>()
            || observed.extract::<usize>().ok() != Some(expected)
        {
            return Err(PyValueError::new_err(message));
        }
    }
    Ok(())
}

fn require_exact_iterator_result<'py>(
    value: &Bound<'py, PyAny>,
    expected_type: &Bound<'py, PyAny>,
    compiler_owner: &Bound<'py, PyAny>,
    statistics: &Bound<'py, PyAny>,
    batch_edges: usize,
    message: &'static str,
) -> PyResult<()> {
    require_exact_factory_result(value, expected_type, message)?;
    let observed_owner = value
        .getattr("_compiler")
        .map_err(|_| PyValueError::new_err(message))?;
    let observed_statistics = value
        .getattr("statistics")
        .map_err(|_| PyValueError::new_err(message))?;
    if !observed_owner.is(compiler_owner) || !observed_statistics.is(statistics) {
        return Err(PyValueError::new_err(message));
    }
    for (name, expected) in [
        ("batch_edges", batch_edges),
        ("_yielded_edges", 0),
        ("_boundary_calls", 1),
        ("_edge_batches", 0),
        ("_peak_buffered_edges", 0),
    ] {
        let observed = value
            .getattr(name)
            .map_err(|_| PyValueError::new_err(message))?;
        if !observed.is_exact_instance_of::<PyInt>()
            || observed.extract::<usize>().ok() != Some(expected)
        {
            return Err(PyValueError::new_err(message));
        }
    }
    let terminal_state = value
        .getattr("_terminal_state")
        .map_err(|_| PyValueError::new_err(message))?;
    if !terminal_state.is_exact_instance_of::<PyString>() {
        return Err(PyValueError::new_err(message));
    }
    let terminal_state = terminal_state
        .cast::<PyString>()
        .map_err(|_| PyValueError::new_err(message))?
        .to_str()
        .map_err(|_| PyValueError::new_err(message))?;
    if terminal_state != "active" {
        return Err(PyValueError::new_err(message));
    }
    Ok(())
}

const DIRECT_STATISTICS_FIELDS: [&str; 60] = [
    "roots",
    "nodes",
    "anonymous_individuals",
    "ontology_annotations",
    "swrl_rules",
    "declarations",
    "subclasses",
    "restriction_subclasses",
    "ignored_subclasses",
    "equivalents",
    "aggregate_equivalents",
    "equivalent_base_edges",
    "ignored_equivalents",
    "disjoint_classes",
    "disjoint_unions",
    "has_keys",
    "same_individuals",
    "different_individuals",
    "class_assertions",
    "ignored_class_assertions",
    "object_property_assertions",
    "negative_object_property_assertions",
    "sub_object_properties",
    "object_property_chains",
    "equivalent_object_properties",
    "disjoint_object_properties",
    "inverse_object_properties",
    "functional_object_properties",
    "inverse_functional_object_properties",
    "reflexive_object_properties",
    "irreflexive_object_properties",
    "symmetric_object_properties",
    "asymmetric_object_properties",
    "transitive_object_properties",
    "sub_data_properties",
    "equivalent_data_properties",
    "disjoint_data_properties",
    "data_property_domains",
    "data_property_ranges",
    "functional_data_properties",
    "datatype_definitions",
    "data_property_assertions",
    "negative_data_property_assertions",
    "annotation_assertions",
    "selected_annotation_assertions",
    "sub_annotation_properties",
    "annotation_property_domains",
    "annotation_property_ranges",
    "annotation_edges",
    "non_string_literal_renderings",
    "skipped_axioms",
    "object_property_domains",
    "object_property_ranges",
    "ignored_object_property_domains",
    "ignored_object_property_ranges",
    "domain_range_edges",
    "role_expansion_edges",
    "edges",
    "buffer_bytes",
    "root_provenance_buffer_bytes",
];

fn allocate_exact_statistics(
    py: Python<'_>,
    statistics_type: &Bound<'_, PyType>,
    stats: DirectCompileStats,
) -> PyResult<Py<PyAny>> {
    let value = allocate_exact_slotted_instance(py, statistics_type)?;
    for (name, field) in DIRECT_STATISTICS_FIELDS
        .into_iter()
        .zip(direct_statistics_values(stats))
    {
        let name = PyString::intern(py, name);
        let field = field.into_bound_py_any(py)?;
        set_exact_slot(&value, &name, &field)?;
    }
    Ok(value.unbind())
}

fn allocate_exact_iterator(
    py: Python<'_>,
    iterator_type: &Bound<'_, PyType>,
    compiler_owner: &Bound<'_, PyAny>,
    statistics: &Bound<'_, PyAny>,
    batch_edges: usize,
) -> PyResult<Py<PyAny>> {
    let value = allocate_exact_slotted_instance(py, iterator_type)?;
    for (name, field) in [("_compiler", compiler_owner), ("statistics", statistics)] {
        let name = PyString::intern(py, name);
        set_exact_slot(&value, &name, field)?;
    }
    for (name, field) in [
        ("batch_edges", batch_edges),
        ("_yielded_edges", 0),
        ("_boundary_calls", 1),
        ("_edge_batches", 0),
        ("_peak_buffered_edges", 0),
    ] {
        let name = PyString::intern(py, name);
        let field = field.into_bound_py_any(py)?;
        set_exact_slot(&value, &name, &field)?;
    }
    let name = PyString::intern(py, "_terminal_state");
    let field = PyString::new(py, "active");
    set_exact_slot(&value, &name, field.as_any())?;
    Ok(value.unbind())
}

fn direct_statistics_values(stats: DirectCompileStats) -> [usize; 60] {
    [
        stats.roots,
        stats.nodes,
        stats.anonymous_individuals,
        stats.ontology_annotations,
        stats.swrl_rules,
        stats.declarations,
        stats.subclasses,
        stats.restriction_subclasses,
        stats.ignored_subclasses,
        stats.equivalents,
        stats.aggregate_equivalents,
        stats.equivalent_base_edges,
        stats.ignored_equivalents,
        stats.disjoint_classes,
        stats.disjoint_unions,
        stats.has_keys,
        stats.same_individuals,
        stats.different_individuals,
        stats.class_assertions,
        stats.ignored_class_assertions,
        stats.object_property_assertions,
        stats.negative_object_property_assertions,
        stats.sub_object_properties,
        stats.object_property_chains,
        stats.equivalent_object_properties,
        stats.disjoint_object_properties,
        stats.inverse_object_properties,
        stats.functional_object_properties,
        stats.inverse_functional_object_properties,
        stats.reflexive_object_properties,
        stats.irreflexive_object_properties,
        stats.symmetric_object_properties,
        stats.asymmetric_object_properties,
        stats.transitive_object_properties,
        stats.sub_data_properties,
        stats.equivalent_data_properties,
        stats.disjoint_data_properties,
        stats.data_property_domains,
        stats.data_property_ranges,
        stats.functional_data_properties,
        stats.datatype_definitions,
        stats.data_property_assertions,
        stats.negative_data_property_assertions,
        stats.annotation_assertions,
        stats.selected_annotation_assertions,
        stats.sub_annotation_properties,
        stats.annotation_property_domains,
        stats.annotation_property_ranges,
        stats.annotation_edges,
        stats.non_string_literal_renderings,
        stats.skipped_axioms,
        stats.object_property_domains,
        stats.object_property_ranges,
        stats.ignored_object_property_domains,
        stats.ignored_object_property_ranges,
        stats.domain_range_edges,
        stats.role_expansion_edges,
        stats.edges,
        stats.buffer_bytes,
        stats.root_provenance_buffer_bytes,
    ]
}

fn retained_direct_buffers(
    encoded_view: &Bound<'_, PyAny>,
    expected_owner: &Bound<'_, PyAny>,
    descriptor_sha256: &Bound<'_, PyAny>,
) -> PyResult<Vec<RetainedDirectBuffer>> {
    validate_encoded_view_header(encoded_view, expected_owner, descriptor_sha256)?;
    validate_direct_segment(encoded_view, expected_owner)?;
    retained_structural_buffers(encoded_view)
}

fn retained_overlay_delta_buffers(
    encoded_view: &Bound<'_, PyAny>,
    expected_owner: &Bound<'_, PyAny>,
    descriptor_sha256: &Bound<'_, PyAny>,
    source_view: &Bound<'_, PyAny>,
    source_owner: &Bound<'_, PyAny>,
    excluded_root_ids: Option<&Bound<'_, PyAny>>,
) -> PyResult<Vec<RetainedDirectBuffer>> {
    validate_encoded_view_header(encoded_view, expected_owner, descriptor_sha256)?;
    validate_overlay_delta_segments(
        encoded_view,
        expected_owner,
        source_view,
        source_owner,
        excluded_root_ids,
    )?;
    retained_structural_buffers(encoded_view)
}

#[allow(clippy::too_many_arguments)] // Every retained composite identity is explicit.
fn validate_two_member_composite_manifest(
    encoded_view: &Bound<'_, PyAny>,
    expected_owner: &Bound<'_, PyAny>,
    descriptor_sha256: &Bound<'_, PyAny>,
    left_view: &Bound<'_, PyAny>,
    left_owner: &Bound<'_, PyAny>,
    right_view: &Bound<'_, PyAny>,
    right_owner: &Bound<'_, PyAny>,
    included_root_ids: Option<&Bound<'_, PyAny>>,
    excluded_root_ids: Option<&Bound<'_, PyAny>>,
    right_excluded_root_ids: Option<&Bound<'_, PyAny>>,
    anonymous_scope_map: Option<&Bound<'_, PyAny>>,
    right_anonymous_scope_map: Option<&Bound<'_, PyAny>>,
) -> PyResult<()> {
    validate_encoded_view_header(encoded_view, expected_owner, descriptor_sha256)?;
    if left_view.is(right_view) || left_owner.is(right_owner) {
        return Err(EncodedDirectUnsupportedError::new_err(
            "bounded two-member composite requires distinct direct members",
        ));
    }

    let local_buffers = retained_structural_buffers(encoded_view)?;
    for (name, buffer) in BUFFER_NAMES.into_iter().zip(&local_buffers) {
        let bytes = buffer.as_slice();
        if name == "node_field_offsets" {
            if bytes != [0_u8; 8] {
                return Err(EncodedDirectUnsupportedError::new_err(
                    "bounded two-member composite requires empty local columns",
                ));
            }
        } else if !bytes.is_empty() {
            return Err(EncodedDirectUnsupportedError::new_err(
                "bounded two-member composite does not support bridge roots",
            ));
        }
    }

    let raw_segments = required_attribute(encoded_view, "segments")?;
    if !raw_segments.is_exact_instance_of::<PyTuple>() {
        return Err(encoded_buffer_error(
            "encoded composite segment manifest must be an exact tuple",
        ));
    }
    let segments = raw_segments
        .cast::<PyTuple>()
        .map_err(|_| encoded_buffer_error("encoded composite manifest is inaccessible"))?;
    if segments.len() != 2 {
        return Err(EncodedDirectUnsupportedError::new_err(
            "bounded two-member composite requires exactly two member segments",
        ));
    }

    let mut matched_left = false;
    let mut matched_right = false;
    let mut previous_token: Option<[u8; 32]> = None;
    for index in 0..2 {
        let segment = segments
            .get_item(index)
            .map_err(|_| encoded_buffer_error("encoded composite member is inaccessible"))?;
        if exact_nonnegative_integer(&required_attribute(&segment, "role")?, "segment role")?
            != COMPOSITE_MEMBER_SEGMENT
        {
            return Err(EncodedDirectUnsupportedError::new_err(
                "bounded two-member composite requires member-only segments",
            ));
        }
        let source = required_attribute(&segment, "source")?;
        let (member_owner, expected_include, expected_exclude, expected_scope_map) =
            if source.is(left_view) {
                if matched_left {
                    return Err(encoded_buffer_error(
                        "encoded composite repeats its left source identity",
                    ));
                }
                matched_left = true;
                (
                    left_owner,
                    included_root_ids,
                    excluded_root_ids,
                    anonymous_scope_map,
                )
            } else if source.is(right_view) {
                if matched_right {
                    return Err(encoded_buffer_error(
                        "encoded composite repeats its right source identity",
                    ));
                }
                matched_right = true;
                (
                    right_owner,
                    None,
                    right_excluded_root_ids,
                    right_anonymous_scope_map,
                )
            } else {
                return Err(encoded_buffer_error(
                    "encoded composite member lost its retained source identity",
                ));
            };
        if !required_attribute(&segment, "owner")?.is(member_owner) {
            return Err(encoded_buffer_error(
                "encoded composite member lost its retained owner identity",
            ));
        }

        let posting_mode = exact_nonnegative_integer(
            &required_attribute(&segment, "posting_mode")?,
            "segment posting_mode",
        )?;
        let root_ids = required_attribute(&segment, "root_ids")?;
        let root_id_bytes = checked_memoryview_length(&root_ids, "root_ids")?;
        match (expected_include, expected_exclude) {
            (None, None) => {
                if posting_mode != POSTINGS_ALL || root_id_bytes != 0 {
                    return Err(EncodedDirectUnsupportedError::new_err(
                        "bounded two-member composite requires ALL selection on every unposted member",
                    ));
                }
            }
            (Some(expected), None) => {
                if posting_mode != POSTINGS_INCLUDE || root_id_bytes == 0 {
                    return Err(EncodedDirectUnsupportedError::new_err(
                        "bounded two-member composite requires one nonempty INCLUDE table",
                    ));
                }
                if !root_ids.is(expected) {
                    return Err(encoded_buffer_error(
                        "encoded composite member lost its exact INCLUDE table",
                    ));
                }
            }
            (None, Some(expected)) => {
                if posting_mode != POSTINGS_EXCLUDE || root_id_bytes == 0 {
                    return Err(EncodedDirectUnsupportedError::new_err(
                        "bounded two-member composite requires one nonempty EXCLUDE table",
                    ));
                }
                if !root_ids.is(expected) {
                    return Err(encoded_buffer_error(
                        "encoded composite member lost its exact EXCLUDE table",
                    ));
                }
            }
            (Some(_), Some(_)) => {
                return Err(encoded_buffer_error(
                    "encoded composite member has ambiguous duplicate selectors",
                ));
            }
        }
        let scope_map = required_attribute(&segment, "anonymous_scope_map")?;
        let scope_map_bytes = checked_memoryview_length(&scope_map, "anonymous_scope_map")?;
        match expected_scope_map {
            None if scope_map_bytes == 0 => {}
            Some(expected) if scope_map_bytes != 0 && scope_map.is(expected) => {}
            None => {
                return Err(EncodedDirectUnsupportedError::new_err(
                    "bounded two-member composite received an unexpected anonymous scope map",
                ));
            }
            Some(_) if scope_map_bytes == 0 => {
                return Err(encoded_buffer_error(
                    "encoded composite member lost its anonymous scope map",
                ));
            }
            Some(_) => {
                return Err(encoded_buffer_error(
                    "encoded composite member lost its exact anonymous scope map",
                ));
            }
        }

        let token = required_attribute(&segment, "member_token")?;
        if !token.is_exact_instance_of::<PyBytes>() {
            return Err(encoded_buffer_error(
                "encoded composite member token must be exact immutable bytes",
            ));
        }
        let token = token
            .cast::<PyBytes>()
            .map_err(|_| encoded_buffer_error("encoded composite member token is inaccessible"))?
            .as_bytes();
        let token: [u8; 32] = token.try_into().map_err(|_| {
            encoded_buffer_error("encoded composite member token must contain exactly 32 bytes")
        })?;
        if previous_token.is_some_and(|previous| previous >= token) {
            return Err(encoded_buffer_error(
                "encoded composite member tokens are not sorted unique",
            ));
        }
        previous_token = Some(token);
    }
    if !matched_left || !matched_right {
        return Err(encoded_buffer_error(
            "encoded composite did not retain both merge tables",
        ));
    }
    Ok(())
}

struct DynamicCompositeMemberInput<'py> {
    view: Bound<'py, PyAny>,
    owner: Bound<'py, PyAny>,
    descriptor_sha256: Bound<'py, PyAny>,
    included_root_ids: Option<Bound<'py, PyAny>>,
    excluded_root_ids: Option<Bound<'py, PyAny>>,
    anonymous_scope_map: Option<Bound<'py, PyAny>>,
    anonymous_scope_map_chain: Option<Bound<'py, PyTuple>>,
    path: Option<Bound<'py, PyAny>>,
}

fn optional_composite_row_item<'py>(
    row: &Bound<'py, PyTuple>,
    index: usize,
    name: &'static str,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    let value = row
        .get_item(index)
        .map_err(|_| encoded_buffer_error(format!("encoded composite {name} is inaccessible")))?;
    Ok((!value.is_none()).then_some(value))
}

fn parse_dynamic_composite_members<'py>(
    value: &Bound<'py, PyAny>,
) -> PyResult<Vec<DynamicCompositeMemberInput<'py>>> {
    if !value.is_exact_instance_of::<PyTuple>() {
        return Err(encoded_buffer_error(
            "encoded dynamic composite members must be an exact tuple",
        ));
    }
    let rows = value
        .cast::<PyTuple>()
        .map_err(|_| encoded_buffer_error("encoded dynamic composite members are inaccessible"))?;
    if rows.len() < 2 {
        return Err(EncodedDirectUnsupportedError::new_err(
            "encoded dynamic composite requires at least two members",
        ));
    }
    let mut members = Vec::new();
    members.try_reserve_exact(rows.len()).map_err(|_| {
        PyMemoryError::new_err("encoded dynamic composite member allocation failed")
    })?;
    for index in 0..rows.len() {
        let value = rows
            .get_item(index)
            .map_err(|_| encoded_buffer_error("encoded dynamic composite row is inaccessible"))?;
        if !value.is_exact_instance_of::<PyTuple>() {
            return Err(encoded_buffer_error(
                "encoded dynamic composite rows must be exact tuples",
            ));
        }
        let row = value
            .cast::<PyTuple>()
            .map_err(|_| encoded_buffer_error("encoded dynamic composite row is inaccessible"))?;
        if !matches!(row.len(), 6 | 7) {
            return Err(encoded_buffer_error(
                "encoded dynamic composite rows require six fields or one recursive path",
            ));
        }
        let recursive = row.len() == 7;
        let (anonymous_scope_map, anonymous_scope_map_chain) = if recursive {
            let value = row.get_item(5).map_err(|_| {
                encoded_buffer_error("encoded recursive anonymous scope-map chain is inaccessible")
            })?;
            if !value.is_exact_instance_of::<PyTuple>() {
                return Err(encoded_buffer_error(
                    "encoded recursive anonymous scope-map chain must be an exact tuple",
                ));
            }
            (
                None,
                Some(
                    value
                        .cast::<PyTuple>()
                        .map_err(|_| {
                            encoded_buffer_error(
                                "encoded recursive anonymous scope-map chain is inaccessible",
                            )
                        })?
                        .clone(),
                ),
            )
        } else {
            (
                optional_composite_row_item(row, 5, "member anonymous scope map")?,
                None,
            )
        };
        members.push(DynamicCompositeMemberInput {
            view: row.get_item(0).map_err(|_| {
                encoded_buffer_error("encoded composite member view is inaccessible")
            })?,
            owner: row.get_item(1).map_err(|_| {
                encoded_buffer_error("encoded composite member owner is inaccessible")
            })?,
            descriptor_sha256: row.get_item(2).map_err(|_| {
                encoded_buffer_error("encoded composite member digest is inaccessible")
            })?,
            included_root_ids: optional_composite_row_item(row, 3, "member INCLUDE selection")?,
            excluded_root_ids: optional_composite_row_item(row, 4, "member EXCLUDE selection")?,
            anonymous_scope_map,
            anonymous_scope_map_chain,
            path: if recursive {
                Some(row.get_item(6).map_err(|_| {
                    encoded_buffer_error("encoded recursive leaf path is inaccessible")
                })?)
            } else {
                None
            },
        });
    }
    Ok(members)
}

fn retain_dynamic_scope_maps(
    input: &DynamicCompositeMemberInput<'_>,
    label: &'static str,
) -> PyResult<RetainedAnonymousScopeMaps> {
    let mut buffers = Vec::new();
    if let Some(chain) = input.anonymous_scope_map_chain.as_ref() {
        buffers.try_reserve_exact(chain.len()).map_err(|_| {
            PyMemoryError::new_err("encoded recursive scope-map retention allocation failed")
        })?;
        for index in 0..chain.len() {
            let value = chain.get_item(index).map_err(|_| {
                encoded_buffer_error("encoded recursive scope-map chain is inaccessible")
            })?;
            let buffer = retained_exact_bytes_buffer(&value, label)?;
            if buffer.as_slice().is_empty() || buffer.as_slice().len() % 64 != 0 {
                return Err(encoded_buffer_error(
                    "encoded recursive scope-map chain contains an invalid map",
                ));
            }
            buffers.push(buffer);
        }
    } else if let Some(value) = input.anonymous_scope_map.as_ref() {
        buffers.push(retained_exact_bytes_buffer(value, label)?);
    }
    Ok(RetainedAnonymousScopeMaps { buffers })
}

#[derive(Clone, Copy, Eq, Hash, PartialEq)]
enum RecursiveLeafSelection {
    All,
    LocalOnly,
}

#[derive(Clone)]
struct RecursiveTopologyChild<'py> {
    segment_index: usize,
    view: Bound<'py, PyAny>,
    owner: Bound<'py, PyAny>,
    selection: RecursiveLeafSelection,
}

#[derive(Clone)]
struct RecursiveValidatedTopologyNode<'py> {
    owner: Bound<'py, PyAny>,
    overlay_increment: usize,
    local_leaf_index: Option<usize>,
    children: Vec<RecursiveTopologyChild<'py>>,
}

#[derive(Clone)]
struct RecursiveResolvedTopology {
    paths: Vec<Vec<usize>>,
    overlay_span: usize,
}

struct RecursiveTopologyFrame<'py> {
    identity: usize,
    selection: RecursiveLeafSelection,
    node: RecursiveValidatedTopologyNode<'py>,
    next_child: usize,
    pending_child_index: Option<usize>,
    paths: Vec<Vec<usize>>,
    overlay_span: usize,
    absolute_overlay_depth: usize,
}

struct RecursiveReference<'py> {
    source: Bound<'py, PyAny>,
    owner: Bound<'py, PyAny>,
    posting_mode: usize,
    root_ids: Bound<'py, PyAny>,
    scope_map: Bound<'py, PyAny>,
}

fn recursive_segments<'py>(view: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyTuple>> {
    let raw = required_attribute(view, "segments")?;
    if !raw.is_exact_instance_of::<PyTuple>() {
        return Err(encoded_buffer_error(
            "encoded recursive leaf segment manifest must be an exact tuple",
        ));
    }
    raw.cast::<PyTuple>()
        .cloned()
        .map_err(|_| encoded_buffer_error("encoded recursive leaf manifest is inaccessible"))
}

fn recursive_segment_role(segment: &Bound<'_, PyAny>) -> PyResult<usize> {
    exact_nonnegative_integer(&required_attribute(segment, "role")?, "segment role")
}

fn recursive_source_root_count(source: &Bound<'_, PyAny>) -> PyResult<usize> {
    let buffers = required_attribute(source, "buffers")?;
    let mapping = buffers
        .cast::<PyMapping>()
        .map_err(|_| encoded_buffer_error("encoded recursive source buffers must be a mapping"))?;
    let root_kinds = mapping
        .get_item("root_kinds")
        .map_err(|_| encoded_buffer_error("encoded recursive source has no root_kinds buffer"))?;
    checked_memoryview_length(&root_kinds, "root_kinds")
}

fn validate_recursive_posting_rows(
    root_ids: &Bound<'_, PyAny>,
    source: &Bound<'_, PyAny>,
) -> PyResult<()> {
    let rows = retained_exact_bytes_buffer(root_ids, "recursive root_ids")?;
    let source_root_count = recursive_source_root_count(source)?;
    let mut previous = 0_usize;
    for row in rows.as_slice().chunks_exact(4) {
        let position =
            usize::try_from(u32::from_le_bytes(row.try_into().map_err(|_| {
                encoded_buffer_error("encoded recursive posting row is truncated")
            })?))
            .map_err(|_| {
                encoded_buffer_error("encoded recursive posting row does not fit usize")
            })?;
        if position == 0 || position <= previous || position > source_root_count {
            return Err(encoded_buffer_error(
                "encoded recursive postings are not sorted unique in-range local positions",
            ));
        }
        previous = position;
    }
    Ok(())
}

fn validate_recursive_scope_map_rows(scope_map: &Bound<'_, PyAny>) -> PyResult<()> {
    let rows = retained_exact_bytes_buffer(scope_map, "recursive anonymous_scope_map")?;
    let mut previous_source: Option<&[u8]> = None;
    for row in rows.as_slice().chunks_exact(64) {
        let (source, target) = row.split_at(32);
        if source == target {
            return Err(encoded_buffer_error(
                "encoded recursive scope map contains an identity row",
            ));
        }
        if previous_source.is_some_and(|previous| previous >= source) {
            return Err(encoded_buffer_error(
                "encoded recursive scope-map sources are not sorted unique",
            ));
        }
        previous_source = Some(source);
    }
    Ok(())
}

fn validate_recursive_reference<'py>(
    current_view: &Bound<'py, PyAny>,
    segment: &Bound<'py, PyAny>,
    role: usize,
    validate_reference_rows: bool,
) -> PyResult<RecursiveReference<'py>> {
    if !matches!(role, OVERLAY_BASE_SEGMENT | COMPOSITE_MEMBER_SEGMENT) {
        return Err(encoded_buffer_error(
            "encoded recursive leaf path contains a non-reference segment",
        ));
    }
    let source = required_attribute(segment, "source")?;
    if source.is_none() || source.is(current_view) {
        return Err(encoded_buffer_error(
            "encoded recursive leaf reference is absent or cyclic",
        ));
    }
    let owner = required_attribute(segment, "owner")?;
    if !required_attribute(&source, "owner")?.is(&owner) {
        return Err(encoded_buffer_error(
            "encoded recursive leaf reference lost its exact source owner",
        ));
    }
    let current_scope = required_attribute(current_view, "scope")?;
    if !required_attribute(&source, "scope")?.is(&current_scope) {
        return Err(encoded_buffer_error(
            "encoded recursive leaf reference changed its exact source scope",
        ));
    }
    let posting_mode = exact_nonnegative_integer(
        &required_attribute(segment, "posting_mode")?,
        "segment posting_mode",
    )?;
    if role == OVERLAY_BASE_SEGMENT && !matches!(posting_mode, POSTINGS_ALL | POSTINGS_EXCLUDE) {
        return Err(EncodedDirectUnsupportedError::new_err(
            "encoded recursive overlay base supports only ALL or EXCLUDE selection",
        ));
    }
    if role == COMPOSITE_MEMBER_SEGMENT
        && !matches!(
            posting_mode,
            POSTINGS_ALL | POSTINGS_INCLUDE | POSTINGS_EXCLUDE
        )
    {
        return Err(encoded_buffer_error(
            "encoded recursive composite member posting mode is invalid",
        ));
    }
    let root_ids = required_attribute(segment, "root_ids")?;
    let root_id_bytes = checked_memoryview_length(&root_ids, "root_ids")?;
    if root_id_bytes % 4 != 0 {
        return Err(encoded_buffer_error(
            "encoded recursive leaf postings contain a partial u32 row",
        ));
    }
    if (posting_mode == POSTINGS_ALL) != (root_id_bytes == 0) {
        return Err(encoded_buffer_error(
            "encoded recursive leaf posting mode and table disagree",
        ));
    }
    if validate_reference_rows {
        validate_recursive_posting_rows(&root_ids, &source)?;
    }
    let scope_map = required_attribute(segment, "anonymous_scope_map")?;
    if checked_memoryview_length(&scope_map, "anonymous_scope_map")? % 64 != 0 {
        return Err(encoded_buffer_error(
            "encoded recursive leaf scope map contains a partial row",
        ));
    }
    if validate_reference_rows {
        validate_recursive_scope_map_rows(&scope_map)?;
    }
    let token = required_attribute(segment, "member_token")?;
    if role == COMPOSITE_MEMBER_SEGMENT {
        if !token.is_exact_instance_of::<PyBytes>()
            || token
                .cast::<PyBytes>()
                .map_err(|_| {
                    encoded_buffer_error("encoded recursive composite member token is inaccessible")
                })?
                .as_bytes()
                .len()
                != 32
        {
            return Err(encoded_buffer_error(
                "encoded recursive composite member token must be exact bytes32",
            ));
        }
    } else if !token.is_none() {
        return Err(encoded_buffer_error(
            "encoded recursive overlay base unexpectedly has a member token",
        ));
    }
    Ok(RecursiveReference {
        source,
        owner,
        posting_mode,
        root_ids,
        scope_map,
    })
}

fn validate_recursive_empty_local_buffers(
    buffers: &[RetainedDirectBuffer],
    label: &str,
) -> PyResult<()> {
    for (name, buffer) in BUFFER_NAMES.into_iter().zip(buffers) {
        let bytes = buffer.as_slice();
        if name == "node_field_offsets" {
            if bytes != [0_u8; 8] {
                return Err(EncodedDirectUnsupportedError::new_err(format!(
                    "encoded recursive {label} requires empty local columns",
                )));
            }
        } else if !bytes.is_empty() {
            return Err(EncodedDirectUnsupportedError::new_err(format!(
                "encoded recursive {label} contains roots without a local leaf segment",
            )));
        }
    }
    Ok(())
}

fn recursive_manifest_validation_work(
    buffers: &[RetainedDirectBuffer],
    segments: &Bound<'_, PyTuple>,
) -> PyResult<usize> {
    let mut work = buffers.iter().try_fold(0_usize, |total, buffer| {
        total
            .checked_add(buffer.as_slice().len())
            .ok_or_else(|| PyMemoryError::new_err("encoded recursive validation-work overflow"))
    })?;
    for index in 0..segments.len() {
        let segment = segments
            .get_item(index)
            .map_err(|_| encoded_buffer_error("encoded recursive segment is inaccessible"))?;
        let root_ids = required_attribute(&segment, "root_ids")?;
        let scope_map = required_attribute(&segment, "anonymous_scope_map")?;
        let root_id_bytes = checked_memoryview_length(&root_ids, "root_ids")?;
        let scope_map_bytes = checked_memoryview_length(&scope_map, "anonymous_scope_map")?;
        let segment_work = 128_usize
            .checked_add(root_id_bytes)
            .and_then(|value| value.checked_add(scope_map_bytes))
            .ok_or_else(|| {
                PyMemoryError::new_err("encoded recursive segment-work counter overflow")
            })?;
        work = work.checked_add(segment_work).ok_or_else(|| {
            PyMemoryError::new_err("encoded recursive validation-work counter overflow")
        })?;
    }
    Ok(work)
}

fn direct_composite_validation_work(
    manifest: &Bound<'_, PyAny>,
    inputs: &[DynamicCompositeMemberInput<'_>],
    validated: &mut HashSet<usize>,
) -> PyResult<usize> {
    let mut work = 0_usize;
    for view in std::iter::once(manifest).chain(inputs.iter().map(|input| &input.view)) {
        if !validated.insert(view.as_ptr() as usize) {
            continue;
        }
        let buffers = retained_structural_buffers(view)?;
        let segments = recursive_segments(view)?;
        work = work
            .checked_add(recursive_manifest_validation_work(&buffers, &segments)?)
            .ok_or_else(|| {
                PyMemoryError::new_err("encoded direct validation-work counter overflow")
            })?;
    }
    Ok(work)
}

fn validate_recursive_topology_node<'py>(
    view: &Bound<'py, PyAny>,
    owner: &Bound<'py, PyAny>,
    buffers: &[RetainedDirectBuffer],
    segments: &Bound<'py, PyTuple>,
) -> PyResult<RecursiveValidatedTopologyNode<'py>> {
    let local_root_count = buffers[0].as_slice().len();
    let mut roles = Vec::new();
    roles
        .try_reserve_exact(segments.len())
        .map_err(|_| PyMemoryError::new_err("encoded recursive role allocation failed"))?;
    for index in 0..segments.len() {
        roles.push(recursive_segment_role(&segments.get_item(index).map_err(
            |_| encoded_buffer_error("encoded recursive segment is inaccessible"),
        )?)?);
    }

    if roles == [DIRECT_SEGMENT] {
        validate_direct_segment(view, owner)?;
        return Ok(RecursiveValidatedTopologyNode {
            owner: owner.clone(),
            overlay_increment: 0,
            local_leaf_index: (local_root_count != 0).then_some(0),
            children: Vec::new(),
        });
    }

    if matches!(
        roles.as_slice(),
        [OVERLAY_BASE_SEGMENT] | [OVERLAY_BASE_SEGMENT, OVERLAY_DELTA_SEGMENT]
    ) {
        let base = segments
            .get_item(0)
            .map_err(|_| encoded_buffer_error("encoded recursive overlay base is inaccessible"))?;
        let reference = validate_recursive_reference(view, &base, OVERLAY_BASE_SEGMENT, true)?;
        let mut children = Vec::new();
        children
            .try_reserve_exact(1)
            .map_err(|_| PyMemoryError::new_err("encoded recursive child allocation failed"))?;
        children.push(RecursiveTopologyChild {
            segment_index: 0,
            view: reference.source,
            owner: reference.owner,
            selection: RecursiveLeafSelection::All,
        });
        let local_leaf_index = if roles.len() == 2 {
            let delta = segments.get_item(1).map_err(|_| {
                encoded_buffer_error("encoded recursive overlay delta is inaccessible")
            })?;
            validate_all_segment(
                &delta,
                OVERLAY_DELTA_SEGMENT,
                owner,
                None,
                "recursive overlay delta",
            )?;
            if local_root_count == 0 {
                return Err(encoded_buffer_error(
                    "encoded recursive overlay delta has no local roots",
                ));
            }
            Some(1)
        } else {
            validate_recursive_empty_local_buffers(buffers, "overlay alias")?;
            None
        };
        return Ok(RecursiveValidatedTopologyNode {
            owner: owner.clone(),
            overlay_increment: 1,
            local_leaf_index,
            children,
        });
    }

    let member_count = roles
        .iter()
        .take_while(|role| **role == COMPOSITE_MEMBER_SEGMENT)
        .count();
    let bridge_count = usize::from(
        roles.len() == member_count + 1 && roles.last() == Some(&COMPOSITE_BRIDGE_SEGMENT),
    );
    if member_count < 2 || member_count + bridge_count != roles.len() {
        return Err(EncodedDirectUnsupportedError::new_err(
            "encoded recursive leaf manifest has an unsupported segment family",
        ));
    }
    let mut children = Vec::new();
    children
        .try_reserve_exact(member_count)
        .map_err(|_| PyMemoryError::new_err("encoded recursive child allocation failed"))?;
    let mut previous_token: Option<[u8; 32]> = None;
    for index in 0..member_count {
        let member = segments.get_item(index).map_err(|_| {
            encoded_buffer_error("encoded recursive composite member is inaccessible")
        })?;
        let reference =
            validate_recursive_reference(view, &member, COMPOSITE_MEMBER_SEGMENT, true)?;
        let token = required_attribute(&member, "member_token")?;
        let token: [u8; 32] = token
            .cast::<PyBytes>()
            .map_err(|_| {
                encoded_buffer_error("encoded recursive composite member token is inaccessible")
            })?
            .as_bytes()
            .try_into()
            .map_err(|_| {
                encoded_buffer_error(
                    "encoded recursive composite member token must be exact bytes32",
                )
            })?;
        if previous_token.is_some_and(|previous| previous >= token) {
            return Err(encoded_buffer_error(
                "encoded recursive composite member tokens are not sorted unique",
            ));
        }
        previous_token = Some(token);
        children.push(RecursiveTopologyChild {
            segment_index: index,
            view: reference.source,
            owner: reference.owner,
            selection: if reference.posting_mode == POSTINGS_INCLUDE {
                RecursiveLeafSelection::LocalOnly
            } else {
                RecursiveLeafSelection::All
            },
        });
    }
    let local_leaf_index = if bridge_count == 1 {
        let bridge = segments.get_item(member_count).map_err(|_| {
            encoded_buffer_error("encoded recursive composite bridge is inaccessible")
        })?;
        validate_all_segment(
            &bridge,
            COMPOSITE_BRIDGE_SEGMENT,
            owner,
            None,
            "recursive composite bridge",
        )?;
        if local_root_count == 0 {
            return Err(encoded_buffer_error(
                "encoded recursive composite bridge has no local roots",
            ));
        }
        Some(member_count)
    } else {
        validate_recursive_empty_local_buffers(buffers, "composite")?;
        None
    };
    Ok(RecursiveValidatedTopologyNode {
        owner: owner.clone(),
        overlay_increment: 0,
        local_leaf_index,
        children,
    })
}

fn recursive_topology_node<'py>(
    validated: &mut HashMap<usize, RecursiveValidatedTopologyNode<'py>>,
    view: &Bound<'py, PyAny>,
    owner: &Bound<'py, PyAny>,
    descriptor_sha256: &Bound<'py, PyAny>,
    validation_work: &mut usize,
    max_work: usize,
) -> PyResult<RecursiveValidatedTopologyNode<'py>> {
    let identity = view.as_ptr() as usize;
    if let Some(node) = validated.get(&identity) {
        if !node.owner.is(owner) {
            return Err(encoded_buffer_error(
                "encoded recursive DAG source changed its exact owner",
            ));
        }
        return Ok(node.clone());
    }
    validate_encoded_view_header(view, owner, descriptor_sha256)?;
    let buffers = retained_structural_buffers(view)?;
    let segments = recursive_segments(view)?;
    if segments.is_empty() {
        return Err(encoded_buffer_error(
            "encoded recursive leaf manifest cannot be empty",
        ));
    }
    let node_work = recursive_manifest_validation_work(&buffers, &segments)?;
    *validation_work = validation_work.checked_add(node_work).ok_or_else(|| {
        PyMemoryError::new_err("encoded recursive validation-work counter overflow")
    })?;
    if *validation_work > max_work {
        return Err(PyMemoryError::new_err(
            "encoded recursive validation exceeds max_canonical_work",
        ));
    }
    let node = validate_recursive_topology_node(view, owner, &buffers, &segments)?;
    validated.insert(identity, node.clone());
    Ok(node)
}

fn recursive_topology_frame<'py>(
    identity: usize,
    selection: RecursiveLeafSelection,
    node: RecursiveValidatedTopologyNode<'py>,
    parent_overlay_depth: usize,
    max_overlay_depth: usize,
    max_leaves: usize,
) -> PyResult<RecursiveTopologyFrame<'py>> {
    let absolute_overlay_depth = parent_overlay_depth
        .checked_add(node.overlay_increment)
        .ok_or_else(|| {
            PyMemoryError::new_err("encoded recursive overlay-depth counter overflow")
        })?;
    if absolute_overlay_depth > max_overlay_depth {
        return Err(encoded_buffer_error(
            "encoded recursive leaf graph exceeds max_overlay_depth",
        ));
    }
    let mut paths = Vec::new();
    if let Some(local_leaf_index) = node.local_leaf_index {
        paths
            .try_reserve_exact(1)
            .map_err(|_| PyMemoryError::new_err("encoded recursive path allocation failed"))?;
        paths.push(vec![local_leaf_index]);
    }
    if paths.len() > max_leaves {
        return Err(EncodedDirectUnsupportedError::new_err(
            "encoded recursive leaf graph exceeds its retained-table plan",
        ));
    }
    let next_child = if selection == RecursiveLeafSelection::All {
        0
    } else {
        node.children.len()
    };
    Ok(RecursiveTopologyFrame {
        identity,
        selection,
        overlay_span: node.overlay_increment,
        node,
        next_child,
        pending_child_index: None,
        paths,
        absolute_overlay_depth,
    })
}

fn merge_recursive_topology(
    frame: &mut RecursiveTopologyFrame<'_>,
    segment_index: usize,
    child: &RecursiveResolvedTopology,
    max_overlay_depth: usize,
    max_leaves: usize,
) -> PyResult<()> {
    let deepest_overlay = frame
        .absolute_overlay_depth
        .checked_add(child.overlay_span)
        .ok_or_else(|| {
            PyMemoryError::new_err("encoded recursive overlay-depth counter overflow")
        })?;
    if deepest_overlay > max_overlay_depth {
        return Err(encoded_buffer_error(
            "encoded recursive leaf graph exceeds max_overlay_depth",
        ));
    }
    let relative_overlay_span = frame
        .node
        .overlay_increment
        .checked_add(child.overlay_span)
        .ok_or_else(|| {
            PyMemoryError::new_err("encoded recursive overlay-depth counter overflow")
        })?;
    frame.overlay_span = frame.overlay_span.max(relative_overlay_span);
    let resulting_count = frame
        .paths
        .len()
        .checked_add(child.paths.len())
        .ok_or_else(|| PyMemoryError::new_err("encoded recursive leaf-count overflow"))?;
    if resulting_count > max_leaves {
        return Err(EncodedDirectUnsupportedError::new_err(
            "encoded recursive leaf graph exceeds its retained-table plan",
        ));
    }
    frame
        .paths
        .try_reserve_exact(child.paths.len())
        .map_err(|_| PyMemoryError::new_err("encoded recursive path allocation failed"))?;
    for suffix in &child.paths {
        let mut path = Vec::new();
        path.try_reserve_exact(suffix.len().saturating_add(1))
            .map_err(|_| PyMemoryError::new_err("encoded recursive path allocation failed"))?;
        path.push(segment_index);
        path.extend_from_slice(suffix);
        frame.paths.push(path);
    }
    Ok(())
}

fn enumerate_recursive_leaf_paths<'py>(
    manifest: &Bound<'py, PyAny>,
    manifest_owner: &Bound<'py, PyAny>,
    descriptor_sha256: &Bound<'py, PyAny>,
    max_work: usize,
    max_overlay_depth: usize,
    max_leaves: usize,
) -> PyResult<(HashSet<Vec<usize>>, usize)> {
    let mut validation_work = 0_usize;
    let mut validated_nodes = HashMap::new();
    let mut resolved_topologies = HashMap::new();
    let root_identity = manifest.as_ptr() as usize;
    let root_node = recursive_topology_node(
        &mut validated_nodes,
        manifest,
        manifest_owner,
        descriptor_sha256,
        &mut validation_work,
        max_work,
    )?;
    let mut active = HashSet::new();
    active.insert(root_identity);
    let mut stack = vec![recursive_topology_frame(
        root_identity,
        RecursiveLeafSelection::All,
        root_node,
        0,
        max_overlay_depth,
        max_leaves,
    )?];
    let root_topology = loop {
        let child = {
            let frame = stack
                .last_mut()
                .ok_or_else(|| encoded_buffer_error("encoded recursive stack is empty"))?;
            if frame.next_child < frame.node.children.len() {
                let child = frame.node.children[frame.next_child].clone();
                frame.next_child += 1;
                Some(child)
            } else {
                None
            }
        };
        if let Some(child) = child {
            let child_identity = child.view.as_ptr() as usize;
            if active.contains(&child_identity) {
                return Err(encoded_buffer_error(
                    "encoded recursive leaf graph contains an active-path cycle",
                ));
            }
            let child_node = recursive_topology_node(
                &mut validated_nodes,
                &child.view,
                &child.owner,
                descriptor_sha256,
                &mut validation_work,
                max_work,
            )?;
            let cache_key = (child_identity, child.selection);
            if let Some(cached) = resolved_topologies.get(&cache_key).cloned() {
                let frame = stack
                    .last_mut()
                    .ok_or_else(|| encoded_buffer_error("encoded recursive stack is empty"))?;
                merge_recursive_topology(
                    frame,
                    child.segment_index,
                    &cached,
                    max_overlay_depth,
                    max_leaves,
                )?;
                continue;
            }
            let parent_overlay_depth = stack
                .last()
                .ok_or_else(|| encoded_buffer_error("encoded recursive stack is empty"))?
                .absolute_overlay_depth;
            stack
                .last_mut()
                .ok_or_else(|| encoded_buffer_error("encoded recursive stack is empty"))?
                .pending_child_index = Some(child.segment_index);
            active.insert(child_identity);
            stack.push(recursive_topology_frame(
                child_identity,
                child.selection,
                child_node,
                parent_overlay_depth,
                max_overlay_depth,
                max_leaves,
            )?);
            continue;
        }

        let frame = stack
            .pop()
            .ok_or_else(|| encoded_buffer_error("encoded recursive stack is empty"))?;
        active.remove(&frame.identity);
        let resolved = RecursiveResolvedTopology {
            paths: frame.paths,
            overlay_span: frame.overlay_span,
        };
        resolved_topologies.insert((frame.identity, frame.selection), resolved.clone());
        let Some(parent) = stack.last_mut() else {
            break resolved;
        };
        let segment_index = parent.pending_child_index.take().ok_or_else(|| {
            encoded_buffer_error("encoded recursive parent lost its child coordinate")
        })?;
        merge_recursive_topology(
            parent,
            segment_index,
            &resolved,
            max_overlay_depth,
            max_leaves,
        )?;
    };

    let mut expected = HashSet::new();
    expected
        .try_reserve(root_topology.paths.len())
        .map_err(|_| PyMemoryError::new_err("encoded recursive path-set allocation failed"))?;
    for path in root_topology.paths {
        if !expected.insert(path) {
            return Err(EncodedDirectUnsupportedError::new_err(
                "encoded recursive leaf graph repeats one exact leaf path",
            ));
        }
    }
    if expected.len() > max_leaves {
        return Err(EncodedDirectUnsupportedError::new_err(
            "encoded recursive leaf graph exceeds its retained-table plan",
        ));
    }
    Ok((expected, validation_work))
}

fn optional_recursive_binding_matches(
    observed: Option<&Bound<'_, PyAny>>,
    expected: Option<&Bound<'_, PyAny>>,
) -> bool {
    match (observed, expected) {
        (None, None) => true,
        (Some(observed), Some(expected)) => observed.is(expected),
        _ => false,
    }
}

fn recursive_u64_at(bytes: &[u8], index: usize) -> Option<usize> {
    let offset = index.checked_mul(8)?;
    let end = offset.checked_add(8)?;
    let row: [u8; 8] = bytes.get(offset..end)?.try_into().ok()?;
    usize::try_from(u64::from_le_bytes(row)).ok()
}

fn recursive_scope_map_applies(
    view: &Bound<'_, PyAny>,
    prior_maps: &[Bound<'_, PyAny>],
    scope_map: &Bound<'_, PyAny>,
) -> PyResult<bool> {
    let buffers = retained_structural_buffers(view)?;
    let map = retained_exact_bytes_buffer(scope_map, "recursive anonymous_scope_map")?;
    let node_tags = buffers[2].as_slice();
    let node_offsets = buffers[3].as_slice();
    let field_kinds = buffers[4].as_slice();
    let field_values = buffers[5].as_slice();
    let field_lengths = buffers[6].as_slice();
    let scalar_bytes = buffers[10].as_slice();
    for node_index in 0..node_tags.len() / 2 {
        let tag = u16::from_le_bytes([node_tags[node_index * 2], node_tags[node_index * 2 + 1]]);
        if tag != 3 {
            continue;
        }
        let Some(start) = recursive_u64_at(node_offsets, node_index) else {
            return Err(encoded_buffer_error(
                "encoded recursive node offsets are inaccessible",
            ));
        };
        let Some(end) = recursive_u64_at(node_offsets, node_index + 1) else {
            return Err(encoded_buffer_error(
                "encoded recursive node offsets are inaccessible",
            ));
        };
        if end <= start
            || field_kinds.get(start).copied() != Some(3)
            || recursive_u64_at(field_lengths, start) != Some(32)
        {
            continue;
        }
        let Some(scope_offset) = recursive_u64_at(field_values, start) else {
            return Err(encoded_buffer_error(
                "encoded recursive anonymous scope offset is inaccessible",
            ));
        };
        let Some(scope) = scalar_bytes.get(scope_offset..scope_offset.saturating_add(32)) else {
            return Err(encoded_buffer_error(
                "encoded recursive anonymous scope is out of range",
            ));
        };
        let mut effective: [u8; 32] = scope.try_into().map_err(|_| {
            encoded_buffer_error("encoded recursive anonymous scope is not bytes32")
        })?;
        for prior in prior_maps {
            let prior = retained_exact_bytes_buffer(prior, "recursive anonymous_scope_map")?;
            if let Some(row) = prior
                .as_slice()
                .chunks_exact(64)
                .find(|row| row[..32] == effective)
            {
                effective.copy_from_slice(&row[32..]);
            }
        }
        if map
            .as_slice()
            .chunks_exact(64)
            .any(|row| row[..32] == effective)
        {
            return Ok(true);
        }
    }
    Ok(false)
}

fn recursive_scope_chain_matches(
    input: &DynamicCompositeMemberInput<'_>,
    expected: &[Bound<'_, PyAny>],
) -> PyResult<bool> {
    let Some(observed) = input.anonymous_scope_map_chain.as_ref() else {
        return Ok(false);
    };
    if observed.len() != expected.len() {
        return Ok(false);
    }
    for (index, expected) in expected.iter().enumerate() {
        let observed = observed.get_item(index).map_err(|_| {
            encoded_buffer_error("encoded recursive scope-map chain is inaccessible")
        })?;
        if !observed.is(expected) {
            return Ok(false);
        }
    }
    Ok(true)
}

fn recursive_input_path(input: &DynamicCompositeMemberInput<'_>) -> PyResult<Vec<usize>> {
    let raw_path = input.path.as_ref().ok_or_else(|| {
        encoded_buffer_error("encoded recursive retained table lost its exact path")
    })?;
    if !raw_path.is_exact_instance_of::<PyTuple>() {
        return Err(encoded_buffer_error(
            "encoded recursive retained-table path must be an exact tuple",
        ));
    }
    let path = raw_path
        .cast::<PyTuple>()
        .map_err(|_| encoded_buffer_error("encoded recursive path is inaccessible"))?;
    if path.is_empty() {
        return Err(encoded_buffer_error(
            "encoded recursive retained-table path cannot be empty",
        ));
    }
    let mut result = Vec::new();
    result
        .try_reserve_exact(path.len())
        .map_err(|_| PyMemoryError::new_err("encoded recursive path allocation failed"))?;
    for index in 0..path.len() {
        let coordinate = path
            .get_item(index)
            .map_err(|_| encoded_buffer_error("encoded recursive path is inaccessible"))?;
        if !coordinate.is_exact_instance_of::<PyInt>() {
            return Err(encoded_buffer_error(
                "encoded recursive path coordinates must be exact integers",
            ));
        }
        result.push(coordinate.extract::<usize>().map_err(|_| {
            encoded_buffer_error("encoded recursive path coordinate is out of range")
        })?);
    }
    Ok(result)
}

fn validate_recursive_missing_local_leaf(
    view: &Bound<'_, PyAny>,
    owner: &Bound<'_, PyAny>,
    segment_index: usize,
    input: &DynamicCompositeMemberInput<'_>,
) -> PyResult<()> {
    if !view.is(&input.view) || !owner.is(&input.owner) {
        return Err(encoded_buffer_error(
            "encoded recursive missing ROOT leaf ended at another retained table",
        ));
    }
    let segments = recursive_segments(view)?;
    let mut roles = Vec::new();
    roles
        .try_reserve_exact(segments.len())
        .map_err(|_| PyMemoryError::new_err("encoded recursive role allocation failed"))?;
    for index in 0..segments.len() {
        roles.push(recursive_segment_role(&segments.get_item(index).map_err(
            |_| encoded_buffer_error("encoded recursive segment is inaccessible"),
        )?)?);
    }
    let missing_delta = segment_index == 1 && roles.as_slice() == [OVERLAY_BASE_SEGMENT];
    let member_count = roles
        .iter()
        .take_while(|role| **role == COMPOSITE_MEMBER_SEGMENT)
        .count();
    let missing_bridge =
        member_count >= 2 && segment_index == member_count && member_count == roles.len();
    if !missing_delta && !missing_bridge {
        return Err(encoded_buffer_error(
            "encoded recursive ROOT path lost a noncanonical local segment",
        ));
    }
    let buffers = retained_structural_buffers(view)?;
    validate_recursive_empty_local_buffers(&buffers, "paired ROOT container")
}

fn validate_recursive_member_path<'py>(
    manifest: &Bound<'py, PyAny>,
    manifest_owner: &Bound<'py, PyAny>,
    input: &DynamicCompositeMemberInput<'py>,
    expected_paths: &mut HashSet<Vec<usize>>,
    allow_missing_local_leaf: bool,
) -> PyResult<()> {
    let path_identity = recursive_input_path(input)?;
    if !expected_paths.remove(&path_identity) {
        return Err(encoded_buffer_error(
            "encoded recursive retained-table path is absent or duplicated",
        ));
    }

    let mut current_view = manifest.clone();
    let mut current_owner = manifest_owner.clone();
    let mut expected_include: Option<Bound<'py, PyAny>> = None;
    let mut expected_exclude: Option<Bound<'py, PyAny>> = None;
    let mut expected_scope_maps: Vec<Bound<'py, PyAny>> = Vec::new();
    let mut encountered_scope_maps: Vec<Bound<'py, PyAny>> = Vec::new();
    for (index, segment_index) in path_identity.iter().copied().enumerate() {
        let segments = recursive_segments(&current_view)?;
        let terminal = index + 1 == path_identity.len();
        let segment = match segments.get_item(segment_index) {
            Ok(segment) => segment,
            Err(_) if terminal && allow_missing_local_leaf => {
                validate_recursive_missing_local_leaf(
                    &current_view,
                    &current_owner,
                    segment_index,
                    input,
                )?;
                break;
            }
            Err(_) => {
                return Err(encoded_buffer_error(
                    "encoded recursive path coordinate is absent from its exact container",
                ))
            }
        };
        let role = recursive_segment_role(&segment)?;
        if terminal {
            if !current_view.is(&input.view) || !current_owner.is(&input.owner) {
                return Err(encoded_buffer_error(
                    "encoded recursive leaf path ended at another retained table",
                ));
            }
            if !matches!(
                role,
                DIRECT_SEGMENT | OVERLAY_DELTA_SEGMENT | COMPOSITE_BRIDGE_SEGMENT
            ) {
                return Err(encoded_buffer_error(
                    "encoded recursive path does not end at a local leaf segment",
                ));
            }
            validate_all_segment(
                &segment,
                role,
                &current_owner,
                None,
                "recursive retained leaf",
            )?;
            break;
        }

        let reference = validate_recursive_reference(&current_view, &segment, role, false)?;
        if reference.posting_mode == POSTINGS_INCLUDE {
            if !reference.source.is(&input.view) || index + 2 != path_identity.len() {
                return Err(encoded_buffer_error(
                    "encoded recursive INCLUDE selected a nonlocal descendant leaf",
                ));
            }
            if expected_include.is_some() || expected_exclude.is_some() {
                return Err(encoded_buffer_error(
                    "encoded recursive leaf received multiple selectors",
                ));
            }
            expected_include = Some(reference.root_ids.clone());
        } else if reference.posting_mode == POSTINGS_EXCLUDE && reference.source.is(&input.view) {
            if expected_include.is_some() || expected_exclude.is_some() {
                return Err(encoded_buffer_error(
                    "encoded recursive leaf received multiple selectors",
                ));
            }
            expected_exclude = Some(reference.root_ids.clone());
        }
        if checked_memoryview_length(&reference.scope_map, "anonymous_scope_map")? != 0 {
            encountered_scope_maps.push(reference.scope_map.clone());
        }
        current_view = reference.source;
        current_owner = reference.owner;
    }
    expected_scope_maps
        .try_reserve_exact(encountered_scope_maps.len())
        .map_err(|_| {
            PyMemoryError::new_err("encoded recursive scope-map plan allocation failed")
        })?;
    for scope_map in encountered_scope_maps.into_iter().rev() {
        if recursive_scope_map_applies(&input.view, &expected_scope_maps, &scope_map)? {
            expected_scope_maps.push(scope_map);
        }
    }

    if !optional_recursive_binding_matches(
        input.included_root_ids.as_ref(),
        expected_include.as_ref(),
    ) || !optional_recursive_binding_matches(
        input.excluded_root_ids.as_ref(),
        expected_exclude.as_ref(),
    ) || !recursive_scope_chain_matches(input, &expected_scope_maps)?
    {
        return Err(encoded_buffer_error(
            "encoded recursive leaf parameters lost exact source-local identity",
        ));
    }
    Ok(())
}

fn validate_recursive_leaf_plan<'py>(
    manifest: &Bound<'py, PyAny>,
    manifest_owner: &Bound<'py, PyAny>,
    descriptor_sha256: &Bound<'py, PyAny>,
    inputs: &[DynamicCompositeMemberInput<'py>],
    max_work: usize,
    max_overlay_depth: usize,
) -> PyResult<usize> {
    if inputs.len() < 2 {
        return Err(EncodedDirectUnsupportedError::new_err(
            "encoded recursive leaf plan requires at least two retained tables",
        ));
    }
    let (mut expected_paths, validation_work) = enumerate_recursive_leaf_paths(
        manifest,
        manifest_owner,
        descriptor_sha256,
        max_work,
        max_overlay_depth,
        inputs.len(),
    )?;
    for input in inputs {
        validate_recursive_member_path(
            manifest,
            manifest_owner,
            input,
            &mut expected_paths,
            false,
        )?;
    }
    if !expected_paths.is_empty() {
        return Err(encoded_buffer_error(
            "encoded recursive leaf plan omitted one selected local table",
        ));
    }
    Ok(validation_work)
}

fn validate_recursive_manifest_pair(
    closure_manifest: &Bound<'_, PyAny>,
    closure_manifest_owner: &Bound<'_, PyAny>,
    closure_inputs: &[DynamicCompositeMemberInput<'_>],
    root_manifest: &Bound<'_, PyAny>,
    root_manifest_owner: &Bound<'_, PyAny>,
    root_inputs: &[DynamicCompositeMemberInput<'_>],
) -> PyResult<()> {
    if !closure_manifest_owner.is(root_manifest_owner) || closure_inputs.len() != root_inputs.len()
    {
        return Err(encoded_buffer_error(
            "encoded recursive ROOT manifest lost its CLOSURE owner or row pairing",
        ));
    }
    for (closure_input, root_input) in closure_inputs.iter().zip(root_inputs) {
        let closure_path = recursive_input_path(closure_input)?;
        let root_path = recursive_input_path(root_input)?;
        if closure_path != root_path || !closure_input.owner.is(&root_input.owner) {
            return Err(encoded_buffer_error(
                "encoded recursive ROOT leaf lost its stable CLOSURE coordinate or owner",
            ));
        }
        let mut closure_view = closure_manifest.clone();
        let mut closure_owner = closure_manifest_owner.clone();
        let mut root_view = root_manifest.clone();
        let mut root_owner = root_manifest_owner.clone();
        for (depth, coordinate) in closure_path.iter().copied().enumerate() {
            let terminal = depth + 1 == closure_path.len();
            let closure_segments = recursive_segments(&closure_view)?;
            let closure_segment = closure_segments.get_item(coordinate).map_err(|_| {
                encoded_buffer_error(
                    "encoded recursive CLOSURE coordinate is absent from its container",
                )
            })?;
            let closure_role = recursive_segment_role(&closure_segment)?;
            let root_segments = recursive_segments(&root_view)?;
            let root_segment = match root_segments.get_item(coordinate) {
                Ok(segment) => segment,
                Err(_) if terminal => {
                    validate_recursive_missing_local_leaf(
                        &root_view,
                        &root_owner,
                        coordinate,
                        root_input,
                    )?;
                    break;
                }
                Err(_) => {
                    return Err(encoded_buffer_error(
                        "encoded recursive ROOT reference coordinate is absent",
                    ))
                }
            };
            let root_role = recursive_segment_role(&root_segment)?;
            if closure_role != root_role {
                return Err(encoded_buffer_error(
                    "encoded recursive ROOT coordinate changed its CLOSURE role",
                ));
            }
            if terminal {
                break;
            }
            if !matches!(
                closure_role,
                OVERLAY_BASE_SEGMENT | COMPOSITE_MEMBER_SEGMENT
            ) {
                return Err(encoded_buffer_error(
                    "encoded recursive paired path crosses a nonreference segment",
                ));
            }
            let closure_reference =
                validate_recursive_reference(&closure_view, &closure_segment, closure_role, false)?;
            let root_reference =
                validate_recursive_reference(&root_view, &root_segment, root_role, false)?;
            if !closure_reference.owner.is(&root_reference.owner) {
                return Err(encoded_buffer_error(
                    "encoded recursive ROOT reference changed its CLOSURE source owner",
                ));
            }
            if closure_role == COMPOSITE_MEMBER_SEGMENT {
                let closure_token = required_attribute(&closure_segment, "member_token")?;
                let root_token = required_attribute(&root_segment, "member_token")?;
                let closure_token = closure_token.cast::<PyBytes>().map_err(|_| {
                    encoded_buffer_error("encoded recursive CLOSURE member token is inaccessible")
                })?;
                let root_token = root_token.cast::<PyBytes>().map_err(|_| {
                    encoded_buffer_error("encoded recursive ROOT member token is inaccessible")
                })?;
                if closure_token.as_bytes() != root_token.as_bytes() {
                    return Err(encoded_buffer_error(
                        "encoded recursive ROOT member changed its CLOSURE token",
                    ));
                }
            }
            closure_view = closure_reference.source;
            closure_owner = closure_reference.owner;
            root_view = root_reference.source;
            root_owner = root_reference.owner;
        }
        if !closure_view.is(&closure_input.view)
            || !closure_owner.is(&closure_input.owner)
            || !root_view.is(&root_input.view)
            || !root_owner.is(&root_input.owner)
        {
            return Err(encoded_buffer_error(
                "encoded recursive paired path ended at another retained table",
            ));
        }
    }
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn validate_recursive_root_plan<'py>(
    closure_manifest: &Bound<'py, PyAny>,
    closure_manifest_owner: &Bound<'py, PyAny>,
    closure_inputs: &[DynamicCompositeMemberInput<'py>],
    root_manifest: &Bound<'py, PyAny>,
    root_manifest_owner: &Bound<'py, PyAny>,
    root_descriptor_sha256: &Bound<'py, PyAny>,
    root_inputs: &[DynamicCompositeMemberInput<'py>],
    max_work: usize,
    max_overlay_depth: usize,
) -> PyResult<()> {
    if closure_inputs.len() != root_inputs.len() {
        return Err(encoded_buffer_error(
            "encoded recursive ROOT plan must pair every CLOSURE leaf",
        ));
    }
    let mut paired_paths = HashSet::new();
    for (closure_input, root_input) in closure_inputs.iter().zip(root_inputs) {
        let closure_path = recursive_input_path(closure_input)?;
        if closure_path != recursive_input_path(root_input)? || !paired_paths.insert(closure_path) {
            return Err(encoded_buffer_error(
                "encoded recursive ROOT plan lost or duplicated a stable CLOSURE path",
            ));
        }
    }
    let (actual_root_paths, _root_validation_work) = enumerate_recursive_leaf_paths(
        root_manifest,
        root_manifest_owner,
        root_descriptor_sha256,
        max_work,
        max_overlay_depth,
        root_inputs.len(),
    )?;
    if !actual_root_paths.is_subset(&paired_paths) {
        return Err(encoded_buffer_error(
            "encoded recursive ROOT manifest contains an unpaired selected leaf",
        ));
    }
    let mut remaining = paired_paths;
    for input in root_inputs {
        validate_recursive_member_path(
            root_manifest,
            root_manifest_owner,
            input,
            &mut remaining,
            true,
        )?;
    }
    if !remaining.is_empty() {
        return Err(encoded_buffer_error(
            "encoded recursive ROOT plan omitted one paired CLOSURE leaf",
        ));
    }
    validate_recursive_manifest_pair(
        closure_manifest,
        closure_manifest_owner,
        closure_inputs,
        root_manifest,
        root_manifest_owner,
        root_inputs,
    )
}

type CompositeMemberBinding<'a, 'py> = (
    &'a Bound<'py, PyAny>,
    &'a Bound<'py, PyAny>,
    Option<&'a Bound<'py, PyAny>>,
    Option<&'a Bound<'py, PyAny>>,
    Option<&'a Bound<'py, PyAny>>,
);

type NestedDirectMemberBinding<'a, 'py> = (
    &'a Bound<'py, PyAny>,
    &'a Bound<'py, PyAny>,
    Option<&'a Bound<'py, PyAny>>,
);

fn validate_direct_member_composite_manifest(
    encoded_view: &Bound<'_, PyAny>,
    expected_owner: &Bound<'_, PyAny>,
    descriptor_sha256: &Bound<'_, PyAny>,
    members: &[CompositeMemberBinding<'_, '_>],
    require_manifest_order: bool,
) -> PyResult<()> {
    let member_count = members.len();
    if member_count < 2 {
        return Err(EncodedDirectUnsupportedError::new_err(
            "bounded direct-member composite requires at least two members",
        ));
    }
    validate_encoded_view_header(encoded_view, expected_owner, descriptor_sha256)?;
    for left in 0..member_count {
        for right in left + 1..member_count {
            if members[left].0.is(members[right].0) || members[left].1.is(members[right].1) {
                return Err(EncodedDirectUnsupportedError::new_err(format!(
                    "bounded {member_count}-member composite requires distinct direct members",
                )));
            }
        }
    }

    let local_buffers = retained_structural_buffers(encoded_view)?;
    for (name, buffer) in BUFFER_NAMES.into_iter().zip(&local_buffers) {
        let bytes = buffer.as_slice();
        if name == "node_field_offsets" {
            if bytes != [0_u8; 8] {
                return Err(EncodedDirectUnsupportedError::new_err(format!(
                    "bounded {member_count}-member composite requires empty local columns",
                )));
            }
        } else if !bytes.is_empty() {
            return Err(EncodedDirectUnsupportedError::new_err(format!(
                "bounded {member_count}-member composite does not support bridge roots",
            )));
        }
    }

    let raw_segments = required_attribute(encoded_view, "segments")?;
    if !raw_segments.is_exact_instance_of::<PyTuple>() {
        return Err(encoded_buffer_error(
            "encoded composite segment manifest must be an exact tuple",
        ));
    }
    let segments = raw_segments
        .cast::<PyTuple>()
        .map_err(|_| encoded_buffer_error("encoded composite manifest is inaccessible"))?;
    if segments.len() != member_count {
        return Err(EncodedDirectUnsupportedError::new_err(format!(
            "bounded {member_count}-member composite requires exactly {member_count} member segments",
        )));
    }

    let mut matched = Vec::new();
    matched
        .try_reserve_exact(member_count)
        .map_err(|_| PyMemoryError::new_err("encoded composite member-match allocation failed"))?;
    matched.resize(member_count, false);
    let mut previous_token: Option<[u8; 32]> = None;
    for index in 0..member_count {
        let segment = segments
            .get_item(index)
            .map_err(|_| encoded_buffer_error("encoded composite member is inaccessible"))?;
        if exact_nonnegative_integer(&required_attribute(&segment, "role")?, "segment role")?
            != COMPOSITE_MEMBER_SEGMENT
        {
            return Err(EncodedDirectUnsupportedError::new_err(format!(
                "bounded {member_count}-member composite requires member-only segments",
            )));
        }
        let source = required_attribute(&segment, "source")?;
        let member_index = if require_manifest_order {
            if !source.is(members[index].0) {
                return Err(encoded_buffer_error(
                    "encoded dynamic composite member lost its exact manifest order",
                ));
            }
            index
        } else {
            members
                .iter()
                .position(|(view, _, _, _, _)| source.is(*view))
                .ok_or_else(|| {
                    encoded_buffer_error(
                        "encoded composite member lost its retained source identity",
                    )
                })?
        };
        if matched[member_index] {
            return Err(encoded_buffer_error(
                "encoded composite repeats one retained source identity",
            ));
        }
        matched[member_index] = true;
        let (_, member_owner, expected_include, expected_exclude, expected_scope_map) =
            members[member_index];
        if !required_attribute(&segment, "owner")?.is(member_owner) {
            return Err(encoded_buffer_error(
                "encoded composite member lost its retained owner identity",
            ));
        }

        let posting_mode = exact_nonnegative_integer(
            &required_attribute(&segment, "posting_mode")?,
            "segment posting_mode",
        )?;
        let root_ids = required_attribute(&segment, "root_ids")?;
        let root_id_bytes = checked_memoryview_length(&root_ids, "root_ids")?;
        match (expected_include, expected_exclude) {
            (None, None) => {
                if posting_mode != POSTINGS_ALL || root_id_bytes != 0 {
                    return Err(EncodedDirectUnsupportedError::new_err(format!(
                        "bounded {member_count}-member composite requires ALL selection on every unposted member",
                    )));
                }
            }
            (Some(expected), None) => {
                if posting_mode != POSTINGS_INCLUDE || root_id_bytes == 0 {
                    return Err(EncodedDirectUnsupportedError::new_err(format!(
                        "bounded {member_count}-member composite requires nonempty INCLUDE tables",
                    )));
                }
                if !root_ids.is(expected) {
                    return Err(encoded_buffer_error(
                        "encoded composite member lost its exact INCLUDE table",
                    ));
                }
            }
            (None, Some(expected)) => {
                if posting_mode != POSTINGS_EXCLUDE || root_id_bytes == 0 {
                    return Err(EncodedDirectUnsupportedError::new_err(format!(
                        "bounded {member_count}-member composite requires nonempty EXCLUDE tables",
                    )));
                }
                if !root_ids.is(expected) {
                    return Err(encoded_buffer_error(
                        "encoded composite member lost its exact EXCLUDE table",
                    ));
                }
            }
            (Some(_), Some(_)) => {
                return Err(encoded_buffer_error(
                    "encoded composite member cannot combine INCLUDE and EXCLUDE tables",
                ));
            }
        }
        let scope_map = required_attribute(&segment, "anonymous_scope_map")?;
        let scope_map_bytes = checked_memoryview_length(&scope_map, "anonymous_scope_map")?;
        match expected_scope_map {
            None if scope_map_bytes == 0 => {}
            Some(expected) if scope_map_bytes != 0 && scope_map.is(expected) => {}
            None => {
                return Err(encoded_buffer_error(
                    "encoded composite member received an unexpected anonymous scope map",
                ));
            }
            Some(_) if scope_map_bytes == 0 => {
                return Err(encoded_buffer_error(
                    "encoded composite member lost its anonymous scope map",
                ));
            }
            Some(_) => {
                return Err(encoded_buffer_error(
                    "encoded composite member lost its exact anonymous scope map",
                ));
            }
        }

        let token = required_attribute(&segment, "member_token")?;
        if !token.is_exact_instance_of::<PyBytes>() {
            return Err(encoded_buffer_error(
                "encoded composite member token must be exact immutable bytes",
            ));
        }
        let token = token
            .cast::<PyBytes>()
            .map_err(|_| encoded_buffer_error("encoded composite member token is inaccessible"))?
            .as_bytes();
        let token: [u8; 32] = token.try_into().map_err(|_| {
            encoded_buffer_error("encoded composite member token must contain exactly 32 bytes")
        })?;
        if previous_token.is_some_and(|previous| previous >= token) {
            return Err(encoded_buffer_error(
                "encoded composite member tokens are not sorted unique",
            ));
        }
        previous_token = Some(token);
    }
    if matched.iter().any(|value| !value) {
        return Err(encoded_buffer_error(format!(
            "encoded composite did not retain all {member_count} merge tables",
        )));
    }
    Ok(())
}

fn validate_paired_composite_manifests(
    closure_view: &Bound<'_, PyAny>,
    closure_owner: &Bound<'_, PyAny>,
    root_view: &Bound<'_, PyAny>,
    root_owner: &Bound<'_, PyAny>,
    member_count: usize,
) -> PyResult<()> {
    if !closure_owner.is(root_owner) {
        return Err(encoded_buffer_error(
            "encoded composite ROOT manifest belongs to another closure owner",
        ));
    }
    let closure_segments = required_attribute(closure_view, "segments")?;
    let root_segments = required_attribute(root_view, "segments")?;
    if !closure_segments.is_exact_instance_of::<PyTuple>()
        || !root_segments.is_exact_instance_of::<PyTuple>()
    {
        return Err(encoded_buffer_error(
            "encoded paired composite manifests must be exact tuples",
        ));
    }
    let closure_segments = closure_segments
        .cast::<PyTuple>()
        .map_err(|_| encoded_buffer_error("encoded closure manifest is inaccessible"))?;
    let root_segments = root_segments
        .cast::<PyTuple>()
        .map_err(|_| encoded_buffer_error("encoded ROOT manifest is inaccessible"))?;
    if closure_segments.len() != member_count || root_segments.len() != member_count {
        return Err(encoded_buffer_error(
            "encoded composite ROOT manifest lost its positional member pairing",
        ));
    }
    for index in 0..member_count {
        let closure = closure_segments
            .get_item(index)
            .map_err(|_| encoded_buffer_error("encoded closure member is inaccessible"))?;
        let root = root_segments
            .get_item(index)
            .map_err(|_| encoded_buffer_error("encoded ROOT member is inaccessible"))?;
        let closure_role =
            exact_nonnegative_integer(&required_attribute(&closure, "role")?, "segment role")?;
        let root_role =
            exact_nonnegative_integer(&required_attribute(&root, "role")?, "segment role")?;
        let closure_member_owner = required_attribute(&closure, "owner")?;
        let root_member_owner = required_attribute(&root, "owner")?;
        let closure_token = required_attribute(&closure, "member_token")?;
        let root_token = required_attribute(&root, "member_token")?;
        if closure_role != COMPOSITE_MEMBER_SEGMENT
            || root_role != COMPOSITE_MEMBER_SEGMENT
            || !closure_member_owner.is(&root_member_owner)
            || !closure_token.is_exact_instance_of::<PyBytes>()
            || !root_token.is_exact_instance_of::<PyBytes>()
        {
            return Err(encoded_buffer_error(
                "encoded composite ROOT member lost its exact token/owner pairing",
            ));
        }
        let closure_token = closure_token
            .cast::<PyBytes>()
            .map_err(|_| encoded_buffer_error("encoded closure member token is inaccessible"))?
            .as_bytes();
        let root_token = root_token
            .cast::<PyBytes>()
            .map_err(|_| encoded_buffer_error("encoded ROOT member token is inaccessible"))?
            .as_bytes();
        if closure_token != root_token {
            return Err(encoded_buffer_error(
                "encoded composite ROOT member lost its exact token/owner pairing",
            ));
        }
    }
    Ok(())
}

#[allow(clippy::too_many_arguments)] // Every nested retained identity is explicit.
fn validate_nested_member_composite_manifest(
    encoded_view: &Bound<'_, PyAny>,
    expected_owner: &Bound<'_, PyAny>,
    descriptor_sha256: &Bound<'_, PyAny>,
    nested_view: &Bound<'_, PyAny>,
    nested_owner: &Bound<'_, PyAny>,
    base_view: &Bound<'_, PyAny>,
    base_owner: &Bound<'_, PyAny>,
    nested_excluded_root_ids: Option<&Bound<'_, PyAny>>,
    direct_members: &[NestedDirectMemberBinding<'_, '_>],
    anonymous_scope_map: Option<&Bound<'_, PyAny>>,
    right_anonymous_scope_map: Option<&Bound<'_, PyAny>>,
) -> PyResult<()> {
    validate_encoded_view_header(encoded_view, expected_owner, descriptor_sha256)?;
    if !(1..=2).contains(&direct_members.len()) {
        return Err(EncodedDirectUnsupportedError::new_err(
            "bounded nested-member composite requires one or two direct siblings",
        ));
    }
    if anonymous_scope_map.is_some() != right_anonymous_scope_map.is_some() {
        return Err(encoded_buffer_error(
            "encoded scope-mapped nested composite requires both outer member scope maps",
        ));
    }
    if nested_view.is(base_view) || nested_owner.is(base_owner) {
        return Err(EncodedDirectUnsupportedError::new_err(
            "bounded nested-member composite requires distinct retained sources",
        ));
    }
    for (index, (direct_view, direct_owner, _excluded_root_ids)) in
        direct_members.iter().enumerate()
    {
        if direct_view.is(nested_view)
            || direct_view.is(base_view)
            || direct_owner.is(nested_owner)
            || direct_owner.is(base_owner)
        {
            return Err(EncodedDirectUnsupportedError::new_err(
                "bounded nested-member composite requires distinct retained sources",
            ));
        }
        for (prior_view, prior_owner, _prior_excluded_root_ids) in direct_members.iter().take(index)
        {
            if direct_view.is(*prior_view) || direct_owner.is(*prior_owner) {
                return Err(EncodedDirectUnsupportedError::new_err(
                    "bounded nested-member composite requires distinct retained sources",
                ));
            }
        }
    }

    let local_buffers = retained_structural_buffers(encoded_view)?;
    for (name, buffer) in BUFFER_NAMES.into_iter().zip(&local_buffers) {
        let bytes = buffer.as_slice();
        if name == "node_field_offsets" {
            if bytes != [0_u8; 8] {
                return Err(EncodedDirectUnsupportedError::new_err(
                    "bounded nested-member composite requires empty outer columns",
                ));
            }
        } else if !bytes.is_empty() {
            return Err(EncodedDirectUnsupportedError::new_err(
                "bounded nested-member composite does not support bridge roots",
            ));
        }
    }

    let raw_segments = required_attribute(encoded_view, "segments")?;
    if !raw_segments.is_exact_instance_of::<PyTuple>() {
        return Err(encoded_buffer_error(
            "encoded nested composite segment manifest must be an exact tuple",
        ));
    }
    let segments = raw_segments
        .cast::<PyTuple>()
        .map_err(|_| encoded_buffer_error("encoded nested composite manifest is inaccessible"))?;
    let outer_member_count = direct_members.len() + 1;
    if segments.len() != outer_member_count {
        return Err(EncodedDirectUnsupportedError::new_err(
            "bounded nested-member composite has an unsupported outer member count",
        ));
    }

    let mut matched = [false; 3];
    let mut previous_token: Option<[u8; 32]> = None;
    for index in 0..outer_member_count {
        let segment = segments
            .get_item(index)
            .map_err(|_| encoded_buffer_error("encoded nested composite member is inaccessible"))?;
        if exact_nonnegative_integer(&required_attribute(&segment, "role")?, "segment role")?
            != COMPOSITE_MEMBER_SEGMENT
        {
            return Err(EncodedDirectUnsupportedError::new_err(
                "bounded nested-member composite requires member-only outer segments",
            ));
        }
        let source = required_attribute(&segment, "source")?;
        let (member_index, member_owner) = if source.is(nested_view) {
            (0, nested_owner)
        } else {
            let Some((index, (_view, owner, _excluded_root_ids))) = direct_members
                .iter()
                .enumerate()
                .find(|(_index, (view, _owner, _excluded_root_ids))| source.is(*view))
            else {
                return Err(encoded_buffer_error(
                    "encoded nested composite member lost its retained source identity",
                ));
            };
            (index + 1, *owner)
        };
        if matched[member_index] {
            return Err(encoded_buffer_error(
                "encoded nested composite repeats one retained source identity",
            ));
        }
        matched[member_index] = true;
        if !required_attribute(&segment, "owner")?.is(member_owner) {
            return Err(encoded_buffer_error(
                "encoded nested composite member lost its retained owner identity",
            ));
        }
        let posting_mode = exact_nonnegative_integer(
            &required_attribute(&segment, "posting_mode")?,
            "segment posting_mode",
        )?;
        let root_ids = required_attribute(&segment, "root_ids")?;
        let root_id_bytes = checked_memoryview_length(&root_ids, "root_ids")?;
        let expected_exclude = if member_index == 0 {
            nested_excluded_root_ids
        } else {
            direct_members[member_index - 1].2
        };
        match expected_exclude {
            None if posting_mode == POSTINGS_ALL && root_id_bytes == 0 => {}
            Some(expected)
                if posting_mode == POSTINGS_EXCLUDE
                    && root_id_bytes != 0
                    && root_ids.is(expected) => {}
            Some(_) if posting_mode != POSTINGS_EXCLUDE || root_id_bytes == 0 => {
                return Err(EncodedDirectUnsupportedError::new_err(
                    "bounded nested-member composite requires a nonempty direct-sibling EXCLUDE table",
                ));
            }
            Some(_) => {
                return Err(encoded_buffer_error(if member_index == 0 {
                    "encoded nested member lost its exact EXCLUDE table"
                } else {
                    "encoded nested direct sibling lost its exact EXCLUDE table"
                }));
            }
            None => {
                return Err(EncodedDirectUnsupportedError::new_err(
                    "bounded nested-member composite received an unexpected outer EXCLUDE table",
                ));
            }
        }
        let expected_scope_map = match member_index {
            0 => anonymous_scope_map,
            1 => right_anonymous_scope_map,
            _ => None,
        };
        let scope_map = required_attribute(&segment, "anonymous_scope_map")?;
        let scope_map_bytes = checked_memoryview_length(&scope_map, "anonymous_scope_map")?;
        match expected_scope_map {
            None if scope_map_bytes == 0 => {}
            Some(expected) if scope_map_bytes != 0 && scope_map.is(expected) => {}
            None => {
                return Err(EncodedDirectUnsupportedError::new_err(
                    "bounded nested-member composite received an unexpected anonymous scope map",
                ));
            }
            Some(_) if scope_map_bytes == 0 => {
                return Err(encoded_buffer_error(
                    "encoded nested composite member lost its anonymous scope map",
                ));
            }
            Some(_) => {
                return Err(encoded_buffer_error(
                    "encoded nested composite member lost its exact anonymous scope map",
                ));
            }
        }

        let token = required_attribute(&segment, "member_token")?;
        if !token.is_exact_instance_of::<PyBytes>() {
            return Err(encoded_buffer_error(
                "encoded nested composite member token must be exact immutable bytes",
            ));
        }
        let token = token
            .cast::<PyBytes>()
            .map_err(|_| {
                encoded_buffer_error("encoded nested composite member token is inaccessible")
            })?
            .as_bytes();
        let token: [u8; 32] = token.try_into().map_err(|_| {
            encoded_buffer_error(
                "encoded nested composite member token must contain exactly 32 bytes",
            )
        })?;
        if previous_token.is_some_and(|previous| previous >= token) {
            return Err(encoded_buffer_error(
                "encoded nested composite member tokens are not sorted unique",
            ));
        }
        previous_token = Some(token);
    }
    if !matched[..outer_member_count].iter().all(|value| *value) {
        return Err(encoded_buffer_error(
            "encoded nested composite did not retain every outer member",
        ));
    }
    Ok(())
}

fn validate_encoded_view_header(
    encoded_view: &Bound<'_, PyAny>,
    expected_owner: &Bound<'_, PyAny>,
    descriptor_sha256: &Bound<'_, PyAny>,
) -> PyResult<()> {
    let schema_name = required_attribute(encoded_view, "schema_name")?
        .extract::<String>()
        .map_err(|_| encoded_buffer_error("encoded schema_name must be text"))?;
    if schema_name != ENCODED_SCHEMA_NAME {
        return Err(encoded_buffer_error(
            "encoded schema_name is not structural-columns",
        ));
    }
    if exact_nonnegative_integer(
        &required_attribute(encoded_view, "schema_version")?,
        "encoded schema_version",
    )? != ENCODED_SCHEMA_VERSION
    {
        return Err(encoded_buffer_error(
            "encoded schema_version is not supported",
        ));
    }
    if exact_nonnegative_integer(
        &required_attribute(encoded_view, "model_schema")?,
        "encoded model_schema",
    )? != ENCODED_MODEL_SCHEMA
    {
        return Err(encoded_buffer_error(
            "encoded model_schema is not supported",
        ));
    }
    let owner = required_attribute(encoded_view, "owner")?;
    if !owner.is(expected_owner) {
        return Err(encoded_buffer_error(
            "encoded view does not retain the expected owner identity",
        ));
    }
    // The public Python adapter hashes the exact descriptor bytes while it
    // validates the lease.  Require that bound digest explicitly at the PyO3
    // seam; the current core view does not expose a digest attribute itself.
    if !descriptor_sha256.is_exact_instance_of::<PyBytes>()
        || descriptor_sha256
            .cast::<PyBytes>()
            .map_err(|_| encoded_buffer_error("encoded descriptor digest is inaccessible"))?
            .as_bytes()
            != ENCODED_DESCRIPTOR_SHA256
    {
        return Err(encoded_buffer_error(
            "encoded descriptor digest differs from structural-columns v1",
        ));
    }
    let descriptor = required_attribute(encoded_view, "descriptor")?;
    if !descriptor.is_exact_instance_of::<PyBytes>()
        || descriptor
            .cast::<PyBytes>()
            .map_err(|_| encoded_buffer_error("encoded descriptor is inaccessible"))?
            .as_bytes()
            .is_empty()
    {
        return Err(encoded_buffer_error(
            "encoded descriptor must be nonempty exact immutable bytes",
        ));
    }
    Ok(())
}

fn retained_structural_buffers(
    encoded_view: &Bound<'_, PyAny>,
) -> PyResult<Vec<RetainedDirectBuffer>> {
    let raw_buffers = required_attribute(encoded_view, "buffers")?;
    let mapping = raw_buffers
        .cast::<PyMapping>()
        .map_err(|_| encoded_buffer_error("encoded buffers must be a mapping"))?;
    if mapping
        .len()
        .map_err(|_| encoded_buffer_error("encoded buffer count is inaccessible"))?
        != BUFFER_COUNT
    {
        return Err(encoded_buffer_error(
            "encoded buffer set does not contain exactly eleven columns",
        ));
    }
    let mut candidates = Vec::new();
    candidates
        .try_reserve_exact(BUFFER_COUNT)
        .map_err(|_| PyMemoryError::new_err("encoded buffer-retention allocation failed"))?;
    for name in BUFFER_NAMES {
        let buffer = mapping
            .get_item(name)
            .map_err(|_| encoded_buffer_error(format!("encoded view is missing buffer {name}")))?;
        candidates.push(ValidatedDirectBuffer::inspect(buffer, name)?);
    }
    retain_direct_exporters(candidates)
}

fn retained_exact_bytes_buffer(
    buffer: &Bound<'_, PyAny>,
    name: &'static str,
) -> PyResult<RetainedDirectBuffer> {
    let candidate = ValidatedDirectBuffer::inspect(buffer.clone(), name)?;
    let (exporter, length) = candidate.into_exact_bytes()?;
    if exporter.as_bytes().len() != length {
        return Err(EncodedDirectUnsupportedError::new_err(format!(
            "encoded buffer {name} does not cover its complete bytes exporter",
        )));
    }
    Ok(RetainedDirectBuffer {
        storage: RetainedDirectStorage::ExactImmutableBytes(PyBackedBytes::from(exporter)),
        start: 0,
        end: length,
    })
}

fn validate_direct_segment(
    encoded_view: &Bound<'_, PyAny>,
    expected_owner: &Bound<'_, PyAny>,
) -> PyResult<()> {
    let raw_segments = required_attribute(encoded_view, "segments")?;
    if !raw_segments.is_exact_instance_of::<PyTuple>() {
        return Err(encoded_buffer_error(
            "encoded segment manifest must be an exact tuple",
        ));
    }
    let segments = raw_segments
        .cast::<PyTuple>()
        .map_err(|_| encoded_buffer_error("encoded segment manifest is inaccessible"))?;
    if segments.len() != 1 {
        return Err(EncodedDirectUnsupportedError::new_err(
            "direct native slice does not support segmented encoded views",
        ));
    }
    let segment = segments
        .get_item(0)
        .map_err(|_| encoded_buffer_error("encoded direct segment is inaccessible"))?;
    if exact_nonnegative_integer(&required_attribute(&segment, "role")?, "segment role")?
        != DIRECT_SEGMENT
    {
        return Err(EncodedDirectUnsupportedError::new_err(
            "direct native slice requires the canonical direct segment role",
        ));
    }
    if !required_attribute(&segment, "owner")?.is(expected_owner) {
        return Err(encoded_buffer_error(
            "encoded direct segment does not retain the expected owner",
        ));
    }
    if !required_attribute(&segment, "source")?.is_none() {
        return Err(encoded_buffer_error(
            "encoded direct segment unexpectedly references a source view",
        ));
    }
    if exact_nonnegative_integer(
        &required_attribute(&segment, "posting_mode")?,
        "segment posting_mode",
    )? != POSTINGS_ALL
    {
        return Err(encoded_buffer_error(
            "encoded direct segment posting mode is not ALL",
        ));
    }
    for name in ["root_ids", "anonymous_scope_map"] {
        let buffer = required_attribute(&segment, name)?;
        if checked_memoryview_length(&buffer, name)? != 0 {
            return Err(encoded_buffer_error(format!(
                "encoded direct segment {name} must be empty",
            )));
        }
    }
    if !required_attribute(&segment, "member_token")?.is_none() {
        return Err(encoded_buffer_error(
            "encoded direct segment member_token must be None",
        ));
    }
    Ok(())
}

fn validate_overlay_delta_segments(
    encoded_view: &Bound<'_, PyAny>,
    expected_owner: &Bound<'_, PyAny>,
    source_view: &Bound<'_, PyAny>,
    source_owner: &Bound<'_, PyAny>,
    excluded_root_ids: Option<&Bound<'_, PyAny>>,
) -> PyResult<()> {
    let raw_segments = required_attribute(encoded_view, "segments")?;
    if !raw_segments.is_exact_instance_of::<PyTuple>() {
        return Err(encoded_buffer_error(
            "encoded local-overlay segment manifest must be an exact tuple",
        ));
    }
    let segments = raw_segments
        .cast::<PyTuple>()
        .map_err(|_| encoded_buffer_error("encoded local-overlay manifest is inaccessible"))?;
    if segments.len() != 2 {
        return Err(EncodedDirectUnsupportedError::new_err(
            "bounded local-overlay compilation requires exactly base and delta segments",
        ));
    }
    let base = segments
        .get_item(0)
        .map_err(|_| encoded_buffer_error("encoded local-overlay base is inaccessible"))?;
    let delta = segments
        .get_item(1)
        .map_err(|_| encoded_buffer_error("encoded local-overlay delta is inaccessible"))?;
    validate_overlay_base_segment(&base, source_owner, source_view, excluded_root_ids)?;
    validate_all_segment(
        &delta,
        OVERLAY_DELTA_SEGMENT,
        expected_owner,
        None,
        "local-overlay delta",
    )
}

fn validate_overlay_base_segment(
    segment: &Bound<'_, PyAny>,
    expected_owner: &Bound<'_, PyAny>,
    expected_source: &Bound<'_, PyAny>,
    excluded_root_ids: Option<&Bound<'_, PyAny>>,
) -> PyResult<()> {
    if exact_nonnegative_integer(&required_attribute(segment, "role")?, "segment role")?
        != OVERLAY_BASE_SEGMENT
    {
        return Err(EncodedDirectUnsupportedError::new_err(
            "encoded local-overlay base has an unsupported role",
        ));
    }
    if !required_attribute(segment, "owner")?.is(expected_owner) {
        return Err(encoded_buffer_error(
            "encoded local-overlay base does not retain its expected owner",
        ));
    }
    if !required_attribute(segment, "source")?.is(expected_source) {
        return Err(encoded_buffer_error(
            "encoded local-overlay base does not retain the exact direct source",
        ));
    }
    let posting_mode = exact_nonnegative_integer(
        &required_attribute(segment, "posting_mode")?,
        "segment posting_mode",
    )?;
    let root_ids = required_attribute(segment, "root_ids")?;
    let root_id_bytes = checked_memoryview_length(&root_ids, "root_ids")?;
    match excluded_root_ids {
        None => {
            if posting_mode != POSTINGS_ALL || root_id_bytes != 0 {
                return Err(EncodedDirectUnsupportedError::new_err(
                    "encoded local-overlay base requires ALL root selection",
                ));
            }
        }
        Some(expected) => {
            if posting_mode != POSTINGS_EXCLUDE || root_id_bytes == 0 {
                return Err(EncodedDirectUnsupportedError::new_err(
                    "encoded local-overlay base requires one nonempty EXCLUDE table",
                ));
            }
            if !root_ids.is(expected) {
                return Err(encoded_buffer_error(
                    "encoded local-overlay base does not retain the exact EXCLUDE table",
                ));
            }
        }
    }
    let anonymous_scope_map = required_attribute(segment, "anonymous_scope_map")?;
    if checked_memoryview_length(&anonymous_scope_map, "anonymous_scope_map")? != 0 {
        return Err(EncodedDirectUnsupportedError::new_err(
            "encoded local-overlay base anonymous_scope_map must be empty",
        ));
    }
    if !required_attribute(segment, "member_token")?.is_none() {
        return Err(encoded_buffer_error(
            "encoded local-overlay base member_token must be None",
        ));
    }
    Ok(())
}

fn validate_all_segment(
    segment: &Bound<'_, PyAny>,
    expected_role: usize,
    expected_owner: &Bound<'_, PyAny>,
    expected_source: Option<&Bound<'_, PyAny>>,
    label: &str,
) -> PyResult<()> {
    if exact_nonnegative_integer(&required_attribute(segment, "role")?, "segment role")?
        != expected_role
    {
        return Err(EncodedDirectUnsupportedError::new_err(format!(
            "encoded {label} has an unsupported role",
        )));
    }
    if !required_attribute(segment, "owner")?.is(expected_owner) {
        return Err(encoded_buffer_error(format!(
            "encoded {label} does not retain its expected owner",
        )));
    }
    let source = required_attribute(segment, "source")?;
    match expected_source {
        Some(expected) if !source.is(expected) => {
            return Err(encoded_buffer_error(format!(
                "encoded {label} does not retain the exact direct source",
            )));
        }
        None if !source.is_none() => {
            return Err(encoded_buffer_error(format!(
                "encoded {label} unexpectedly references a source view",
            )));
        }
        _ => {}
    }
    if exact_nonnegative_integer(
        &required_attribute(segment, "posting_mode")?,
        "segment posting_mode",
    )? != POSTINGS_ALL
    {
        return Err(EncodedDirectUnsupportedError::new_err(format!(
            "encoded {label} requires ALL root selection",
        )));
    }
    for name in ["root_ids", "anonymous_scope_map"] {
        let buffer = required_attribute(segment, name)?;
        if checked_memoryview_length(&buffer, name)? != 0 {
            return Err(EncodedDirectUnsupportedError::new_err(format!(
                "encoded {label} {name} must be empty",
            )));
        }
    }
    if !required_attribute(segment, "member_token")?.is_none() {
        return Err(encoded_buffer_error(format!(
            "encoded {label} member_token must be None",
        )));
    }
    Ok(())
}

enum ValidatedDirectExporter<'py> {
    ExactImmutableBytes(Bound<'py, PyBytes>),
    GeneralReadonlyContiguous(Bound<'py, PyAny>),
}

struct ValidatedDirectBuffer<'py> {
    name: &'static str,
    view: Bound<'py, PyAny>,
    exporter: ValidatedDirectExporter<'py>,
    length: usize,
}

impl<'py> ValidatedDirectBuffer<'py> {
    fn inspect(view: Bound<'py, PyAny>, name: &'static str) -> PyResult<Self> {
        let length = checked_memoryview_length(&view, name)?;
        let exporter = required_attribute(&view, "obj")?;
        let exporter = if exporter.is_exact_instance_of::<PyBytes>() {
            ValidatedDirectExporter::ExactImmutableBytes(exporter.cast_into::<PyBytes>().map_err(
                |_| encoded_buffer_error(format!("encoded buffer {name} owner is inaccessible")),
            )?)
        } else {
            ValidatedDirectExporter::GeneralReadonlyContiguous(exporter)
        };
        Ok(Self {
            name,
            view,
            exporter,
            length,
        })
    }

    fn is_general_exporter(&self) -> bool {
        matches!(
            self.exporter,
            ValidatedDirectExporter::GeneralReadonlyContiguous(_)
        )
    }

    fn exact_bytes(&self) -> &Bound<'py, PyBytes> {
        match &self.exporter {
            ValidatedDirectExporter::ExactImmutableBytes(exporter) => exporter,
            ValidatedDirectExporter::GeneralReadonlyContiguous(_) => {
                unreachable!("general exporters are rejected before bytes-layout validation")
            }
        }
    }

    fn into_exact_bytes(self) -> PyResult<(Bound<'py, PyBytes>, usize)> {
        match self.exporter {
            ValidatedDirectExporter::ExactImmutableBytes(exporter) => Ok((exporter, self.length)),
            ValidatedDirectExporter::GeneralReadonlyContiguous(exporter) => {
                drop(exporter);
                Err(general_buffer_retention_error(self.name))
            }
        }
    }
}

fn general_buffer_retention_error(name: &str) -> PyErr {
    EncodedDirectUnsupportedError::new_err(format!(
        "encoded buffer {name} has a valid readonly C-contiguous non-bytes exporter, but \
         zero-copy retention requires pyo3::buffer::PyUntypedBuffer \
         ({GENERAL_BUFFER_STABLE_ABI_MINIMUM} or newer); this extension is abi3-py310",
    ))
}

fn retain_direct_exporters<'py>(
    candidates: Vec<ValidatedDirectBuffer<'py>>,
) -> PyResult<Vec<RetainedDirectBuffer>> {
    if let Some(candidate) = candidates
        .iter()
        .find(|candidate| candidate.is_general_exporter())
    {
        return Err(general_buffer_retention_error(candidate.name));
    }
    retain_direct_bytes_exporters(candidates)
}

fn retain_direct_bytes_exporters<'py>(
    borrowed: Vec<ValidatedDirectBuffer<'py>>,
) -> PyResult<Vec<RetainedDirectBuffer>> {
    let mut retained = Vec::new();
    retained
        .try_reserve_exact(BUFFER_COUNT)
        .map_err(|_| PyMemoryError::new_err("encoded buffer-retention allocation failed"))?;
    if borrowed
        .iter()
        .all(|candidate| candidate.exact_bytes().as_bytes().len() == candidate.length)
    {
        for candidate in borrowed {
            let (exporter, length) = candidate.into_exact_bytes()?;
            retained.push(RetainedDirectBuffer {
                storage: RetainedDirectStorage::ExactImmutableBytes(PyBackedBytes::from(exporter)),
                start: 0,
                end: length,
            });
        }
        return Ok(retained);
    }

    let Some(first) = borrowed.first() else {
        return Err(encoded_buffer_error("encoded buffer set is empty"));
    };
    let first_exporter = first.exact_bytes();
    if borrowed
        .iter()
        .any(|candidate| !candidate.exact_bytes().is(first_exporter))
    {
        let name = borrowed
            .iter()
            .find(|candidate| candidate.exact_bytes().as_bytes().len() != candidate.length)
            .map(|candidate| candidate.name)
            .unwrap_or("unknown");
        return Err(EncodedDirectUnsupportedError::new_err(format!(
            "encoded buffer {name} does not cover its complete bytes exporter",
        )));
    }

    let total_length = borrowed.iter().try_fold(0_usize, |total, candidate| {
        total
            .checked_add(candidate.length)
            .ok_or_else(|| encoded_buffer_error("encoded packed-buffer byte length overflow"))
    })?;
    if total_length != first_exporter.as_bytes().len() {
        return Err(EncodedDirectUnsupportedError::new_err(
            "encoded packed buffers do not exactly cover their shared bytes exporter",
        ));
    }

    let packed_view = PyMemoryView::from(first_exporter.as_any()).map_err(|_| {
        encoded_buffer_error("encoded packed bytes exporter is not memoryview-compatible")
    })?;
    let mut start = 0_usize;
    for candidate in borrowed {
        let ValidatedDirectBuffer {
            name,
            view: buffer,
            exporter,
            length,
        } = candidate;
        let exporter = match exporter {
            ValidatedDirectExporter::ExactImmutableBytes(exporter) => exporter,
            ValidatedDirectExporter::GeneralReadonlyContiguous(_) => {
                unreachable!("general exporters are rejected before bytes-layout validation")
            }
        };
        let end = start
            .checked_add(length)
            .ok_or_else(|| encoded_buffer_error("encoded packed-buffer range overflow"))?;
        let start_index = isize::try_from(start)
            .map_err(|_| encoded_buffer_error("encoded packed-buffer start exceeds Py_ssize_t"))?;
        let end_index = isize::try_from(end)
            .map_err(|_| encoded_buffer_error("encoded packed-buffer end exceeds Py_ssize_t"))?;
        let expected = packed_view
            .get_item(PySlice::new(buffer.py(), start_index, end_index, 1))
            .map_err(|_| {
                encoded_buffer_error(format!(
                    "encoded buffer {name} canonical packed range is inaccessible",
                ))
            })?;
        if !buffer.eq(&expected).map_err(|_| {
            encoded_buffer_error(format!(
                "encoded buffer {name} cannot be compared with its packed range",
            ))
        })? {
            return Err(EncodedDirectUnsupportedError::new_err(format!(
                "encoded buffer {name} does not match the canonical packed bytes layout",
            )));
        }
        retained.push(RetainedDirectBuffer {
            storage: RetainedDirectStorage::ExactImmutableBytes(PyBackedBytes::from(exporter)),
            start,
            end,
        });
        start = end;
    }
    Ok(retained)
}

fn checked_memoryview_length(buffer: &Bound<'_, PyAny>, name: &str) -> PyResult<usize> {
    let invalid = |message: &str| encoded_buffer_error(format!("encoded buffer {name} {message}"));
    if !buffer.is_exact_instance_of::<PyMemoryView>() {
        return Err(invalid("is not an exact memoryview"));
    }
    if !required_attribute(buffer, "readonly")?
        .extract::<bool>()
        .map_err(|_| invalid("has invalid readonly metadata"))?
    {
        return Err(invalid("is writable"));
    }
    let dimensions = required_attribute(buffer, "ndim")?
        .extract::<usize>()
        .map_err(|_| invalid("has invalid dimension metadata"))?;
    let item_size = required_attribute(buffer, "itemsize")?
        .extract::<usize>()
        .map_err(|_| invalid("has invalid item-size metadata"))?;
    let contiguous = required_attribute(buffer, "c_contiguous")?
        .extract::<bool>()
        .map_err(|_| invalid("has invalid contiguity metadata"))?;
    let format = required_attribute(buffer, "format")?
        .extract::<String>()
        .map_err(|_| invalid("has invalid format metadata"))?;
    if dimensions != 1 || item_size != 1 || !contiguous || format != "B" {
        return Err(invalid(
            "is not a contiguous one-dimensional unsigned-byte view",
        ));
    }
    let length = required_attribute(buffer, "nbytes")?
        .extract::<usize>()
        .map_err(|_| invalid("has invalid byte-length metadata"))?;
    if buffer
        .len()
        .map_err(|_| invalid("has inaccessible length"))?
        != length
    {
        return Err(invalid("has inconsistent byte-length metadata"));
    }
    Ok(length)
}

fn required_attribute<'py>(value: &Bound<'py, PyAny>, name: &str) -> PyResult<Bound<'py, PyAny>> {
    value
        .getattr(name)
        .map_err(|_| encoded_buffer_error(format!("encoded input is missing attribute {name}")))
}

fn exact_nonnegative_integer(value: &Bound<'_, PyAny>, name: &str) -> PyResult<usize> {
    if !value.is_exact_instance_of::<PyInt>() {
        return Err(encoded_buffer_error(format!(
            "{name} must be an exact nonnegative integer",
        )));
    }
    value.extract::<usize>().map_err(|_| {
        encoded_buffer_error(format!(
            "{name} must be a nonnegative integer fitting usize"
        ))
    })
}

fn encoded_buffer_error(message: impl Into<String>) -> PyErr {
    EncodedDirectBufferError::new_err(message.into())
}

fn kernel_error(error: KernelError) -> PyErr {
    match error {
        KernelError::Malformed(message) => EncodedDirectBufferError::new_err(message),
        KernelError::Unsupported(message) => EncodedDirectUnsupportedError::new_err(message),
        KernelError::ReferenceFailure(message) => EncodedDirectReferenceError::new_err(message),
        KernelError::Resource(message) => PyMemoryError::new_err(message),
        KernelError::Cancelled => {
            EncodedDirectCancelledError::new_err("encoded direct compiler was cancelled")
        }
    }
}

#[pymodule(gil_used = true)]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add("__version__", env!("CARGO_PKG_VERSION"))?;
    module.add("NATIVE_API_VERSION", NATIVE_API_VERSION)?;
    module.add("FEATURES", ("abi3-py310", "bounded-batches"))?;
    module.add(
        "ENCODED_DIRECT_KERNEL_VERSION",
        ENCODED_DIRECT_KERNEL_VERSION,
    )?;
    module.add("ENCODED_DIRECT_BUFFER_ORDER", BUFFER_NAMES)?;
    module.add(
        "EncodedDirectUnsupportedError",
        module.py().get_type::<EncodedDirectUnsupportedError>(),
    )?;
    module.add(
        "EncodedDirectBufferError",
        module.py().get_type::<EncodedDirectBufferError>(),
    )?;
    module.add(
        "EncodedDirectCancelledError",
        module.py().get_type::<EncodedDirectCancelledError>(),
    )?;
    module.add(
        "EncodedDirectReferenceError",
        module.py().get_type::<EncodedDirectReferenceError>(),
    )?;
    module.add_class::<EdgeBatchProcessor>()?;
    module.add_class::<EncodedDirectRoleState>()?;
    module.add_class::<EncodedDirectCompiler>()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn edge(source: &str, relation: &str, destination: &str) -> Edge {
        Edge {
            source: source.into(),
            relation: relation.into(),
            destination: destination.into(),
        }
    }

    #[test]
    fn encounter_unique_is_stable_across_batches() {
        let a = edge("a", "r", "z");
        let b = edge("b", "r", "y");
        let mut engine = EdgeEngine::new(OutputOrder::Encounter, DuplicatePolicy::Unique, None);
        assert_eq!(
            engine.push(vec![b.clone(), a.clone()]).unwrap(),
            vec![b, a.clone()]
        );
        assert!(engine.push(vec![a]).unwrap().is_empty());
        engine.finish().unwrap();
        assert_eq!(
            (engine.raw_edges, engine.seen.len(), engine.duplicate_edges),
            (3, 2, 1)
        );
    }

    #[test]
    fn canonical_preserve_orders_utf8_and_multiplicity() {
        let composed = edge("é", "r", "x");
        let decomposed = edge("e\u{301}", "r", "x");
        let mut engine = EdgeEngine::new(OutputOrder::Canonical, DuplicatePolicy::Preserve, None);
        engine
            .push(vec![composed.clone(), decomposed.clone(), composed.clone()])
            .unwrap();
        engine.finish().unwrap();
        assert_eq!(
            engine.drain(8).unwrap(),
            vec![decomposed, composed.clone(), composed]
        );
        assert_eq!(engine.duplicate_edges, 1);
    }

    #[test]
    fn configured_allocation_limit_fails_before_mutation() {
        let mut engine =
            EdgeEngine::new(OutputOrder::Encounter, DuplicatePolicy::Preserve, Some(1));
        let error = engine
            .push(vec![edge("a", "r", "b"), edge("c", "r", "d")])
            .unwrap_err();
        Python::initialize();
        Python::attach(|py| assert!(error.is_instance_of::<PyMemoryError>(py)));
        assert_eq!(engine.raw_edges, 0);
    }

    #[test]
    fn cancellation_releases_owned_buffers() {
        let mut engine = EdgeEngine::new(OutputOrder::Canonical, DuplicatePolicy::Preserve, None);
        engine.push(vec![edge("a", "r", "b")]).unwrap();
        engine.cancel();
        assert!(engine.cancelled);
        assert!(engine.canonical.is_empty());
        assert!(engine.seen.is_empty());
    }

    #[test]
    fn retained_role_state_rejects_overlap_and_releases_after_failure() {
        let retained = Arc::new(RetainedRoleState::default());
        let empty: [&[u8]; BUFFER_COUNT] = std::array::from_fn(|_| &[] as &[u8]);
        let columns = DirectColumns::from_ordered(empty);
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 1,
            max_iri_bytes: 1,
        };
        let compiler_state = AtomicU8::new(STATE_RUNNING);

        let role_use = retained.claim().unwrap();
        let error = retained.claim().unwrap_err();
        Python::initialize();
        Python::attach(|py| assert!(error.is_instance_of::<PyValueError>(py)));
        drop(role_use);

        let role_use = retained.claim().unwrap();
        assert!(retained
            .compile_claimed(columns, None, options, &compiler_state)
            .is_err());
        drop(role_use);
        assert!(!retained.in_use.load(Ordering::Acquire));
    }
}
