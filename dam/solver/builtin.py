"""Built-in solver factories, registered together.

These are the solvers DAM ships with. User code registers its own the same way
via :func:`dam.register_solver_factory` (the name you register under is the name
stackfiles reference — there is no separate ``type``).

  - ``pinocchio_kinematics`` — arm FK/IK from a URDF (Pinocchio).
  - ``ackermann`` — planar ``[v, omega]`` rollout for differential-drive /
    Ackermann bases (see :class:`dam.kinematics.ackermann.AckermannSolver`).
"""

from __future__ import annotations

import contextlib
from collections.abc import Mapping
from typing import Any


def _pinocchio_kinematics(params: Mapping[str, Any]) -> Any:
    from dam.kinematics.resolver import KinematicsResolver

    urdf_path = params.get("asset_path")
    if not urdf_path:
        raise ValueError(
            "pinocchio_kinematics solver requires preset asset type='urdf' or params.asset_path"
        )
    return KinematicsResolver(
        str(urdf_path),
        controlled_joints=params.get("controlled_joints"),
        ee_link_name=str(params.get("ee_link_name", "gripper_link")),
        observation_joint_names=params.get("observation_joint_names"),
    )


def _ackermann(params: Mapping[str, Any]) -> Any:
    from dam.kinematics.ackermann import AckermannSolver

    return AckermannSolver(
        wheel_base=params.get("wheel_base"),
        track_width=params.get("track_width"),
    )


def register_all() -> None:
    """Register all built-in solver factories (idempotent)."""
    from dam.solver.registry import get_global_solver_registry

    registry = get_global_solver_registry()
    with contextlib.suppress(ValueError):
        registry.register_factory(
            "pinocchio_kinematics", _pinocchio_kinematics, capabilities=("fk", "ik")
        )
    with contextlib.suppress(ValueError):
        registry.register_factory("ackermann", _ackermann, capabilities=("rollout", "ackermann"))
