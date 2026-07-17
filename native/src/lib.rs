//! Bounded-batch edge policy engine for the optional Python accelerator.
//!
//! The crate deliberately has no OWL types. Python owns semantic compilation;
//! this engine receives owned edge batches, applies exact multiplicity/order
//! policy, and never stores a Python object or a borrowed ontology view.

#![forbid(unsafe_code)]

use std::collections::HashSet;
use std::panic::{catch_unwind, AssertUnwindSafe};

use pyo3::exceptions::{PyMemoryError, PyRuntimeError, PyValueError};
use pyo3::prelude::*;

const NATIVE_API_VERSION: u32 = 1;

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

#[pymodule(gil_used = true)]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add("__version__", env!("CARGO_PKG_VERSION"))?;
    module.add("NATIVE_API_VERSION", NATIVE_API_VERSION)?;
    module.add("FEATURES", ("abi3-py310", "bounded-batches"))?;
    module.add_class::<EdgeBatchProcessor>()?;
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
