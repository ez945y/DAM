"""Isaac Sim safety filter — wraps SafetyGuard for Isaac Sim tensor workflows.

Designed for Isaac Sim 6.0+ (isaacsim pip package). Handles:
  - torch.Tensor input/output (preserves device and dtype)
  - Batched environments (vectorized safe() over N envs)
  - Auto-extraction of joint limits from ArticulationView
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    import torch


class IsaacSafetyFilter:
    """Drop-in safety filter for Isaac Sim control loops.

    Wraps :class:`dam.SafetyGuard` with Isaac-native tensor I/O and
    optional vectorized (multi-env) support.

    .. code-block:: python

        from dam.adapter.isaac import IsaacSafetyFilter

        filt = IsaacSafetyFilter("franka_safety.yaml", task="manipulation")

        # Single env
        safe_action = filt(action_tensor, obs_tensor)

        # Batched (N envs × J joints)
        safe_actions = filt(actions_batch, obs_batch)
    """

    def __init__(
        self,
        stackfile: str,
        *,
        task: str | None = None,
        joint_names: list[str] | None = None,
        degrees_mode: bool | None = None,
        num_envs: int = 1,
    ) -> None:
        from dam.api import SafetyGuard

        self._num_envs = num_envs
        self._stackfile = stackfile
        self._task = task
        self._joint_names = joint_names
        self._degrees_mode = degrees_mode

        self._guards: list[SafetyGuard] = []
        for _ in range(num_envs):
            self._guards.append(
                SafetyGuard(
                    stackfile,
                    task=task,
                    joint_names=joint_names,
                    degrees_mode=degrees_mode,
                )
            )

    def __call__(
        self,
        action: torch.Tensor,
        obs: torch.Tensor,
    ) -> torch.Tensor:
        """Validate action tensor; return safe version on same device/dtype.

        Shapes:
          - Single env: (J,) or (1, J)
          - Batched: (N, J) where N == num_envs
        """
        import torch

        device = action.device
        dtype = action.dtype
        squeeze = False

        if action.dim() == 1:
            action = action.unsqueeze(0)
            obs = obs.unsqueeze(0) if obs.dim() == 1 else obs
            squeeze = True

        n = action.shape[0]
        if n != self._num_envs:
            raise ValueError(
                f"Batch size {n} != num_envs {self._num_envs}. "
                f"Reinitialize with num_envs={n} or reshape input."
            )

        action_np = action.detach().cpu().numpy()
        obs_np = obs.detach().cpu().numpy()

        results = np.empty_like(action_np)
        for i in range(n):
            results[i] = self._guards[i](action_np[i], obs_np[i])

        out = torch.as_tensor(results, dtype=dtype, device=device)
        if squeeze:
            out = out.squeeze(0)
        return out

    @property
    def guards(self) -> list[Any]:
        """Underlying SafetyGuard instances (one per env)."""
        return self._guards

    @property
    def last_results(self) -> list[list[Any]]:
        """Guard results from most recent call, per env."""
        return [g.last_results for g in self._guards]

    def close(self) -> None:
        """Stop MCAP recording on all guard instances."""
        for g in self._guards:
            g.close()

    @classmethod
    def from_articulation(
        cls,
        articulation_view: Any,
        stackfile: str,
        *,
        task: str | None = None,
    ) -> IsaacSafetyFilter:
        """Build filter from an Isaac Sim ArticulationView.

        Auto-extracts joint_names and num_envs from the articulation.
        """
        joint_names = list(articulation_view.joint_names)
        num_envs = articulation_view.count

        return cls(
            stackfile,
            task=task,
            joint_names=joint_names,
            degrees_mode=False,
            num_envs=num_envs,
        )
