"""LeRobotPolicyAdapter — wraps lerobot ACT / JIT policy to DAM predict() interface.

Supports two policy backends:

1. **Official lerobot** (ACT, Diffusion Policy, …)
   ``policy.select_action(obs_dict) → tensor[action_dim]``
   Auto-detected when the policy object has a ``select_action`` attribute.

2. **JIT / Isaac Lab style** (``torch.jit.load()``)
   ``policy(flat_obs_vector) → tensor[action_dim]``
   Used when ``select_action`` is absent (e.g. exported TorchScript models).
   The observation vector is built as ``[joint_positions, joint_velocities]``
   concatenated into a flat 1-D tensor.

Both backends return a DAM ``ActionProposal`` with joint positions in radians.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from dam.adapter.base import PolicyAdapter
from dam.types.action import ActionProposal
from dam.types.observation import Observation

logger = logging.getLogger(__name__)

_DEG2RAD = float(np.pi / 180.0)
_RAD2DEG = float(180.0 / np.pi)


class LeRobotPolicyAdapter(PolicyAdapter):
    def __init__(
        self,
        policy: Any,
        policy_name: str = "lerobot",
        n_action_steps: int = 1,
        device: str = "cpu",
        joint_names: list[str] | None = None,
        degrees_mode: bool = True,  # Most LeRobot policies are trained on Degrees
        preprocessor: Any | None = None,
        postprocessor: Any | None = None,
    ) -> None:
        self._policy = policy
        self._policy_name = policy_name
        self._n_action_steps = n_action_steps
        self._device = device
        self._joint_names: list[str] = joint_names or []
        self._degrees_mode = degrees_mode
        self._preprocessor = preprocessor
        self._postprocessor = postprocessor

        # Vectorised unit-conversion scales, bound once at init — same shape
        # AND same rule as LeRobotAdapter: ALL joints (gripper included) go
        # through deg↔rad together.  The framework's default position limits
        # express the gripper at ``1.75 rad ≈ 100°``; any "gripper exception"
        # used to leave gripper raw while limits were converted, which broke
        # joint_position_limits / joint_velocity_limit at the gripper index.
        n = len(self._joint_names) if self._joint_names else 6
        scale_in = _DEG2RAD if degrees_mode else 1.0
        scale_out = _RAD2DEG if degrees_mode else 1.0
        self._pos_scale_in = np.full(n, scale_in, dtype=np.float64)
        self._pos_scale_out = np.full(n, scale_out, dtype=np.float64)

        # Detect API: JIT models have no select_action method
        self._is_jit: bool = not hasattr(policy, "select_action")
        logger.info(
            "LeRobotPolicyAdapter: name=%s  device=%s  jit=%s  degrees_mode=%s",
            policy_name,
            device,
            self._is_jit,
            degrees_mode,
        )

    # ── PolicyAdapter ABC ──────────────────────────────────────────────────

    def initialize(self, config: dict[str, Any]) -> None:
        if "device" in config:
            self._device = config["device"]
        if "n_action_steps" in config:
            self._n_action_steps = int(config["n_action_steps"])

    def predict(self, obs: Observation) -> ActionProposal:
        if self._is_jit:
            return self._predict_jit(obs)
        return self._predict_lerobot(obs)

    def get_policy_name(self) -> str:
        return self._policy_name

    def reset(self) -> None:
        if hasattr(self._policy, "reset"):
            self._policy.reset()

    def preflight(self, camera_shapes: dict[str, tuple[int, int]] | None = None) -> None:
        """One dummy predict() at the real camera resolution to compile the
        PyTorch graph + run cuDNN's first-pass kernel benchmark for that
        shape, so the first real control cycle skips the ~400ms compile.

        We intentionally do *not* call ``self.reset()`` afterwards: empirically
        that wipes some lazily-initialised state and the next predict re-pays
        the full compile cost. The cost of leaving the warmup chunk in place
        is that the first N cycles of the next task may pop dummy actions
        from the action queue (N = policy chunk size). That's acceptable
        because L1 motion guards clamp output velocities regardless of
        what the policy produces."""
        import time

        dummy_images: dict[str, np.ndarray] = {}
        if camera_shapes:
            for cam, (h, w) in camera_shapes.items():
                dummy_images[cam] = np.zeros((h, w, 3), dtype=np.uint8)
        elif not self._is_jit:
            logger.info("LeRobotPolicyAdapter: skipping preflight (no camera shapes available)")
            return

        dummy_obs = Observation(
            timestamp=time.monotonic(),
            joint_positions=np.zeros(
                len(self._joint_names) if self._joint_names else 6, dtype=np.float32
            ),
            images=dummy_images or None,
        )
        t0 = time.perf_counter()
        try:
            self.predict(dummy_obs)
        except Exception as exc:
            logger.warning("LeRobotPolicyAdapter: preflight predict failed: %s", exc)
            return
        logger.info(
            "LeRobotPolicyAdapter: preflight warmup done in %.1f ms (shapes=%s)",
            (time.perf_counter() - t0) * 1000.0,
            camera_shapes or {},
        )

    # ── Official lerobot API ───────────────────────────────────────────────

    def _predict_lerobot(self, obs: Observation) -> ActionProposal:
        # Build the formal observation frame as expected by LeRobot processors
        # This matches the build_dataset_frame logic in lerobot_record.py
        observation_frame = self._build_lerobot_obs(obs)

        if self._preprocessor and self._postprocessor:
            # Use the official predict_action pattern
            import torch
            from lerobot.utils.control_utils import predict_action

            raw_action = predict_action(
                observation=observation_frame,
                policy=self._policy,
                device=torch.device(self._device),
                preprocessor=self._preprocessor,
                postprocessor=self._postprocessor,
                use_amp=False,
            )
        else:
            # Fallback to direct select_action (legacy or simple models)
            raw_action = self._policy.select_action(observation_frame)

        return self._convert_action(raw_action, obs.timestamp)

    def _build_lerobot_obs(self, obs: Observation) -> dict[str, Any]:
        """Build a dict of NumPy arrays expected by predict_action.

        DAM stores joint state in radians; lerobot policies were trained on the
        source's native unit (degrees when ``degrees_mode=True``).  One
        vectorised multiply by ``_pos_scale_out`` handles the conversion for
        all joints (gripper included — see ``__init__`` for the rationale).
        """
        rad = np.asarray(obs.joint_positions, dtype=np.float64)
        n = min(rad.shape[0], self._pos_scale_out.shape[0])
        state = rad.copy()
        state[:n] = rad[:n] * self._pos_scale_out[:n]
        out: dict[str, Any] = {"observation.state": state.astype(np.float32)}
        if obs.images:
            for cam_name, img in obs.images.items():
                # Raw image [H, W, C] uint8
                out[f"observation.images.{cam_name}"] = img.copy()
        return out

    # ── JIT / Isaac Lab API ────────────────────────────────────────────────

    def _predict_jit(self, obs: Observation) -> ActionProposal:
        obs_vector = self._build_jit_obs(obs)
        try:
            import torch

            with torch.no_grad():
                raw = self._policy(obs_vector)
        except Exception as e:
            raise RuntimeError(f"JIT policy forward pass failed: {e}") from e
        return self._convert_action(raw, obs.timestamp)

    def _build_jit_obs(self, obs: Observation) -> Any:
        """Flat obs vector for JIT models: [joint_positions, joint_velocities]."""
        import torch

        parts = [obs.joint_positions.astype(np.float32)]
        if obs.joint_velocities is not None:
            parts.append(obs.joint_velocities.astype(np.float32))
        flat = np.concatenate(parts)
        return torch.tensor(flat, dtype=torch.float32).unsqueeze(0).to(self._device)

    # ── Shared action conversion ───────────────────────────────────────────

    def _convert_action(self, raw: Any, timestamp: float = 0.0) -> ActionProposal:
        """Converts raw LeRobot policy output to a DAM ActionProposal.
        Robustly handles:
          - Dictionary outputs (extracts 'action')
          - Multi-step (Diffusion) outputs (takes index 0)
          - Batch dimensions
        """
        # 1. Extract from dict if necessary
        if isinstance(raw, dict):
            raw = raw.get("action", raw)

        try:
            arr = raw.detach().cpu().numpy() if hasattr(raw, "detach") else np.asarray(raw)
        except Exception:  # noqa: BLE001 — tensor conversion failed; fall back to np.asarray
            arr = np.asarray(raw)

        # 3. Handle multi-step/batch dimensions [..., T, D] -> [D]
        # Common shapes: [D], [T, D], [1, T, D]
        if arr.ndim >= 2:
            # We assume index 0 is the immediate next step.
            if arr.shape[0] == 1 and arr.ndim == 3:
                arr = arr[0]
            if arr.ndim == 2:
                arr = arr[0]

        arr = arr.flatten()

        # so101/so100: 6 joints, last is gripper.  One vectorised multiply
        # converts deg → rad while leaving the gripper element untouched
        # (the pre-bound ``_pos_scale_in`` has a 1.0 at the gripper index).
        joints_raw = arr[:6].astype(np.float64) if len(arr) >= 6 else arr.astype(np.float64)
        n = min(joints_raw.shape[0], self._pos_scale_in.shape[0])
        joints = joints_raw.copy()
        joints[:n] = joints_raw[:n] * self._pos_scale_in[:n]

        # Gripper action stays in its native unit (matches LeRobotAdapter's
        # rule that gripper is never deg/rad-converted).
        gripper_val = float(arr[-1]) if len(arr) > 6 else (float(arr[5]) if len(arr) == 6 else None)

        return ActionProposal(
            target_joint_positions=joints.astype(np.float32),
            timestamp=timestamp or 0.0,
            gripper_action=gripper_val,
            confidence=1.0,
            policy_name=self._policy_name,
        )
