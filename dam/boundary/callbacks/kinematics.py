"""L1 — Physical kinematics boundary callbacks.

Geometric / kinematic invariants enforced every cycle, independent of task
state: joint and workspace limits, plus the human-collaborative constraints
(keep-out volumes, payload orientation, base geofence).

Action-clamp policy
-------------------
L1 boundary callbacks that have a well-defined corrective action (joint
limits, velocity + acceleration limits, workspace box) return
``CallbackResult.clamp(...)`` with the corrected ``ValidatedAction``.
They do not REJECT — the inner pipeline prefers correcting the policy's
proposal to halting the robot mid-trajectory.
Callbacks for which no clean clamp exists (keep-out zones, orientation,
geofence) return ``bool`` and surface as REJECT.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from dam.boundary.callbacks._helpers import (
    _all_finite,
    _point_in_polygon,
    _read_channel,
    _resolve_ee_rotation,
    _resolve_ee_translation,
)
from dam.boundary.callbacks._registry import boundary_callback
from dam.guard.aggregators.motion_qp import MotionQPConstraint, motion_qp_units
from dam.guard.pipeline import CallbackResult
from dam.kinematics.resolver import KinematicsResolver
from dam.types.action import ActionProposal, ValidatedAction
from dam.types.observation import Observation

logger = logging.getLogger(__name__)

# ── Velocity-limit state (acceleration tracking) ─────────────────────────────
_prev_vel: dict[str, np.ndarray] = {}


_DIM_WARNED: set[str] = set()


def _to_array(x: Any, *, name: str) -> np.ndarray:
    arr = np.asarray(x, dtype=np.float64)
    if not _all_finite(arr):
        raise ValueError(f"non-finite values in {name}")
    return arr


def _check_dim(n_joints: int, n_param: int, *, callback: str, param: str) -> None:
    """Warn once if param array length doesn't match the observed joint count."""
    if n_joints != n_param:
        key = f"{callback}:{param}"
        if key not in _DIM_WARNED:
            _DIM_WARNED.add(key)
            logger.warning(
                "%s: %s has %d elements but observation has %d joints; "
                "mismatched joints are unconstrained",
                callback,
                param,
                n_param,
                n_joints,
            )


@boundary_callback(
    name="joint_velocity_limit",
    layer="L1",
    category="kinematics",
    description="Clamps the action's joint velocities to ±max_velocities and joint accelerations to ±max_acceleration (radians/sec, radians/sec²).",
    params={
        "max_velocities": "Per-joint max velocity. Radians/sec by default unless use_degrees is true.",
        "max_acceleration": "Per-joint max acceleration in rad/s². Prevents sudden speed jumps (爆衝). Default 10.0 rad/s².",
        "slack_weight": "QP soft-constraint penalty. Higher values make violating this limit more expensive.",
        "use_degrees": "UI/loader hint: interpret max_velocities as deg/s. Normalised to rad/s once at load — runtime never sees this flag.",
    },
    unit_params=("max_velocities",),
    internal_params=("slack_weight",),
)
def joint_velocity_limit(
    *,
    obs: Observation,
    action: ActionProposal,
    dt: float,
    max_velocities: list[float] | float | None = None,
    max_acceleration: list[float] | float | None = None,
    slack_weight: float = 1e6,
) -> CallbackResult:
    """Clamp proposed velocity and acceleration per joint.

    Two-stage clamp applied to the *proposed* action:

    1. **Acceleration limit** — derives the previous cycle's velocity from
       the tracking buffer.  If ``|v_proposed - v_prev| / dt`` exceeds
       ``max_acceleration``, the velocity is clamped so the acceleration
       stays within the allowed band.
    2. **Velocity limit** — the (possibly acceleration-clamped) velocity is
       then scaled so ``|v_i| ≤ max_velocities_i``.

    Target positions are rebuilt from the final velocity so policy and
    adapter stay consistent.  ``dt`` is auto-injected by GuardRuntime.
    """
    bname = "joint_velocity_limit"
    if obs.joint_positions is None:
        return CallbackResult.ok(bname, "no joint state to act on")

    target_pos = np.asarray(action.target_joint_positions, dtype=np.float64)
    cur_pos = np.asarray(obs.joint_positions, dtype=np.float64)
    if not _all_finite(target_pos) or not _all_finite(cur_pos):
        return CallbackResult.violate(bname, "non-finite joint state or action")
    n = min(target_pos.shape[0], cur_pos.shape[0])
    dt_safe = max(float(dt), 1e-6)

    # ── Resolve limits ────────────────────────────────────────────────────
    if max_velocities is None:
        v_max = np.full(n, 1.5)
    else:
        v_max = _to_array(max_velocities, name="max_velocities")
        _check_dim(n, v_max.shape[0], callback=bname, param="max_velocities")

    if max_acceleration is None:
        a_max = np.full(n, 10.0)
    else:
        a_max = np.atleast_1d(_to_array(max_acceleration, name="max_acceleration"))
        if a_max.shape[0] == 1:
            a_max = np.full(n, a_max[0])
        _check_dim(n, a_max.shape[0], callback=bname, param="max_acceleration")
    a_max = a_max[:n]

    # ── Derive proposed velocity ──────────────────────────────────────────
    if action.target_joint_velocities is not None:
        velocities = np.asarray(action.target_joint_velocities, dtype=np.float64)[:n]
        if not _all_finite(velocities):
            return CallbackResult.violate(bname, "non-finite joint velocity action")
        derived = False
    else:
        velocities = (target_pos[:n] - cur_pos[:n]) / dt_safe
        derived = True

    v_max_1d = np.atleast_1d(v_max)
    if v_max_1d.shape[0] == 1:
        v_max_1d = np.full(n, v_max_1d[0])
    v_max_1d = v_max_1d[:n]

    # ── Stage 1: acceleration clamp ───────────────────────────────────────
    accel_clamped = False
    prev_v = _prev_vel.get(bname)
    if prev_v is not None and prev_v.shape == velocities.shape:
        accel = (velocities - prev_v) / dt_safe
        accel_abs = np.abs(accel)
        over_accel = accel_abs > a_max
        if np.any(over_accel):
            accel_clamped = True
            clamped_accel = np.clip(accel, -a_max, a_max)
            velocities = prev_v + clamped_accel * dt_safe

    # ── Stage 2: velocity clamp ───────────────────────────────────────────
    ratio = np.abs(velocities) / (np.abs(v_max_1d) + 1e-12)
    max_ratio = float(np.max(ratio)) if ratio.size else 0.0
    vel_clamped = max_ratio > 1.0
    if vel_clamped:
        velocities = velocities / max_ratio

    # Update tracking buffer with the (possibly clamped) velocity
    _prev_vel[bname] = velocities.copy()

    was_clamped = accel_clamped or vel_clamped
    if not was_clamped:
        return CallbackResult.ok(
            bname,
            metadata={
                "max_velocity": v_max_1d.tolist(),
                "max_acceleration": a_max.tolist(),
                "current_velocity": velocities.tolist(),
                "scale_ratio": max_ratio,
                "_units": {
                    "max_velocity": "rad/s",
                    "max_acceleration": "rad/s²",
                    "current_velocity": "rad/s",
                },
            },
        )

    # Rebuild positions from the limited velocities
    if derived:
        new_pos = target_pos.copy()
        new_pos[:n] = cur_pos[:n] + velocities * dt_safe
    else:
        new_pos = target_pos.copy()

    reasons = []
    if accel_clamped:
        reasons.append("acceleration clamped")
    if vel_clamped:
        worst = int(np.argmax(ratio))
        reasons.append(
            f"velocity ratio={max_ratio:.2f} "
            f"(worst: J{worst + 1} > {float(v_max_1d[worst]):.3f} rad/s)"
        )
    reason = "; ".join(reasons)

    clamped_action = ValidatedAction(
        target_joint_positions=new_pos,
        target_joint_velocities=velocities if action.target_joint_velocities is not None else None,
        was_clamped=True,
        original_proposal=action,
        timestamp=action.timestamp,
    )
    qp_meta = MotionQPConstraint(
        max_velocity=v_max_1d.copy(),
        q=cur_pos[:n].copy(),
        dt=float(dt),
        slack_weight=float(slack_weight),
    )
    return CallbackResult.clamp(
        bname,
        clamped_action,
        reason=reason,
        metadata={"motion_qp": qp_meta, "_units": motion_qp_units(qp_meta)},
    )


@boundary_callback(
    name="joint_position_limits",
    layer="L1",
    category="kinematics",
    description="Clamps the action's joint positions into [lower, upper] (radians).",
    params={
        "upper": "Per-joint upper position limits. Radians by default unless use_degrees is true.",
        "lower": "Per-joint lower position limits. Radians by default unless use_degrees is true.",
        "slack_weight": "QP soft-constraint penalty. Higher values make violating this limit more expensive.",
        "use_degrees": "UI/loader hint: interpret upper/lower as degrees. Normalised to radians once at load — runtime never sees this flag.",
    },
    unit_params=("upper", "lower"),
    internal_params=("slack_weight",),
)
def joint_position_limits(
    *,
    action: ActionProposal,
    upper: list[float] | None = None,
    lower: list[float] | None = None,
    slack_weight: float = 1e6,
) -> CallbackResult:
    """Clip ``action.target_joint_positions`` element-wise into [lower, upper].

    When limits are omitted, defaults to ±π for however many joints the
    action contains — no hardcoded joint count assumption.
    """
    bname = "joint_position_limits"

    target = np.asarray(action.target_joint_positions, dtype=np.float64)
    if not _all_finite(target):
        return CallbackResult.violate(bname, "non-finite joint position action")
    n_joints = target.shape[0]

    if lower is None:
        lo = np.full(n_joints, -np.pi)
    else:
        lo = _to_array(lower, name="lower")
        _check_dim(n_joints, lo.shape[0], callback=bname, param="lower")
    if upper is None:
        up = np.full(n_joints, np.pi)
    else:
        up = _to_array(upper, name="upper")
        _check_dim(n_joints, up.shape[0], callback=bname, param="upper")

    n = min(n_joints, lo.shape[0], up.shape[0])
    clipped = target.copy()
    clipped[:n] = np.clip(target[:n], lo[:n], up[:n])

    if np.array_equal(clipped, target):
        # PASS-path telemetry: surface the configured box + the target so
        # the cycle inspector shows headroom (how close target is to the
        # box edges) without polluting the QP metadata namespace.
        return CallbackResult.ok(
            bname,
            metadata={
                "upper": up[:n].tolist(),
                "lower": lo[:n].tolist(),
                "target": target[:n].tolist(),
                "_units": {"upper": "rad", "lower": "rad", "target": "rad"},
            },
        )

    diff_mask = ~np.isclose(clipped, target)
    idx = int(np.argmax(diff_mask))
    reason = (
        f"position clamp J{idx + 1}: {float(target[idx]):.3f} rad -> {float(clipped[idx]):.3f} rad"
    )
    clamped = ValidatedAction(
        target_joint_positions=clipped,
        target_joint_velocities=action.target_joint_velocities,
        was_clamped=True,
        original_proposal=action,
        timestamp=action.timestamp,
    )
    # Advertise the joint-position box for an optional QP aggregator (limits in
    # radians, already unit-normalised above).
    qp_meta = MotionQPConstraint(
        upper=up[:n].copy(), lower=lo[:n].copy(), slack_weight=float(slack_weight)
    )
    return CallbackResult.clamp(
        bname,
        clamped,
        reason=reason,
        metadata={"motion_qp": qp_meta, "_units": motion_qp_units(qp_meta)},
    )


@boundary_callback(
    name="workspace",
    layer="L1",
    category="kinematics",
    description="Halts motion when the end-effector is outside the workspace box.",
    params={
        "bounds": "Axis-aligned allowed EE box: [[xmin,xmax],[ymin,ymax],[zmin,zmax]] in metres.",
    },
)
def workspace(
    *,
    obs: Observation,
    action: ActionProposal,
    bounds: list[list[float]] | None = None,
    kinematics_resolver: KinematicsResolver | None = None,
    dynamics: Any | None = None,
) -> CallbackResult:
    """When the current EE is outside ``bounds``, freeze the action.

    Without IK the framework cannot rewrite a joint target so the EE re-enters
    the box; the safe default is to clamp the proposal back to the current
    joint state — a full brake.
    """
    bname = "workspace"
    if bounds is None:
        bounds = [[-0.4, 0.4], [-0.4, 0.4], [0.02, 0.6]]

    ee_pos = _resolve_ee_translation(
        obs, kinematics_resolver=kinematics_resolver, dynamics=dynamics
    )
    if ee_pos is None or obs.joint_positions is None:
        return CallbackResult.ok(
            bname,
            "EE pose unavailable; skip workspace check",
            metadata={"bounds": np.asarray(bounds, dtype=np.float64).tolist()},
        )

    b = np.asarray(bounds, dtype=np.float64)
    if np.all((ee_pos >= b[:, 0]) & (ee_pos <= b[:, 1])):
        return CallbackResult.ok(
            bname,
            metadata={
                "bounds": b.tolist(),
                "ee_pos": ee_pos.tolist(),
            },
        )

    halt = ValidatedAction(
        target_joint_positions=np.asarray(obs.joint_positions, dtype=np.float64).copy(),
        target_joint_velocities=None,
        was_clamped=True,
        original_proposal=action,
        timestamp=action.timestamp,
    )
    reason = f"EE {ee_pos.tolist()} outside workspace {bounds}; halting"
    return CallbackResult.clamp(
        bname,
        halt,
        reason=reason,
        metadata={
            "workspace_bounds": bounds,
            "ee_pos": ee_pos.tolist(),
            "_units": {"workspace_bounds": "m", "ee_pos": "m"},
        },
    )


@boundary_callback(
    name="keep_out_zone",
    layer="L1",
    category="kinematics",
    description="Rejects if the end-effector enters a keep-out box or sphere.",
    params={
        "boxes": "Keep-out boxes as a list of [[xmin,xmax],[ymin,ymax],[zmin,zmax]] regions.",
        "spheres": "Keep-out spheres as a list of [cx,cy,cz,radius] regions in metres.",
    },
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
    if not _all_finite(pos):
        return False, "non-finite EE position"
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
    category="kinematics",
    description="Rejects if end-effector tilt from a reference axis exceeds a limit (deg).",
    params={
        "max_tilt_deg": "Maximum allowed tool tilt from the reference axis, in degrees.",
        "reference_axis": "World-frame axis to align with. Defaults to [0,0,1].",
        "tool_axis": "Tool-frame axis that should stay aligned. Defaults to [0,0,1].",
    },
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
    if not _all_finite(rot):
        return False, "non-finite EE orientation"
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
    category="kinematics",
    description="Rejects if the mobile base leaves a geofence box or polygon.",
    params={
        "bounds": "Allowed base x/y box: [[xmin,xmax],[ymin,ymax]] in metres.",
        "polygon": "Allowed base geofence polygon as [[x,y], ...].",
        "channel": "Observation channel name containing the base pose.",
    },
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
