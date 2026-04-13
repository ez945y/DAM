"""MCAP Context Capture — saves a ring buffer of observations around violations."""

from __future__ import annotations

import logging
import pickle
import time
from pathlib import Path
from typing import Any

from dam.bus import ObservationBus
from dam.types.observation import Observation

logger = logging.getLogger(__name__)

try:
    from mcap.writer import Writer as MCAPWriter

    HAS_MCAP = True
except ImportError:
    HAS_MCAP = False
    MCAPWriter = None  # type: ignore[assignment,misc]


class MCAPContextCapture:
    """Maintains a rolling ring buffer of observations.

    On ``capture_violation()``, saves ±window_sec of observations to disk.

    File format: MCAP if available (pip install mcap), otherwise pickle.
    """

    def __init__(
        self,
        window_sec: float = 30.0,
        hz: float = 50.0,
        output_path: str = "/tmp/dam_loopback",
        capture_on_violation: bool = True,
    ) -> None:
        # Compute ring-buffer capacity from window parameters.
        # ObservationBus(capacity) is the unified constructor for both
        # the Rust extension and the Python fallback.
        capacity = int(window_sec * hz) + 10
        self._bus = ObservationBus(capacity=capacity)
        self._window_samples = capacity
        self._window_sec = window_sec
        self._hz = hz
        self._output_path = output_path
        self._capture_on_violation = capture_on_violation
        self._violation_count = 0

    def record(self, obs: Observation) -> None:
        """Call each cycle to keep the ring buffer up to date."""
        self._bus.write(obs)

    def capture_violation(self, reason: str = "", cycle_id: int = 0) -> str | None:
        """Save the current ring buffer to disk.

        Returns the output file path, or None if capture is disabled.
        """
        if not self._capture_on_violation:
            return None

        self._violation_count += 1
        # read_window(n) returns last n samples — take the full window
        observations = self._bus.read_window(self._window_samples)

        timestamp = int(time.time())
        base = f"{self._output_path}_{timestamp}_v{self._violation_count}"

        if HAS_MCAP:
            path = base + ".mcap"
            self._write_mcap(path, observations, reason, cycle_id)
        else:
            path = base + ".pkl"
            self._write_pickle(path, observations, reason, cycle_id)

        logger.info("Context capture saved: %s (%d obs)", path, len(observations))
        return path

    def _write_pickle(self, path: str, observations: list[Any], reason: str, cycle_id: int) -> None:
        payload = {
            "reason": reason,
            "cycle_id": cycle_id,
            "captured_at": time.time(),
            "window_sec": self._window_sec,
            "hz": self._hz,
            "observations": observations,
        }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(payload, f)

    def _write_mcap(self, path: str, observations: list[Any], reason: str, cycle_id: int) -> None:
        """Write observations to MCAP format.  Requires ``pip install mcap``."""
        import json

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as stream:
            writer = MCAPWriter(stream)
            writer.start()
            schema_id = writer.register_schema(
                name="dam.Observation",
                encoding="jsonschema",
                data=json.dumps({"type": "object"}).encode(),
            )
            channel_id = writer.register_channel(
                topic="/dam/observations",
                message_encoding="json",
                schema_id=schema_id,
            )
            for obs in observations:
                if not isinstance(obs, Observation):
                    continue
                msg_data: dict[str, Any] = {
                    "timestamp": obs.timestamp,
                    "joint_positions": obs.joint_positions.tolist(),
                }
                if obs.joint_velocities is not None:
                    msg_data["joint_velocities"] = obs.joint_velocities.tolist()
                if obs.end_effector_pose is not None:
                    msg_data["end_effector_pose"] = obs.end_effector_pose.tolist()
                msg = json.dumps(msg_data).encode()
                writer.add_message(
                    channel_id=channel_id,
                    log_time=int(obs.timestamp * 1e9),
                    data=msg,
                    publish_time=int(obs.timestamp * 1e9),
                )
            writer.finish()
