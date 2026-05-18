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
    semantic_state,
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

# Module-level OOD guard cache lived here historically; some tests clear it
# via ``builtin_callbacks._ood_guard_cache``.  Re-export the same objects so
# in-place mutation (``.clear()``) still reaches the real cache.
from dam.boundary.callbacks.perception import (
    _ood_cache_lock,
    _ood_guard_cache,
)

__all__ = [
    "_CALLBACKS",
    "_CATALOG",
    "_get_ee_pose",
    "_ood_cache_lock",
    "_ood_guard_cache",
    "_point_in_polygon",
    "_quat_to_rotmat",
    "_read_channel",
    "_resolve_ee_rotation",
    "_resolve_ee_translation",
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
    "semantic_state",
    "temperature_limit",
    "voltage_limit",
    "workspace",
]
