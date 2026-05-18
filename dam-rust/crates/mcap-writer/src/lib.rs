//! McapWriter — high-performance async MCAP writing for DAM cycle records.
//!
//! Uses a background thread with crossbeam channel. Python calls write_cycle()
//! which drops data into channel and returns immediately. Background thread handles
//! all serialization and MCAP file I/O.

use std::collections::{HashMap, VecDeque};
use std::fs::File;
use std::io::BufWriter;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use crossbeam::channel::{bounded, RecvTimeoutError, Sender, TrySendError};
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
    #[serde(default)]
    pub active_cameras: Vec<String>,
    pub obs_joint_positions: Vec<f64>,
    /// Generic per-channel observation data (joint_velocities, end_effector_pose,
    /// force_torque, current, temperature, …).  Channel names are device-specific.
    pub obs_channels: HashMap<String, Vec<f64>>,
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
    /// Provenance: runtime config swap counter (0 = initial / unknown).
    /// `serde(default)` lets older Python writers omit this field.
    #[serde(default)]
    pub config_version: u64,
    #[serde(default)]
    pub failure_type: Option<String>,
    #[serde(default)]
    pub failure_guard_names: Vec<String>,
    #[serde(default)]
    pub failure_layers: Vec<String>,
    #[serde(default)]
    pub failure_decisions: Vec<String>,
    #[serde(default)]
    pub failure_reasons: Vec<String>,
    #[serde(default)]
    pub failure_tuple: Option<serde_json::Value>,
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

#[derive(Clone)]
pub struct ImageHub {
    inner: Arc<Mutex<ImageHubInner>>,
}

struct ImageHubInner {
    window_sec: f64,
    next_sequence: u64,
    frames: VecDeque<SequencedImage>,
    latest: HashMap<String, SequencedImage>,
}

#[derive(Clone)]
struct SequencedImage {
    sequence: u64,
    image: ImageData,
}

impl ImageHub {
    pub fn new(window_sec: f64) -> Self {
        Self {
            inner: Arc::new(Mutex::new(ImageHubInner {
                window_sec: window_sec.max(0.1),
                next_sequence: 0,
                frames: VecDeque::new(),
                latest: HashMap::new(),
            })),
        }
    }

    pub fn submit_jpeg(
        &self,
        camera_name: impl Into<String>,
        timestamp: f64,
        width: u32,
        height: u32,
        data: Vec<u8>,
    ) {
        if data.is_empty() {
            return;
        }
        let image = ImageData {
            camera_name: camera_name.into(),
            timestamp,
            width,
            height,
            data,
        };
        let mut inner = self.inner.lock().expect("ImageHub lock poisoned");
        inner.next_sequence += 1;
        let frame = SequencedImage {
            sequence: inner.next_sequence,
            image,
        };
        let should_update_latest = inner
            .latest
            .get(&frame.image.camera_name)
            .map(|latest| frame.image.timestamp >= latest.image.timestamp)
            .unwrap_or(true);
        if should_update_latest {
            inner
                .latest
                .insert(frame.image.camera_name.clone(), frame.clone());
        }
        inner.frames.push_back(frame);
        trim_image_hub_locked(&mut inner, timestamp);
    }

    pub fn current_sequence(&self) -> u64 {
        let inner = self.inner.lock().expect("ImageHub lock poisoned");
        inner.next_sequence
    }

    pub fn latest_all(&self) -> Vec<ImageData> {
        let inner = self.inner.lock().expect("ImageHub lock poisoned");
        inner
            .latest
            .values()
            .map(|frame| frame.image.clone())
            .collect()
    }

    pub fn latest_for(&self, camera_name: &str) -> Option<ImageData> {
        let inner = self.inner.lock().expect("ImageHub lock poisoned");
        inner
            .latest
            .get(camera_name)
            .map(|frame| frame.image.clone())
    }

    pub fn frames_between(&self, start: f64, end: f64) -> Vec<ImageData> {
        let inner = self.inner.lock().expect("ImageHub lock poisoned");
        inner
            .frames
            .iter()
            .filter(|frame| frame.image.timestamp >= start && frame.image.timestamp <= end)
            .map(|frame| frame.image.clone())
            .collect()
    }

    pub fn latest_window(&self, end: f64, window_sec: f64) -> Vec<ImageData> {
        self.frames_between(end - window_sec.max(0.0), end)
    }

    pub fn frames_after_until(&self, cursor: u64, end: f64) -> Vec<(u64, ImageData)> {
        let inner = self.inner.lock().expect("ImageHub lock poisoned");
        inner
            .frames
            .iter()
            .filter(|frame| frame.sequence > cursor && frame.image.timestamp <= end)
            .map(|frame| (frame.sequence, frame.image.clone()))
            .collect()
    }
}

#[derive(Clone)]
struct ImageHubAttachment {
    hub: ImageHub,
    cursor: u64,
}

pub struct McapWriter {
    sender: Sender<WorkItem>,
    sequence: Arc<AtomicU64>,
    started: Arc<AtomicBool>,
    stop_requested: Arc<AtomicBool>,
    image_hub: Arc<Mutex<Option<ImageHubAttachment>>>,
}

enum WorkItem {
    Start(PathBuf), // Path to start writing
    Cycle(u64, Box<CycleRecordData>),
    Stop(u64, f64),
}

impl McapWriter {
    pub fn new() -> Result<Self, String> {
        let (tx, rx) = bounded::<WorkItem>(1024);
        let sequence = Arc::new(AtomicU64::new(0));
        let started = Arc::new(AtomicBool::new(false));
        let stop_requested = Arc::new(AtomicBool::new(false));
        let image_hub = Arc::new(Mutex::new(None));
        let sequence_for_py = Arc::clone(&sequence);
        let started_for_py = Arc::clone(&started);
        let stop_requested_for_py = Arc::clone(&stop_requested);
        let image_hub_for_worker = Arc::clone(&image_hub);

        thread::spawn(move || {
            if let Err(e) = run_worker(rx, stop_requested, image_hub_for_worker) {
                log::error!("McapWriter worker failed: {}", e);
            }
        });

        Ok(Self {
            sender: tx,
            sequence: sequence_for_py,
            started: started_for_py,
            stop_requested: stop_requested_for_py,
            image_hub,
        })
    }

    pub fn start(&self, path: impl AsRef<Path>) -> Result<(), String> {
        if self.started.load(Ordering::SeqCst) {
            return Ok(()); // Already started
        }
        self.stop_requested.store(false, Ordering::SeqCst);
        self.started.store(true, Ordering::SeqCst);
        self.sender
            .send(WorkItem::Start(path.as_ref().to_path_buf()))
            .map_err(|_| "Channel closed".to_string())
    }

    pub fn write_cycle(&self, record: CycleRecordData) -> Result<u64, String> {
        if !self.started.load(Ordering::SeqCst) || self.stop_requested.load(Ordering::SeqCst) {
            return Err("McapWriter not started".to_string());
        }
        let seq = self.sequence.fetch_add(1, Ordering::SeqCst);
        match self.sender.try_send(WorkItem::Cycle(seq, Box::new(record))) {
            Ok(()) => {}
            Err(TrySendError::Full(_)) => {
                return Err("McapWriter queue full; dropping cycle".to_string());
            }
            Err(TrySendError::Disconnected(_)) => return Err("Channel closed".to_string()),
        }
        Ok(seq)
    }

    pub fn stop(&self) {
        self.stop_at(f64::INFINITY);
    }

    pub fn stop_at(&self, stop_timestamp: f64) {
        if !self.started.swap(false, Ordering::SeqCst) {
            return;
        }
        let seq = self.sequence.fetch_add(1, Ordering::SeqCst);
        let _ = self.sender.send(WorkItem::Stop(seq, stop_timestamp));
    }

    pub fn attach_image_hub(
        &self,
        hub: ImageHub,
        _window_sec: f64,
        _capture_images_on_clamp: bool,
        cursor: u64,
    ) {
        let mut image_hub = self
            .image_hub
            .lock()
            .expect("McapWriter image hub lock poisoned");
        *image_hub = Some(ImageHubAttachment { hub, cursor });
    }

    pub fn current_sequence(&self) -> u64 {
        self.sequence.load(Ordering::SeqCst)
    }
}

impl Drop for McapWriter {
    fn drop(&mut self) {
        self.stop();
    }
}

fn run_worker(
    rx: crossbeam::channel::Receiver<WorkItem>,
    stop_requested: Arc<AtomicBool>,
    image_hub: Arc<Mutex<Option<ImageHubAttachment>>>,
) -> Result<(), String> {
    let mut mcap: Option<McapWriterInner<BufWriter<File>>> = None;

    loop {
        if stop_requested.load(Ordering::SeqCst) {
            break;
        }
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
            Ok(WorkItem::Cycle(seq, record)) => {
                if stop_requested.load(Ordering::SeqCst) {
                    break;
                }
                if let Some(ref mut m) = mcap {
                    let record = enrich_record_with_images(*record, &image_hub);
                    if let Err(e) = process_cycle(m, seq, &record) {
                        log::error!("Failed to process cycle {}: {}", record.cycle_id, e);
                    }
                    if let Err(e) = m.flush() {
                        log::error!("Failed to flush: {}", e);
                    }
                } else {
                    log::warn!("McapWriter: received cycle but file not started");
                }
            }
            Ok(WorkItem::Stop(seq, stop_timestamp)) => {
                if let Some(ref mut m) = mcap {
                    let images = drain_images_until(&image_hub, stop_timestamp);
                    if let Err(e) = write_images(m, seq, &images) {
                        log::error!("Failed to flush stop images: {}", e);
                    }
                    if let Err(e) = m.flush() {
                        log::error!("Failed to flush stop: {}", e);
                    }
                }
                break;
            }
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

fn enrich_record_with_images(
    mut record: CycleRecordData,
    image_hub: &Arc<Mutex<Option<ImageHubAttachment>>>,
) -> CycleRecordData {
    if !record.image_data.is_empty() {
        return record;
    }
    record.image_data = drain_images_until(image_hub, record.obs_timestamp);
    record
}

fn drain_images_until(
    image_hub: &Arc<Mutex<Option<ImageHubAttachment>>>,
    end_timestamp: f64,
) -> Vec<ImageData> {
    let mut image_hub = image_hub
        .lock()
        .expect("McapWriter image hub lock poisoned");
    let Some(attachment) = image_hub.as_mut() else {
        return Vec::new();
    };
    let frames = attachment
        .hub
        .frames_after_until(attachment.cursor, end_timestamp);
    if let Some((max_sequence, _)) = frames.last() {
        attachment.cursor = *max_sequence;
    }
    frames.into_iter().map(|(_, image)| image).collect()
}

fn trim_image_hub_locked(inner: &mut ImageHubInner, now: f64) {
    let min_ts = now - inner.window_sec;
    while inner
        .frames
        .front()
        .map(|frame| frame.image.timestamp < min_ts)
        .unwrap_or(false)
    {
        inner.frames.pop_front();
    }
}

fn process_cycle<W: std::io::Write + std::io::Seek>(
    mcap: &mut McapWriterInner<W>,
    seq: u64,
    record: &CycleRecordData,
) -> Result<(), String> {
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

    write_images(mcap, seq, &record.image_data)?;

    Ok(())
}

fn write_images<W: std::io::Write + std::io::Seek>(
    mcap: &mut McapWriterInner<W>,
    seq: u64,
    image_data: &[ImageData],
) -> Result<(), String> {
    // Write images to /dam/images/{camera_name}
    if !image_data.is_empty() {
        let image_schema_id = mcap
            .add_schema("dam.Image", "application/msgpack", &[])
            .map_err(|e| format!("Failed to add image schema: {}", e))?;

        for img in image_data {
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
            active_cameras: vec![],
            obs_joint_positions: vec![0.0; 7],
            obs_channels: HashMap::new(),
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
            config_version: 0,
            failure_type: None,
            failure_guard_names: vec![],
            failure_layers: vec![],
            failure_decisions: vec![],
            failure_reasons: vec![],
            failure_tuple: None,
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
