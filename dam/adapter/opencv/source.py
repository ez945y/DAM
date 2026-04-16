"""OpenCVSourceAdapter — bridges standard webcams/video devices to DAM Observation."""

from __future__ import annotations

import logging
import time
from typing import Any

import cv2
import numpy as np

from dam.adapter.base import SensorAdapter
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
    ) -> None:
        self._index = index
        self._name = name
        self._width = width
        self._height = height
        self._cap: cv2.VideoCapture | None = None
        self._connected = False

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

        self._connected = True
        logger.info("OpenCVSourceAdapter: Camera '%s' connected (idx=%s)", self._name, self._index)

    def verify(self) -> None:
        """Perform a preflight check by reading a single frame."""
        if not self._connected:
            self.connect()

        ret, frame = self._cap.read()
        if not ret or frame is None:
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
        """Capture one frame into an Observation metadata container."""
        if not self._connected:
            self.connect()

        ret, frame = self._cap.read()
        now = time.monotonic()

        if not ret or frame is None:
            logger.error("OpenCVSourceAdapter: Frame capture failed on '%s'", self._name)
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

        # OpenCV returns BGR, but we often want RGB for policies (LeRobot/ACT)
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        return Observation(
            timestamp=now,
            joint_positions=np.array([]),  # Partial observation
            images={self._name: frame_rgb},
        )

    def is_healthy(self) -> bool:
        return self._connected and self._cap is not None and self._cap.isOpened()

    def disconnect(self) -> None:
        """Release the camera."""
        self._connected = False
        if self._cap:
            self._cap.release()
            self._cap = None
        logger.info("OpenCVSourceAdapter: Camera '%s' disconnected", self._name)
