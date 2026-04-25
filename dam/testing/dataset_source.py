"""DatasetSimSource — replay a LeRobot HuggingFace dataset as live observations.

Usage
-----
Used in simulation mode when a ``simulation.dataset_repo_id`` is set in the
Stackfile.  Replays observations (joint positions + camera images) from the
dataset so that the full DAM pipeline (policy → guards → MCAP) runs on real
robot data without physical hardware.

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
    ) -> None:
        self._repo_id = repo_id
        self._episode = episode
        self._hz = hz
        self._degrees_mode = degrees_mode
        self._cursor = 0

        # Pre-loaded episode data: list of {"joint_positions", "images"?, "action"?}
        self._frames: list[dict[str, Any]] = []
        self._prev_pos: np.ndarray | None = None

        logger.info("DatasetSimSource: loading %s episode %d …", repo_id, episode)
        try:
            self._frames = self._load_episode(repo_id, episode)
            logger.info(
                "DatasetSimSource: ready — %d frames, cameras: %s",
                len(self._frames),
                sorted({k for f in self._frames[:1] for k in (f.get("images") or {})}),
            )
        except Exception:
            logger.exception(
                "DatasetSimSource: failed to load %s — falling back to random walk",
                repo_id,
            )
            self._frames = []

    # ── Public API ─────────────────────────────────────────────────────────

    def read(self) -> Observation:
        if not self._frames:
            return self._random_obs()

        frame = self._frames[self._cursor % len(self._frames)]
        self._cursor += 1

        joint_pos: np.ndarray = frame["joint_positions"]

        # Convert degrees → radians if the dataset stores angles in degrees
        # (LeRobot SO-101 protocol uses degrees; DAM guard limits are in radians)
        if self._degrees_mode:
            joint_pos = joint_pos * (np.pi / 180.0)

        # Finite-difference velocity when dataset doesn't include it
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
            images=frame.get("images"),  # dict[str, np.ndarray HWC uint8] or None
        )

    # ── Dataset loading ────────────────────────────────────────────────────

    @staticmethod
    def _load_episode(repo_id: str, episode: int) -> list[dict[str, Any]]:
        """Download + decode one episode.  Returns a list of frame dicts."""
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

            frames.append(frame)

        # Explicitly delete the loader so Python can clean up its internal
        # shared-memory / semaphore state before the resource_tracker runs.
        del loader

        return frames

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
