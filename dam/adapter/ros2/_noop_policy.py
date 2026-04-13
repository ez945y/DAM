"""NoOpPolicyAdapter — placeholder policy that returns zero actions.

Used by ROS2Runner.from_stackfile() when no policy object is provided.
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
        pass
