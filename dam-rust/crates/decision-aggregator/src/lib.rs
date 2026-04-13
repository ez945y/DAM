//! DecisionAggregator — collects per-guard decisions on the Rust data plane
//! and reduces them to a single cycle verdict before handing off to the
//! Python control plane's sink.
//!
//! # Architecture
//! ```text
//!  Python guards (L0–L4)
//!       │  push GuardResult via FFI / IPC
//!       ▼
//!  DecisionAggregator (Rust, lock-free MPMC)
//!       │  reduce() → CycleVerdict
//!       ▼
//!  Python Sink  (dispatch ValidatedAction or trigger fallback)
//! ```
//!
//! The aggregator uses a simple priority chain:
//!   FAULT > REJECT > CLAMP > PASS
//!
//! Any FAULT immediately returns FAULT.
//! Any REJECT (no FAULT) returns REJECT.
//! Any CLAMP (no REJECT/FAULT) returns CLAMP.
//! All PASS returns PASS.

use parking_lot::Mutex;
use serde::{Deserialize, Serialize};

/// Individual guard decision — mirrors Python ``GuardDecision`` enum.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "UPPERCASE")]
pub enum Decision {
    Pass,
    Clamp,
    Reject,
    Fault,
}

impl Decision {
    fn priority(self) -> u8 {
        match self {
            Decision::Pass => 0,
            Decision::Clamp => 1,
            Decision::Reject => 2,
            Decision::Fault => 3,
        }
    }
}

/// Result contributed by one guard for one cycle.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct GuardResult {
    pub guard_name: String,
    pub layer: String,
    pub decision: Decision,
    pub reason: Option<String>,
    pub latency_us: u64,
}

/// Aggregated verdict for a complete cycle.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CycleVerdict {
    pub decision: Decision,
    /// Guard that produced the worst decision (None if all PASS).
    pub deciding_guard: Option<String>,
    pub results: Vec<GuardResult>,
    pub total_latency_us: u64,
}

/// Thread-safe accumulator for one control cycle.
///
/// Call ``push()`` from each guard (potentially from different threads),
/// then ``reduce()`` once all guards have reported.
pub struct DecisionAggregator {
    results: Mutex<Vec<GuardResult>>,
}

impl DecisionAggregator {
    pub fn new() -> Self {
        DecisionAggregator {
            results: Mutex::new(Vec::new()),
        }
    }

    /// Push a guard result.  Thread-safe — multiple guards may call concurrently.
    pub fn push(&self, result: GuardResult) {
        self.results.lock().push(result);
    }

    /// Reduce all pushed results into a single ``CycleVerdict`` and clear the buffer.
    ///
    /// Must be called exactly once per cycle, after all guards have pushed.
    pub fn reduce(&self) -> CycleVerdict {
        let mut results = self.results.lock();
        let total_latency_us = results.iter().map(|r| r.latency_us).sum();

        let worst = results.iter().max_by_key(|r| r.decision.priority());

        let (decision, deciding_guard) = match worst {
            Some(r) if r.decision != Decision::Pass => (r.decision, Some(r.guard_name.clone())),
            _ => (Decision::Pass, None),
        };

        CycleVerdict {
            decision,
            deciding_guard,
            results: std::mem::take(&mut *results),
            total_latency_us,
        }
    }

    /// Clear without reducing (e.g., on emergency stop).
    pub fn clear(&self) {
        self.results.lock().clear();
    }

    /// Number of results currently held.
    pub fn len(&self) -> usize {
        self.results.lock().len()
    }

    /// Returns true if no results have been pushed yet.
    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
}

impl Default for DecisionAggregator {
    fn default() -> Self {
        Self::new()
    }
}

// ── Tests ──────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn result(name: &str, d: Decision) -> GuardResult {
        GuardResult {
            guard_name: name.to_owned(),
            layer: "L0".to_owned(),
            decision: d,
            reason: None,
            latency_us: 100,
        }
    }

    #[test]
    fn all_pass_gives_pass() {
        let agg = DecisionAggregator::new();
        agg.push(result("ood", Decision::Pass));
        agg.push(result("motion", Decision::Pass));
        let v = agg.reduce();
        assert_eq!(v.decision, Decision::Pass);
        assert!(v.deciding_guard.is_none());
    }

    #[test]
    fn clamp_beats_pass() {
        let agg = DecisionAggregator::new();
        agg.push(result("motion", Decision::Clamp));
        agg.push(result("ood", Decision::Pass));
        let v = agg.reduce();
        assert_eq!(v.decision, Decision::Clamp);
        assert_eq!(v.deciding_guard, Some("motion".to_owned()));
    }

    #[test]
    fn reject_beats_clamp() {
        let agg = DecisionAggregator::new();
        agg.push(result("motion", Decision::Clamp));
        agg.push(result("ood", Decision::Reject));
        let v = agg.reduce();
        assert_eq!(v.decision, Decision::Reject);
        assert_eq!(v.deciding_guard, Some("ood".to_owned()));
    }

    #[test]
    fn fault_beats_all() {
        let agg = DecisionAggregator::new();
        agg.push(result("hardware", Decision::Fault));
        agg.push(result("ood", Decision::Reject));
        agg.push(result("motion", Decision::Clamp));
        let v = agg.reduce();
        assert_eq!(v.decision, Decision::Fault);
        assert_eq!(v.deciding_guard, Some("hardware".to_owned()));
    }

    #[test]
    fn reduce_clears_buffer() {
        let agg = DecisionAggregator::new();
        agg.push(result("ood", Decision::Pass));
        let v1 = agg.reduce();
        assert_eq!(v1.results.len(), 1);
        // Second reduce with empty buffer
        let v2 = agg.reduce();
        assert_eq!(v2.results.len(), 0);
        assert_eq!(v2.decision, Decision::Pass);
    }

    #[test]
    fn total_latency_is_summed() {
        let agg = DecisionAggregator::new();
        agg.push(GuardResult {
            guard_name: "a".to_owned(),
            layer: "L0".to_owned(),
            decision: Decision::Pass,
            reason: None,
            latency_us: 200,
        });
        agg.push(GuardResult {
            guard_name: "b".to_owned(),
            layer: "L1".to_owned(),
            decision: Decision::Pass,
            reason: None,
            latency_us: 350,
        });
        let v = agg.reduce();
        assert_eq!(v.total_latency_us, 550);
    }
}
