//! ActionBus — SPSC (single-producer single-consumer) queue for validated actions.
//!
//! Internally backed by a ``Mutex<VecDeque<Vec<u8>>>`` with a capacity limit
//! (default 1 — latest-wins semantics).  When the queue is full, the oldest
//! entry is silently overwritten so the consumer always sees the freshest action.
//!
//! The Python fallback (``dam/bus/fallback.py``) provides an equivalent API for
//! environments where the Rust extension is not compiled.

use parking_lot::Mutex;
use std::collections::VecDeque;

pub struct ActionBus {
    queue: Mutex<VecDeque<Vec<u8>>>,
    capacity: usize,
}

impl ActionBus {
    /// Create an ActionBus with the given capacity.
    ///
    /// ``capacity = 1`` gives latest-wins semantics: each ``write`` overwrites
    /// any previous unread action.
    pub fn new(capacity: usize) -> Self {
        let cap = capacity.max(1);
        ActionBus {
            queue: Mutex::new(VecDeque::with_capacity(cap)),
            capacity: cap,
        }
    }

    /// Store the latest action bytes.
    ///
    /// If the queue is at capacity, the oldest entry is evicted first so the
    /// newest action is always available for reading.
    pub fn write(&self, data: Vec<u8>) {
        let mut q = self.queue.lock();
        if q.len() >= self.capacity {
            q.pop_front();
        }
        q.push_back(data);
    }

    /// Take the next action from the queue (removes it).
    ///
    /// Returns ``None`` if the queue is empty.
    pub fn read(&self) -> Option<Vec<u8>> {
        self.queue.lock().pop_front()
    }

    /// Return ``true`` if there are no pending actions.
    pub fn is_empty(&self) -> bool {
        self.queue.lock().is_empty()
    }
}

// ── Tests ──────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn write_then_read_returns_data() {
        let bus = ActionBus::new(1);
        bus.write(vec![1, 2, 3]);
        let got = bus.read().expect("should have data");
        assert_eq!(got, vec![1, 2, 3]);
    }

    #[test]
    fn empty_read_returns_none() {
        let bus = ActionBus::new(1);
        assert!(bus.read().is_none());
    }

    #[test]
    fn overwrite_returns_latest() {
        let bus = ActionBus::new(1);
        bus.write(vec![1]);
        bus.write(vec![2]); // overwrites previous
        let got = bus.read().expect("should have data");
        assert_eq!(got, vec![2]);
    }

    #[test]
    fn read_removes_entry() {
        let bus = ActionBus::new(1);
        bus.write(vec![42]);
        let _first = bus.read();
        assert!(bus.read().is_none(), "second read should return None");
    }

    #[test]
    fn is_empty_after_write_and_read() {
        let bus = ActionBus::new(1);
        assert!(bus.is_empty());
        bus.write(vec![7]);
        assert!(!bus.is_empty());
        let _ = bus.read();
        assert!(bus.is_empty());
    }
}
