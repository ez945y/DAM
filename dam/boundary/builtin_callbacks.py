"""Backward-compatible shim.

The built-in boundary callbacks now live in the :mod:`dam.boundary.callbacks`
package, organised one module per guard layer.  This module re-exports the
full surface so existing imports (``from dam.boundary.builtin_callbacks
import register_all``, …) keep working.
"""

from __future__ import annotations

from dam.boundary.callbacks import (
    boundary_callback,
    current_limit,
    ee_velocity_limit,
    force_torque_limit,
    get_catalog,
    hardware_watchdog,
    host_health_limit,
    joint_acceleration_limit,
    joint_position_limits,
    joint_velocity_limit,
    keep_out_zone,
    ood_detector,
    orientation_limit,
    register_all,
    task_gripper_command_guard,
    task_joint_speed_limit,
    task_workspace_bounds,
    temperature_limit,
    voltage_limit,
    workspace,
)

__all__ = [
    "boundary_callback",
    "current_limit",
    "force_torque_limit",
    "get_catalog",
    "hardware_watchdog",
    "host_health_limit",
    "ee_velocity_limit",
    "joint_acceleration_limit",
    "joint_position_limits",
    "joint_velocity_limit",
    "keep_out_zone",
    "ood_detector",
    "orientation_limit",
    "register_all",
    "task_gripper_command_guard",
    "task_joint_speed_limit",
    "task_workspace_bounds",
    "temperature_limit",
    "voltage_limit",
    "workspace",
]
