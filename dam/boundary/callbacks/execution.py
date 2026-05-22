"""L2 — Task execution boundary callbacks.

Constraints tied to task semantics / expected mission state (as opposed to
the geometric invariants in :mod:`kinematics`).

These were previously hard-coded inside ``ExecutionGuard.check()`` (``max_speed``
/ ``bounds``); they now live here as ordinary L2 callbacks so adding a new
task-level limit means writing one function, not editing the guard.
"""

from __future__ import annotations

import numpy as np

from dam.boundary.callbacks._helpers import _all_finite
from dam.boundary.callbacks._registry import boundary_callback
from dam.guard.pipeline import CallbackResult
from dam.types.action import ActionProposal
from dam.types.observation import Observation


@boundary_callback(
    name="semantic_state",
    layer="L2",
    description="High-level semantic task state validation (pre/post-condition checks).",
)
def semantic_state(*, obs: Observation) -> bool:
    """Validate task-level semantic invariants."""
    return True


@boundary_callback(
    name="task_joint_speed_limit",
    layer="L2",
    description="Rejects if the joint velocity norm exceeds a task-level max_speed.",
)
def task_joint_speed_limit(
    *,
    obs: Observation,
    max_speed: float | None = None,
    use_degrees: bool = False,
) -> CallbackResult:
    """Reject when ‖joint_velocities‖ exceeds ``max_speed``.

    Task-level speed cap (as opposed to the per-joint L1 velocity clamp): a
    whole-arm scalar limit on the velocity norm.  Passes when ``max_speed`` or
    the joint-velocity channel is absent (degrade gracefully).
    """
    bname = "task_joint_speed_limit"
    if max_speed is None or obs.joint_velocities is None:
        return CallbackResult.ok(bname)
    if not _all_finite(obs.joint_velocities):
        return CallbackResult.violate(bname, "non-finite joint velocities")
    limit = float(np.radians(max_speed)) if use_degrees else float(max_speed)
    speed_norm = float(np.linalg.norm(obs.joint_velocities))
    if speed_norm > limit:
        return CallbackResult.violate(
            bname, f"joint speed norm {speed_norm:.3f} > max_speed {limit:.3f}"
        )
    return CallbackResult.ok(bname)


@boundary_callback(
    name="task_workspace_bounds",
    layer="L2",
    description="Rejects if the end-effector position leaves a task workspace box.",
)
def task_workspace_bounds(
    *,
    obs: Observation,
    bounds: list[list[float]] | None = None,
) -> CallbackResult:
    """Reject when the end-effector position leaves the axis-aligned ``bounds``.

    ``bounds`` is ``[[xmin,xmax],[ymin,ymax],[zmin,zmax]]`` (metres).  Unlike the
    L1 ``workspace`` callback (which halts/clamps the action), this is a
    task-level REJECT.  Passes when ``bounds`` or the EE pose is absent.
    """
    bname = "task_workspace_bounds"
    if bounds is None or obs.end_effector_pose is None:
        return CallbackResult.ok(bname)
    ee_pos = np.asarray(obs.end_effector_pose[:3], dtype=np.float64)
    if not _all_finite(ee_pos):
        return CallbackResult.violate(bname, "non-finite end-effector position")
    b = np.asarray(bounds, dtype=np.float64)
    if not np.all((ee_pos >= b[:, 0]) & (ee_pos <= b[:, 1])):
        return CallbackResult.violate(
            bname, f"end-effector {ee_pos.tolist()} outside bounds {bounds}"
        )
    return CallbackResult.ok(bname)


@boundary_callback(
    name="check_gripper_clear",
    layer="L2",
    description="Rejects if the gripper appears closed when it should be open.",
)
def check_gripper_clear(*, obs: Observation, min_gripper_opening_m: float = 0.005) -> bool:
    g_pos = obs.metadata.get("gripper_pos")
    return g_pos is None or float(g_pos) >= min_gripper_opening_m


def _metadata_str(obs: Observation, action: ActionProposal | None, key: str) -> str | None:
    value = None
    if action is not None:
        value = action.metadata.get(key)
    if value is None:
        value = obs.metadata.get(key)
    return str(value).lower() if value is not None else None


def _in_box(point: np.ndarray, bounds: list[list[float]] | None) -> bool:
    if bounds is None:
        return True
    b = np.asarray(bounds, dtype=np.float64)
    if b.shape != (3, 2):
        raise ValueError("zone bounds must be [[xmin,xmax],[ymin,ymax],[zmin,zmax]]")
    return bool(np.all((point >= b[:, 0]) & (point <= b[:, 1])))


@boundary_callback(
    name="task_gripper_command_guard",
    layer="L2",
    description=(
        "Rejects gripper open/close commands that are incompatible with the "
        "current task segment or pick/place zones."
    ),
)
def task_gripper_command_guard(
    *,
    obs: Observation,
    action: ActionProposal | None = None,
    task_segment: str | None = None,
    segment_metadata_key: str = "task_segment",
    pick_zone: list[list[float]] | None = None,
    place_zone: list[list[float]] | None = None,
    close_segments: list[str] | None = None,
    open_segments: list[str] | None = None,
    move_segments: list[str] | None = None,
    close_threshold: float = 0.25,
    open_threshold: float = 0.75,
) -> CallbackResult:
    """Reject task-section gripper command anomalies.

    Intended for adversarial events like closing before entering the planned
    pick zone, opening before entering the planned place zone, or injected
    open/close chatter while the task is in a movement segment.  Segment can
    be supplied as a boundary param, ``action.metadata[segment_metadata_key]``,
    or ``obs.metadata[segment_metadata_key]``.
    """
    bname = "task_gripper_command_guard"
    if action is None or action.gripper_action is None:
        return CallbackResult.ok(bname)
    if obs.end_effector_pose is None:
        return CallbackResult.violate(bname, "missing end-effector pose for gripper task gate")

    gripper = float(action.gripper_action)
    if not np.isfinite(gripper):
        return CallbackResult.violate(bname, "non-finite gripper action")

    close_segments = [s.lower() for s in (close_segments or ["pick", "grasp", "pre_grasp"])]
    open_segments = [s.lower() for s in (open_segments or ["place", "release"])]
    move_segments = [
        s.lower() for s in (move_segments or ["move", "transfer", "approach", "retreat"])
    ]

    segment = (task_segment.lower() if task_segment else None) or _metadata_str(
        obs, action, segment_metadata_key
    )
    ee_pos = np.asarray(obs.end_effector_pose[:3], dtype=np.float64)
    if not _all_finite(ee_pos):
        return CallbackResult.violate(bname, "non-finite end-effector position")

    command: str | None = None
    if gripper <= close_threshold:
        command = "close"
    elif gripper >= open_threshold:
        command = "open"
    if command is None:
        return CallbackResult.ok(bname)

    if segment in move_segments:
        return CallbackResult.violate(
            bname, f"gripper {command} command is not allowed during movement segment '{segment}'"
        )

    if command == "close":
        if segment not in close_segments:
            return CallbackResult.violate(
                bname, f"gripper close command outside close segment: {segment or 'unknown'}"
            )
        if not _in_box(ee_pos, pick_zone):
            return CallbackResult.violate(
                bname, f"gripper close before entering pick zone: ee={ee_pos.tolist()}"
            )

    if command == "open":
        if segment not in open_segments:
            return CallbackResult.violate(
                bname, f"gripper open command outside open segment: {segment or 'unknown'}"
            )
        if not _in_box(ee_pos, place_zone):
            return CallbackResult.violate(
                bname, f"gripper open before entering place zone: ee={ee_pos.tolist()}"
            )

    return CallbackResult.ok(
        bname,
        metadata={"task_segment": segment, "gripper_command": command},
    )
