from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass(frozen=True)
class ActionProposal:
    """Raw action emitted by a policy before any safety validation.

    Fields
    ------
    target_joint_positions  Desired joint angles [rad], shape (n_joints,). Always required.
    target_joint_velocities Desired joint velocities [rad/s]. Optional.
    timestamp               When the action was generated (time.monotonic()).
    target_ee_pose          Desired end-effector pose [x,y,z,qx,qy,qz,qw]. Used for IK-based
                            policies; shape (7,). Optional.
    gripper_action          0.0 = fully closed, 1.0 = fully open. None = no gripper.
    confidence              Policy confidence in [0.0, 1.0].
    policy_name             Human-readable policy identifier.
    metadata                Arbitrary pass-through info (chunk_index, model_version, …).
    """

    target_joint_positions: np.ndarray
    target_joint_velocities: np.ndarray | None = None
    timestamp: float = 0.0
    target_ee_pose: np.ndarray | None = None  # [x,y,z,qx,qy,qz,qw]
    gripper_action: float | None = None  # 0.0=closed, 1.0=open
    confidence: float = 1.0
    policy_name: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "target_joint_positions",
            np.asarray(self.target_joint_positions, dtype=np.float64).copy(),
        )
        if self.target_joint_velocities is not None:
            object.__setattr__(
                self,
                "target_joint_velocities",
                np.asarray(self.target_joint_velocities, dtype=np.float64).copy(),
            )
        if self.target_ee_pose is not None:
            object.__setattr__(
                self, "target_ee_pose", np.asarray(self.target_ee_pose, dtype=np.float64).copy()
            )
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence}")


@dataclass(frozen=True)
class ValidatedAction:
    """Action that has passed all guard checks and is safe to send to hardware.

    Fields
    ------
    target_joint_positions  Final joint angles [rad] (may have been clamped).
    target_joint_velocities Final joint velocities [rad/s]. Optional.
    timestamp               Monotonic time when the action was validated.
    gripper_action          0.0 = fully closed, 1.0 = fully open. None = no gripper.
    was_clamped             True if at least one guard modified the original proposal.
    original_proposal       The unmodified ActionProposal for auditing / logging.
    """

    target_joint_positions: np.ndarray
    target_joint_velocities: np.ndarray | None = None
    timestamp: float = 0.0
    gripper_action: float | None = None  # 0.0=closed, 1.0=open
    was_clamped: bool = False
    original_proposal: ActionProposal | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "target_joint_positions",
            np.asarray(self.target_joint_positions, dtype=np.float64).copy(),
        )
        if self.target_joint_velocities is not None:
            object.__setattr__(
                self,
                "target_joint_velocities",
                np.asarray(self.target_joint_velocities, dtype=np.float64).copy(),
            )

    def merge_restrictive(self, other: ValidatedAction) -> ValidatedAction:
        """Combine two validated actions, taking the most conservative value for each joint.

        If both actions modified the same joint, we pick the value furthest from the
        original proposal (assuming the original was 'too aggressive' and was clamped inwards).
        """
        orig = self.original_proposal
        if not orig:
            # Fallback to simple mean or return self if no baseline to compare against
            return self

        p1 = self.target_joint_positions
        p2 = other.target_joint_positions
        p0 = orig.target_joint_positions

        # Calculate deltas from original
        d1 = p1 - p0
        d2 = p2 - p0

        # Most restrictive logic:
        # If both corrected in same direction, pick the LARGER correction (further from origin)
        # If they corrected in opposite directions (unlikely with limits), pick the sum?
        # Actually, if d1 is -0.5 and d2 is -1.0, we want -1.0.
        # So for negative deltas, take min. For positive deltas, take max.

        merged_p = p0.copy()
        for i in range(len(p0)):
            val1, val2 = p1[i], p2[i]
            if val1 == val2:
                merged_p[i] = val1
                continue

            # If only one changed from origin, take that one
            if np.isclose(val1, p0[i]):
                merged_p[i] = val2
                continue
            if np.isclose(val2, p0[i]):
                merged_p[i] = val1
                continue

            # Both changed. Take the one that is 'most conservative'.
            # If the original was > limit, both d1 and d2 will be negative.
            # We want the smaller value (more negative delta).
            if d1[i] < 0 and d2[i] < 0:
                merged_p[i] = min(val1, val2)
            elif d1[i] > 0 and d2[i] > 0:
                merged_p[i] = max(val1, val2)
            else:
                # Opposite corrections? This implies overlapping forbidden zones from
                # different directions. Take the one with higher magnitude.
                merged_p[i] = val1 if abs(d1[i]) > abs(d2[i]) else val2

        return ValidatedAction(
            target_joint_positions=merged_p,
            target_joint_velocities=self.target_joint_velocities,  # Simplified
            timestamp=max(self.timestamp, other.timestamp),
            gripper_action=self.gripper_action,  # Placeholder
            was_clamped=self.was_clamped or other.was_clamped,
            original_proposal=orig,
        )
