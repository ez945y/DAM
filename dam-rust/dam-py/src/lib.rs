//! dam_rs — PyO3 Python bindings for the DAM Rust data-plane components.
// PyO3 macros generate From<PyErr> for PyErr conversions in PyResult-returning methods.
#![allow(clippy::useless_conversion)]
//!
//! Exposes five types to Python:
//!   ObservationBus  — ring buffer for sensor snapshots (pickling handled here)
//!   WatchdogTimer   — deadline watchdog; fires outside Python GIL
//!   RiskController  — windowed risk aggregation
//!   MetricBus       — per-guard latency / score history
//!   ActionBus       — SPSC queue for validated actions
//!
//! Build:
//!   cd dam-rust/dam-py
//!   maturin develop --release     # install editable into active venv
//!
//! The pure-Python fallback in dam/bus/fallback.py provides an identical API
//! and is used automatically when this extension is not compiled.

use pyo3::prelude::*;
use pyo3::types::PyBytes;

use action_bus::ActionBus as RustActionBus;
use metric_bus::MetricBus as RustMetricBus;
use observation_bus::ObservationBus as RustObsBus;
use risk_controller::RiskController as RustRiskController;
use watchdog::WatchdogTimer as RustWatchdog;

// ── Helpers ────────────────────────────────────────────────────────────────

/// Pickle-serialise a Python object to bytes.
fn pickle_dumps<'py>(py: Python<'py>, obj: &Bound<'py, PyAny>) -> PyResult<Vec<u8>> {
    let pickle = py.import_bound("pickle")?;
    let dumped = pickle.call_method1("dumps", (obj,))?;
    dumped.extract::<Vec<u8>>()
}

/// Pickle-deserialise bytes to a Python object.
fn pickle_loads(py: Python<'_>, data: &[u8]) -> PyResult<PyObject> {
    let pickle = py.import_bound("pickle")?;
    let obj = pickle.call_method1("loads", (PyBytes::new_bound(py, data),))?;
    Ok(obj.unbind())
}

// ── ObservationBus ─────────────────────────────────────────────────────────

/// Bounded ring buffer for sensor observations.
///
/// Observations are serialised with ``pickle`` before being stored in the
/// Rust ring buffer, and deserialised on read.  This keeps the hot path
/// lock-free while remaining compatible with arbitrary Python objects.
///
/// Constructor: ``ObservationBus(capacity: int)``
#[pyclass]
struct ObservationBus {
    inner: RustObsBus,
}

#[pymethods]
impl ObservationBus {
    #[new]
    fn new(capacity: usize) -> Self {
        ObservationBus {
            inner: RustObsBus::new(capacity),
        }
    }

    /// Write one observation (any Python object) to the ring buffer.
    fn write(&self, py: Python<'_>, data: &Bound<'_, PyAny>) -> PyResult<()> {
        let bytes = pickle_dumps(py, data)?;
        self.inner.write(bytes);
        Ok(())
    }

    /// Return the most recently written observation, or None if the buffer is empty.
    fn read_latest(&self, py: Python<'_>) -> PyResult<Option<PyObject>> {
        match self.inner.read_latest() {
            Some(bytes) => Ok(Some(pickle_loads(py, &bytes)?)),
            None => Ok(None),
        }
    }

    /// Return the last ``n`` observations in chronological order (oldest first).
    fn read_window(&self, py: Python<'_>, n: usize) -> PyResult<Vec<PyObject>> {
        self.inner
            .read_window(n)
            .iter()
            .map(|b| pickle_loads(py, b))
            .collect()
    }

    fn len(&self) -> usize {
        self.inner.len()
    }

    fn is_empty(&self) -> bool {
        self.inner.is_empty()
    }

    #[getter]
    fn capacity(&self) -> usize {
        self.inner.capacity()
    }

    fn __len__(&self) -> usize {
        self.inner.len()
    }

    fn __repr__(&self) -> String {
        format!(
            "ObservationBus(len={}/{})",
            self.inner.len(),
            self.inner.capacity()
        )
    }
}

// ── WatchdogTimer ──────────────────────────────────────────────────────────

/// Deadline watchdog running on a dedicated OS thread (outside the Python GIL).
///
/// If ``ping()`` is not called within ``deadline_ms`` after ``arm()``, the
/// emergency flag is set atomically.  The Python control loop must call
/// ``ping()`` once per cycle and check ``is_emergency()`` after ``step()``.
///
/// Constructor: ``WatchdogTimer(deadline_ms: float)``
#[pyclass]
struct WatchdogTimer {
    inner: RustWatchdog,
}

#[pymethods]
impl WatchdogTimer {
    #[new]
    fn new(deadline_ms: f64) -> Self {
        WatchdogTimer {
            inner: RustWatchdog::new(deadline_ms),
        }
    }

    /// Arm (or re-arm) the watchdog.  Clears any previous emergency flag.
    fn arm(&self) {
        self.inner.arm();
    }

    /// Reset the deadline timer.  Must be called once per control cycle.
    fn ping(&self) {
        self.inner.ping();
    }

    /// Disarm without triggering emergency.
    fn disarm(&self) {
        self.inner.disarm();
    }

    /// True if the deadline was exceeded since the last arm()/clear_emergency().
    fn is_emergency(&self) -> bool {
        self.inner.is_emergency()
    }

    /// Clear the emergency flag (after a safe stop has been performed).
    fn clear_emergency(&self) {
        self.inner.clear_emergency();
    }

    /// Configured deadline in milliseconds.
    #[getter]
    fn deadline_ms(&self) -> f64 {
        self.inner.deadline_ms()
    }

    /// Milliseconds elapsed since the last ping (for diagnostics).
    fn elapsed_since_ping_ms(&self) -> f64 {
        self.inner.elapsed_since_ping_ms()
    }

    fn __repr__(&self) -> String {
        format!(
            "WatchdogTimer(deadline_ms={}, emergency={})",
            self.inner.deadline_ms(),
            self.inner.is_emergency(),
        )
    }
}

// ── RiskController ─────────────────────────────────────────────────────────

/// Windowed risk aggregation.
///
/// Maintains a sliding window of cycle outcomes and computes risk level:
///   0 = NORMAL, 1 = ELEVATED, 2 = CRITICAL, 3 = EMERGENCY.
///
/// Constructor: ``RiskController(window_samples, clamp_threshold, reject_threshold)``
#[pyclass]
struct RiskController {
    inner: RustRiskController,
}

#[pymethods]
impl RiskController {
    #[new]
    fn new(window_samples: usize, clamp_threshold: usize, reject_threshold: usize) -> Self {
        RiskController {
            inner: RustRiskController::new(window_samples, clamp_threshold, reject_threshold),
        }
    }

    fn record(&self, was_clamped: bool, was_rejected: bool) {
        self.inner.record(was_clamped, was_rejected);
    }

    fn risk_level(&self) -> u8 {
        self.inner.risk_level()
    }

    fn is_emergency(&self) -> bool {
        self.inner.is_emergency()
    }

    fn trigger_emergency(&self) {
        self.inner.trigger_emergency();
    }

    fn clear_emergency(&self) {
        self.inner.clear_emergency();
    }

    fn stats(&self, py: Python<'_>) -> PyResult<PyObject> {
        let s = self.inner.stats();
        let d = pyo3::types::PyDict::new_bound(py);
        d.set_item("window_size", s.window_size)?;
        d.set_item("clamp_count", s.clamp_count)?;
        d.set_item("reject_count", s.reject_count)?;
        d.set_item("clamp_threshold", s.clamp_threshold)?;
        d.set_item("reject_threshold", s.reject_threshold)?;
        d.set_item("risk_level", s.risk_level)?;
        d.set_item("is_emergency", s.is_emergency)?;
        Ok(d.unbind().into())
    }

    fn __repr__(&self) -> String {
        format!(
            "RiskController(risk_level={}, emergency={})",
            self.inner.risk_level(),
            self.inner.is_emergency(),
        )
    }
}

// ── MetricBus ──────────────────────────────────────────────────────────────

/// Per-guard latency / score metrics store with rolling history.
///
/// Constructor: ``MetricBus()``
#[pyclass]
struct MetricBus {
    inner: RustMetricBus,
}

#[pymethods]
impl MetricBus {
    #[new]
    fn new() -> Self {
        MetricBus {
            inner: RustMetricBus::new(),
        }
    }

    fn push(&self, guard_name: &str, value: f64) {
        self.inner.push(guard_name, value);
    }

    fn latest(&self, guard_name: &str) -> Option<f64> {
        self.inner.latest(guard_name)
    }

    fn mean(&self, guard_name: &str) -> Option<f64> {
        self.inner.mean(guard_name)
    }

    fn max(&self, guard_name: &str) -> Option<f64> {
        self.inner.max(guard_name)
    }

    fn all_latest(&self, py: Python<'_>) -> PyResult<PyObject> {
        let d = pyo3::types::PyDict::new_bound(py);
        for (k, v) in self.inner.all_latest() {
            d.set_item(k, v)?;
        }
        Ok(d.unbind().into())
    }

    fn guard_names(&self) -> Vec<String> {
        self.inner.guard_names()
    }

    fn clear(&self) {
        self.inner.clear();
    }

    fn __repr__(&self) -> String {
        format!("MetricBus(guards={})", self.inner.guard_names().len())
    }
}

// ── ActionBus ──────────────────────────────────────────────────────────────

/// SPSC queue for validated actions (latest-wins with capacity=1).
///
/// Constructor: ``ActionBus(capacity: int)``
#[pyclass]
struct ActionBus {
    inner: RustActionBus,
}

#[pymethods]
impl ActionBus {
    #[new]
    fn new(capacity: usize) -> Self {
        ActionBus {
            inner: RustActionBus::new(capacity),
        }
    }

    fn write(&self, py: Python<'_>, data: &Bound<'_, PyAny>) -> PyResult<()> {
        let bytes = pickle_dumps(py, data)?;
        self.inner.write(bytes);
        Ok(())
    }

    fn read(&self, py: Python<'_>) -> PyResult<Option<PyObject>> {
        match self.inner.read() {
            Some(bytes) => Ok(Some(pickle_loads(py, &bytes)?)),
            None => Ok(None),
        }
    }

    fn is_empty(&self) -> bool {
        self.inner.is_empty()
    }

    fn __repr__(&self) -> String {
        format!("ActionBus(empty={})", self.inner.is_empty())
    }
}

// ── Module registration ────────────────────────────────────────────────────

#[pymodule]
fn dam_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<ObservationBus>()?;
    m.add_class::<WatchdogTimer>()?;
    m.add_class::<RiskController>()?;
    m.add_class::<MetricBus>()?;
    m.add_class::<ActionBus>()?;
    Ok(())
}
