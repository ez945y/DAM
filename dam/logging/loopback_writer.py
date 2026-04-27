"""Streaming MCAP writer for the DAM control-loop loopback pipeline.

Architecture
------------
The control-loop thread calls ``submit(CycleRecord)`` — a non-blocking
``queue.Queue.put_nowait``.  A single daemon thread drains the queue and
performs all serialisation and disk I/O, keeping the hot path latency to
< 5 µs per cycle.

Every cycle is written (not just violations).  Violations are flagged via
``has_violation`` / ``violated_layer_mask`` in ``/dam/cycle``; clamped
actions via ``has_clamp`` / ``clamped_layer_mask``.  Images are fetched
from the ObservationBus ring buffer on REJECT/FAULT (always) and on CLAMP
(when ``capture_images_on_clamp=True``).

MCAP channel layout (flat, training-friendly)
---------------------------------------------
/dam/cycle          — one per cycle: summary, context, latency, violation flags
/dam/obs            — one per cycle: joint state, end-effector, force-torque
/dam/action         — one per cycle: proposal + validated action
/dam/L0 … /dam/L4  — one message *per guard result* per cycle; ``is_violation``
                       distinguishes normal from violated results; multiple
                       boundaries firing simultaneously → multiple messages with
                       the same ``cycle_id``
/dam/latency        — one per cycle: per-stage and per-layer breakdown (ms)
/dam/images/{cam}   — one per camera frame; written only on violation cycles;
                       frames come from the ObservationBus ring buffer so
                       pre-violation context is captured automatically

Session metadata (written once per file, not per message)
----------------------------------------------------------
``writer.add_metadata("session", {...})`` stores control_frequency_hz,
session_id, robot_id, stackfile details, dam_version, joint_names etc.
Analysis tools read this once; repetitive constants never pollute messages.

File rotation
-------------
A new ``.mcap`` file is opened when the current file exceeds ``rotate_mb``
or ``rotate_minutes``.  Files are named
``{output_dir}/session_{session_id}_{unix_ts}.mcap``.

Back-pressure
-------------
Non-violation cycles are dropped with a warning when the queue is full.
Violation cycles always replace the oldest entry to guarantee capture.
"""

from __future__ import annotations

import contextlib
import json
import logging
import queue
import threading
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from dam.logging.cycle_record import CycleRecord
from dam.types.observation import Observation
from dam.types.result import GuardDecision

if TYPE_CHECKING:
    from dam.bus import ObservationBus

logger = logging.getLogger(__name__)


# ── JSON Encoder ─────────────────────────────────────────────────────────────
class _DAMEncoder(json.JSONEncoder):
    """Robust JSON encoder for DAM cycle records.

    Handles types that the standard encoder misses:
    - bytes/bytearray -> list[int] (for MCAP image data)
    - np.ndarray      -> list (for any stray arrays)
    """

    def default(self, obj: Any) -> Any:
        if isinstance(obj, bytes | bytearray):
            return list(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


# ── Rust McapWriter (required) ─────────────────────────────────────────────────
# Uses background thread for all serialization and I/O.

try:
    from dam_rs import McapWriter as _RustMcapWriter
except ImportError as e:
    raise ImportError(
        "dam_rs (Rust MCAP writer) is required. Build it with: "
        "cd dam-rust/dam-py && maturin develop --release"
    ) from e

# ── Optional deps ─────────────────────────────────────────────────────────────

try:
    import msgpack  # type: ignore[import-untyped]

    _HAS_MSGPACK = True
except ImportError:
    _HAS_MSGPACK = False

try:
    from mcap.writer import Writer as _MCAPWriter

    _HAS_MCAP = True
except ImportError:
    _HAS_MCAP = False
    _MCAPWriter = None  # type: ignore[assignment,misc]

try:
    from mcap.writer import CompressionType as _CompressionType  # type: ignore[import-untyped]

    _COMPRESSION = _CompressionType.lz4
except (ImportError, AttributeError):
    _COMPRESSION = None

# ── Rust ImageWriter (optional) ────────────────────────────────────────────────
# Uses Rayon thread pool for parallel JPEG encoding without Python GIL.

try:
    from dam_rs import ImageWriter as _RustImageWriter

    _HAS_RUST_IMAGE_WRITER = True
except ImportError:
    _HAS_RUST_IMAGE_WRITER = False
    _RustImageWriter = None  # type: ignore[assignment,misc]

# Internal sentinel — signals the worker to finish and close the file.
_SENTINEL: object = object()

# ── Helpers ───────────────────────────────────────────────────────────────────


def _json(obj: dict[str, Any]) -> bytes:
    return json.dumps(obj, separators=(",", ":")).encode()


def _record_to_dict(
    rec: CycleRecord, images: dict[str, np.ndarray] | None = None
) -> dict[str, Any]:
    """Convert CycleRecord to dict for Rust McapWriter.

    Args:
        rec: CycleRecord to convert
        images: Optional dict of camera_name -> numpy image array (HWC, RGB)
    """
    from dam.types.result import GuardDecision

    guard_results = []
    for result in rec.guard_results:
        is_violation = result.decision in (GuardDecision.REJECT, GuardDecision.FAULT)
        is_clamp = result.decision == GuardDecision.CLAMP
        guard_results.append(
            {
                "cycle_id": rec.cycle_id,
                "timestamp": rec.obs_timestamp,
                "guard_name": result.guard_name,
                "layer": int(result.layer),
                "decision": int(result.decision),
                "decision_name": result.decision.name,
                "reason": result.reason,
                "latency_ms": rec.latency_guards.get(result.guard_name),
                "is_violation": is_violation,
                "is_clamp": is_clamp,
                "fault_source": result.fault_source,
            }
        )

    # Convert images to ImageData format for Rust
    image_data_list = []
    if images:
        for cam_name, frame in images.items():
            if isinstance(frame, np.ndarray) and frame.size > 0:
                # Compress using Rust or fallback
                try:
                    from dam.logging.loopback_writer import _compress_image

                    compressed, w, h, fmt = _compress_image(frame)
                    image_data_list.append(
                        {
                            "camera_name": cam_name,
                            "timestamp": rec.obs_timestamp,
                            "width": w,
                            "height": h,
                            "data": list(compressed),
                        }
                    )
                except Exception:  # noqa: BLE001 — skip if encoding fails
                    pass

    return {
        "cycle_id": rec.cycle_id,
        "obs_timestamp": rec.obs_timestamp,
        "has_violation": rec.has_violation,
        "has_clamp": rec.has_clamp,
        "violated_layer_mask": rec.violated_layer_mask,
        "clamped_layer_mask": rec.clamped_layer_mask,
        "active_task": rec.active_task,
        "active_boundaries": list(rec.active_boundaries),
        "active_cameras": list(rec.active_cameras),
        "obs_joint_positions": rec.obs_joint_positions,
        "obs_joint_velocities": rec.obs_joint_velocities,
        "obs_end_effector_pose": rec.obs_end_effector_pose,
        "obs_force_torque": rec.obs_force_torque,
        "action_positions": rec.action_positions,
        "action_velocities": rec.action_velocities,
        "validated_positions": rec.validated_positions,
        "validated_velocities": rec.validated_velocities,
        "was_clamped": rec.was_clamped,
        "fallback_triggered": rec.fallback_triggered,
        "guard_results": guard_results,
        "latency_stages": rec.latency_stages,
        "latency_layers": rec.latency_layers,
        "latency_guards": rec.latency_guards,
        "image_data": image_data_list,
    }


def _compress_image(arr: np.ndarray | bytes) -> tuple[bytes, int, int, str]:
    """Return (compressed_bytes, width, height, encoding_label).

    If input is already bytes, assume it is pre-compressed JPEG.
    Otherwise, tries Rust JPEG encoder → cv2 → PIL → raw fallback.
    """
    if isinstance(arr, bytes):
        # Already compressed (e.g. from a hardware source that provides JPEGs).
        # We don't know dimensions, but the frontend/viewer can usually sniff them from headers.
        return arr, 0, 0, "jpeg"

    h, w = arr.shape[:2]

    # Try Rust JPEG encoder first (fastest, no GIL)
    if _HAS_RUST_IMAGE_WRITER:
        try:
            # Ensure RGB format (3 channels)
            if arr.ndim != 3 or arr.shape[2] != 3:
                logger.debug(
                    "LoopbackWriter: image not RGB (%s), falling back to cv2",
                    arr.shape,
                )
            else:
                # Convert to contiguous byte buffer
                rgb_bytes = np.ascontiguousarray(arr, dtype=np.uint8).tobytes()
                try:
                    jpeg_bytes = _RustImageWriter().encode_jpeg(rgb_bytes, w, h, 85)
                    return bytes(jpeg_bytes), w, h, "jpeg"
                except Exception as e:
                    logger.debug(
                        "LoopbackWriter: Rust JPEG encoder failed: %s, falling back",
                        e,
                    )
        except Exception:  # noqa: BLE001 — image conversion failure is non-fatal
            pass

    try:
        import cv2  # type: ignore[import-untyped]

        ok, buf = cv2.imencode(".jpg", arr, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if ok:
            return bytes(buf), w, h, "jpeg"
    except ImportError:
        pass
    try:
        import io

        from PIL import Image  # type: ignore[import-untyped]

        img = Image.fromarray(arr)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue(), w, h, "jpeg"
    except ImportError:
        pass
    # Last resort — raw planar bytes; lossless but large.
    ch = arr.shape[2] if arr.ndim == 3 else 1
    return arr.tobytes(), w, h, f"raw_{arr.dtype}_{ch}ch"


def _encode_image(
    frame: np.ndarray,
    cam_name: str,
    cycle_id: int,
    timestamp: float,
) -> bytes:
    """Pack an image frame as msgpack (binary-native) or base64-JSON fallback."""
    compressed, w, h, fmt = _compress_image(frame)
    header: dict[str, Any] = {
        "cycle_id": cycle_id,
        "timestamp": timestamp,
        "camera_name": cam_name,
        "encoding": fmt,
        "width": w,
        "height": h,
    }
    if _HAS_MSGPACK:
        return msgpack.packb({**header, "data": compressed}, use_bin_type=True)
    import base64

    return _json({**header, "data": base64.b64encode(compressed).decode()})


# ── _WriterSession ────────────────────────────────────────────────────────────


class _WriterSession:
    """Owns one open MCAP file: writer, channel registry, and byte counter.

    All interaction happens from the single worker thread — no locking needed.
    """

    _GUARD_RESULT_SCHEMA_PROPS: dict[str, Any] = {
        "cycle_id": {"type": "integer"},
        "timestamp": {"type": "number"},
        "guard_name": {"type": "string"},
        "layer": {"type": "integer"},
        "decision": {"type": "integer"},
        "decision_name": {"type": "string"},
        "reason": {"type": "string"},
        "latency_ms": {"type": ["number", "null"]},
        "is_violation": {"type": "boolean"},  # REJECT or FAULT
        "is_clamp": {"type": "boolean"},  # CLAMP (action was modified)
        "fault_source": {"type": ["string", "null"]},
    }

    def __init__(self, path: Path, session_meta: dict[str, str]) -> None:
        self.path = path
        self._stream = open(path, "wb")  # noqa: SIM115 — kept open across cycles
        self._closed = False
        try:
            # Use a small chunk_size (4 KB) so each cycle group is flushed to disk
            # within ~200 ms at 15 Hz rather than waiting for the 1 MB default.
            # This makes in-progress sessions readable in the MCAP viewer with
            # minimal latency without disabling compression.
            _CHUNK_SIZE = 4096
            if _COMPRESSION is not None:
                self._writer = _MCAPWriter(
                    self._stream, compression=_COMPRESSION, chunk_size=_CHUNK_SIZE
                )
            else:
                self._writer = _MCAPWriter(self._stream, chunk_size=_CHUNK_SIZE)
            self._writer.start(profile="dam", library="dam-loopback-writer/1.0")

            self._opened_at = time.monotonic()
            self._bytes_written = 0

            # topic → channel_id  (lazily extended for /dam/images/{cam})
            self._channels: dict[str, int] = {}
            # schema_name → schema_id
            self._schemas: dict[str, int] = {}

            self._write_session_meta(session_meta)
            self._register_fixed_channels()
        except Exception:
            # Prevent fd leak if any post-open init step fails.
            self._stream.close()
            self._closed = True
            raise

    # ── Schema / channel helpers ───────────────────────────────────────────

    def _reg_schema(self, name: str, props: dict[str, Any]) -> int:
        if name not in self._schemas:
            self._schemas[name] = self._writer.register_schema(
                name=name,
                encoding="jsonschema",
                data=json.dumps({"type": "object", "properties": props}).encode(),
            )
        return self._schemas[name]

    def _reg_channel(
        self,
        topic: str,
        schema_id: int,
        encoding: str = "json",
        user_data: dict[str, str] | None = None,
    ) -> int:
        ch_id = self._writer.register_channel(
            topic=topic,
            message_encoding=encoding,
            schema_id=schema_id,
            metadata=user_data or {},
        )
        self._channels[topic] = ch_id
        return ch_id

    def _write_session_meta(self, data: dict[str, str]) -> None:
        self._writer.add_metadata(name="session", data=data)

    def _register_fixed_channels(self) -> None:
        # /dam/cycle
        sid = self._reg_schema(
            "dam.Cycle",
            {
                "cycle_id": {"type": "integer"},
                "timestamp": {"type": "number"},
                "active_task": {"type": ["string", "null"]},
                "active_boundaries": {"type": "array", "items": {"type": "string"}},
                "active_cameras": {"type": "array", "items": {"type": "string"}},
                "has_violation": {"type": "boolean"},
                "has_clamp": {"type": "boolean"},
                "violated_layer_mask": {"type": "integer"},
                "clamped_layer_mask": {"type": "integer"},
                "source_ms": {"type": "number"},
                "policy_ms": {"type": "number"},
                "guards_ms": {"type": "number"},
                "sink_ms": {"type": "number"},
                "total_ms": {"type": "number"},
            },
        )
        self._reg_channel("/dam/cycle", sid)

        # /dam/obs
        sid = self._reg_schema(
            "dam.Observation",
            {
                "cycle_id": {"type": "integer"},
                "timestamp": {"type": "number"},
                "joint_positions": {"type": "array", "items": {"type": "number"}},
                "joint_velocities": {"type": ["array", "null"]},
                "end_effector_pose": {"type": ["array", "null"]},
                "force_torque": {"type": ["array", "null"]},
            },
        )
        self._reg_channel(
            "/dam/obs",
            sid,
            user_data={"joint_position_unit": "rad", "joint_velocity_unit": "rad/s"},
        )

        # /dam/action
        sid = self._reg_schema(
            "dam.Action",
            {
                "cycle_id": {"type": "integer"},
                "timestamp": {"type": "number"},
                "target_positions": {"type": "array", "items": {"type": "number"}},
                "target_velocities": {"type": ["array", "null"]},
                "validated_positions": {"type": ["array", "null"]},
                "validated_velocities": {"type": ["array", "null"]},
                "was_clamped": {"type": "boolean"},
                "fallback_triggered": {"type": ["string", "null"]},
            },
        )
        self._reg_channel("/dam/action", sid)

        # /dam/L0 … /dam/L4  — each layer gets its own channel so downstream
        # tools can subscribe to exactly the layer they care about.
        for layer_int in range(5):
            sid = self._reg_schema(
                f"dam.GuardResult.L{layer_int}",
                self._GUARD_RESULT_SCHEMA_PROPS,
            )
            self._reg_channel(f"/dam/L{layer_int}", sid)

        # /dam/latency
        latency_props: dict[str, Any] = {
            "cycle_id": {"type": "integer"},
            "timestamp": {"type": "number"},
        }
        for key in ("source", "policy", "guards", "sink", "total", "L0", "L1", "L2", "L3", "L4"):
            latency_props[f"{key}_ms"] = {"type": "number"}
        sid = self._reg_schema("dam.Latency", latency_props)
        self._reg_channel("/dam/latency", sid, user_data={"unit": "ms"})

    def image_channel(self, cam_name: str) -> int:
        """Lazily register /dam/images/{cam} on first use."""
        topic = f"/dam/images/{cam_name}"
        if topic not in self._channels:
            enc = "msgpack" if _HAS_MSGPACK else "json"
            sid = self._reg_schema(
                f"dam.Image.{cam_name}",
                {
                    "cycle_id": {"type": "integer"},
                    "timestamp": {"type": "number"},
                    "camera_name": {"type": "string"},
                    "encoding": {"type": "string"},
                    "width": {"type": "integer"},
                    "height": {"type": "integer"},
                    "data": {"type": "string", "description": "JPEG bytes (msgpack bin / base64)"},
                },
            )
            self._reg_channel(topic, sid, encoding=enc)
        return self._channels[topic]

    # ── Message writing ────────────────────────────────────────────────────

    def write(self, topic: str, data: bytes, log_time_ns: int) -> None:
        self._writer.add_message(
            channel_id=self._channels[topic],
            log_time=log_time_ns,
            data=data,
            publish_time=log_time_ns,
        )
        self._bytes_written += len(data)

    def write_image(self, cam_name: str, data: bytes, log_time_ns: int) -> None:
        self._writer.add_message(
            channel_id=self.image_channel(cam_name),
            log_time=log_time_ns,
            data=data,
            publish_time=log_time_ns,
        )
        self._bytes_written += len(data)

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._writer.finish()
        finally:
            self._stream.close()

    def __del__(self) -> None:
        # Last-resort fd cleanup if close() was never called (e.g. GC).
        # writer.finish() is skipped intentionally — calling it from __del__
        # is unsafe (interpreter may be shutting down).
        if not self._closed:
            self._closed = True
            with contextlib.suppress(Exception):
                self._stream.close()

    @property
    def bytes_written(self) -> int:
        return self._bytes_written

    @property
    def age_seconds(self) -> float:
        return time.monotonic() - self._opened_at


# ── LoopbackWriter ────────────────────────────────────────────────────────────


class LoopbackWriter:
    """Streaming MCAP writer using Rust backend for all serialization and I/O.

    Usage::

        writer = LoopbackWriter(
            output_dir="/data/sessions",
            obs_bus=runtime._obs_bus,
            control_frequency_hz=config.safety.control_frequency_hz,
            window_sec=loopback_cfg.window_sec,
            rotate_mb=loopback_cfg.rotate_mb,
            rotate_minutes=loopback_cfg.rotate_minutes,
            max_queue_depth=loopback_cfg.max_queue_depth,
            session_meta={"robot_id": "arm_0", ...},
        )
        writer.start()
        # … control loop …
        writer.shutdown()
    """

    def __init__(
        self,
        output_dir: str,
        obs_bus: ObservationBus,
        control_frequency_hz: float,
        window_sec: float = 10.0,
        rotate_mb: float = 500.0,
        rotate_minutes: float = 60.0,
        max_queue_depth: int = 256,
        capture_images_on_clamp: bool = False,
        session_meta: dict[str, Any] | None = None,
    ) -> None:
        self._output_dir = Path(output_dir)
        self._obs_bus = obs_bus
        self._window_samples = max(1, int(window_sec * control_frequency_hz))
        self._rotate_bytes = int(rotate_mb * 1024 * 1024)
        self._rotate_seconds = rotate_minutes * 60.0
        self._max_queue_depth = max_queue_depth
        self._capture_images_on_clamp = capture_images_on_clamp

        self._session_id = uuid.uuid4().hex[:8]
        self._session_meta = self._build_session_meta(session_meta or {}, control_frequency_hz)

        self._mcap_writer: Any = None
        self._started = False
        self._session_path: Path | None = None
        self._pending_session_path: Path | None = None

    def start(self, session_path: str | None = None) -> None:
        if self._started:
            return
        self._started = True
        self._output_dir.mkdir(parents=True, exist_ok=True)
        # Store the requested path override (if any); actual file creation is
        # deferred to the first submit() call to avoid empty MCAP files when the
        # control loop never produces a cycle.
        self._pending_session_path = Path(session_path) if session_path else None
        logger.info(
            "LoopbackWriter: ready (file creation deferred until first cycle) → %s",
            self._output_dir,
        )

    def _ensure_writer(self) -> None:
        """Open the MCAP file on the first submitted cycle (lazy init)."""
        if self._mcap_writer is not None:
            return
        if self._pending_session_path is not None:
            path = self._pending_session_path
        else:
            path = self._output_dir / f"session_{self._session_id}_{int(time.time())}.mcap"
        self._mcap_writer = _RustMcapWriter()
        self._mcap_writer.start(str(path))
        self._session_path = path
        logger.info("LoopbackWriter: using Rust McapWriter → %s", path)
        logger.info("LoopbackWriter started → %s", self._output_dir)

    def submit(self, rec: CycleRecord, images: dict[str, np.ndarray] | None = None) -> None:
        """Submit a cycle record. Never blocks.

        Thread-safe: designed to be called from the control-loop thread.
        All I/O happens in Rust background thread.

        Args:
            rec: CycleRecord to write
            images: Optional dict of camera_name -> numpy image array (HWC, RGB)
        """
        if not self._started:
            return
        self._ensure_writer()
        # Capture reference to avoid NoneType during shutdown
        writer = self._mcap_writer
        if writer is not None:
            try:
                record_dict = _record_to_dict(rec, images)
                writer.write_cycle(json.dumps(record_dict, cls=_DAMEncoder))
            except Exception:
                logger.exception("LoopbackWriter: Rust write failed for cycle %d", rec.cycle_id)

    def shutdown(self, timeout: float = 15.0) -> None:
        """Signal stop, close file."""
        if not self._started:
            return
        self._started = False
        if self._mcap_writer is not None:
            self._mcap_writer = None
            logger.info("LoopbackWriter: closed")

    def __del__(self) -> None:
        if self._started:
            self.shutdown()

    # ── Worker thread ──────────────────────────────────────────────────────
    # Thread-safe: runs in dedicated daemon thread, never blocks the control loop.

    def _worker_loop(self) -> None:
        self._output_dir.mkdir(parents=True, exist_ok=True)
        session: _WriterSession | None = None

        try:
            while True:
                # Short timeout so _stop_event is checked promptly when idle.
                try:
                    rec = self._queue.get(timeout=0.05)
                except queue.Empty:
                    if self._stop_event.is_set():
                        break
                    continue

                # Check for sentinel FIRST, then process record
                if rec is _SENTINEL or self._stop_event.is_set():
                    # Flush remaining priority cycles before exit
                    if session:
                        self._flush_priority(session)
                    break

                if session is None:
                    try:
                        session = self._open_session()
                    except Exception:
                        logger.exception("LoopbackWriter: failed to open initial session")
                        continue

                try:
                    session = self._maybe_rotate(session)
                    self._write_cycle(session, rec, skip_images=False)
                except Exception:
                    logger.exception(
                        "LoopbackWriter: failed to write cycle %d — skipping",
                        rec.cycle_id,
                    )
        finally:
            try:
                if session:
                    session.close()
                    logger.info("LoopbackWriter: closed %s", session.path)
            except Exception:
                logger.exception("LoopbackWriter: error closing session")

    def _flush_priority(self, session: _WriterSession) -> None:
        """Write remaining violation/clamp cycles; discard normal cycles."""
        flushed = 0
        deadline = time.monotonic() + 3.0  # 3 second budget for flushing
        while True:
            if time.monotonic() > deadline:
                logger.warning("LoopbackWriter: flush deadline exceeded, dropping remaining cycles")
                break
            try:
                rec = self._queue.get_nowait()
            except queue.Empty:
                break
            if rec is _SENTINEL:
                break
            if isinstance(rec, CycleRecord) and (rec.has_violation or rec.has_clamp):
                try:
                    self._write_cycle(session, rec, skip_images=self._fast_flush)
                    flushed += 1
                except Exception:
                    logger.exception(
                        "LoopbackWriter: error flushing priority cycle %d", rec.cycle_id
                    )
        if flushed > 0:
            logger.info("LoopbackWriter: flushed %d priority cycles", flushed)

    def _open_session(self) -> _WriterSession:
        ts = int(time.time())
        path = self._output_dir / f"session_{self._session_id}_{ts}.mcap"
        session = _WriterSession(path, self._session_meta)
        logger.info("LoopbackWriter: opened %s", path)
        return session

    def _maybe_rotate(self, session: _WriterSession) -> _WriterSession:
        if (
            session.bytes_written >= self._rotate_bytes
            or session.age_seconds >= self._rotate_seconds
        ):
            logger.info(
                "LoopbackWriter: rotating (%.1f MB, %.0f min elapsed)",
                session.bytes_written / 1024 / 1024,
                session.age_seconds / 60.0,
            )
            session.close()
            return self._open_session()
        return session

    # ── Per-cycle serialisation ────────────────────────────────────────────

    def _write_cycle(
        self, session: _WriterSession, rec: CycleRecord, skip_images: bool = False
    ) -> None:
        log_time_ns = int(rec.obs_timestamp * 1_000_000_000)

        # 1. /dam/cycle — summary + violation / clamp flags
        #    All fields are already plain Python types; no numpy here.
        cycle_msg: dict[str, Any] = {
            "cycle_id": rec.cycle_id,
            "timestamp": rec.obs_timestamp,
            "active_task": rec.active_task,
            "active_boundaries": list(rec.active_boundaries),
            "has_violation": rec.has_violation,
            "has_clamp": rec.has_clamp,
            "violated_layer_mask": rec.violated_layer_mask,
            "clamped_layer_mask": rec.clamped_layer_mask,
            "source_ms": rec.latency_stages.get("source", 0.0),
            "policy_ms": rec.latency_stages.get("policy", 0.0),
            "guards_ms": rec.latency_stages.get("guards", 0.0),
            "sink_ms": rec.latency_stages.get("sink", 0.0),
            "total_ms": rec.latency_stages.get("total", 0.0),
        }
        session.write("/dam/cycle", _json(cycle_msg), log_time_ns)

        # 2. /dam/obs — joint state (fields already list[float])
        obs_msg: dict[str, Any] = {
            "cycle_id": rec.cycle_id,
            "timestamp": rec.obs_timestamp,
            "joint_positions": rec.obs_joint_positions,
        }
        if rec.obs_joint_velocities is not None:
            obs_msg["joint_velocities"] = rec.obs_joint_velocities
        if rec.obs_end_effector_pose is not None:
            obs_msg["end_effector_pose"] = rec.obs_end_effector_pose
        if rec.obs_force_torque is not None:
            obs_msg["force_torque"] = rec.obs_force_torque
        session.write("/dam/obs", _json(obs_msg), log_time_ns)

        # 3. /dam/action — proposal + validated result (fields already list[float])
        action_msg: dict[str, Any] = {
            "cycle_id": rec.cycle_id,
            "timestamp": rec.obs_timestamp,
            "target_positions": rec.action_positions,
            "was_clamped": rec.was_clamped,
            "fallback_triggered": rec.fallback_triggered,
        }
        if rec.action_velocities is not None:
            action_msg["target_velocities"] = rec.action_velocities
        if rec.validated_positions is not None:
            action_msg["validated_positions"] = rec.validated_positions
        if rec.validated_velocities is not None:
            action_msg["validated_velocities"] = rec.validated_velocities
        session.write("/dam/action", _json(action_msg), log_time_ns)

        # 4. /dam/L0 … /dam/L4 — one message per guard result.
        #    Multiple boundaries firing at once → multiple messages on the same
        #    channel, all sharing cycle_id for easy joining in analysis.
        for result in rec.guard_results:
            layer_int = int(result.layer)
            is_violation = result.decision in (GuardDecision.REJECT, GuardDecision.FAULT)
            is_clamp = result.decision == GuardDecision.CLAMP
            guard_msg: dict[str, Any] = {
                "cycle_id": rec.cycle_id,
                "timestamp": rec.obs_timestamp,
                "guard_name": result.guard_name,
                "layer": layer_int,
                "decision": int(result.decision),
                "decision_name": result.decision.name,
                "reason": result.reason,
                "latency_ms": rec.latency_guards.get(result.guard_name),
                "is_violation": is_violation,
                "is_clamp": is_clamp,
                "fault_source": result.fault_source,
            }
            session.write(f"/dam/L{layer_int}", _json(guard_msg), log_time_ns)

        # 5. /dam/latency — per-stage and per-layer breakdown
        latency_msg: dict[str, Any] = {
            "cycle_id": rec.cycle_id,
            "timestamp": rec.obs_timestamp,
        }
        for key in ("source", "policy", "guards", "sink", "total"):
            latency_msg[f"{key}_ms"] = rec.latency_stages.get(key, 0.0)
        for key in ("L0", "L1", "L2", "L3", "L4"):
            latency_msg[f"{key}_ms"] = rec.latency_layers.get(key, 0.0)
        session.write("/dam/latency", _json(latency_msg), log_time_ns)

        # 6. /dam/images/{cam} — fetched from ring buffer in the worker thread.
        #    Always written on REJECT/FAULT.  Written on CLAMP only when
        #    capture_images_on_clamp=True (off by default; CLAMPs are frequent).
        #    Skipped during fast flush (shutdown) to avoid blocking.
        want_images = not skip_images and (
            rec.has_violation or (self._capture_images_on_clamp and rec.has_clamp)
        )
        if want_images:
            self._write_images(session, rec, log_time_ns)

    def _write_images(
        self,
        session: _WriterSession,
        rec: CycleRecord,
        log_time_ns: int,
    ) -> None:
        """Read the ObservationBus ring buffer and write image frames to MCAP.

        Called from the worker thread only.  The ring buffer has 2× window
        capacity, so a few cycles of lag before we read is harmless.
        Pre-violation context is captured automatically because the ring buffer
        holds the last ``window_sec`` seconds of observations.
        """
        try:
            window: list[Any] = self._obs_bus.read_window(self._window_samples)
        except Exception:
            logger.exception(
                "LoopbackWriter: obs_bus.read_window() failed for cycle %d", rec.cycle_id
            )
            return

        for obs in window:
            if not isinstance(obs, Observation) or not obs.images:
                continue
            obs_log_time_ns = int(obs.timestamp * 1_000_000_000)
            for cam_name, frame in obs.images.items():
                try:
                    img_bytes = _encode_image(frame, cam_name, rec.cycle_id, obs.timestamp)
                    session.write_image(cam_name, img_bytes, obs_log_time_ns)
                except Exception:
                    logger.exception(
                        "LoopbackWriter: failed to encode image '%s' for cycle %d",
                        cam_name,
                        rec.cycle_id,
                    )

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _build_session_meta(user_meta: dict[str, Any], hz: float) -> dict[str, str]:
        """Build the once-per-file metadata dict (all values must be strings)."""
        import sys

        meta: dict[str, str] = {
            "session_id": uuid.uuid4().hex,
            "started_at_unix": str(int(time.time())),
            "control_frequency_hz": str(hz),
            "python_version": sys.version.split()[0],
            "writer": "dam-loopback-writer/1.0",
        }
        try:
            import dam  # type: ignore[import-untyped]

            meta["dam_version"] = getattr(dam, "__version__", "unknown")
        except ImportError:
            pass
        # Merge caller-supplied metadata last so it can override defaults.
        meta.update({str(k): str(v) for k, v in user_meta.items()})
        return meta
