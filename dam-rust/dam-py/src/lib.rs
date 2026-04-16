//! dam_rs — PyO3 Python bindings for the DAM Rust data-plane components.
// PyO3 macros generate From<PyErr> for PyErr conversions in PyResult-returning methods.
#![allow(clippy::useless_conversion)]
//!
//! Exposes types to Python:
//!   ObservationBus  — ring buffer for sensor snapshots (pickling handled here)
//!   WatchdogTimer   — deadline watchdog; fires outside Python GIL
//!   RiskController  — windowed risk aggregation
//!   MetricBus       — per-guard latency / score history
//!   ActionBus       — SPSC queue for validated actions
//!   SerializerBus   — JSON serialization for cycle records
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
use image_writer::ImageWriter as RustImageWriter;
use mcap_writer::CycleRecordData;
use mcap_writer::McapWriter as RustMcapWriter;
use metric_bus::MetricBus as RustMetricBus;
use observation_bus::ObservationBus as RustObsBus;
use risk_controller::RiskController as RustRiskController;
use serializer_bus::SerializerBus as RustSerializerBus;
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

// ── MetricBus ─────────────────────────────────────────────────────────────

/// Per-guard latency / score metrics store with rolling history.
///
/// Three aggregation tiers:
///   - Per-guard rolling history (push / push_guard)
///   - Pipeline-stage timing (push_stage): "source", "policy", "guards", "sink", "total"
///   - Per-layer guard sums (accumulated via push_guard, committed via commit_cycle)
///
/// Typical call sequence per cycle:
///   1. push_guard(name, layer, ms)  — once per guard
///   2. push_stage("source", ms) … push_stage("total", ms)
///   3. commit_cycle()
///   4. snapshot() → dict for telemetry broadcast
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

    // ── Guard metrics ─────────────────────────────────────────────────────

    /// Record a guard latency (ms) without layer info.  Kept for backward compat;
    /// prefer ``push_guard`` for new call-sites.
    fn push(&self, guard_name: &str, value: f64) {
        self.inner.push(guard_name, value);
    }

    /// Record a guard latency (ms) and accumulate into the per-layer sum for
    /// the current cycle.  ``layer`` is the raw ``GuardLayer`` integer value.
    fn push_guard(&self, guard_name: &str, layer: u8, value_ms: f64) {
        self.inner.push_guard(guard_name, layer, value_ms);
    }

    // ── Pipeline stage metrics ─────────────────────────────────────────────

    /// Record a pipeline-stage latency (ms).
    /// Stage names: ``"source"``, ``"policy"``, ``"guards"``, ``"sink"``, ``"total"``.
    fn push_stage(&self, stage: &str, value_ms: f64) {
        self.inner.push_stage(stage, value_ms);
    }

    // ── Cycle boundary ─────────────────────────────────────────────────────

    /// Atomically commit the current cycle's layer accumulators to history and
    /// reset them to zero.  Call exactly once per cycle after all push_guard /
    /// push_stage calls.
    fn commit_cycle(&self) {
        self.inner.commit_cycle();
    }

    // ── Snapshot ───────────────────────────────────────────────────────────

    /// Return a point-in-time snapshot of all metrics as a nested dict::
    ///
    ///   {
    ///     "stages": {"source": ms, "policy": ms, "guards": ms, "sink": ms, "total": ms},
    ///     "layers": {"L0": ms, "L2": ms, ...},   # only layers with data
    ///     "guards": {"guard_name": ms, ...},
    ///   }
    fn snapshot(&self, py: Python<'_>) -> PyResult<PyObject> {
        let snap = self.inner.snapshot();

        let stages_d = pyo3::types::PyDict::new_bound(py);
        for (k, v) in snap.stages {
            stages_d.set_item(k, v)?;
        }

        // Layer keys are formatted as "L0", "L1", … matching frontend convention.
        let layers_d = pyo3::types::PyDict::new_bound(py);
        for (k, v) in snap.layers {
            layers_d.set_item(format!("L{k}"), v)?;
        }

        let guards_d = pyo3::types::PyDict::new_bound(py);
        for (k, v) in snap.guards {
            guards_d.set_item(k, v)?;
        }

        let d = pyo3::types::PyDict::new_bound(py);
        d.set_item("stages", stages_d)?;
        d.set_item("layers", layers_d)?;
        d.set_item("guards", guards_d)?;
        Ok(d.unbind().into())
    }

    // ── Legacy read API ─────────────────────────────────────────────────────

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

// ── ActionBus ─────────────────────────────────────────────────────────────

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

// ── ImageWriter ────────────────────────────────────────────────────────────

/// Async JPEG encoder for fire-and-forget image encoding (no GIL).
///
/// Accepts raw RGB bytes from Python and encodes to JPEG in background threads.
/// Returns immediately without waiting for encoding to complete.
///
/// Constructor: ``ImageWriter()``
///
/// Methods:
///   - encode_jpeg(data, width, height, quality) -> bytes (synchronous)
///   - submit_async(data, width, height, quality) -> None (fire-and-forget)
///
/// Example (Python):
/// ```python
/// from dam_rs import ImageWriter
/// writer = ImageWriter()
/// # Fire-and-forget: returns immediately, encoding happens on background thread
/// writer.submit_async(frame.tobytes(), 640, 480, 85)
/// ```
#[pyclass]
struct ImageWriter {
    _marker: std::marker::PhantomData<()>,
}

#[pymethods]
impl ImageWriter {
    #[new]
    fn new() -> Self {
        ImageWriter {
            _marker: std::marker::PhantomData,
        }
    }

    /// Encode raw RGB bytes to JPEG synchronously.
    ///
    /// Args:
    ///   data: Raw RGB bytes (width * height * 3 bytes)
    ///   width: Image width in pixels
    ///   height: Image height in pixels
    ///   quality: JPEG quality (1-100, default 85)
    ///
    /// Returns:
    ///   JPEG bytes encoded from the input image
    fn encode_jpeg(&self, data: &[u8], width: u32, height: u32, quality: u8) -> PyResult<Vec<u8>> {
        match RustImageWriter::encode_jpeg(data, width, height, quality) {
            Ok(bytes) => Ok(bytes),
            Err(e) => Err(pyo3::exceptions::PyValueError::new_err(e)),
        }
    }

    /// Submit image for async JPEG encoding (fire-and-forget).
    ///
    /// Returns immediately. Encoding happens on a background thread.
    /// No callback or return value — designed for hot-path Python code.
    ///
    /// Args:
    ///   data: Raw RGB bytes (width * height * 3 bytes)
    ///   width: Image width in pixels
    ///   height: Image height in pixels
    ///   quality: JPEG quality (1-100, default 85)
    fn submit_async(&self, data: Vec<u8>, width: u32, height: u32, quality: u8) {
        RustImageWriter::submit_async(data, width, height, quality, |result| match result {
            Ok(bytes) => log::debug!("ImageWriter: encoded {} bytes async", bytes.len()),
            Err(e) => log::error!("ImageWriter: async encoding failed: {}", e),
        });
    }

    fn __repr__(&self) -> String {
        "ImageWriter()".to_string()
    }
}

// ── McapWriter ─────────────────────────────────────────────────────────────

/// High-performance MCAP writer for DAM cycle records.
///
/// Handles serialization (JSON for metadata, MessagePack for data) and
/// MCAP channel/schema registration. Designed for fire-and-forget from Python.
///
/// Constructor: ``McapWriter()``
///
/// Methods:
///   - start(path: str) -> None  (starts writing to file)
///   - write_cycle(record: dict) -> int  (returns sequence number)
///   - current_sequence() -> int
///
/// Example (Python):
/// ```python
/// from dam_rs import McapWriter
/// writer = McapWriter()
/// writer.start("/tmp/session.mcap")
/// seq = writer.write_cycle(record_dict)  # Returns sequence number
/// ```
#[pyclass]
struct McapWriter {
    inner: RustMcapWriter,
}

#[pymethods]
impl McapWriter {
    #[new]
    fn new() -> PyResult<Self> {
        match RustMcapWriter::new() {
            Ok(inner) => Ok(McapWriter { inner }),
            Err(e) => Err(pyo3::exceptions::PyIOError::new_err(e)),
        }
    }

    /// Start writing to the MCAP file.
    ///
    /// Args:
    ///   path: Path to the MCAP file to create
    fn start(&self, path: &str) -> PyResult<()> {
        match self.inner.start(path) {
            Ok(()) => Ok(()),
            Err(e) => Err(pyo3::exceptions::PyIOError::new_err(e)),
        }
    }

    /// Write a complete cycle to MCAP.
    ///
    /// Args:
    ///   record: Dictionary representation of CycleRecord (as JSON string from Python)
    ///
    /// Returns:
    ///   Sequence number (u64) for ordering verification
    fn write_cycle(&self, record_json: &str) -> PyResult<u64> {
        let cycle_record: CycleRecordData = serde_json::from_str(record_json).map_err(|e| {
            pyo3::exceptions::PyValueError::new_err(format!(
                "Failed to parse CycleRecord JSON: {}",
                e
            ))
        })?;

        match self.inner.write_cycle(cycle_record) {
            Ok(seq) => Ok(seq),
            Err(e) => Err(pyo3::exceptions::PyRuntimeError::new_err(e)),
        }
    }

    /// Get the current sequence counter.
    fn current_sequence(&self) -> u64 {
        self.inner.current_sequence()
    }

    fn __repr__(&self) -> String {
        "McapWriter(async)".to_string()
    }
}

// ── SerializerBus ────────────────────────────────────────────────────────────

/// High-performance JSON serializer for DAM cycle records.
///
/// Serializes a CycleRecord dict to pre-serialized JSON messages (as bytes).
/// No state; each call is independent.
///
/// Constructor: ``SerializerBus()``
///
/// Methods:
///   - serialize_cycle(record_dict: dict) -> dict  (returns topic -> bytes mapping)
///
/// Example (Python):
/// ```python
/// from dam_rs import SerializerBus
/// serializer = SerializerBus()
/// messages = serializer.serialize_cycle(record_dict)
/// # messages = {
/// #     "/dam/cycle": b'{"cycle_id": ...}',
/// #     "/dam/obs": b'{"cycle_id": ...}',
/// #     "/dam/action": b'{"cycle_id": ...}',
/// #     "/dam/L0": [b'...', ...],
/// #     ...
/// # }
/// ```
#[pyclass]
struct SerializerBus;

#[pymethods]
impl SerializerBus {
    #[new]
    fn new() -> Self {
        SerializerBus
    }

    /// Serialize a CycleRecord dict to pre-serialized JSON messages.
    ///
    /// Args:
    ///   record_dict: Dict representation of CycleRecord (from dataclasses.asdict())
    ///
    /// Returns:
    ///   Dict mapping topic name -> bytes (or list of bytes for /dam/L0-L4)
    fn serialize_cycle(
        &self,
        py: Python<'_>,
        record_dict: &Bound<'_, PyAny>,
    ) -> PyResult<PyObject> {
        // Convert Python dict to serde_json::Value
        let json_str = pyo3::types::PyModule::import_bound(py, "json")?
            .call_method1("dumps", (record_dict,))?
            .extract::<String>()?;

        let record_json: serde_json::Value = serde_json::from_str(&json_str).map_err(|e| {
            pyo3::exceptions::PyValueError::new_err(format!(
                "Failed to parse CycleRecord dict: {}",
                e
            ))
        })?;

        // Call Rust serializer
        let messages = RustSerializerBus::serialize_cycle(&record_json)
            .map_err(pyo3::exceptions::PyRuntimeError::new_err)?;

        // Convert result back to Python dict
        let result_dict = pyo3::types::PyDict::new_bound(py);

        for (topic, value) in messages.iter() {
            match value {
                serde_json::Value::String(s) => {
                    // Single message: convert to bytes
                    result_dict.set_item(topic.clone(), PyBytes::new_bound(py, s.as_bytes()))?;
                }
                serde_json::Value::Array(msgs) => {
                    // Multiple messages: convert each to bytes in a list
                    let py_list = pyo3::types::PyList::empty_bound(py);
                    for msg_val in msgs {
                        if let serde_json::Value::String(msg_str) = msg_val {
                            py_list.append(PyBytes::new_bound(py, msg_str.as_bytes()))?;
                        }
                    }
                    result_dict.set_item(topic.clone(), py_list)?;
                }
                _ => {
                    return Err(pyo3::exceptions::PyRuntimeError::new_err(format!(
                        "Unexpected value type for topic {}",
                        topic
                    )));
                }
            }
        }

        Ok(result_dict.unbind().into())
    }

    fn __repr__(&self) -> String {
        "SerializerBus()".to_string()
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
    m.add_class::<ImageWriter>()?;
    m.add_class::<McapWriter>()?;
    m.add_class::<SerializerBus>()?;
    Ok(())
}
