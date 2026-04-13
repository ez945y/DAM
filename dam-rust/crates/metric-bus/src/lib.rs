//! MetricBus — per-guard latency / score metrics store.
//!
//! Each guard writes its per-cycle execution time (µs) and/or decision score
//! into the bus.  The control loop reads these for dashboard display and for
//! RiskController input.  Two storage tiers:
//!
//!   latest   — most recent value per guard (O(1) read).
//!   history  — last HISTORY_DEPTH values per guard (for rolling averages).

use parking_lot::Mutex;
use std::collections::{HashMap, VecDeque};

const HISTORY_DEPTH: usize = 64;

struct GuardMetric {
    latest: f64,
    history: VecDeque<f64>,
}

impl GuardMetric {
    fn new(value: f64) -> Self {
        let mut history = VecDeque::with_capacity(HISTORY_DEPTH);
        history.push_back(value);
        GuardMetric {
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

pub struct MetricBus {
    metrics: Mutex<HashMap<String, GuardMetric>>,
}

impl MetricBus {
    pub fn new() -> Self {
        MetricBus {
            metrics: Mutex::new(HashMap::new()),
        }
    }

    /// Record a value for `guard_name`.  Thread-safe.
    pub fn push(&self, guard_name: &str, value: f64) {
        let mut m = self.metrics.lock();
        m.entry(guard_name.to_string())
            .and_modify(|e| e.push(value))
            .or_insert_with(|| GuardMetric::new(value));
    }

    /// Return the most recently pushed value, or `None` if the guard has no data.
    pub fn latest(&self, guard_name: &str) -> Option<f64> {
        self.metrics.lock().get(guard_name).map(|e| e.latest)
    }

    /// Return a snapshot of all guards' latest values.
    pub fn all_latest(&self) -> HashMap<String, f64> {
        self.metrics
            .lock()
            .iter()
            .map(|(k, v)| (k.clone(), v.latest))
            .collect()
    }

    /// Return the rolling mean of the last HISTORY_DEPTH values for `guard_name`.
    pub fn mean(&self, guard_name: &str) -> Option<f64> {
        self.metrics.lock().get(guard_name).map(|e| e.mean())
    }

    /// Return the maximum value seen in the history window for `guard_name`.
    pub fn max(&self, guard_name: &str) -> Option<f64> {
        self.metrics.lock().get(guard_name).map(|e| e.max())
    }

    /// Return all guard names that have at least one data point.
    pub fn guard_names(&self) -> Vec<String> {
        self.metrics.lock().keys().cloned().collect()
    }

    /// Clear all metrics (e.g. between tasks).
    pub fn clear(&self) {
        self.metrics.lock().clear();
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
    fn push_and_latest() {
        let bus = MetricBus::new();
        bus.push("motion", 0.5);
        bus.push("motion", 1.2);
        assert!((bus.latest("motion").unwrap() - 1.2).abs() < 1e-9);
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
        assert!((mean - 2.5).abs() < 1e-9); // (1+2+3+4)/4 = 2.5
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
        // Push HISTORY_DEPTH + 1 values; mean should reflect only last HISTORY_DEPTH
        for i in 0..=(super::HISTORY_DEPTH as i64) {
            bus.push("g", i as f64);
        }
        // The oldest value (0) should be evicted; mean of 1..=HISTORY_DEPTH
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
        bus.push("g", 42.0);
        bus.clear();
        assert!(bus.latest("g").is_none());
        assert!(bus.all_latest().is_empty());
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
}
