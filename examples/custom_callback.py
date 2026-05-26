#!/usr/bin/env python3
"""Register a custom boundary callback and use it in a Stackfile.

Run with: python examples/custom_callback.py

Shows how to write a reusable safety check as a @boundary_callback,
then wire it into the guard pipeline through a minimal Stackfile.
"""

import numpy as np

from dam.boundary.callbacks._registry import boundary_callback
from dam.guard.layer import GuardLayer
from dam.guard.pipeline import CallbackResult
from dam.types.action import ActionProposal, ValidatedAction
from dam.types.observation import Observation


@boundary_callback(
    name="speed_governor",
    layer=GuardLayer.L1,
    description="Clamp joint velocities to a configurable fraction of max.",
)
def speed_governor(
    obs: Observation,
    action: ActionProposal,
    max_speed_fraction: float = 0.5,
    **_kwargs,
) -> CallbackResult:
    """Scale down velocities that exceed max_speed_fraction of the position delta."""
    if action.target_joint_positions is None:
        return CallbackResult.ok("speed_governor")

    delta = action.target_joint_positions - obs.joint_positions
    limit = max_speed_fraction * np.ones_like(delta)

    if np.all(np.abs(delta) <= limit):
        return CallbackResult.ok("speed_governor", reason="within speed limit")

    clamped_delta = np.clip(delta, -limit, limit)
    clamped_pos = obs.joint_positions + clamped_delta

    return CallbackResult.clamp(
        "speed_governor",
        clamped_action=ValidatedAction(target_joint_positions=clamped_pos),
        reason=f"delta clamped to +/-{max_speed_fraction}",
    )


if __name__ == "__main__":
    obs = Observation(timestamp=0.0, joint_positions=np.array([0.0, 0.0, 0.0]))

    # A gentle move — should pass
    gentle = ActionProposal(target_joint_positions=np.array([0.1, 0.2, 0.3]))
    result = speed_governor(obs=obs, action=gentle, max_speed_fraction=0.5)
    print(f"Gentle move: {result.decision.name}  reason={result.reason!r}")

    # An aggressive move — should be clamped
    aggressive = ActionProposal(target_joint_positions=np.array([1.0, -2.0, 3.0]))
    result = speed_governor(obs=obs, action=aggressive, max_speed_fraction=0.5)
    print(f"Aggressive:  {result.decision.name}  reason={result.reason!r}")
    if result.clamped_action:
        print(f"             clamped_to={result.clamped_action.target_joint_positions}")
