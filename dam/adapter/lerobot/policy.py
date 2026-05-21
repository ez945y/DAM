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

        # Detect API: JIT models have no select_action method
        self._is_jit: bool = not hasattr(policy, "select_action")
        logger.info(
            "LeRobotPolicyAdapter: name=%s  device=%s  jit=%s",
            policy_name,
            device,
            self._is_jit,
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
        """Build a dict of NumPy arrays expected by predict_action."""
        state = obs.joint_positions.astype(np.float32)
        if self._degrees_mode:
            # Convert DAM radians to LeRobot expected degrees
            state = np.degrees(state)

        out: dict[str, Any] = {"observation.state": state}
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

        # so101/so100: 6 joints, last is gripper
        joints = arr[:6] if len(arr) >= 6 else arr

        if self._degrees_mode:
            # Convert LeRobot degrees back to DAM radians
            for i in range(len(joints)):
                # We convert all joints to radians.
                # Grippers in degrees (0-100) will be converted (0-1.74 rad).
                # If a gripper is truly 0-1 normalized, radians(1.0) is 0.017 rad,
                # which is usually safe but might need special handling if accuracy matters.
                # For SoArm, they are degrees, so conversion is REQUIRED.
                joints[i] = np.radians(joints[i])

        gripper_val = float(arr[-1]) if len(arr) > 6 else (float(arr[5]) if len(arr) == 6 else None)

        return ActionProposal(
            target_joint_positions=joints.astype(np.float32),
            timestamp=timestamp or 0.0,
            gripper_action=gripper_val,
            confidence=1.0,
            policy_name=self._policy_name,
        )

    def _is_gripper_joint(self, index: int) -> bool:
        """Heuristic to detect if a joint index is the gripper."""
        if not self._joint_names:
            return index == 5  # Standard so101
        name = self._joint_names[index].lower()
        return "gripper" in name or "finger" in name
