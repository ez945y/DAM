"""Built-in boundary callbacks, organised one module per guard layer.

Importing this package imports every per-layer module, which fires the
``@boundary_callback`` decorators and populates the registry catalog.
``register_all()`` then registers them with the global callback registry.

Add a new callback by dropping a ``@boundary_callback`` function into the
matching layer module (or a new module imported below) — registration is
automatic, no list to maintain.
"""

from __future__ import annotations

from dam.boundary.callbacks._registry import (
    _CALLBACKS,
    _CATALOG,
    boundary_callback,
    get_catalog,
    register_all,
)

# Importing the layer modules triggers their decorators (registry population).
from dam.boundary.callbacks.execution import (
    task_gripper_command_guard,
)
from dam.boundary.callbacks.hardware import (
    check_force_torque_safe,
    current_limit,
    force_limit,
    force_torque_limit,
    hardware_watchdog,
    host_health_limit,
    temperature_limit,
    voltage_limit,
)
from dam.boundary.callbacks.kinematics import (
    joint_position_limits,
    joint_velocity_limit,
    workspace,
)
from dam.boundary.callbacks.ood import ood_detector

__all__ = [
    "_CALLBACKS",
    "_CATALOG",
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
    "ood_detector",
    "register_all",
    "task_gripper_command_guard",
    "temperature_limit",
    "voltage_limit",
    "workspace",
]
