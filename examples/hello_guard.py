#!/usr/bin/env python3
"""Minimal DAM guard example — run with: python examples/hello_guard.py

Defines a custom L1 guard that clamps any action proposing joint positions
outside [-1, 1] radians, then tests it with safe and dangerous inputs.
No hardware, no stackfile, no server required.
"""

import numpy as np

import dam
from dam import ActionProposal, Guard, GuardResult, Observation, ValidatedAction


@dam.guard("L1")
class MyJointLimitGuard(Guard):
    """Clamp proposals that exceed symmetric joint limits."""

    expected_decisions = frozenset({dam.GuardDecision.PASS, dam.GuardDecision.CLAMP})

    def __init__(self, limit: float = 1.0):
        self.limit = limit

    def check(self, obs: Observation, action: ActionProposal, **kwargs) -> GuardResult:
        target = action.target_joint_positions
        if target is None:
            return GuardResult.pass_result(self.get_name(), self.get_layer())

        if np.any(np.abs(target) > self.limit):
            clamped = np.clip(target, -self.limit, self.limit)
            return GuardResult.clamp(
                ValidatedAction(target_joint_positions=clamped),
                self.get_name(),
                self.get_layer(),
                reason=f"Clamped joints to [{-self.limit}, {self.limit}]",
            )

        return GuardResult.pass_result(self.get_name(), self.get_layer())


if __name__ == "__main__":
    guard = MyJointLimitGuard(limit=1.0)

    obs = Observation(timestamp=0.0, joint_positions=np.zeros(3))

    # Safe action
    safe = ActionProposal(target_joint_positions=np.array([0.5, -0.3, 0.8]))
    result = guard.check(obs=obs, action=safe)
    assert result.decision == dam.GuardDecision.PASS
    print(f"Safe:      {result.decision.name}  reason={result.reason!r}")

    # Dangerous action
    danger = ActionProposal(target_joint_positions=np.array([0.5, -1.5, 2.0]))
    result = guard.check(obs=obs, action=danger)
    assert result.decision == dam.GuardDecision.CLAMP
    np.testing.assert_allclose(result.clamped_action.target_joint_positions, [0.5, -1, 1])
    print(f"Dangerous: {result.decision.name}  reason={result.reason!r}")
    print(f"           clamped_to={result.clamped_action.target_joint_positions}")
