//! ObservationBus — bounded ring buffer for serialised sensor observations.
//!
//! Observations are stored as raw bytes (msgpack / pickle, serialised on the
//! Python side).  The bus is write-biased: writes are O(1) and always succeed;
//! the oldest sample is silently evicted when the buffer is full.
//!
//! The Python fallback (`dam/bus/fallback.py`) provides an equivalent API for
//! environments where the Rust extension is not compiled.

use parking_lot::RwLock;
use std::collections::VecDeque;
use std::sync::Arc;

pub struct ObservationBus {
    buffer: Arc<RwLock<VecDeque<Vec<u8>>>>,
    capacity: usize,
}

impl ObservationBus {
    /// Create a ring buffer with `capacity` slots.
    ///
    /// Recommended sizing: `capacity = window_sec * hz + margin`
    pub fn new(capacity: usize) -> Self {
        ObservationBus {
            buffer: Arc::new(RwLock::new(VecDeque::with_capacity(capacity))),
            capacity,
        }
    }

    /// Write serialised observation bytes.  Evicts the oldest sample when full.
    pub fn write(&self, data: Vec<u8>) {
        let mut buf = self.buffer.write();
        if buf.len() >= self.capacity {
            buf.pop_front();
        }
        buf.push_back(data);
    }

    /// Return the most recently written sample, or `None` if empty.
    pub fn read_latest(&self) -> Option<Vec<u8>> {
        self.buffer.read().back().cloned()
    }

    /// Return the last `n` samples in chronological order (oldest first).
    /// If fewer than `n` samples are available, all stored samples are returned.
    pub fn read_window(&self, n: usize) -> Vec<Vec<u8>> {
        let buf = self.buffer.read();
        let start = buf.len().saturating_sub(n);
        buf.iter().skip(start).cloned().collect()
    }

    /// Number of samples currently stored.
    pub fn len(&self) -> usize {
        self.buffer.read().len()
    }

    /// True if the buffer contains no samples.
    pub fn is_empty(&self) -> bool {
        self.buffer.read().is_empty()
    }

    /// Maximum number of samples the buffer can hold.
    pub fn capacity(&self) -> usize {
        self.capacity
    }
}

// ── Tests ──────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn new_bus_is_empty() {
        let bus = ObservationBus::new(10);
        assert!(bus.is_empty());
        assert_eq!(bus.len(), 0);
        assert!(bus.read_latest().is_none());
    }

    #[test]
    fn write_and_read_latest() {
        let bus = ObservationBus::new(10);
        bus.write(vec![1, 2, 3]);
        bus.write(vec![4, 5, 6]);
        assert_eq!(bus.read_latest().unwrap(), vec![4, 5, 6]);
    }

    #[test]
    fn capacity_evicts_oldest() {
        let bus = ObservationBus::new(3);
        bus.write(vec![1]);
        bus.write(vec![2]);
        bus.write(vec![3]);
        bus.write(vec![4]); // should evict vec![1]
        assert_eq!(bus.len(), 3);
        let window = bus.read_window(3);
        assert_eq!(window, vec![vec![2], vec![3], vec![4]]);
    }

    #[test]
    fn read_window_returns_chronological_order() {
        let bus = ObservationBus::new(10);
        for i in 0u8..5 {
            bus.write(vec![i]);
        }
        let w = bus.read_window(3);
        assert_eq!(w, vec![vec![2], vec![3], vec![4]]);
    }

    #[test]
    fn read_window_larger_than_buf_returns_all() {
        let bus = ObservationBus::new(10);
        bus.write(vec![10]);
        bus.write(vec![20]);
        let w = bus.read_window(100);
        assert_eq!(w.len(), 2);
    }

    #[test]
    fn capacity_reported_correctly() {
        let bus = ObservationBus::new(42);
        assert_eq!(bus.capacity(), 42);
    }
}
