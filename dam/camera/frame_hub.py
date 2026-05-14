"""Camera frame hub for live preview and recorder consumers.

This is deliberately outside ``dam.runtime``. Camera adapters publish
pre-compressed JPEG frames from their own capture threads; control-loop code
can stay scalar-only, while live preview and Rust recording consume the latest
or windowed frames independently.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_RustImageHub: Any
try:
    from dam_rs import ImageHub as _RustImageHub
except ImportError:
    _RustImageHub = None


@dataclass(frozen=True)
class ImageFrame:
    camera: str
    timestamp: float
    jpeg: bytes
    width: int
    height: int


class CameraFrameHub:
    def __init__(self, window_sec: float = 10.0) -> None:
        self._window_sec = max(0.1, float(window_sec))
        self._frames: deque[ImageFrame] = deque()
        self._latest: dict[str, ImageFrame] = {}
        self._latest_arrays: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._rust_hub = _RustImageHub(self._window_sec) if _RustImageHub is not None else None
        if self._rust_hub is None:
            logger.warning(
                "CameraFrameHub: dam_rs.ImageHub unavailable; using Python fallback image cache"
            )

    def put_frame(
        self,
        camera: str,
        timestamp: float,
        frame: Any,
        jpeg: bytes | None = None,
        width: int | None = None,
        height: int | None = None,
    ) -> None:
        with self._lock:
            self._latest_arrays[camera] = frame
        if jpeg:
            inferred_height = height if height is not None else getattr(frame, "shape", [0, 0])[0]
            inferred_width = width if width is not None else getattr(frame, "shape", [0, 0])[1]
            self.put_jpeg(camera, timestamp, jpeg, int(inferred_width), int(inferred_height))

    def put_jpeg(
        self,
        camera: str,
        timestamp: float,
        jpeg: bytes,
        width: int,
        height: int,
    ) -> None:
        if not jpeg:
            return
        jpeg_bytes = bytes(jpeg)
        if self._rust_hub is not None:
            self._rust_hub.submit_jpeg(
                camera,
                float(timestamp),
                int(width),
                int(height),
                jpeg_bytes,
            )
        frame = ImageFrame(
            camera=camera,
            timestamp=timestamp,
            jpeg=jpeg_bytes,
            width=int(width),
            height=int(height),
        )
        with self._lock:
            self._frames.append(frame)
            self._latest[camera] = frame
            self._trim_locked(timestamp)

    def latest_jpegs(self) -> dict[str, bytes]:
        if self._rust_hub is not None:
            return {name: bytes(data) for name, _ts, _w, _h, data in self._rust_hub.latest_all()}
        with self._lock:
            return {name: frame.jpeg for name, frame in self._latest.items()}

    def latest_arrays(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._latest_arrays)

    def frames_between(self, start: float, end: float) -> list[ImageFrame]:
        with self._lock:
            return [frame for frame in self._frames if start <= frame.timestamp <= end]

    def latest_window(self, end: float, window_sec: float) -> list[ImageFrame]:
        return self.frames_between(end - max(0.0, window_sec), end)

    def attach_to_writer(
        self,
        writer: Any,
        *,
        window_sec: float,
        capture_images_on_clamp: bool,
    ) -> None:
        if self._rust_hub is None or not hasattr(writer, "attach_image_hub"):
            return
        writer.attach_image_hub(
            self._rust_hub,
            float(window_sec),
            bool(capture_images_on_clamp),
        )

    def _trim_locked(self, now: float) -> None:
        min_ts = now - self._window_sec
        while self._frames and self._frames[0].timestamp < min_ts:
            self._frames.popleft()
