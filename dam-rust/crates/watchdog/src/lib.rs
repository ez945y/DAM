//! WatchdogTimer — deadline-based cycle watchdog.
//!
//! Runs on a dedicated OS thread, completely outside the Python GIL.  If
//! `ping()` is not called within `deadline_ms` after `arm()`, the emergency
//! flag is set atomically (SeqCst).  The Python control loop must call
//! `ping()` once per cycle and check `is_emergency()` after each `step()`.
//!
//! # Thread safety
//! All public methods are `Send + Sync`.  The internal watcher thread holds
//! only `Arc`s, so dropping `WatchdogTimer` while the thread is alive is safe
//! — the thread exits within one tick once `running` is false.

use parking_lot::Mutex;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};

pub struct WatchdogTimer {
    deadline_ms: f64,
    last_ping: Arc<Mutex<Instant>>,
    emergency: Arc<AtomicBool>,
    running: Arc<AtomicBool>,
}

impl WatchdogTimer {
    /// Create a disarmed watchdog with the given deadline.
    pub fn new(deadline_ms: f64) -> Self {
        WatchdogTimer {
            deadline_ms,
            last_ping: Arc::new(Mutex::new(Instant::now())),
            emergency: Arc::new(AtomicBool::new(false)),
            running: Arc::new(AtomicBool::new(false)),
        }
    }

    /// Arm (or re-arm) the watchdog.  Stops any previous watcher thread first,
    /// clears the emergency flag, then spawns a new watcher OS thread.
    pub fn arm(&self) {
        self.disarm(); // stop previous thread gracefully
        self.emergency.store(false, Ordering::SeqCst);
        self.running.store(true, Ordering::SeqCst);
        *self.last_ping.lock() = Instant::now();

        let last_ping = Arc::clone(&self.last_ping);
        let emergency = Arc::clone(&self.emergency);
        let running = Arc::clone(&self.running);
        let deadline = Duration::from_micros((self.deadline_ms * 1000.0) as u64);

        thread::Builder::new()
            .name("dam-watchdog".into())
            .spawn(move || {
                while running.load(Ordering::Relaxed) {
                    thread::sleep(Duration::from_millis(1));
                    if last_ping.lock().elapsed() > deadline {
                        emergency.store(true, Ordering::SeqCst);
                        running.store(false, Ordering::SeqCst);
                        break;
                    }
                }
            })
            .expect("failed to spawn watchdog thread");
    }

    /// Reset the last-ping timestamp.  Must be called once per control cycle.
    pub fn ping(&self) {
        *self.last_ping.lock() = Instant::now();
    }

    /// Disarm without triggering emergency.  Call when leaving the control loop.
    pub fn disarm(&self) {
        self.running.store(false, Ordering::SeqCst);
    }

    /// True if the deadline was exceeded.
    pub fn is_emergency(&self) -> bool {
        self.emergency.load(Ordering::SeqCst)
    }

    /// Clear the emergency flag (after a safe stop has been performed).
    pub fn clear_emergency(&self) {
        self.emergency.store(false, Ordering::SeqCst);
    }

    /// Return the configured deadline in milliseconds.
    pub fn deadline_ms(&self) -> f64 {
        self.deadline_ms
    }

    /// Return elapsed milliseconds since the last ping (for diagnostics).
    pub fn elapsed_since_ping_ms(&self) -> f64 {
        self.last_ping.lock().elapsed().as_secs_f64() * 1000.0
    }
}

// ── Tests ──────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn new_watchdog_not_emergency() {
        let w = WatchdogTimer::new(100.0);
        assert!(!w.is_emergency());
    }

    #[test]
    fn ping_prevents_emergency() {
        let w = WatchdogTimer::new(50.0); // 50 ms deadline
        w.arm();
        for _ in 0..8 {
            thread::sleep(Duration::from_millis(10));
            w.ping();
        }
        w.disarm();
        assert!(!w.is_emergency(), "regular pings should prevent emergency");
    }

    #[test]
    fn missed_ping_triggers_emergency() {
        let w = WatchdogTimer::new(20.0); // 20 ms deadline
        w.arm();
        thread::sleep(Duration::from_millis(80)); // no ping
        assert!(w.is_emergency(), "missed ping should trigger emergency");
        w.disarm();
    }

    #[test]
    fn clear_emergency_resets_flag() {
        let w = WatchdogTimer::new(10.0);
        w.arm();
        thread::sleep(Duration::from_millis(50));
        assert!(w.is_emergency());
        w.clear_emergency();
        assert!(!w.is_emergency());
    }

    #[test]
    fn rearm_clears_emergency() {
        let w = WatchdogTimer::new(10.0);
        w.arm();
        thread::sleep(Duration::from_millis(50));
        assert!(w.is_emergency());
        w.arm(); // re-arm should clear
        assert!(!w.is_emergency());
        w.disarm();
    }

    #[test]
    fn elapsed_since_ping_increases_over_time() {
        let w = WatchdogTimer::new(1000.0);
        w.ping();
        thread::sleep(Duration::from_millis(20));
        assert!(w.elapsed_since_ping_ms() >= 10.0);
    }
}
