//! Bounded native kernels for the optional Python accelerator.
//!
//! The established engine receives owned edge batches from Python and applies
//! exact multiplicity/order policy.  P7 additionally owns a deliberately narrow
//! structural-columns v1 compiler for a small family of unannotated named class
//! and object-property axioms.  That private compiler retains the public view
//! and immutable `bytes` exporters, validates the whole slice, and borrows them
//! only during a GIL-released call.  The advertised feature ledger remains
//! unchanged until the complete compiler and acceptance matrix exist.

#![forbid(unsafe_code)]

mod encoded_direct;

use std::collections::HashSet;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::atomic::{AtomicU8, Ordering};

use encoded_direct::{
    compile_direct, DirectColumns, KernelError, BUFFER_COUNT, BUFFER_NAMES, STATE_CANCELLED,
    STATE_FAILED, STATE_FINISHED, STATE_IDLE, STATE_RUNNING,
};
use pyo3::create_exception;
use pyo3::exceptions::{PyMemoryError, PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::pybacked::PyBackedBytes;
use pyo3::types::{PyBytes, PyInt, PyMapping, PyMemoryView, PyTuple};

const NATIVE_API_VERSION: u32 = 1;
const ENCODED_DIRECT_KERNEL_VERSION: u32 = 4;
const ENCODED_SCHEMA_NAME: &str = "pyowl-core/structural-columns";
const ENCODED_SCHEMA_VERSION: usize = 1;
const ENCODED_MODEL_SCHEMA: usize = 1;
const DIRECT_SEGMENT: usize = 1;
const POSTINGS_ALL: usize = 0;
const ENCODED_DESCRIPTOR_SHA256: [u8; 32] = [
    0x9a, 0xd2, 0x9d, 0xb6, 0xa7, 0xe6, 0x16, 0xf6, 0x5c, 0xea, 0x29, 0x57, 0xbc, 0x5b, 0xa8, 0xd1,
    0xf9, 0xb9, 0x9e, 0xf0, 0xeb, 0x1f, 0xe1, 0x43, 0x2c, 0x09, 0xbe, 0x25, 0x78, 0x62, 0x67, 0xb5,
];

create_exception!(_native, EncodedDirectUnsupportedError, PyValueError);
create_exception!(_native, EncodedDirectBufferError, PyValueError);
create_exception!(_native, EncodedDirectCancelledError, PyRuntimeError);
create_exception!(_native, EncodedDirectReferenceError, PyValueError);

type EdgeTuple = (String, String, String);
type EncodedDirectBatch = (Vec<EdgeTuple>, Py<PyTuple>);

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
    buffers: Vec<PyBackedBytes>,
    state: AtomicU8,
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
}

#[pymethods]
impl EncodedDirectCompiler {
    #[new]
    fn new(
        encoded_view: &Bound<'_, PyAny>,
        expected_owner: &Bound<'_, PyAny>,
        descriptor_sha256: &Bound<'_, PyAny>,
    ) -> PyResult<Self> {
        let buffers = retained_direct_buffers(encoded_view, expected_owner, descriptor_sha256)?;
        Ok(Self {
            _encoded_view: encoded_view.clone().unbind(),
            _owner: expected_owner.clone().unbind(),
            buffers,
            state: AtomicU8::new(STATE_IDLE),
        })
    }

    /// Compile the complete supported view into one caller-bounded coarse batch.
    #[pyo3(signature = (
        bidirectional,
        max_edges,
        max_iri_bytes,
        asserted_taxonomy_only=false,
        only_taxonomy=false,
    ))]
    fn compile_batch(
        &self,
        py: Python<'_>,
        bidirectional: bool,
        max_edges: usize,
        max_iri_bytes: usize,
        asserted_taxonomy_only: bool,
        only_taxonomy: bool,
    ) -> PyResult<EncodedDirectBatch> {
        if max_edges == 0 {
            return Err(PyValueError::new_err("max_edges must be positive"));
        }
        if max_iri_bytes == 0 {
            return Err(PyValueError::new_err("max_iri_bytes must be positive"));
        }
        self.begin()?;
        let slices: [&[u8]; BUFFER_COUNT] =
            std::array::from_fn(|index| self.buffers[index].as_ref());
        let columns = DirectColumns::from_ordered(slices);
        let result = guarded(|| {
            py.detach(|| {
                compile_direct(
                    columns,
                    bidirectional,
                    asserted_taxonomy_only,
                    only_taxonomy,
                    max_edges,
                    max_iri_bytes,
                    &self.state,
                )
            })
            .map_err(kernel_error)
            .and_then(|(edges, stats)| {
                let mut output = Vec::new();
                output.try_reserve_exact(edges.len()).map_err(|_| {
                    PyMemoryError::new_err("encoded native tuple-batch allocation failed")
                })?;
                output.extend(
                    edges
                        .into_iter()
                        .map(|edge| (edge.source, edge.relation, edge.destination)),
                );
                let statistics = PyTuple::new(
                    py,
                    [
                        stats.roots,
                        stats.nodes,
                        stats.declarations,
                        stats.subclasses,
                        stats.restriction_subclasses,
                        stats.equivalents,
                        stats.class_assertions,
                        stats.object_property_assertions,
                        stats.negative_object_property_assertions,
                        stats.skipped_axioms,
                        stats.object_property_domains,
                        stats.object_property_ranges,
                        stats.domain_range_edges,
                        stats.edges,
                        stats.buffer_bytes,
                    ],
                )?
                .unbind();
                Ok((output, statistics))
            })
        });
        self.finish_result(result)
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

fn retained_direct_buffers(
    encoded_view: &Bound<'_, PyAny>,
    expected_owner: &Bound<'_, PyAny>,
    descriptor_sha256: &Bound<'_, PyAny>,
) -> PyResult<Vec<PyBackedBytes>> {
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

    validate_direct_segment(encoded_view, expected_owner)?;

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
    let mut retained = Vec::new();
    retained
        .try_reserve_exact(BUFFER_COUNT)
        .map_err(|_| PyMemoryError::new_err("encoded buffer-retention allocation failed"))?;
    for name in BUFFER_NAMES {
        let buffer = mapping
            .get_item(name)
            .map_err(|_| encoded_buffer_error(format!("encoded view is missing buffer {name}")))?;
        retained.push(retained_bytes_exporter(&buffer, name)?);
    }
    Ok(retained)
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

fn retained_bytes_exporter(buffer: &Bound<'_, PyAny>, name: &str) -> PyResult<PyBackedBytes> {
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
    Ok(PyBackedBytes::from(exporter))
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
}
