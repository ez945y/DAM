"""OpenCVSourceAdapter — bridges standard webcams/video devices to DAM Observation."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import cv2
import numpy as np

from dam.adapter.base import SensorAdapter
from dam.camera.frame_hub import CameraFrameHub
from dam.types.observation import Observation

logger = logging.getLogger(__name__)


class OpenCVSourceAdapter(SensorAdapter):
    """SensorAdapter for standard OpenCV-compatible cameras.

    This adapter captures single frames and wraps them into a partial
    DAM Observation (containing only images). It is designed to be
    merged with other sources (like robot arms) in the GuardRuntime.

    Parameters
    ----------
    index:
        The camera index (integer, e.g., 0) or device path (string).
    name:
        The identifying name for this camera (e.g., 'top', 'wrist').
        This will be the key in observation.images.
    width:
        Desired width (optional).
    height:
        Desired height (optional).
    """

    def __init__(
        self,
        index: int | str,
        name: str = "camera",
        width: int | None = None,
        height: int | None = None,
        jpeg_fps: float = 30.0,
        frame_hub: CameraFrameHub | None = None,
        max_consecutive_failures: int = 5,
    ) -> None:
        self._index = index
        self._name = name
        self._width = width
        self._height = height
        self._jpeg_interval_s = 1.0 / jpeg_fps if jpeg_fps > 0 else 0.0
        self._frame_hub = frame_hub
        self._max_consecutive_failures = max(1, int(max_consecutive_failures))
        self._cap: cv2.VideoCapture | None = None
        self._connected = False
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._capture_thread: threading.Thread | None = None
        self._latest_frame_rgb: np.ndarray | None = None
        self._latest_ts: float | None = None
        self._latest_jpeg_t = 0.0
        self._last_error_log_t = 0.0
        self._consecutive_failures = 0
        self._fatal_error: str | None = None

    def set_frame_hub(self, frame_hub: CameraFrameHub) -> None:
        self._frame_hub = frame_hub

    def connect(self) -> None:
        """Open the camera device."""
        if self._connected:
            return

        self._cap = cv2.VideoCapture(self._index)
        if not self._cap.isOpened():
            raise RuntimeError(f"OpenCVSourceAdapter: Could not open camera at index {self._index}")

        if self._width:
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
        if self._height:
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        self._connected = True
        self._stop_event.clear()
        self._capture_thread = threading.Thread(
            target=self._capture_loop,
            daemon=True,
            name=f"opencv-source-{self._name}",
        )
        self._capture_thread.start()
        logger.info("OpenCVSourceAdapter: Camera '%s' connected (idx=%s)", self._name, self._index)

    def verify(self) -> None:
        """Perform a preflight check by waiting for the capture thread's first frame."""
        if not self._connected:
            self.connect()

        deadline = time.monotonic() + 2.0
        frame: np.ndarray | None = None
        while time.monotonic() < deadline:
            with self._lock:
                frame = self._latest_frame_rgb
            if frame is not None:
                break
            time.sleep(0.01)

        if frame is None:
            raise RuntimeError(
                f"OpenCVSourceAdapter: Camera '{self._name}' failed preflight read check."
            )

        logger.info(
            "OpenCVSourceAdapter: Camera '%s' verify OK (%dx%d)",
            self._name,
            frame.shape[1],
            frame.shape[0],
        )

    def read(self) -> Observation:
        """Return the latest captured frame without blocking the control loop."""
        with self._lock:
            fatal_error = self._fatal_error
        if fatal_error is not None:
            raise RuntimeError(fatal_error)

        if not self._connected:
            self.connect()

        with self._lock:
            frame_rgb = (
                self._latest_frame_rgb.copy() if self._latest_frame_rgb is not None else None
            )
            frame_ts = self._latest_ts

        now = time.monotonic()
        if frame_rgb is None:
            return Observation(
                timestamp=now,
                joint_positions=np.array([]),
                metadata={
                    "hardware_status": {
                        "error_codes": [-1],
                        "reason": f"Camera '{self._name}' read failure",
                    }
                },
            )

        return Observation(
            timestamp=frame_ts or now,
            joint_positions=np.array([]),  # Partial observation
            images={self._name: frame_rgb},
        )

    def is_healthy(self) -> bool:
        with self._lock:
            fatal_error = self._fatal_error
        return (
            fatal_error is None
            and self._connected
            and self._cap is not None
            and self._cap.isOpened()
        )

    def disconnect(self) -> None:
        """Release the camera."""
        if not self._connected and self._cap is None:
            return
        self._connected = False
        self._stop_event.set()
        thread = self._capture_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._capture_thread = None
        if self._cap:
            self._cap.release()
            self._cap = None
        with self._lock:
            self._latest_frame_rgb = None
            self._latest_ts = None
            self._fatal_error = None
            self._consecutive_failures = 0
        logger.info("OpenCVSourceAdapter: Camera '%s' disconnected", self._name)

    def _capture_loop(self) -> None:
        while not self._stop_event.is_set():
            cap = self._cap
            if cap is None:
                break
            ret, frame = cap.read()
            if not ret or frame is None:
                if self._stop_event.is_set() or not self._connected:
                    break
                now = time.monotonic()
                with self._lock:
                    self._consecutive_failures += 1
                    consecutive_failures = self._consecutive_failures
                if now - self._last_error_log_t > 1.0:
                    self._last_error_log_t = now
                    logger.error("OpenCVSourceAdapter: Frame capture failed on '%s'", self._name)
                if consecutive_failures >= self._max_consecutive_failures:
                    reason = (
                        f"OpenCVSourceAdapter: Camera '{self._name}' disconnected after "
                        f"{consecutive_failures} consecutive capture failures"
                    )
                    with self._lock:
                        self._fatal_error = reason
                        self._latest_frame_rgb = None
                        self._latest_ts = None
                    self._connected = False
                    self._stop_event.set()
                    cap.release()
                    self._cap = None
                    logger.error(reason)
                    break
                time.sleep(0.01)
                continue

            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            ts = time.monotonic()
            with self._lock:
                self._consecutive_failures = 0
                self._latest_frame_rgb = frame_rgb
                self._latest_ts = ts

            if self._frame_hub is not None and ts - self._latest_jpeg_t >= self._jpeg_interval_s:
                self._latest_jpeg_t = ts
                ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if ok:
                    self._frame_hub.put_frame(
                        camera=self._name,
                        timestamp=ts,
                        frame=frame_rgb,
                        jpeg=bytes(buf),
                        width=frame_rgb.shape[1],
                        height=frame_rgb.shape[0],
                    )
            elif self._frame_hub is not None:
                self._frame_hub.put_frame(camera=self._name, timestamp=ts, frame=frame_rgb)
