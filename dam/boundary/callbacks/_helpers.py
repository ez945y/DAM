"""Shared geometry / observation-channel helpers for boundary callbacks."""

from __future__ import annotations

from typing import Any

import numpy as np

from dam.kinematics.resolver import KinematicsResolver
from dam.types.observation import Observation


def _all_finite(*arrays: object) -> bool:
    """True iff every element of every given array is finite (no NaN/Inf).

    Adversarial inputs (NaN/Inf injection) silently defeat naive comparison
    checks — ``nan > limit`` is ``False`` — so safety callbacks treat
    non-finite inputs as a violation rather than a pass.
    """
    for arr in arrays:
        if arr is None:
            continue
        if not np.all(np.isfinite(np.asarray(arr, dtype=np.float64))):
            return False
    return True


def _read_channel(obs: Observation, channel: str) -> np.ndarray | None:
    """Read a named channel from obs.channels (or iter_channels fallback)."""
    if obs.channels and channel in obs.channels:
        return np.asarray(obs.channels[channel])
    # Try iter_channels for typed fields like force_torque
    for name, value in obs.iter_channels():
        if name == channel:
            return np.asarray(value)
    return None


def _get_ee_pose(
    obs: Observation, kinematics_resolver: KinematicsResolver | None = None
) -> np.ndarray | None:
    if obs.end_effector_pose is not None:
        return obs.end_effector_pose
    if kinematics_resolver is not None:
        try:
            return kinematics_resolver.compute_fk(obs.joint_positions)
        except Exception:  # noqa: BLE001 — FK failure is non-fatal; caller falls back to None
            pass
    return None


def _quat_to_rotmat(quat: np.ndarray) -> np.ndarray:
    """Rotation matrix from a ``[qx, qy, qz, qw]`` quaternion."""
    x, y, z, w = (float(v) for v in quat[:4])
    n = x * x + y * y + z * z + w * w
    if n == 0.0:
        return np.eye(3)
    s = 2.0 / n
    return np.array(
        [
            [1.0 - s * (y * y + z * z), s * (x * y - z * w), s * (x * z + y * w)],
            [s * (x * y + z * w), 1.0 - s * (x * x + z * z), s * (y * z - x * w)],
            [s * (x * z - y * w), s * (y * z + x * w), 1.0 - s * (x * x + y * y)],
        ],
        dtype=np.float64,
    )


def _resolve_ee_translation(
    obs: Observation,
    kinematics_resolver: KinematicsResolver | None = None,
    dynamics: Any | None = None,
) -> np.ndarray | None:
    """End-effector position (3,), preferring the cached ``dynamics`` context."""
    if (
        dynamics is not None
        and getattr(dynamics, "available", False)
        and dynamics.default_frame_id is not None
        and obs.joint_positions is not None
    ):
        dynamics.update(np.asarray(obs.joint_positions, dtype=np.float64))
        placement = dynamics.frame_placement(dynamics.default_frame_id)
        return np.asarray(placement.translation, dtype=np.float64)
    pose = _get_ee_pose(obs, kinematics_resolver=kinematics_resolver)
    if pose is None:
        return None
    return np.asarray(pose[:3], dtype=np.float64)


def _resolve_ee_rotation(
    obs: Observation,
    kinematics_resolver: KinematicsResolver | None = None,
    dynamics: Any | None = None,
) -> np.ndarray | None:
    """End-effector rotation matrix (3, 3), or None if unobtainable."""
    if (
        dynamics is not None
        and getattr(dynamics, "available", False)
        and dynamics.default_frame_id is not None
        and obs.joint_positions is not None
    ):
        dynamics.update(np.asarray(obs.joint_positions, dtype=np.float64))
        placement = dynamics.frame_placement(dynamics.default_frame_id)
        return np.asarray(placement.rotation, dtype=np.float64)
    pose = _get_ee_pose(obs, kinematics_resolver=kinematics_resolver)
    if pose is None or len(pose) < 7:
        return None
    return _quat_to_rotmat(np.asarray(pose[3:7], dtype=np.float64))


def _point_in_polygon(point: np.ndarray, vertices: np.ndarray) -> bool:
    """Ray-casting point-in-polygon test for a 2D ``[x, y]`` point."""
    x, y = float(point[0]), float(point[1])
    inside = False
    n = len(vertices)
    j = n - 1
    for i in range(n):
        xi, yi = float(vertices[i][0]), float(vertices[i][1])
        xj, yj = float(vertices[j][0]), float(vertices[j][1])
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside
