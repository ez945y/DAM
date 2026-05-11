"""NoOpPolicyAdapter — placeholder policy that returns zero actions.

The ROS2 factory path falls back to this when the stackfile has no
``policy:`` section, so the runtime can still spin and execute guards
without a real policy loaded.
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np

from dam.adapter.base import PolicyAdapter
from dam.types.action import ActionProposal
from dam.types.observation import Observation


class NoOpPolicyAdapter(PolicyAdapter):
    """Policy that always returns a zero-action proposal (safe stand-still)."""

    def initialize(self, config: dict[str, Any]) -> None:
        # No-op policy requires no initialization.
        pass

    def predict(self, obs: Observation) -> ActionProposal:
        n = len(obs.joint_positions)
        return ActionProposal(
            target_joint_positions=np.zeros(n),
            target_joint_velocities=np.zeros(n),
            timestamp=time.monotonic(),
            policy_name="noop",
        )

    def get_policy_name(self) -> str:
        return "noop"

    def reset(self) -> None:
        # No state to reset.
        pass
