//! Loopback — ring-buffer capture of observation/action pairs for post-hoc analysis.
//!
//! Stores the last N (observation_bytes, action_bytes, timestamp_us, metadata) tuples
//! in a fixed-size ring buffer. Flushes to MCAP-compatible JSON on demand or on
//! violation trigger.
//!
//! # Usage
//! ```rust
//! use loopback::{Loopback, LoopbackConfig};
//! let lb = Loopback::new(LoopbackConfig { capacity: 512, capture_on_violation: true });
//! lb.push(vec![1, 2, 3], vec![4, 5, 6], 0);
//! let snapshot = lb.snapshot();
//! assert_eq!(snapshot.len(), 1);
//! ```

use parking_lot::Mutex;
use serde::{Deserialize, Serialize};

/// One captured cycle: raw observation + action bytes plus a microsecond timestamp.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct LoopbackFrame {
    pub timestamp_us: u64,
    pub observation: Vec<u8>,
    pub action: Vec<u8>,
    /// True if a guard violation (reject or clamp) occurred on this cycle.
    pub violation: bool,
}

/// Configuration for the Loopback buffer.
#[derive(Clone, Debug)]
pub struct LoopbackConfig {
    /// Maximum number of frames stored before oldest are evicted.
    pub capacity: usize,
    /// Whether to mark frames that coincide with a guard violation.
    pub capture_on_violation: bool,
}

impl Default for LoopbackConfig {
    fn default() -> Self {
        LoopbackConfig {
            capacity: 512,
            capture_on_violation: true,
        }
    }
}

pub struct Loopback {
    buf: Mutex<Vec<LoopbackFrame>>,
    head: Mutex<usize>,
    capacity: usize,
    capture_on_violation: bool,
}

impl Loopback {
    /// Create a new Loopback buffer with the given configuration.
    pub fn new(cfg: LoopbackConfig) -> Self {
        let cap = cfg.capacity.max(1);
        Loopback {
            buf: Mutex::new(Vec::with_capacity(cap)),
            head: Mutex::new(0),
            capacity: cap,
            capture_on_violation: cfg.capture_on_violation,
        }
    }

    /// Push a new frame into the ring buffer.
    ///
    /// If the buffer is full, the oldest frame is overwritten.
    pub fn push(&self, observation: Vec<u8>, action: Vec<u8>, timestamp_us: u64) {
        self.push_with_violation(observation, action, timestamp_us, false)
    }

    /// Push a frame and mark whether a violation occurred.
    pub fn push_with_violation(
        &self,
        observation: Vec<u8>,
        action: Vec<u8>,
        timestamp_us: u64,
        violation: bool,
    ) {
        if !violation || self.capture_on_violation {
            let frame = LoopbackFrame {
                timestamp_us,
                observation,
                action,
                violation,
            };
            let mut buf = self.buf.lock();
            let mut head = self.head.lock();
            if buf.len() < self.capacity {
                buf.push(frame);
            } else {
                buf[*head % self.capacity] = frame;
            }
            *head = head.wrapping_add(1);
        }
    }

    /// Return a snapshot of all buffered frames in chronological order.
    ///
    /// The snapshot is a clone — it is safe to iterate without holding a lock.
    pub fn snapshot(&self) -> Vec<LoopbackFrame> {
        let buf = self.buf.lock();
        let head = *self.head.lock();
        if buf.len() < self.capacity {
            // Not yet wrapped — slice is already in order
            buf.clone()
        } else {
            // Wrapped ring: reorder from oldest to newest
            let pivot = head % self.capacity;
            let mut out = Vec::with_capacity(self.capacity);
            out.extend_from_slice(&buf[pivot..]);
            out.extend_from_slice(&buf[..pivot]);
            out
        }
    }

    /// Return only frames where ``violation == true``.
    pub fn violations(&self) -> Vec<LoopbackFrame> {
        self.snapshot()
            .into_iter()
            .filter(|f| f.violation)
            .collect()
    }

    /// Serialise all frames to a JSON string (for export / MCAP injection).
    pub fn export_json(&self) -> String {
        serde_json::to_string(&self.snapshot()).unwrap_or_else(|_| "[]".to_owned())
    }

    /// Clear the buffer.
    pub fn clear(&self) {
        self.buf.lock().clear();
        *self.head.lock() = 0;
    }

    /// Current number of frames in the buffer.
    pub fn len(&self) -> usize {
        self.buf.lock().len()
    }

    /// Returns true if the buffer is empty.
    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
}

// ── Tests ──────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn lb(cap: usize) -> Loopback {
        Loopback::new(LoopbackConfig {
            capacity: cap,
            capture_on_violation: true,
        })
    }

    #[test]
    fn push_and_snapshot() {
        let l = lb(4);
        l.push(vec![1], vec![10], 1000);
        l.push(vec![2], vec![20], 2000);
        let snap = l.snapshot();
        assert_eq!(snap.len(), 2);
        assert_eq!(snap[0].observation, vec![1]);
        assert_eq!(snap[1].timestamp_us, 2000);
    }

    #[test]
    fn ring_wrap_preserves_order() {
        let l = lb(3);
        for i in 0u8..6 {
            l.push(vec![i], vec![i * 2], i as u64 * 100);
        }
        let snap = l.snapshot();
        assert_eq!(snap.len(), 3);
        // Should be frames 3, 4, 5 in order
        assert_eq!(snap[0].observation, vec![3]);
        assert_eq!(snap[2].observation, vec![5]);
    }

    #[test]
    fn violation_filter() {
        let l = lb(10);
        l.push_with_violation(vec![1], vec![1], 1, false);
        l.push_with_violation(vec![2], vec![2], 2, true);
        l.push_with_violation(vec![3], vec![3], 3, false);
        let viols = l.violations();
        assert_eq!(viols.len(), 1);
        assert_eq!(viols[0].observation, vec![2]);
    }

    #[test]
    fn export_json_is_valid() {
        let l = lb(4);
        l.push(vec![9], vec![8], 999);
        let json = l.export_json();
        assert!(json.starts_with('['));
        assert!(json.contains("999"));
    }

    #[test]
    fn clear_empties_buffer() {
        let l = lb(4);
        l.push(vec![1], vec![2], 1);
        l.clear();
        assert!(l.is_empty());
        assert_eq!(l.snapshot().len(), 0);
    }
}
