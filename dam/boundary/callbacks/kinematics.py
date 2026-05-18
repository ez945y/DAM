"""L1 — Physical kinematics boundary callbacks.

Geometric / kinematic invariants enforced every cycle, independent of task
state: joint and workspace limits, plus the human-collaborative constraints
(Cartesian speed, keep-out volumes, payload orientation, base geofence).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from dam.boundary.callbacks._helpers import (
    _get_ee_pose,
    _point_in_polygon,
    _read_channel,
    _resolve_ee_rotation,
    _resolve_ee_translation,
)
from dam.boundary.callbacks._registry import boundary_callback
from dam.kinematics.resolver import KinematicsResolver
from dam.types.observation import Observation


@boundary_callback(
    name="joint_velocity_limit",
    layer="L1",
    description="Joint speed safety check (Radians or Degrees).",
)
def joint_velocity_limit(
    *,
    obs: Observation,
    max_velocities: list[float] = None,
    use_degrees: bool = False,
) -> bool:
    """Return False if any joint velocity exceeds limits."""
    if max_velocities is None:
        max_velocities = [1.5, 1.5, 1.5, 1.5, 1.5, 1.5]
    if obs.joint_velocities is None or max_velocities is None:
        return True
    v_max = np.array(max_velocities)
    if use_degrees:
        v_max = np.radians(v_max)
    vel = np.abs(obs.joint_velocities)
    if v_max.ndim == 0:
        if np.any(vel > v_max):
            return False
    else:
        v_max_1d = np.atleast_1d(v_max)
        n = min(len(vel), len(v_max_1d))
        if np.any(vel[:n] > v_max_1d[:n]):
            return False
    return True


@boundary_callback(
    name="joint_position_limits",
    layer="L1",
    description="Joint position safety check (Radians or Degrees).",
)
def joint_position_limits(
    *,
    obs: Observation,
    upper: list[float] = None,
    lower: list[float] = None,
    use_degrees: bool = False,
) -> bool:
    """Return False if any joint position violates limits."""
    if lower is None:
        lower = [-1.82, -1.77, -1.6, -1.81, -3.07, 0.0]
    if upper is None:
        upper = [1.82, 1.77, 1.6, 1.81, 3.07, 1.75]
    if obs.joint_positions is None or upper is None or lower is None:
        return True
    pos, up, lo = obs.joint_positions, np.array(upper), np.array(lower)
    if use_degrees:
        up, lo = np.radians(up), np.radians(lo)
    return not (np.any(pos > up) or np.any(pos < lo))


@boundary_callback(
    name="workspace",
    layer="L1",
    description="Workspace box bounds [x,y,z] min/max in metres.",
)
def workspace(
    *,
    obs: Observation,
    bounds: list[list[float]] = None,
    kinematics_resolver: KinematicsResolver | None = None,
) -> bool:
    """Check if end-effector is within workspace box bounds."""
    if bounds is None:
        bounds = [[-0.4, 0.4], [-0.4, 0.4], [0.02, 0.6]]
    ee_pose = _get_ee_pose(obs, kinematics_resolver=kinematics_resolver)
    if ee_pose is None:
        return True
    ee_pos = ee_pose[:3]
    b = np.array(bounds)
    return np.all((ee_pos >= b[:, 0]) & (ee_pos <= b[:, 1]))


@boundary_callback(
    name="check_velocity_smooth",
    layer="L1",
    description="Rejects if the joint velocity norm exceeds a jerk threshold.",
)
def check_velocity_smooth(*, obs: Observation, max_jerk_norm: float = 10.0) -> bool:
    """Return False if the rate of velocity change is too high."""
    if obs.joint_velocities is None:
        return True
    vel_norm = float(np.linalg.norm(obs.joint_velocities))
    return vel_norm <= max_jerk_norm


@boundary_callback(
    name="check_joints_not_moving",
    layer="L1",
    description="Rejects if any joint velocity exceeds a near-zero threshold.",
)
def check_joints_not_moving(*, obs: Observation, max_speed_rad_s: float = 0.01) -> bool:
    """Return False if any joint is moving faster than threshold."""
    if obs.joint_velocities is None:
        return True
    return float(np.max(np.abs(obs.joint_velocities))) <= max_speed_rad_s


@boundary_callback(
    name="cartesian_velocity_limit",
    layer="L1",
    description="Caps end-effector Cartesian speed (linear m/s, angular rad/s).",
)
def cartesian_velocity_limit(
    *,
    obs: Observation,
    dynamics: Any | None = None,
    max_linear_speed: float = 0.25,
    max_angular_speed: float = 1.0,
    frame: str | int | None = None,
) -> bool | tuple[bool, str]:
    """Return False if the end-effector twist exceeds Cartesian speed limits.

    The default 0.25 m/s linear cap follows the ISO/TS 15066 reduced-speed
    guidance for human-collaborative operation.  Requires a ``dynamics``
    context (pinocchio) for the frame Jacobian; if unavailable the check
    passes (degrade gracefully, like ``workspace``).
    """
    if obs.joint_velocities is None or obs.joint_positions is None:
        return True
    if dynamics is None or not getattr(dynamics, "available", False):
        return True
    fid = frame if frame is not None else dynamics.default_frame_id
    if fid is None:
        return True
    dynamics.update(np.asarray(obs.joint_positions, dtype=np.float64))
    jac = np.asarray(dynamics.frame_jacobian(fid), dtype=np.float64)  # 6 × nq
    qd = np.asarray(obs.joint_velocities, dtype=np.float64)
    n = min(jac.shape[1], qd.shape[0])
    twist = jac[:, :n] @ qd[:n]
    v_lin = float(np.linalg.norm(twist[:3]))
    v_ang = float(np.linalg.norm(twist[3:6]))
    if v_lin > max_linear_speed or v_ang > max_angular_speed:
        return (
            False,
            f"EE speed exceeded: {v_lin:.3f} m/s (max {max_linear_speed}), "
            f"{v_ang:.3f} rad/s (max {max_angular_speed})",
        )
    return True


@boundary_callback(
    name="keep_out_zone",
    layer="L1",
    description="Rejects if the end-effector enters a keep-out box or sphere.",
)
def keep_out_zone(
    *,
    obs: Observation,
    boxes: list[list[list[float]]] | None = None,
    spheres: list[list[float]] | None = None,
    kinematics_resolver: KinematicsResolver | None = None,
    dynamics: Any | None = None,
) -> bool | tuple[bool, str]:
    """Return False if the end-effector position is inside any keep-out region.

    The inverse of ``workspace`` — used for fixtures, no-go corridors, and
    human-occupied volumes.  ``boxes`` is a list of ``[[xmin,xmax],[ymin,ymax],
    [zmin,zmax]]``; ``spheres`` a list of ``[cx,cy,cz,radius]`` (metres).
    """
    pos = _resolve_ee_translation(obs, kinematics_resolver=kinematics_resolver, dynamics=dynamics)
    if pos is None:
        return True
    for box in boxes or []:
        b = np.asarray(box, dtype=np.float64)  # 3 × 2
        if np.all((pos >= b[:, 0]) & (pos <= b[:, 1])):
            return False, f"EE inside keep-out box {box}"
    for sphere in spheres or []:
        s = np.asarray(sphere, dtype=np.float64)  # [cx, cy, cz, r]
        if float(np.linalg.norm(pos - s[:3])) <= float(s[3]):
            return False, f"EE inside keep-out sphere (center {s[:3].tolist()})"
    return True


@boundary_callback(
    name="orientation_limit",
    layer="L1",
    description="Rejects if end-effector tilt from a reference axis exceeds a limit (deg).",
)
def orientation_limit(
    *,
    obs: Observation,
    max_tilt_deg: float = 30.0,
    reference_axis: list[float] | None = None,
    tool_axis: list[float] | None = None,
    kinematics_resolver: KinematicsResolver | None = None,
    dynamics: Any | None = None,
) -> bool | tuple[bool, str]:
    """Return False if the tool axis tilts past *max_tilt_deg* from reference.

    Keeps a carried payload upright (e.g. open containers, trays).
    ``tool_axis`` is the axis in the EE frame to keep aligned (default local
    +Z); ``reference_axis`` is the world direction to align it with (default
    world up ``[0,0,1]``).
    """
    rot = _resolve_ee_rotation(obs, kinematics_resolver=kinematics_resolver, dynamics=dynamics)
    if rot is None:
        return True
    tool = np.asarray(tool_axis if tool_axis is not None else [0.0, 0.0, 1.0], dtype=np.float64)
    ref = np.asarray(
        reference_axis if reference_axis is not None else [0.0, 0.0, 1.0], dtype=np.float64
    )
    tool_norm = float(np.linalg.norm(tool))
    ref_norm = float(np.linalg.norm(ref))
    if tool_norm == 0.0 or ref_norm == 0.0:
        return True
    tool_world = rot @ (tool / tool_norm)
    cos_tilt = float(np.clip(np.dot(tool_world, ref / ref_norm), -1.0, 1.0))
    tilt_deg = float(np.degrees(np.arccos(cos_tilt)))
    if tilt_deg > max_tilt_deg:
        return False, f"EE tilt {tilt_deg:.1f}° exceeds {max_tilt_deg}°"
    return True


@boundary_callback(
    name="base_geofence",
    layer="L1",
    description="Rejects if the mobile base leaves a geofence box or polygon.",
)
def base_geofence(
    *,
    obs: Observation,
    bounds: list[list[float]] | None = None,
    polygon: list[list[float]] | None = None,
    channel: str = "base_pose",
) -> bool | tuple[bool, str]:
    """Return False if the mobile base (x, y) leaves the allowed region.

    The base pose is read from ``obs.channels[channel]`` (first two elements
    are interpreted as planar x, y in metres).  ``bounds`` is an axis-aligned
    box ``[[xmin,xmax],[ymin,ymax]]``; ``polygon`` is a list of ``[x,y]``
    vertices.  When both are given the base must satisfy both.  Passes when
    the channel is absent (degrade gracefully on arms without a base).
    """
    data = _read_channel(obs, channel)
    if data is None or len(data) < 2:
        return True
    p = np.asarray(data, dtype=np.float64)[:2]
    if bounds is not None:
        b = np.asarray(bounds, dtype=np.float64)  # 2 × 2
        if not (b[0, 0] <= p[0] <= b[0, 1] and b[1, 0] <= p[1] <= b[1, 1]):
            return False, f"Base ({p[0]:.2f}, {p[1]:.2f}) outside geofence box"
    if polygon is not None:
        verts = np.asarray(polygon, dtype=np.float64)
        if len(verts) >= 3 and not _point_in_polygon(p, verts):
            return False, f"Base ({p[0]:.2f}, {p[1]:.2f}) outside geofence polygon"
    return True
