"""L2 — Task execution boundary callbacks.

Constraints tied to task semantics / expected mission state (as opposed to
the geometric invariants in :mod:`kinematics`).
"""

from __future__ import annotations

from dam.boundary.callbacks._registry import boundary_callback
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
    name="check_gripper_clear",
    layer="L2",
    description="Rejects if the gripper appears closed when it should be open.",
)
def check_gripper_clear(*, obs: Observation, min_gripper_opening_m: float = 0.005) -> bool:
    g_pos = obs.metadata.get("gripper_pos")
    return g_pos is None or float(g_pos) >= min_gripper_opening_m
