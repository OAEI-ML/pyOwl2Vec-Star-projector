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

use std::collections::HashSet;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::atomic::{AtomicBool, AtomicU8, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

#[cfg(test)]
use encoded_direct::compile_direct_with_retained_role_state;
use encoded_direct::{
    prepare_direct_batches_uncommitted, prepare_direct_batches_with_retained_role_state,
    prepare_single_overlay_delta_batches_uncommitted, DirectColumns, DirectCompileOptions,
    DirectCompileStats, DirectEdge, KernelError, OwnedRoleSnapshot, OwnedRoleState,
    PreparedDirectBatches, BUFFER_COUNT, BUFFER_NAMES, STATE_CANCELLED, STATE_FAILED,
    STATE_FINISHED, STATE_IDLE, STATE_RUNNING,
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
const ENCODED_DIRECT_KERNEL_VERSION: u32 = 66;
const COARSE_OUTPUT_CHUNK_EDGES: usize = 256;
const ENCODED_SCHEMA_NAME: &str = "pyowl-core/structural-columns";
const ENCODED_SCHEMA_VERSION: usize = 1;
const ENCODED_MODEL_SCHEMA: usize = 1;
const DIRECT_SEGMENT: usize = 1;
const OVERLAY_BASE_SEGMENT: usize = 2;
const OVERLAY_DELTA_SEGMENT: usize = 3;
const POSTINGS_ALL: usize = 0;
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

struct RetainedDirectBuffer {
    exporter: PyBackedBytes,
    start: usize,
    end: usize,
}

impl RetainedDirectBuffer {
    fn as_slice(&self) -> &[u8] {
        &self.exporter[self.start..self.end]
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
#[pyclass(module = "pyowl2vec_star_projector._native", frozen)]
struct EncodedDirectCompiler {
    _encoded_view: Py<PyAny>,
    _owner: Py<PyAny>,
    buffers: Vec<RetainedDirectBuffer>,
    _overlay_delta_view: Option<Py<PyAny>>,
    _overlay_delta_owner: Option<Py<PyAny>>,
    overlay_delta_buffers: Option<Vec<RetainedDirectBuffer>>,
    canonical_merge_limits: Option<(usize, usize)>,
    excluded_root_ids: Option<RetainedDirectBuffer>,
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
        let excluded_root_ids = self
            .excluded_root_ids
            .as_ref()
            .map_or(&[][..], RetainedDirectBuffer::as_slice);
        let columns = DirectColumns::from_ordered(slices).with_excluded_root_ids(excluded_root_ids);
        let overlay_delta_columns = self.overlay_delta_buffers.as_ref().map(|buffers| {
            let slices: [&[u8]; BUFFER_COUNT] =
                std::array::from_fn(|index| buffers[index].as_slice());
            DirectColumns::from_ordered(slices)
        });
        let root_annotation_columns = self.root_annotation_buffers.as_ref().map(|buffers| {
            let slices: [&[u8]; BUFFER_COUNT] =
                std::array::from_fn(|index| buffers[index].as_slice());
            DirectColumns::from_ordered(slices)
        });
        if overlay_delta_columns.is_some() && retained_role_state.is_some() {
            return self.finish_result(Err(EncodedDirectUnsupportedError::new_err(
                "bounded local-overlay compilation does not bind retained role state",
            )));
        }
        let retained_role_use = match retained_role_state.as_ref() {
            Some(retained) => match retained.claim() {
                Ok(role_use) => Some(role_use),
                Err(error) => return self.finish_result(Err(error)),
            },
            None => None,
        };
        let result = guarded(|| {
            py.detach(|| {
                if let Some(delta_columns) = overlay_delta_columns {
                    let (max_work, max_workspace_bytes) =
                        self.canonical_merge_limits.ok_or_else(|| {
                            PyRuntimeError::new_err(
                                "encoded local-overlay compiler lost its canonical limits",
                            )
                        })?;
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
    ) -> PyResult<Self> {
        let buffers = retained_direct_buffers(encoded_view, expected_owner, descriptor_sha256)?;
        let excluded_root_ids_view = excluded_root_ids;
        let excluded_root_ids = excluded_root_ids_view
            .map(|value| retained_exact_bytes_buffer(value, "excluded_root_ids"))
            .transpose()?;
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
        let (overlay_delta_buffers, canonical_merge_limits) = match (
            overlay_delta_view,
            overlay_delta_owner,
            overlay_delta_descriptor_sha256,
            canonical_work_limit,
            canonical_workspace_limit,
        ) {
            (None, None, None, None, None) => (None, None),
            (Some(view), Some(owner), Some(digest), Some(max_work), Some(max_workspace_bytes)) => {
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
                (
                    Some(retained_overlay_delta_buffers(
                        view,
                        owner,
                        digest,
                        encoded_view,
                        expected_owner,
                        excluded_root_ids_view,
                    )?),
                    Some((max_work, max_workspace_bytes)),
                )
            }
            _ => {
                return Err(encoded_buffer_error(
                    "encoded local-overlay view, owner, descriptor digest, and canonical limits must be supplied together",
                ));
            }
        };
        Ok(Self {
            _encoded_view: encoded_view.clone().unbind(),
            _owner: expected_owner.clone().unbind(),
            buffers,
            _overlay_delta_view: overlay_delta_view.map(|value| value.clone().unbind()),
            _overlay_delta_owner: overlay_delta_owner.map(|value| value.clone().unbind()),
            overlay_delta_buffers,
            canonical_merge_limits,
            excluded_root_ids,
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
        let excluded_root_ids = self
            .excluded_root_ids
            .as_ref()
            .map_or(&[][..], RetainedDirectBuffer::as_slice);
        let columns = DirectColumns::from_ordered(slices).with_excluded_root_ids(excluded_root_ids);
        let overlay_delta_columns = self.overlay_delta_buffers.as_ref().map(|buffers| {
            let slices: [&[u8]; BUFFER_COUNT] =
                std::array::from_fn(|index| buffers[index].as_slice());
            DirectColumns::from_ordered(slices)
        });
        let root_annotation_columns = self.root_annotation_buffers.as_ref().map(|buffers| {
            let slices: [&[u8]; BUFFER_COUNT] =
                std::array::from_fn(|index| buffers[index].as_slice());
            DirectColumns::from_ordered(slices)
        });
        if overlay_delta_columns.is_some() && retained_role_state.is_some() {
            return self.finish_result(Err(EncodedDirectUnsupportedError::new_err(
                "bounded local-overlay compilation does not bind retained role state",
            )));
        }
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
                if let Some(delta_columns) = overlay_delta_columns {
                    let (max_work, max_workspace_bytes) =
                        self.canonical_merge_limits.ok_or_else(|| {
                            PyRuntimeError::new_err(
                                "encoded local-overlay compiler lost its canonical limits",
                            )
                        })?;
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
                        stream.prepare_next_batch(columns, &self.state, COARSE_OUTPUT_CHUNK_EDGES)
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
            let excluded_root_ids = self
                .excluded_root_ids
                .as_ref()
                .map_or(&[][..], RetainedDirectBuffer::as_slice);
            let columns =
                DirectColumns::from_ordered(slices).with_excluded_root_ids(excluded_root_ids);
            let prepared = py
                .detach(|| stream.prepare_next_batch(columns, &self.state, batch_edges))
                .map_err(kernel_error);
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
        self.buffers.len()
            + self.overlay_delta_buffers.as_ref().map_or(0, Vec::len)
            + self.root_annotation_buffers.as_ref().map_or(0, Vec::len)
            + usize::from(self.excluded_root_ids.is_some())
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
    let mut borrowed = Vec::new();
    borrowed
        .try_reserve_exact(BUFFER_COUNT)
        .map_err(|_| PyMemoryError::new_err("encoded buffer-retention allocation failed"))?;
    for name in BUFFER_NAMES {
        let buffer = mapping
            .get_item(name)
            .map_err(|_| encoded_buffer_error(format!("encoded view is missing buffer {name}")))?;
        let length = checked_memoryview_length(&buffer, name)?;
        let exporter = required_attribute(&buffer, "obj")?;
        if !exporter.is_exact_instance_of::<PyBytes>() {
            return Err(EncodedDirectUnsupportedError::new_err(format!(
                "encoded buffer {name} is not backed by exact immutable bytes",
            )));
        }
        let exporter = exporter.cast_into::<PyBytes>().map_err(|_| {
            encoded_buffer_error(format!("encoded buffer {name} owner is inaccessible"))
        })?;
        borrowed.push((name, buffer, exporter, length));
    }
    retain_direct_bytes_exporters(borrowed)
}

fn retained_exact_bytes_buffer(
    buffer: &Bound<'_, PyAny>,
    name: &'static str,
) -> PyResult<RetainedDirectBuffer> {
    let length = checked_memoryview_length(buffer, name)?;
    let exporter = required_attribute(buffer, "obj")?;
    if !exporter.is_exact_instance_of::<PyBytes>() {
        return Err(EncodedDirectUnsupportedError::new_err(format!(
            "encoded buffer {name} is not backed by exact immutable bytes",
        )));
    }
    let exporter = exporter.cast_into::<PyBytes>().map_err(|_| {
        encoded_buffer_error(format!("encoded buffer {name} owner is inaccessible"))
    })?;
    if exporter.as_bytes().len() != length {
        return Err(EncodedDirectUnsupportedError::new_err(format!(
            "encoded buffer {name} does not cover its complete bytes exporter",
        )));
    }
    Ok(RetainedDirectBuffer {
        exporter: PyBackedBytes::from(exporter),
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

type BorrowedDirectBuffer<'py> = (&'static str, Bound<'py, PyAny>, Bound<'py, PyBytes>, usize);

fn retain_direct_bytes_exporters<'py>(
    borrowed: Vec<BorrowedDirectBuffer<'py>>,
) -> PyResult<Vec<RetainedDirectBuffer>> {
    let mut retained = Vec::new();
    retained
        .try_reserve_exact(BUFFER_COUNT)
        .map_err(|_| PyMemoryError::new_err("encoded buffer-retention allocation failed"))?;
    if borrowed
        .iter()
        .all(|(_, _, exporter, length)| exporter.as_bytes().len() == *length)
    {
        for (_, _, exporter, length) in borrowed {
            retained.push(RetainedDirectBuffer {
                exporter: PyBackedBytes::from(exporter),
                start: 0,
                end: length,
            });
        }
        return Ok(retained);
    }

    let Some((_, _, first_exporter, _)) = borrowed.first() else {
        return Err(encoded_buffer_error("encoded buffer set is empty"));
    };
    if borrowed
        .iter()
        .any(|(_, _, exporter, _)| !exporter.is(first_exporter))
    {
        let name = borrowed
            .iter()
            .find(|(_, _, exporter, length)| exporter.as_bytes().len() != *length)
            .map(|(name, _, _, _)| *name)
            .unwrap_or("unknown");
        return Err(EncodedDirectUnsupportedError::new_err(format!(
            "encoded buffer {name} does not cover its complete bytes exporter",
        )));
    }

    let total_length = borrowed
        .iter()
        .try_fold(0_usize, |total, (_, _, _, length)| {
            total
                .checked_add(*length)
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
    for (name, buffer, exporter, length) in borrowed {
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
            exporter: PyBackedBytes::from(exporter),
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
