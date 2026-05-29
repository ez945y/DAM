"""L1 — Physical kinematics boundary callbacks.

Geometric / kinematic invariants enforced every cycle, independent of task
state: joint and workspace limits, plus the human-collaborative constraints
(Cartesian speed, keep-out volumes, payload orientation, base geofence).

Action-clamp policy
-------------------
L1 boundary callbacks that have a well-defined corrective action (joint
limits, velocity limits, workspace box) return ``CallbackResult.clamp(...)``
with the corrected ``ValidatedAction``. They do not REJECT — the inner pipeline
prefers correcting the policy's proposal to halting the robot mid-trajectory.
Callbacks for which no clean clamp exists (Cartesian speed limit, keep-out
zones, orientation, geofence) keep returning ``bool`` and surface as REJECT
through the legacy shim.
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
    description="Clamps the action's joint velocities to ±max_velocities (radians/sec).",
    params={
        "max_velocities": "Per-joint max velocity. Radians/sec by default unless use_degrees is true.",
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
    slack_weight: float = 1e6,
) -> CallbackResult:
    """Scale action velocities (or implied velocities) so |v_i| ≤ max_i.

    Operates on the *proposed* action: derives velocities from
    ``(target_positions − obs_positions) / dt`` when the action does not
    carry velocities, scales by the worst-offender ratio, then rebuilds the
    target positions from the limited velocities so policy and adapter stay
    consistent.  Reads ``dt`` from the pool (auto-injected by GuardRuntime).
    """
    bname = "joint_velocity_limit"
    if obs.joint_positions is None:
        return CallbackResult.ok(bname, "no joint state to act on")

    target_pos = np.asarray(action.target_joint_positions, dtype=np.float64)
    cur_pos = np.asarray(obs.joint_positions, dtype=np.float64)
    if not _all_finite(target_pos) or not _all_finite(cur_pos):
        return CallbackResult.violate(bname, "non-finite joint state or action")
    n = min(target_pos.shape[0], cur_pos.shape[0])

    if max_velocities is None:
        v_max = np.full(n, 1.5)
    else:
        v_max = _to_array(max_velocities, name="max_velocities")
        _check_dim(n, v_max.shape[0], callback=bname, param="max_velocities")

    if action.target_joint_velocities is not None:
        velocities = np.asarray(action.target_joint_velocities, dtype=np.float64)[:n]
        if not _all_finite(velocities):
            return CallbackResult.violate(bname, "non-finite joint velocity action")
        derived = False
    else:
        velocities = (target_pos[:n] - cur_pos[:n]) / max(float(dt), 1e-6)
        derived = True

    v_max_1d = np.atleast_1d(v_max)
    if v_max_1d.shape[0] == 1:
        v_max_1d = np.full(n, v_max_1d[0])
    v_max_1d = v_max_1d[:n]

    ratio = np.abs(velocities) / (np.abs(v_max_1d) + 1e-12)
    max_ratio = float(np.max(ratio)) if ratio.size else 0.0
    if max_ratio <= 1.0:
        # PASS-path telemetry: surface the limit, current velocity, and the
        # headroom ratio so the cycle inspector shows how close we are even
        # when nothing fires.  ``_units`` annotates sibling fields so the
        # frontend can convert rad → deg without hardcoded key knowledge.
        return CallbackResult.ok(
            bname,
            metadata={
                "max_velocity": v_max_1d.tolist(),
                "current_velocity": velocities.tolist(),
                "scale_ratio": max_ratio,
                "_units": {
                    "max_velocity": "rad/s",
                    "current_velocity": "rad/s",
                },
            },
        )

    clamped_v = velocities / max_ratio
    # Rebuild positions from the limited velocities so the downstream adapter
    # receives a consistent target (positions implied by the rate the joints
    # are actually allowed to move at this cycle).
    if derived:
        new_pos = target_pos.copy()
        new_pos[:n] = cur_pos[:n] + clamped_v * float(dt)
    else:
        new_pos = target_pos.copy()

    worst = int(np.argmax(ratio))
    clamped = ValidatedAction(
        target_joint_positions=new_pos,
        target_joint_velocities=clamped_v if action.target_joint_velocities is not None else None,
        was_clamped=True,
        original_proposal=action,
        timestamp=action.timestamp,
    )
    reason = (
        f"velocity scale ratio={max_ratio:.2f} "
        f"(worst: J{worst + 1} {abs(float(velocities[worst])):.3f} rad/s "
        f"> {float(v_max_1d[worst]):.3f} rad/s)"
    )
    # Advertise the per-joint velocity limit (plus the state needed to express
    # it as a position bound) for an optional QP aggregator.  ``_units`` is
    # derived from the constraint itself so populated-field knowledge isn't
    # duplicated between the dataclass and this dict.
    qp_meta = MotionQPConstraint(
        max_velocity=v_max_1d.copy(),
        q=cur_pos[:n].copy(),
        dt=float(dt),
        slack_weight=float(slack_weight),
    )
    return CallbackResult.clamp(
        bname,
        clamped,
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
        "cbf_gamma": "Workspace CBF decay in [0,1]. 1 is a hard one-step bound; lower values brake earlier.",
        "slack_weight": "QP soft-constraint penalty. Higher values make violating this limit more expensive.",
    },
    internal_params=("cbf_gamma", "cbf_alpha", "slack_weight"),
)
def workspace(
    *,
    obs: Observation,
    action: ActionProposal,
    bounds: list[list[float]] | None = None,
    cbf_gamma: float = 1.0,
    cbf_alpha: float | None = None,
    slack_weight: float = 1e6,
    kinematics_resolver: KinematicsResolver | None = None,
    dynamics: Any | None = None,
) -> CallbackResult:
    """When the current EE is outside ``bounds``, freeze the action.

    Without IK the framework cannot rewrite a joint target so the EE re-enters
    the box; the safe default is to clamp the proposal back to the current
    joint state — a brake.  Users wanting smooth EE-constrained motion should
    inject a QP/CBF aggregator that consumes the workspace constraint this
    callback attaches to ``metadata['motion_qp']``.

    ``cbf_gamma`` is the discrete CBF decay rate γ ∈ [0, 1] (1 = one-step hard
    bound, →0 = brake early with large margins).  The legacy name ``cbf_alpha``
    is accepted as an alias and mapped onto ``cbf_gamma`` with a warning.
    """
    bname = "workspace"
    if bounds is None:
        bounds = [[-0.4, 0.4], [-0.4, 0.4], [0.02, 0.6]]

    if cbf_alpha is not None:
        logger.warning(
            "workspace: 'cbf_alpha' is deprecated; use 'cbf_gamma' (γ ∈ [0,1]). "
            "Treating cbf_alpha=%s as cbf_gamma.",
            cbf_alpha,
        )
        cbf_gamma = float(cbf_alpha)
    if not 0.0 <= cbf_gamma <= 1.0:
        raise ValueError(f"cbf_gamma must be in [0, 1], got {cbf_gamma}")

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
    # A QP/CBF aggregator needs the linear part of the EE Jacobian to turn the
    # workspace box into a control-affine CBF constraint; supply it when a
    # dynamics context is available, else only the box (aggregator skips CBF).
    J_linear = _ee_linear_jacobian(obs, dynamics)
    q_full = np.asarray(obs.joint_positions, dtype=np.float64)
    nq = J_linear.shape[1] if J_linear is not None else len(q_full)
    qp_meta = MotionQPConstraint(
        workspace_bounds=b.copy(),
        cbf_gamma=cbf_gamma,
        ee_pos=ee_pos.copy(),
        q=q_full[:nq].copy(),
        J_linear=J_linear,
        slack_weight=float(slack_weight),
    )
    return CallbackResult.clamp(
        bname,
        halt,
        reason=reason,
        metadata={
            "workspace_bounds": bounds,
            "ee_pos": ee_pos.tolist(),
            "motion_qp": qp_meta,
            # workspace_bounds / ee_pos are metres — declared so the inspector
            # leaves them alone instead of guessing.  motion_qp's own fields
            # are described under the dotted-path keys below.
            "_units": {
                "workspace_bounds": "m",
                "ee_pos": "m",
                **motion_qp_units(qp_meta),
            },
        },
    )


def _ee_linear_jacobian(obs: Observation, dynamics: Any | None) -> np.ndarray | None:
    """Linear (translational) part of the EE Jacobian, shape (3, n), or None."""
    if (
        dynamics is None
        or not getattr(dynamics, "available", False)
        or getattr(dynamics, "default_frame_id", None) is None
        or obs.joint_positions is None
    ):
        return None
    dynamics.update(np.asarray(obs.joint_positions, dtype=np.float64))
    jac = np.asarray(dynamics.frame_jacobian(dynamics.default_frame_id), dtype=np.float64)
    return jac[:3, :].copy()


_prev_velocities: dict[str, np.ndarray] = {}


@boundary_callback(
    name="check_velocity_smooth",
    layer="L1",
    category="kinematics",
    description="Rejects if the joint-space jerk norm (rate of velocity change) exceeds a threshold.",
    params={
        "max_jerk_norm": "Maximum allowed norm of (v_current − v_prev)/dt in rad/s².",
    },
)
def check_velocity_smooth(
    *,
    obs: Observation,
    dt: float = 0.02,
    max_jerk_norm: float = 10.0,
) -> bool:
    """Return False if ‖(v_current − v_prev) / dt‖ exceeds the jerk threshold.

    Requires consecutive observations with ``joint_velocities`` populated.
    Passes on the first cycle (no previous velocity to compare against).
    """
    bname = "check_velocity_smooth"
    if obs.joint_velocities is None:
        return True
    v_cur = np.asarray(obs.joint_velocities, dtype=np.float64)
    v_prev = _prev_velocities.get(bname)
    _prev_velocities[bname] = v_cur.copy()
    if v_prev is None or v_prev.shape != v_cur.shape:
        return True
    jerk = (v_cur - v_prev) / max(float(dt), 1e-6)
    jerk_norm = float(np.linalg.norm(jerk))
    return jerk_norm <= max_jerk_norm


@boundary_callback(
    name="check_joints_not_moving",
    layer="L1",
    category="kinematics",
    description="Rejects if any joint velocity exceeds a near-zero threshold.",
    params={
        "max_speed_rad_s": "Maximum absolute joint speed still treated as stationary, in rad/s."
    },
)
def check_joints_not_moving(*, obs: Observation, max_speed_rad_s: float = 0.01) -> bool:
    """Return False if any joint is moving faster than threshold."""
    if obs.joint_velocities is None:
        return True
    return float(np.max(np.abs(obs.joint_velocities))) <= max_speed_rad_s


@boundary_callback(
    name="cartesian_velocity_limit",
    layer="L1",
    category="kinematics",
    description="Caps end-effector Cartesian speed (linear m/s, angular rad/s).",
    params={
        "max_linear_speed": "Maximum end-effector linear speed in m/s.",
        "max_angular_speed": "Maximum end-effector angular speed in rad/s.",
        "frame": "Optional dynamics frame id/name. Leave empty to use the robot default EE frame.",
    },
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
    if not _all_finite(twist):
        return False, "non-finite EE twist (NaN/Inf in joint state or Jacobian)"
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


# ── Action smoothing ─────────────────────────────────────────────────────────

_ema_state: dict[str, np.ndarray] = {}


@boundary_callback(
    name="action_smooth",
    layer="L1",
    category="kinematics",
    description="Applies exponential moving average to joint position targets, reducing high-frequency oscillation from noisy policies.",
    params={
        "alpha": "EMA smoothing factor in (0, 1]. Lower values smooth more aggressively (0.3 = heavy smoothing, 1.0 = no smoothing).",
    },
)
def action_smooth(
    *,
    obs: Observation,
    action: ActionProposal,
    alpha: float = 0.5,
) -> CallbackResult:
    """Smooth the proposed action using an exponential moving average.

    Each cycle: ``smoothed = alpha * proposed + (1 - alpha) * previous``.
    On the first cycle the proposed action passes through unmodified.
    """
    bname = "action_smooth"
    target = np.asarray(action.target_joint_positions, dtype=np.float64)
    if not _all_finite(target):
        return CallbackResult.ok(bname, "non-finite target; skipping smoothing")

    alpha = float(np.clip(alpha, 0.01, 1.0))

    prev = _ema_state.get(bname)
    if prev is None or prev.shape != target.shape:
        _ema_state[bname] = target.copy()
        return CallbackResult.ok(
            bname,
            metadata={"alpha": alpha, "smoothed": False},
        )

    smoothed = alpha * target + (1.0 - alpha) * prev
    _ema_state[bname] = smoothed.copy()

    if np.allclose(smoothed, target, atol=1e-7):
        return CallbackResult.ok(
            bname,
            metadata={"alpha": alpha, "smoothed": False},
        )

    clamped = ValidatedAction(
        target_joint_positions=smoothed,
        target_joint_velocities=action.target_joint_velocities,
        was_clamped=True,
        original_proposal=action,
        timestamp=action.timestamp,
    )
    delta = float(np.max(np.abs(smoothed - target)))
    return CallbackResult.clamp(
        bname,
        clamped,
        reason=f"EMA smoothing applied (α={alpha:.2f}, max_delta={delta:.4f} rad)",
        metadata={
            "alpha": alpha,
            "max_delta": delta,
            "smoothed": True,
        },
    )
