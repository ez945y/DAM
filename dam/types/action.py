from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np


def action_segment_size(segment: dict[str, Any]) -> int | None:
    """Resolve the same declared segment width for SDK and runtime."""
    keys = segment.get("keys")
    if isinstance(keys, (list, tuple)):
        return len(keys)
    declared = segment.get("size") or segment.get("dim") or segment.get("dimensions")
    if declared is not None:
        return int(declared)
    return {"ee_pose": 7, "scalar": 1}.get(str(segment.get("type", "")).lower())


def declared_command_shape(pool: dict[str, Any] | None) -> tuple[int, ...] | None:
    """Prefer command layout over observation joint count; never infer units."""
    pool = pool or {}
    layout = pool.get("action_layout")
    if layout:
        widths = [action_segment_size(segment) for segment in layout]
        if any(width is None or width <= 0 for width in widths):
            raise ValueError("Action layout must declare positive segment widths")
        return (sum(width for width in widths if width is not None),)
    names = pool.get("joint_names")
    return (len(names),) if names else None


def _validate_command_payload(
    positions: np.ndarray,
    velocities: np.ndarray | None,
    timestamp: float,
    gripper: float | None,
    expected_shape: tuple[int, ...] | None,
) -> None:
    if positions.ndim != 1 or not positions.size or not np.all(np.isfinite(positions)):
        raise ValueError("Command positions must be a nonempty finite one-dimensional vector")
    if expected_shape is not None and positions.shape != expected_shape:
        raise ValueError("Command positions do not match the declared command shape")
    if velocities is not None and (
        velocities.shape != positions.shape or not np.all(np.isfinite(velocities))
    ):
        raise ValueError("Command velocities must be finite and match positions")

    def finite_scalar(value: Any) -> bool:
        return (
            isinstance(value, (int, float, np.integer, np.floating))
            and not isinstance(value, (bool, np.bool_))
            and math.isfinite(float(value))
        )

    if not finite_scalar(timestamp) or float(timestamp) < 0:
        raise ValueError("Command timestamp must be finite and nonnegative")
    if gripper is not None and (not finite_scalar(gripper) or not 0 <= float(gripper) <= 1):
        raise ValueError("Command gripper action must be finite and in [0, 1]")


@dataclass(frozen=True)
class ActionProposal:
    """Raw action emitted by a policy before any safety validation.

    Frozen fields do not make nested arrays or metadata immutable. The slow-lane
    submission boundary creates independent copies before background evaluation.

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

    def validate_payload(self, expected_shape: tuple[int, ...] | None = None) -> None:
        _validate_command_payload(
            self.target_joint_positions,
            self.target_joint_velocities,
            self.timestamp,
            self.gripper_action,
            expected_shape,
        )
        if self.target_ee_pose is not None and (
            self.target_ee_pose.shape != (7,) or not np.all(np.isfinite(self.target_ee_pose))
        ):
            raise ValueError("Command EE pose must contain seven finite values")

    # A proposal duck-types as its command vector so a callback can write
    # ``v, omega = action`` / ``np.asarray(action)`` while builtins keep using
    # ``action.target_joint_positions``.
    def __array__(self, dtype: Any = None) -> np.ndarray:
        arr = self.target_joint_positions
        return arr.astype(dtype) if dtype is not None else arr

    def __iter__(self) -> Any:
        return iter(self.target_joint_positions)

    def __len__(self) -> int:
        return len(self.target_joint_positions)

    def __getitem__(self, index: Any) -> Any:
        return self.target_joint_positions[index]


@dataclass(frozen=True)
class ValidatedAction:
    """Candidate command returned by validation or a fallback context.

    Construction alone does not validate the payload or establish physical
    safety. Runtime dispatch checks payload validity immediately before sending.

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

    def validate_payload(self, expected_shape: tuple[int, ...] | None = None) -> None:
        """Reject malformed corrections before aggregation or hardware output."""
        _validate_command_payload(
            self.target_joint_positions,
            self.target_joint_velocities,
            self.timestamp,
            self.gripper_action,
            expected_shape,
        )
        if self.original_proposal is not None:
            original = self.original_proposal.target_joint_positions
            if original.shape != self.target_joint_positions.shape or not np.all(
                np.isfinite(original)
            ):
                raise ValueError("Command positions do not match a finite original proposal")

    def merge_restrictive(self, other: ValidatedAction) -> ValidatedAction:
        """Combine two validated actions, taking the most conservative value for each joint.

        If both actions modified the same joint, we pick the value furthest from the
        original proposal (assuming the original was 'too aggressive' and was clamped inwards).
        """
        self.validate_payload()
        other.validate_payload(self.target_joint_positions.shape)
        velocity = self.target_joint_velocities
        other_velocity = other.target_joint_velocities
        if (
            velocity is not None
            and other_velocity is not None
            and not np.array_equal(velocity, other_velocity)
        ):
            raise ValueError("Cannot merge conflicting velocity corrections")
        if velocity is None:
            velocity = other_velocity
        gripper = self.gripper_action
        if (
            gripper is not None
            and other.gripper_action is not None
            and gripper != other.gripper_action
        ):
            raise ValueError("Cannot merge conflicting gripper corrections")
        if gripper is None:
            gripper = other.gripper_action
        orig = self.original_proposal
        if orig is None:
            if not np.array_equal(self.target_joint_positions, other.target_joint_positions):
                raise ValueError("Cannot merge distinct corrections without an original proposal")
            return replace(
                self,
                target_joint_velocities=velocity,
                gripper_action=gripper,
                timestamp=max(self.timestamp, other.timestamp),
                was_clamped=self.was_clamped or other.was_clamped,
                original_proposal=other.original_proposal,
            )
        if other.original_proposal is not None and not np.array_equal(
            orig.target_joint_positions, other.original_proposal.target_joint_positions
        ):
            raise ValueError("Cannot merge corrections from different proposals")

        p1 = self.target_joint_positions
        p2 = other.target_joint_positions
        p0 = orig.target_joint_positions

        # Calculate deltas from original
        d1 = p1 - p0
        d2 = p2 - p0

        # Corrections in the same direction use the larger correction.
        # Opposing corrections cannot be safely fused without constraints.

        merged_p = p0.copy()
        for i in range(len(p0)):
            val1, val2 = p1[i], p2[i]
            if val1 == val2:
                merged_p[i] = val1
                continue

            # If only one changed from origin, take that one
            if val1 == p0[i]:
                merged_p[i] = val2
                continue
            if val2 == p0[i]:
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
                raise ValueError("Cannot safely merge corrections in opposite directions")

        return ValidatedAction(
            target_joint_positions=merged_p,
            target_joint_velocities=velocity,
            timestamp=max(self.timestamp, other.timestamp),
            gripper_action=gripper,
            was_clamped=self.was_clamped or other.was_clamped,
            original_proposal=orig,
        )
