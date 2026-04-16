//! McapWriter — high-performance async MCAP writing for DAM cycle records.
//!
//! Uses a background thread with crossbeam channel. Python calls write_cycle()
//! which drops data into channel and returns immediately. Background thread handles
//! all serialization and MCAP file I/O.

use std::collections::HashMap;
use std::fs::File;
use std::io::BufWriter;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::Duration;

use crossbeam::channel::{bounded, RecvTimeoutError, Sender};
use mcap::records::MessageHeader;
use mcap::write::Writer as McapWriterInner;
use mcap::WriteOptions;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CycleRecordData {
    pub cycle_id: u64,
    pub obs_timestamp: f64,
    pub has_violation: bool,
    pub has_clamp: bool,
    pub violated_layer_mask: u32,
    pub clamped_layer_mask: u32,
    pub active_task: Option<String>,
    pub active_boundaries: Vec<String>,
    pub obs_joint_positions: Vec<f64>,
    pub obs_joint_velocities: Option<Vec<f64>>,
    pub obs_end_effector_pose: Option<Vec<f64>>,
    pub obs_force_torque: Option<Vec<f64>>,
    pub action_positions: Vec<f64>,
    pub action_velocities: Option<Vec<f64>>,
    pub validated_positions: Option<Vec<f64>>,
    pub validated_velocities: Option<Vec<f64>>,
    pub was_clamped: bool,
    pub fallback_triggered: Option<String>,
    pub guard_results: Vec<GuardResultData>,
    pub latency_stages: HashMap<String, f64>,
    pub latency_layers: HashMap<String, f64>,
    pub latency_guards: HashMap<String, f64>,
    pub image_data: Vec<ImageData>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct GuardResultData {
    pub cycle_id: u64,
    pub timestamp: f64,
    pub guard_name: String,
    pub layer: u32,
    pub decision: u32,
    pub decision_name: String,
    pub reason: String,
    pub latency_ms: Option<f64>,
    pub is_violation: bool,
    pub is_clamp: bool,
    pub fault_source: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ImageData {
    pub camera_name: String,
    pub timestamp: f64,
    pub width: u32,
    pub height: u32,
    pub data: Vec<u8>,
}

pub struct McapWriter {
    sender: Sender<WorkItem>,
    sequence: Arc<AtomicU64>,
    started: Arc<AtomicBool>,
}

enum WorkItem {
    Start(PathBuf), // Path to start writing
    Cycle(Box<CycleRecordData>),
    Stop,
}

impl McapWriter {
    pub fn new() -> Result<Self, String> {
        let (tx, rx) = bounded::<WorkItem>(1024);
        let sequence = Arc::new(AtomicU64::new(0));
        let started = Arc::new(AtomicBool::new(false));
        let sequence_for_py = Arc::clone(&sequence);
        let started_for_py = Arc::clone(&started);

        thread::spawn(move || {
            if let Err(e) = run_worker(rx, sequence) {
                log::error!("McapWriter worker failed: {}", e);
            }
        });

        Ok(Self {
            sender: tx,
            sequence: sequence_for_py,
            started: started_for_py,
        })
    }

    pub fn start(&self, path: impl AsRef<Path>) -> Result<(), String> {
        if self.started.load(Ordering::SeqCst) {
            return Ok(()); // Already started
        }
        self.started.store(true, Ordering::SeqCst);
        self.sender
            .send(WorkItem::Start(path.as_ref().to_path_buf()))
            .map_err(|_| "Channel closed".to_string())
    }

    pub fn write_cycle(&self, record: CycleRecordData) -> Result<u64, String> {
        if !self.started.load(Ordering::SeqCst) {
            return Err("McapWriter not started".to_string());
        }
        let seq = self.sequence.fetch_add(1, Ordering::SeqCst);
        self.sender
            .send(WorkItem::Cycle(Box::new(record)))
            .map_err(|_| "Channel closed".to_string())?;
        Ok(seq)
    }

    pub fn current_sequence(&self) -> u64 {
        self.sequence.load(Ordering::SeqCst)
    }
}

impl Drop for McapWriter {
    fn drop(&mut self) {
        let _ = self.sender.send(WorkItem::Stop);
    }
}

fn run_worker(
    rx: crossbeam::channel::Receiver<WorkItem>,
    sequence: Arc<AtomicU64>,
) -> Result<(), String> {
    let mut mcap: Option<McapWriterInner<BufWriter<File>>> = None;
    let sequence_for_mcap = Arc::clone(&sequence);

    loop {
        match rx.recv_timeout(Duration::from_millis(50)) {
            Ok(WorkItem::Start(path)) => {
                log::info!("McapWriter: starting file {:?}", path);
                let file =
                    File::create(&path).map_err(|e| format!("Failed to create file: {}", e))?;
                let writer = BufWriter::new(file);

                let opts = WriteOptions::new()
                    .profile("dam".to_string())
                    .library("dam-loopback-writer/1.0".to_string());

                match McapWriterInner::with_options(writer, opts) {
                    Ok(w) => mcap = Some(w),
                    Err(e) => {
                        log::error!("Failed to create MCAP writer: {}", e);
                        mcap = None;
                    }
                }
            }
            Ok(WorkItem::Cycle(record)) => {
                if let Some(ref mut m) = mcap {
                    if let Err(e) = process_cycle(m, &sequence_for_mcap, &record) {
                        log::error!("Failed to process cycle {}: {}", record.cycle_id, e);
                    }
                    if let Err(e) = m.flush() {
                        log::error!("Failed to flush: {}", e);
                    }
                } else {
                    log::warn!("McapWriter: received cycle but file not started");
                }
            }
            Ok(WorkItem::Stop) => break,
            Err(RecvTimeoutError::Timeout) => continue,
            Err(RecvTimeoutError::Disconnected) => break,
        }
    }

    if let Some(mut m) = mcap {
        m.finish().map_err(|e| format!("Finish failed: {}", e))?;
    }
    log::info!("McapWriter worker stopped");
    Ok(())
}

fn process_cycle<W: std::io::Write + std::io::Seek>(
    mcap: &mut McapWriterInner<W>,
    sequence: &Arc<AtomicU64>,
    record: &CycleRecordData,
) -> Result<(), String> {
    let seq = sequence.fetch_add(1, Ordering::SeqCst);
    let log_time = (record.obs_timestamp * 1_000_000_000.0) as u64;

    let cycle_bytes =
        rmp_serde::to_vec(record).map_err(|e| format!("Serialization failed: {}", e))?;

    // Register schema and channel for /dam/cycle
    let cycle_schema_id = mcap
        .add_schema("dam.Cycle", "application/msgpack", &[])
        .map_err(|e| format!("Failed to add schema: {}", e))?;
    let cycle_channel_id = mcap
        .add_channel(
            cycle_schema_id,
            "/dam/cycle",
            "application/msgpack",
            &Default::default(),
        )
        .map_err(|e| format!("Failed to add channel: {}", e))?;

    mcap.write_to_known_channel(
        &MessageHeader {
            channel_id: cycle_channel_id,
            sequence: seq as u32,
            log_time,
            publish_time: log_time,
        },
        &cycle_bytes,
    )
    .map_err(|e| format!("Failed to write cycle: {}", e))?;

    // Write images to /dam/images/{camera_name}
    if !record.image_data.is_empty() {
        let image_schema_id = mcap
            .add_schema("dam.Image", "application/msgpack", &[])
            .map_err(|e| format!("Failed to add image schema: {}", e))?;

        for img in &record.image_data {
            let topic = format!("/dam/images/{}", img.camera_name);
            let image_channel_id = mcap
                .add_channel(
                    image_schema_id,
                    &topic,
                    "application/msgpack",
                    &Default::default(),
                )
                .map_err(|e| format!("Failed to add image channel: {}", e))?;

            let img_bytes =
                rmp_serde::to_vec(img).map_err(|e| format!("Failed to serialize image: {}", e))?;
            let img_log_time = (img.timestamp * 1_000_000_000.0) as u64;

            mcap.write_to_known_channel(
                &MessageHeader {
                    channel_id: image_channel_id,
                    sequence: seq as u32,
                    log_time: img_log_time,
                    publish_time: img_log_time,
                },
                &img_bytes,
            )
            .map_err(|e| format!("Failed to write image: {}", e))?;
        }
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn create_test_record() -> CycleRecordData {
        CycleRecordData {
            cycle_id: 1,
            obs_timestamp: 1.0,
            has_violation: false,
            has_clamp: false,
            violated_layer_mask: 0,
            clamped_layer_mask: 0,
            active_task: None,
            active_boundaries: vec![],
            obs_joint_positions: vec![0.0; 7],
            obs_joint_velocities: None,
            obs_end_effector_pose: None,
            obs_force_torque: None,
            action_positions: vec![0.0; 7],
            action_velocities: None,
            validated_positions: None,
            validated_velocities: None,
            was_clamped: false,
            fallback_triggered: None,
            guard_results: vec![],
            latency_stages: HashMap::new(),
            latency_layers: HashMap::new(),
            latency_guards: HashMap::new(),
            image_data: vec![],
        }
    }

    #[test]
    fn mcap_writer_creates_and_stops() {
        let writer = McapWriter::new().unwrap();
        writer.start("/tmp/test_async.mcap").unwrap();
        let _ = writer.write_cycle(create_test_record());
        std::thread::sleep(std::time::Duration::from_millis(100));
    }
}
