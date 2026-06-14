"""Built-in solver factories, registered together.

These are the solvers DAM ships with. User code registers its own the same way
via the :func:`dam.solver_factory` decorator. A factory declares the params it needs as
keyword arguments; the registry injects matching values from the stackfile
``params`` plus robot context (``asset_path``, ``observation_joint_names``, …).

  - ``pinocchio_kinematics`` — arm FK/IK from a URDF (Pinocchio).
  - ``ackermann`` — planar ``[v, omega]`` rollout for differential-drive /
    Ackermann bases (see :class:`dam.kinematics.ackermann.AckermannSolver`).
"""

from __future__ import annotations

import contextlib
from typing import Any


def _pinocchio_kinematics(
    asset_path: str | None = None,
    controlled_joints: list[str] | None = None,
    ee_link_name: str = "gripper_link",
    observation_joint_names: list[str] | None = None,
) -> Any:
    from dam.kinematics.resolver import KinematicsResolver

    if not asset_path:
        raise ValueError(
            "pinocchio_kinematics solver requires preset asset type='urdf' or params.asset_path"
        )
    return KinematicsResolver(
        str(asset_path),
        controlled_joints=controlled_joints,
        ee_link_name=str(ee_link_name),
        observation_joint_names=observation_joint_names,
    )


def _ackermann(
    wheel_base: float | None = None,
    track_width: float | None = None,
) -> Any:
    from dam.kinematics.ackermann import AckermannSolver

    return AckermannSolver(wheel_base=wheel_base, track_width=track_width)


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
