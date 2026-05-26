"""Dataset adapter source and policy for LeRobot dataset replay.

Usage
-----
Used in simulation mode when a ``simulation.dataset_repo_id`` is set in the
Stackfile, and by dataset-to-hardware replay templates. Replays observations
(joint positions + camera images) from the dataset; ``DatasetReplayPolicy``
can emit each recorded action through the normal guard/sink path.

The source iterates through one episode in sequence; when it reaches the end
it wraps around to the beginning.  This gives infinite continuous replay for
demo and regression-testing purposes.

Initialisation
--------------
Dataset download / decoding happens in ``__init__``.  This call blocks until
the dataset is ready (typically a few seconds for cached data, longer on first
download).  The control loop does NOT start until the factory returns the
source, so blocking in ``__init__`` is acceptable and avoids partial-init race
conditions.

Image format
------------
LeRobot datasets store camera frames as CHW float32 tensors in [0, 1].
This class converts them to HWC uint8 numpy arrays ([0, 255]) to match the
format expected by ``Observation.images`` and the MCAP image encoder.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np

from dam.adapter.base import PolicyAdapter
from dam.types.action import ActionProposal
from dam.types.observation import Observation

logger = logging.getLogger(__name__)


class DatasetSimSource:
    """Replay a LeRobot dataset episode as a live observation stream.

    Parameters
    ----------
    repo_id:
        HuggingFace repo ID for the dataset (e.g. ``MikeChenYZ/soarm-fmb-v2``).
    episode:
        Episode index to replay (default 0).
    hz:
        Control frequency — used only for velocity estimation via finite
        difference when the dataset does not include velocity data.
    """

    def __init__(
        self,
        repo_id: str,
        episode: int = 0,
        hz: float = 10.0,
        degrees_mode: bool = True,
        *,
        strict: bool = False,
    ) -> None:
        self._repo_id = repo_id
        self._episode = episode
        self._hz = hz
        self._degrees_mode = degrees_mode
        self._cursor: float = 0.0
        self._current_frame: dict[str, Any] | None = None
        self._strict = strict

        # Pre-loaded episode data: list of {"joint_positions", "images"?, "action"?}
        self._frames: list[dict[str, Any]] = []
        self._prev_pos: np.ndarray | None = None

        logger.info("DatasetSimSource: loading %s episode %d …", repo_id, episode)
        try:
            self._frames, self._source_fps = self._load_episode(repo_id, episode)
            if strict and (
                not self._frames or any("action" not in frame for frame in self._frames)
            ):
                raise ValueError(
                    "Dataset hardware replay requires a non-empty episode with an action per frame"
                )
            logger.info(
                "DatasetSimSource: ready — %d frames, source_fps: %.1f, cameras: %s",
                len(self._frames),
                self._source_fps,
                sorted({k for f in self._frames[:1] for k in (f.get("images") or {})}),
            )
        except Exception:
            if strict:
                raise
            logger.exception(
                "DatasetSimSource: failed to load %s — falling back to random walk",
                repo_id,
            )
            self._frames = []
            self._source_fps = hz

    # ── Public API ─────────────────────────────────────────────────────────

    def connect(self) -> None:
        """Reset playback state on fresh connection/task start."""
        self._cursor = 0.0
        self._current_frame = None
        self._prev_pos = None
        logger.info("DatasetSimSource: playback cursor reset")

    def disconnect(self) -> None:
        """No-op for simulation."""
        pass

    def read(self) -> Observation:
        if not self._frames:
            return self._random_obs()

        # Step-driven: advance cursor by (source_fps / runtime_hz) frames per call.
        # This keeps replay pace proportional to the control loop rate regardless
        # of wall-clock jitter, GC pauses, or guard latency spikes.
        index = int(self._cursor)
        frame = self._frames[index % len(self._frames)]
        self._current_frame = frame
        self._cursor += self._source_fps / self._hz

        joint_pos: np.ndarray = frame["joint_positions"]

        if self._degrees_mode:
            joint_pos = joint_pos * (np.pi / 180.0)

        # Velocity: dt is the real time between control cycles (1/runtime_hz),
        # which is the interval between successive commands sent to the robot.
        if self._prev_pos is not None:
            dt = 1.0 / self._hz
            velocity = (joint_pos - self._prev_pos) / dt
        else:
            velocity = np.zeros_like(joint_pos)
        self._prev_pos = joint_pos

        return Observation(
            timestamp=time.monotonic(),
            joint_positions=joint_pos.copy(),
            joint_velocities=velocity,
            images=frame.get("images"),
        )

    def current_action(self) -> np.ndarray:
        """Return the recorded action paired with the most recently read observation."""
        if self._current_frame is None:
            raise RuntimeError("DatasetReplayPolicy called before the dataset source was read")
        action = self._current_frame.get("action")
        if action is None:
            raise RuntimeError(
                "Dataset has no 'action' field; hardware replay requires recorded actions"
            )
        action_arr = np.asarray(action, dtype=float)
        if self._degrees_mode:
            action_arr = action_arr * (np.pi / 180.0)
        return action_arr.copy()

    # ── Dataset loading ────────────────────────────────────────────────────

    @staticmethod
    def _load_episode(repo_id: str, episode: int) -> tuple[list[dict[str, Any]], float]:
        """Download + decode one episode.  Returns (frames, fps)."""
        try:
            from lerobot.datasets.lerobot_dataset import LeRobotDataset
        except ImportError as exc:
            raise ImportError(
                "lerobot is required for DatasetSimSource. Install it with: make setup-lerobot"
            ) from exc

        import torch
        from torch.utils.data import DataLoader

        ds = LeRobotDataset(repo_id, episodes=[episode])
        frames: list[dict[str, Any]] = []

        # num_workers=0: single-process iteration — prevents leaked semaphore
        # warnings from torch multiprocessing workers at shutdown.
        # pin_memory=False: avoids CUDA page-locked memory on CPU-only setups.
        loader = DataLoader(ds, batch_size=1, num_workers=0, pin_memory=False)

        for batch in loader:
            # DataLoader wraps each value in a batch dimension — squeeze it back
            item = {k: (v[0] if isinstance(v, torch.Tensor) else v) for k, v in batch.items()}
            frame: dict[str, Any] = {}

            # ── Joint positions ────────────────────────────────────────
            state = item.get("observation.state")
            if state is None:
                # Some datasets use different key names
                state = item.get("state") or item.get("joints")
            if state is not None:
                if isinstance(state, torch.Tensor):
                    state = state.detach().cpu().numpy()
                frame["joint_positions"] = np.asarray(state, dtype=float)
            else:
                frame["joint_positions"] = np.zeros(6)

            # ── Camera images ──────────────────────────────────────────
            images: dict[str, np.ndarray] = {}
            for key, val in item.items():
                if not key.startswith("observation.images."):
                    continue
                cam = key.split(".", 2)[2]  # e.g. "top" or "wrist"
                if isinstance(val, torch.Tensor):
                    # CHW float32 [0,1] → HWC uint8 [0,255]
                    t = val.detach().cpu()
                    if t.ndim == 3 and t.shape[0] in (1, 3, 4):
                        t = t.permute(1, 2, 0)
                    arr: np.ndarray[tuple[int, ...], np.dtype[np.uint8]] = (
                        (t.numpy() * 255).clip(0, 255).astype(np.uint8)
                    )
                else:
                    arr = np.asarray(val, dtype=np.uint8)
                if arr.ndim == 2:
                    arr = arr[:, :, np.newaxis]
                images[cam] = arr

            if images:
                frame["images"] = images

            action = item.get("action")
            if action is not None:
                if isinstance(action, torch.Tensor):
                    action = action.detach().cpu().numpy()
                frame["action"] = np.asarray(action, dtype=float)

            frames.append(frame)

        # Explicitly delete the loader so Python can clean up its internal
        # shared-memory / semaphore state before the resource_tracker runs.
        del loader

        return frames, float(getattr(ds, "fps", 30.0))

    # ── Fallback ───────────────────────────────────────────────────────────

    def _random_obs(self) -> Observation:
        """Random-walk fallback when dataset loading fails."""
        if not hasattr(self, "_rng"):
            self._rng = np.random.default_rng(42)
            self._rng_pos = self._rng.uniform(-0.3, 0.3, size=6)
        delta = self._rng.normal(0.0, 0.03, size=6)
        self._rng_pos = np.clip(self._rng_pos + delta, -1.9, 1.9)
        return Observation(
            timestamp=time.monotonic(),
            joint_positions=self._rng_pos.copy(),
        )


class DatasetReplayPolicy(PolicyAdapter):
    """Emit the action recorded alongside the source's current dataset frame."""

    def __init__(self, source: DatasetSimSource) -> None:
        self._source = source

    def initialize(self, config: dict[str, Any]) -> None:
        pass

    def predict(self, obs: Observation) -> ActionProposal:
        return ActionProposal(
            target_joint_positions=self._source.current_action(),
            timestamp=obs.timestamp,
            policy_name="dataset_replay",
        )

    def get_policy_name(self) -> str:
        return "dataset_replay"

    def reset(self) -> None:
        pass
