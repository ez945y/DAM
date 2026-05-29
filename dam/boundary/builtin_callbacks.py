"""Backward-compatible shim.

The built-in boundary callbacks now live in the :mod:`dam.boundary.callbacks`
package, organised one module per guard layer.  This module re-exports the
full surface so existing imports (``from dam.boundary.builtin_callbacks
import register_all``, …) keep working.
"""

from __future__ import annotations

from dam.boundary.callbacks import (
    _CALLBACKS,
    _CATALOG,
    boundary_callback,
    check_force_torque_safe,
    current_limit,
    force_limit,
    force_torque_limit,
    get_catalog,
    hardware_watchdog,
    host_health_limit,
    joint_position_limits,
    joint_velocity_limit,
    keep_out_zone,
    ood_detector,
    orientation_limit,
    register_all,
    task_gripper_command_guard,
    temperature_limit,
    voltage_limit,
    workspace,
)
from dam.boundary.callbacks._helpers import (
    _get_ee_pose,
    _point_in_polygon,
    _quat_to_rotmat,
    _read_channel,
    _resolve_ee_rotation,
    _resolve_ee_translation,
)

__all__ = [
    "boundary_callback",
    "check_force_torque_safe",
    "current_limit",
    "force_limit",
    "force_torque_limit",
    "get_catalog",
    "hardware_watchdog",
    "host_health_limit",
    "joint_position_limits",
    "joint_velocity_limit",
    "keep_out_zone",
    "ood_detector",
    "orientation_limit",
    "register_all",
    "task_gripper_command_guard",
    "temperature_limit",
    "voltage_limit",
    "workspace",
]
