"""SimAdapters — dynamic synthetic sources/policies for development and testing."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from dam.types.action import ActionProposal
from dam.types.observation import Observation

if TYPE_CHECKING:
    pass


class SimSource:
    """Synthetic 6-DOF robot random-walk observations aligned with config Hz."""

    def __init__(self, n_joints: int = 6, seed: int = 42, hz: float = 10.0) -> None:
        rng = np.random.default_rng(seed)
        self._pos = rng.uniform(-0.3, 0.3, size=n_joints)
        self._vel = np.zeros(n_joints)
        self._step = 0
        self._rng = rng
        self._hz = hz

    def read(self) -> Observation:
        dt = 1.0 / self._hz
        delta = self._rng.normal(0.0, 0.03, size=len(self._pos))
        new_pos = np.clip(self._pos + delta, -1.9, 1.9)
        self._vel = (new_pos - self._pos) / dt
        self._pos = new_pos

        # EE pose (dummy arc)
        x = float(0.2 * np.sin(self._pos[0]))
        y = float(0.2 * (1 - np.cos(self._pos[1])))
        z = float(0.3 + 0.1 * self._pos[2])

        import time

        ts = time.monotonic()

        return Observation(
            timestamp=ts,
            joint_positions=self._pos.copy(),
            joint_velocities=self._vel.copy(),
            end_effector_pose=np.array([x, y, z, 0.0, 0.0, 0.0, 1.0]),
        )


class SimPolicy:
    """Every 5th cycle proposes out-of-range positions to exercise the guards."""

    def __init__(self, n_joints: int = 6, seed: int = 7) -> None:
        self._rng = np.random.default_rng(seed)
        self._cycle = 0

    def predict(self, obs: Observation) -> ActionProposal:
        n = len(obs.joint_positions)
        self._cycle += 1
        if self._cycle % 5 == 0:
            targets = self._rng.uniform(-2.6, 2.6, size=n)
        else:
            phase = float(self._cycle) / 20.0
            arm = 0.7 * np.sin(np.linspace(0, np.pi, n - 1) + phase)
            targets = np.append(arm, 0.04 + 0.02 * np.sin(phase * 2))
        return ActionProposal(
            target_joint_positions=targets,
            confidence=0.93,
            policy_name="sim_policy",
        )


class SimSink:
    """Discards actions (no hardware to send to)."""

    def write(self, action: object) -> None:
        pass

    def emergency_stop(self) -> None:
        pass
