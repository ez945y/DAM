"""Built-in boundary callback catalog.

Import this module when you want examples for authoring stackfile callbacks or
when you need to register the built-in callback set programmatically.
"""

from dam.boundary.builtin_callbacks import (
    base_geofence,
    boundary_callback,
    cartesian_velocity_limit,
    check_force_torque_safe,
    check_gripper_clear,
    check_joints_not_moving,
    check_velocity_smooth,
    current_limit,
    force_limit,
    get_catalog,
    hardware_watchdog,
    joint_position_limits,
    joint_velocity_limit,
    keep_out_zone,
    ood_detector,
    orientation_limit,
    register_all,
    temperature_limit,
    voltage_limit,
    workspace,
)

__all__ = [
    "base_geofence",
    "boundary_callback",
    "cartesian_velocity_limit",
    "check_force_torque_safe",
    "check_gripper_clear",
    "check_joints_not_moving",
    "check_velocity_smooth",
    "current_limit",
    "force_limit",
    "get_catalog",
    "hardware_watchdog",
    "joint_position_limits",
    "joint_velocity_limit",
    "keep_out_zone",
    "ood_detector",
    "orientation_limit",
    "register_all",
    "temperature_limit",
    "voltage_limit",
    "workspace",
]
