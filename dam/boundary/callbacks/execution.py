"""L2 — Task execution boundary callbacks.

Constraints tied to task semantics / expected mission state (as opposed to
the geometric invariants in :mod:`kinematics`).

``task_gripper_command_guard`` gates gripper open/close per task phase.
"""

from __future__ import annotations

import logging

import numpy as np

from dam.boundary.callbacks._helpers import _all_finite
from dam.boundary.callbacks._registry import boundary_callback
from dam.guard.pipeline import CallbackResult
from dam.types.action import ActionProposal, ValidatedAction
from dam.types.observation import Observation

logger = logging.getLogger(__name__)

_VALID_COMMANDS = {"close", "open", "none", "noop", "no_op"}
_LEGACY_WARNED = False


def _in_box(point: np.ndarray, bounds: list[list[float]] | None) -> bool:
    if bounds is None:
        return True
    b = np.asarray(bounds, dtype=np.float64)
    if b.shape != (3, 2):
        raise ValueError("zone bounds must be [[xmin,xmax],[ymin,ymax],[zmin,zmax]]")
    return bool(np.all((point >= b[:, 0]) & (point <= b[:, 1])))


def _clamp_gripper(
    *,
    bname: str,
    action: ActionProposal,
    reason: str,
    command: str | None = None,
    allowed_command: str | None = None,
) -> CallbackResult:
    metadata = {
        "gripper_clamped": True,
        "clamp_mode": "suppress_gripper",
    }
    if command is not None:
        metadata["gripper_command"] = command
    if allowed_command is not None:
        metadata["allowed_command"] = allowed_command
    return CallbackResult.clamp(
        bname,
        _suppress_gripper(action),
        f"{reason}; suppressed gripper command",
        metadata=metadata,
    )


def _suppress_gripper(action: ActionProposal) -> ValidatedAction:
    return ValidatedAction(
        target_joint_positions=action.target_joint_positions.copy(),
        target_joint_velocities=(
            action.target_joint_velocities.copy()
            if action.target_joint_velocities is not None
            else None
        ),
        timestamp=action.timestamp,
        gripper_action=None,
        was_clamped=True,
        original_proposal=action,
    )


@boundary_callback(
    name="task_gripper_command_guard",
    layer="L2",
    category="execution",
    description=(
        "Clamps gripper open/close commands that are incompatible with the active task node rule."
    ),
    params={
        "allowed_command": "Allowed gripper command for this task node: close, open, or none.",
        "zone": "EE zone where the allowed gripper command may run: [[xmin,xmax],[ymin,ymax],[zmin,zmax]] in metres.",
        "close_threshold": "gripper_action (0.0–1.0) at or below this value is treated as close. Default 0.25.",
        "open_threshold": "gripper_action (0.0–1.0) at or above this value is treated as open. Default 0.75.",
    },
    internal_params=("pick_zone", "place_zone"),
)
def task_gripper_command_guard(
    *,
    obs: Observation,
    action: ActionProposal | None = None,
    allowed_command: str | None = None,
    zone: list[list[float]] | None = None,
    pick_zone: list[list[float]] | None = None,
    place_zone: list[list[float]] | None = None,
    close_threshold: float = 0.25,
    open_threshold: float = 0.75,
    ee_pos: np.ndarray | None = None,
) -> CallbackResult:
    """Clamp task-section gripper command anomalies.

    This callback is node-local: the boundary container/list defines phase
    order, and each active node's params define the gripper rule for that
    phase. ``allowed_command`` is "close", "open", or "none"; ``zone`` is the
    EE box where that command is allowed. Legacy ``pick_zone`` / ``place_zone``
    are still accepted for older stackfiles.
    """
    global _LEGACY_WARNED  # noqa: PLW0603
    bname = "task_gripper_command_guard"
    if action is None or action.gripper_action is None:
        return CallbackResult.ok(bname)

    # Warn once about legacy zone params.
    if (pick_zone is not None or place_zone is not None) and not _LEGACY_WARNED:
        _LEGACY_WARNED = True
        logger.warning(
            "%s: pick_zone/place_zone are deprecated; use allowed_command + zone instead",
            bname,
        )

    # Validate allowed_command early.
    if allowed_command is not None and allowed_command.lower() not in _VALID_COMMANDS:
        return _clamp_gripper(
            bname=bname,
            action=action,
            reason=f"invalid allowed_command '{allowed_command}'; expected one of: close, open, none",
            allowed_command=allowed_command,
        )

    gripper = float(action.gripper_action)
    if not np.isfinite(gripper):
        return _clamp_gripper(
            bname=bname,
            action=action,
            reason="non-finite gripper action",
        )

    command: str | None = None
    if gripper <= close_threshold:
        command = "close"
    elif gripper >= open_threshold:
        command = "open"
    if command is None:
        return CallbackResult.ok(bname)

    expected = allowed_command.lower() if allowed_command else command
    expected_zone = zone
    if allowed_command is None:
        expected_zone = pick_zone if command == "close" else place_zone

    if expected in {"none", "noop", "no_op"}:
        return _clamp_gripper(
            bname=bname,
            action=action,
            reason=f"gripper {command} command is not allowed in this task node",
            command=command,
            allowed_command=expected,
        )

    if expected not in {"open", "close"}:
        return _clamp_gripper(
            bname=bname,
            action=action,
            reason=f"unknown allowed gripper command '{expected}'",
            command=command,
            allowed_command=expected,
        )

    if command != expected:
        return _clamp_gripper(
            bname=bname,
            action=action,
            reason=f"gripper {command} command does not match allowed command '{expected}'",
            command=command,
            allowed_command=expected,
        )

    # Zone check requires end-effector pose from FK.
    # Without a zone, the command-type check above is sufficient.
    if expected_zone is None:
        return CallbackResult.ok(
            bname,
            metadata={"gripper_command": command, "allowed_command": expected},
        )

    # Prefer pre-computed ee_pos from pool (post-L1 FK); fall back to obs.
    if ee_pos is None:
        if obs.end_effector_pose is None:
            return _clamp_gripper(
                bname=bname,
                action=action,
                reason="missing end-effector pose for gripper zone check",
            )
        ee_pos = np.asarray(obs.end_effector_pose[:3], dtype=np.float64)
    if not _all_finite(ee_pos):
        return _clamp_gripper(
            bname=bname,
            action=action,
            reason="non-finite end-effector position",
        )

    if _in_box(ee_pos, expected_zone):
        return CallbackResult.ok(
            bname,
            metadata={"gripper_command": command, "allowed_command": expected},
        )

    return _clamp_gripper(
        bname=bname,
        action=action,
        reason=f"gripper {command} outside allowed zone: ee={ee_pos.tolist()}",
        command=command,
        allowed_command=expected,
    )
