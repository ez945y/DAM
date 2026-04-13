//! RiskController — windowed risk aggregation with atomic emergency flag.
//!
//! Maintains a sliding window of cycle outcomes (clamp / reject flags) and
//! computes a risk level from 0 (NORMAL) to 3 (EMERGENCY).
//!
//! Risk levels
//! -----------
//! 0  NORMAL    — clamps and rejects within acceptable bounds.
//! 1  ELEVATED  — clamp_threshold or reject_threshold exceeded in the window.
//! 2  CRITICAL  — reject_threshold * 3 exceeded in the window.
//! 3  EMERGENCY — trigger_emergency() called, or externally set.

use parking_lot::Mutex;
use std::collections::VecDeque;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

pub struct RiskController {
    clamps: Mutex<VecDeque<bool>>,
    rejects: Mutex<VecDeque<bool>>,
    capacity: usize,
    clamp_threshold: usize,
    reject_threshold: usize,
    emergency: Arc<AtomicBool>,
}

/// Snapshot of current window counts for diagnostics / logging.
pub struct RiskStats {
    pub window_size: usize,
    pub clamp_count: usize,
    pub reject_count: usize,
    pub clamp_threshold: usize,
    pub reject_threshold: usize,
    pub risk_level: u8,
    pub is_emergency: bool,
}

impl RiskController {
    pub fn new(window_samples: usize, clamp_threshold: usize, reject_threshold: usize) -> Self {
        RiskController {
            clamps: Mutex::new(VecDeque::with_capacity(window_samples)),
            rejects: Mutex::new(VecDeque::with_capacity(window_samples)),
            capacity: window_samples,
            clamp_threshold,
            reject_threshold,
            emergency: Arc::new(AtomicBool::new(false)),
        }
    }

    /// Record one cycle's outcome.  Evicts the oldest sample when the window is full.
    pub fn record(&self, was_clamped: bool, was_rejected: bool) {
        {
            let mut c = self.clamps.lock();
            if c.len() >= self.capacity {
                c.pop_front();
            }
            c.push_back(was_clamped);
        }
        {
            let mut r = self.rejects.lock();
            if r.len() >= self.capacity {
                r.pop_front();
            }
            r.push_back(was_rejected);
        }
    }

    /// Compute current risk level: 0=NORMAL, 1=ELEVATED, 2=CRITICAL, 3=EMERGENCY.
    pub fn risk_level(&self) -> u8 {
        if self.emergency.load(Ordering::SeqCst) {
            return 3;
        }
        let rejects = self.rejects.lock().iter().filter(|&&x| x).count();
        let clamps = self.clamps.lock().iter().filter(|&&x| x).count();
        if rejects >= self.reject_threshold * 3 {
            return 2;
        }
        if rejects >= self.reject_threshold {
            return 1;
        }
        if clamps >= self.clamp_threshold {
            return 1;
        }
        0
    }

    /// Return a diagnostic snapshot of the current window state.
    pub fn stats(&self) -> RiskStats {
        let clamp_count = self.clamps.lock().iter().filter(|&&x| x).count();
        let reject_count = self.rejects.lock().iter().filter(|&&x| x).count();
        let window_size = self.clamps.lock().len();
        RiskStats {
            window_size,
            clamp_count,
            reject_count,
            clamp_threshold: self.clamp_threshold,
            reject_threshold: self.reject_threshold,
            risk_level: self.risk_level(),
            is_emergency: self.emergency.load(Ordering::SeqCst),
        }
    }

    pub fn trigger_emergency(&self) {
        self.emergency.store(true, Ordering::SeqCst);
    }

    pub fn clear_emergency(&self) {
        self.emergency.store(false, Ordering::SeqCst);
    }

    pub fn is_emergency(&self) -> bool {
        self.emergency.load(Ordering::SeqCst)
    }
}

// ── Tests ──────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn controller() -> RiskController {
        RiskController::new(10, 3, 2) // window=10, clamp_thresh=3, reject_thresh=2
    }

    #[test]
    fn fresh_controller_is_normal() {
        let rc = controller();
        assert_eq!(rc.risk_level(), 0);
        assert!(!rc.is_emergency());
    }

    #[test]
    fn rejects_below_threshold_stays_normal() {
        let rc = controller();
        rc.record(false, true); // 1 reject — below threshold of 2
        assert_eq!(rc.risk_level(), 0);
    }

    #[test]
    fn rejects_at_threshold_elevates() {
        let rc = controller();
        rc.record(false, true);
        rc.record(false, true); // 2 rejects = threshold
        assert_eq!(rc.risk_level(), 1);
    }

    #[test]
    fn many_rejects_become_critical() {
        let rc = controller();
        for _ in 0..6 {
            // 6 >= 2 * 3 = critical threshold
            rc.record(false, true);
        }
        assert_eq!(rc.risk_level(), 2);
    }

    #[test]
    fn clamps_at_threshold_elevates() {
        let rc = controller();
        for _ in 0..3 {
            rc.record(true, false);
        }
        assert_eq!(rc.risk_level(), 1);
    }

    #[test]
    fn window_evicts_old_samples() {
        let rc = RiskController::new(3, 10, 2); // small window
        for _ in 0..3 {
            rc.record(false, true);
        } // fill with rejects
        rc.record(false, false); // push out oldest
        rc.record(false, false);
        rc.record(false, false); // all 3 slots now normal
        assert_eq!(rc.risk_level(), 0);
    }

    #[test]
    fn emergency_overrides_risk_level() {
        let rc = controller();
        rc.trigger_emergency();
        assert_eq!(rc.risk_level(), 3);
    }

    #[test]
    fn clear_emergency_restores_computed_level() {
        let rc = controller();
        rc.trigger_emergency();
        assert_eq!(rc.risk_level(), 3);
        rc.clear_emergency();
        assert_eq!(rc.risk_level(), 0); // no recorded events
    }

    #[test]
    fn stats_snapshot_matches_risk_level() {
        let rc = controller();
        rc.record(true, false);
        rc.record(false, true);
        let s = rc.stats();
        assert_eq!(s.clamp_count, 1);
        assert_eq!(s.reject_count, 1);
        assert_eq!(s.risk_level, rc.risk_level());
    }
}
