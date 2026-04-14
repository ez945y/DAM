//! MetricBus — per-guard latency / score metrics store.
//!
//! Three tiers of aggregation, all written by the control loop:
//!
//!   guards   — per-guard execution time history (ms).
//!   stages   — pipeline-stage timing: "source", "policy", "guards", "sink", "total".
//!   layers   — per-cycle sum of guard latencies grouped by GuardLayer id (u8).
//!
//! Typical call sequence each cycle:
//!   1. push_guard(name, layer, ms)  — once per guard executed
//!   2. push_stage("source", ms)     — once per pipeline stage
//!      push_stage("policy", ms)
//!      push_stage("guards", ms)
//!      push_stage("sink",   ms)
//!      push_stage("total",  ms)
//!   3. commit_cycle()               — atomically moves layer accumulators to history
//!   4. snapshot()                   — read latest values for telemetry broadcast

use parking_lot::Mutex;
use std::collections::{HashMap, VecDeque};

const HISTORY_DEPTH: usize = 64;

// ── Internal metric store (reused for guards and stages) ───────────────────

struct RollingMetric {
    latest: f64,
    history: VecDeque<f64>,
}

impl RollingMetric {
    fn new(value: f64) -> Self {
        let mut history = VecDeque::with_capacity(HISTORY_DEPTH);
        history.push_back(value);
        RollingMetric {
            latest: value,
            history,
        }
    }

    fn push(&mut self, value: f64) {
        if self.history.len() >= HISTORY_DEPTH {
            self.history.pop_front();
        }
        self.history.push_back(value);
        self.latest = value;
    }

    fn mean(&self) -> f64 {
        if self.history.is_empty() {
            return 0.0;
        }
        self.history.iter().sum::<f64>() / self.history.len() as f64
    }

    fn max(&self) -> f64 {
        self.history
            .iter()
            .cloned()
            .fold(f64::NEG_INFINITY, f64::max)
    }
}

// ── Public snapshot type ───────────────────────────────────────────────────

/// A point-in-time snapshot of all MetricBus aggregates for one cycle.
/// Returned by [`MetricBus::snapshot`] and consumed by the telemetry layer.
pub struct PerfSnapshot {
    /// Pipeline-stage latencies (ms): "source", "policy", "guards", "sink", "total".
    pub stages: HashMap<String, f64>,
    /// Per-layer guard latency sums (ms) from the last committed cycle.
    /// Key is the raw `GuardLayer` integer (e.g. 0 for L0, 2 for L2).
    pub layers: HashMap<u8, f64>,
    /// Per-guard latest latency (ms).
    pub guards: HashMap<String, f64>,
}

// ── MetricBus ──────────────────────────────────────────────────────────────

pub struct MetricBus {
    /// Per-guard rolling history.
    guards: Mutex<HashMap<String, RollingMetric>>,
    /// Pipeline-stage rolling history.
    stages: Mutex<HashMap<String, RollingMetric>>,
    /// Mutable accumulator for the *current* cycle's per-layer guard sum.
    /// Reset on each `commit_cycle()` call.
    layer_acc: Mutex<HashMap<u8, f64>>,
    /// Committed per-layer history (one entry per cycle).
    layer_hist: Mutex<HashMap<u8, VecDeque<f64>>>,
}

impl MetricBus {
    pub fn new() -> Self {
        MetricBus {
            guards: Mutex::new(HashMap::new()),
            stages: Mutex::new(HashMap::new()),
            layer_acc: Mutex::new(HashMap::new()),
            layer_hist: Mutex::new(HashMap::new()),
        }
    }

    // ── Guard metrics ──────────────────────────────────────────────────────

    /// Record a guard latency value without layer information (backward compat).
    /// Prefer [`push_guard`] for new call-sites.
    pub fn push(&self, guard_name: &str, value_ms: f64) {
        let mut g = self.guards.lock();
        g.entry(guard_name.to_string())
            .and_modify(|e| e.push(value_ms))
            .or_insert_with(|| RollingMetric::new(value_ms));
    }

    /// Record a guard latency value and accumulate into the per-layer sum for
    /// the current cycle.  `layer` is the raw `GuardLayer` integer value.
    pub fn push_guard(&self, guard_name: &str, layer: u8, value_ms: f64) {
        // Update per-guard rolling history.
        {
            let mut g = self.guards.lock();
            g.entry(guard_name.to_string())
                .and_modify(|e| e.push(value_ms))
                .or_insert_with(|| RollingMetric::new(value_ms));
        }
        // Accumulate into the current cycle's layer sum.
        {
            let mut acc = self.layer_acc.lock();
            *acc.entry(layer).or_insert(0.0) += value_ms;
        }
    }

    // ── Pipeline stage metrics ─────────────────────────────────────────────

    /// Record a pipeline-stage latency (ms).  Typical stage names:
    /// `"source"`, `"policy"`, `"guards"`, `"sink"`, `"total"`.
    pub fn push_stage(&self, stage: &str, value_ms: f64) {
        let mut s = self.stages.lock();
        s.entry(stage.to_string())
            .and_modify(|e| e.push(value_ms))
            .or_insert_with(|| RollingMetric::new(value_ms));
    }

    // ── Cycle boundary ─────────────────────────────────────────────────────

    /// Commit the current cycle's layer accumulators to history and reset them
    /// to zero.  Must be called exactly once per cycle, after all `push_guard`
    /// and `push_stage` calls have been made for that cycle.
    pub fn commit_cycle(&self) {
        let mut acc = self.layer_acc.lock();
        let mut hist = self.layer_hist.lock();
        for (layer, &sum) in acc.iter() {
            let h = hist
                .entry(*layer)
                .or_insert_with(|| VecDeque::with_capacity(HISTORY_DEPTH));
            if h.len() >= HISTORY_DEPTH {
                h.pop_front();
            }
            h.push_back(sum);
        }
        // Reset all accumulators for the next cycle.
        for v in acc.values_mut() {
            *v = 0.0;
        }
    }

    // ── Snapshot ───────────────────────────────────────────────────────────

    /// Return a point-in-time snapshot of all aggregated metrics.
    /// Always reads the last *committed* layer values (not mid-cycle partials).
    pub fn snapshot(&self) -> PerfSnapshot {
        let stages: HashMap<String, f64> = self
            .stages
            .lock()
            .iter()
            .map(|(k, v)| (k.clone(), v.latest))
            .collect();

        let layers: HashMap<u8, f64> = self
            .layer_hist
            .lock()
            .iter()
            .filter_map(|(k, h)| h.back().map(|&v| (*k, v)))
            .collect();

        let guards: HashMap<String, f64> = self
            .guards
            .lock()
            .iter()
            .map(|(k, v)| (k.clone(), v.latest))
            .collect();

        PerfSnapshot {
            stages,
            layers,
            guards,
        }
    }

    // ── Legacy read API (unchanged) ────────────────────────────────────────

    pub fn latest(&self, guard_name: &str) -> Option<f64> {
        self.guards.lock().get(guard_name).map(|e| e.latest)
    }

    pub fn all_latest(&self) -> HashMap<String, f64> {
        self.guards
            .lock()
            .iter()
            .map(|(k, v)| (k.clone(), v.latest))
            .collect()
    }

    pub fn mean(&self, guard_name: &str) -> Option<f64> {
        self.guards.lock().get(guard_name).map(|e| e.mean())
    }

    pub fn max(&self, guard_name: &str) -> Option<f64> {
        self.guards.lock().get(guard_name).map(|e| e.max())
    }

    pub fn guard_names(&self) -> Vec<String> {
        self.guards.lock().keys().cloned().collect()
    }

    pub fn clear(&self) {
        self.guards.lock().clear();
        self.stages.lock().clear();
        self.layer_acc.lock().clear();
        self.layer_hist.lock().clear();
    }
}

impl Default for MetricBus {
    fn default() -> Self {
        Self::new()
    }
}

// ── Tests ──────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn new_bus_returns_none_for_unknown_guard() {
        let bus = MetricBus::new();
        assert!(bus.latest("missing_guard").is_none());
    }

    #[test]
    fn push_and_latest_backward_compat() {
        let bus = MetricBus::new();
        bus.push("motion", 0.5);
        bus.push("motion", 1.2);
        assert!((bus.latest("motion").unwrap() - 1.2).abs() < 1e-9);
    }

    #[test]
    fn push_guard_updates_per_guard_and_layer_acc() {
        let bus = MetricBus::new();
        bus.push_guard("joint_limit", 2, 1.5);
        bus.push_guard("workspace", 2, 2.0);
        bus.push_guard("ood", 0, 3.0);

        assert!((bus.latest("joint_limit").unwrap() - 1.5).abs() < 1e-9);
        assert!((bus.latest("workspace").unwrap() - 2.0).abs() < 1e-9);

        // Before commit, layer_hist should be empty.
        let snap = bus.snapshot();
        assert!(snap.layers.is_empty());

        bus.commit_cycle();

        let snap = bus.snapshot();
        // L2 sum = 1.5 + 2.0 = 3.5
        assert!((snap.layers[&2] - 3.5).abs() < 1e-9);
        // L0 sum = 3.0
        assert!((snap.layers[&0] - 3.0).abs() < 1e-9);
    }

    #[test]
    fn commit_cycle_resets_accumulator() {
        let bus = MetricBus::new();
        bus.push_guard("g", 1, 5.0);
        bus.commit_cycle();
        // Second cycle: no guards pushed → layer acc should be 0 after commit.
        bus.commit_cycle();
        let snap = bus.snapshot();
        // L1 history: [5.0, 0.0]; latest is 0.0
        assert!((snap.layers[&1] - 0.0).abs() < 1e-9);
    }

    #[test]
    fn push_stage_and_snapshot() {
        let bus = MetricBus::new();
        bus.push_stage("source", 1.1);
        bus.push_stage("policy", 3.2);
        bus.push_stage("guards", 5.8);
        bus.push_stage("sink", 0.7);
        bus.push_stage("total", 10.8);

        let snap = bus.snapshot();
        assert!((snap.stages["source"] - 1.1).abs() < 1e-9);
        assert!((snap.stages["total"] - 10.8).abs() < 1e-9);
    }

    #[test]
    fn all_latest_returns_all_guards() {
        let bus = MetricBus::new();
        bus.push("guard_a", 1.0);
        bus.push("guard_b", 2.0);
        let all = bus.all_latest();
        assert_eq!(all.len(), 2);
        assert!((all["guard_a"] - 1.0).abs() < 1e-9);
        assert!((all["guard_b"] - 2.0).abs() < 1e-9);
    }

    #[test]
    fn mean_over_history() {
        let bus = MetricBus::new();
        for i in 1..=4 {
            bus.push("latency", i as f64);
        }
        let mean = bus.mean("latency").unwrap();
        assert!((mean - 2.5).abs() < 1e-9);
    }

    #[test]
    fn max_over_history() {
        let bus = MetricBus::new();
        bus.push("latency", 3.0);
        bus.push("latency", 1.0);
        bus.push("latency", 4.0);
        assert!((bus.max("latency").unwrap() - 4.0).abs() < 1e-9);
    }

    #[test]
    fn history_cap_evicts_oldest() {
        let bus = MetricBus::new();
        for i in 0..=(super::HISTORY_DEPTH as i64) {
            bus.push("g", i as f64);
        }
        let expected: f64 =
            (1..=(super::HISTORY_DEPTH as i64)).sum::<i64>() as f64 / super::HISTORY_DEPTH as f64;
        let got = bus.mean("g").unwrap();
        assert!(
            (got - expected).abs() < 1e-6,
            "expected mean ~{expected}, got {got}"
        );
    }

    #[test]
    fn clear_removes_all_data() {
        let bus = MetricBus::new();
        bus.push_guard("g", 1, 42.0);
        bus.push_stage("source", 1.0);
        bus.commit_cycle();
        bus.clear();
        assert!(bus.latest("g").is_none());
        assert!(bus.all_latest().is_empty());
        let snap = bus.snapshot();
        assert!(snap.stages.is_empty());
        assert!(snap.layers.is_empty());
        assert!(snap.guards.is_empty());
    }

    #[test]
    fn guard_names_returns_all() {
        let bus = MetricBus::new();
        bus.push("a", 1.0);
        bus.push("b", 2.0);
        let mut names = bus.guard_names();
        names.sort();
        assert_eq!(names, vec!["a", "b"]);
    }

    #[test]
    fn snapshot_guards_reflect_push_guard() {
        let bus = MetricBus::new();
        bus.push_guard("velocity_guard", 2, 0.8);
        bus.push_guard("ood_guard", 0, 2.1);
        let snap = bus.snapshot();
        assert!((snap.guards["velocity_guard"] - 0.8).abs() < 1e-9);
        assert!((snap.guards["ood_guard"] - 2.1).abs() < 1e-9);
    }
}
