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
