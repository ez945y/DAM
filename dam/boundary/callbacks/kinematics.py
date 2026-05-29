"""L1 — Joint-space kinematics boundary callbacks.

Physical invariants enforced every cycle via CLAMP, independent of task
state.  All L1 callbacks produce ``CallbackResult.clamp(...)`` with QP
constraint metadata; the QP aggregator fuses them into a single
least-perturbing safe action.

Callbacks:
    ``joint_position_limits`` — box bounds on joint positions
    ``joint_velocity_limit``  — velocity + acceleration limits
    ``workspace``             — EE position box (CBF constraint via QP)
    ``keep_out_zone``         — spherical keep-out zones (CBF constraint via QP)
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from dam.boundary.callbacks._helpers import (
    _all_finite,
    _resolve_ee_translation,
)
from dam.boundary.callbacks._registry import boundary_callback
from dam.guard.aggregators.motion_qp import QPTerm, motion_qp_units
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
    span = v_max_1d * dt_safe
    qp_meta = QPTerm(
        upper=cur_pos[:n] + span,
        lower=cur_pos[:n] - span,
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
    qp_meta = QPTerm(upper=up[:n].copy(), lower=lo[:n].copy(), slack_weight=float(slack_weight))
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
    description="Keeps the end-effector inside an axis-aligned workspace box via CBF constraints fed to the QP solver.",
    params={
        "bounds": "Axis-aligned allowed EE box: [[xmin,xmax],[ymin,ymax],[zmin,zmax]] in metres.",
        "cbf_alpha": "CBF decay rate (0,∞). Higher → brake later, lower → brake earlier. Default 1.0.",
        "slack_weight": "QP soft-constraint penalty. Higher values make violating this limit more expensive.",
    },
    internal_params=("slack_weight",),
)
def workspace(
    *,
    obs: Observation,
    action: ActionProposal,
    bounds: list[list[float]] | None = None,
    cbf_alpha: float = 1.0,
    slack_weight: float = 1e6,
    ee_pos: np.ndarray | None = None,
    J_linear: np.ndarray | None = None,
    kinematics_resolver: KinematicsResolver | None = None,
    dynamics: Any | None = None,
) -> CallbackResult:
    """CBF constraint that keeps the EE inside ``bounds``.

    Linearises the workspace box into ``A @ u ≤ b`` constraints and attaches
    them as a :class:`QPTerm`.  The QP aggregator fuses this with other L1
    constraints to find the least-perturbing safe action.

    Falls back to halt (freeze current joints) when Jacobian is unavailable.
    """
    bname = "workspace"
    if bounds is None:
        bounds = [[-0.4, 0.4], [-0.4, 0.4], [0.02, 0.6]]

    if ee_pos is None:
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
    q = np.asarray(obs.joint_positions, dtype=np.float64)
    target = np.asarray(action.target_joint_positions, dtype=np.float64)

    inside = bool(np.all((ee_pos >= b[:, 0]) & (ee_pos <= b[:, 1])))

    if J_linear is None:
        if inside:
            return CallbackResult.ok(
                bname,
                metadata={"bounds": b.tolist(), "ee_pos": ee_pos.tolist()},
            )
        halt = ValidatedAction(
            target_joint_positions=q.copy(),
            target_joint_velocities=None,
            was_clamped=True,
            original_proposal=action,
            timestamp=action.timestamp,
        )
        return CallbackResult.clamp(
            bname,
            halt,
            reason=f"EE {ee_pos.tolist()} outside workspace (no Jacobian); halting",
            metadata={
                "workspace_bounds": b.tolist(),
                "ee_pos": ee_pos.tolist(),
                "_units": {"workspace_bounds": "m", "ee_pos": "m"},
            },
        )

    from dam.runtime import qp_solver

    cbf_A, cbf_b = qp_solver.workspace_cbf_constraints(
        q=q,
        ee_pos=ee_pos,
        J_linear=J_linear,
        bounds=b,
        cbf_alpha=cbf_alpha,
    )

    n = J_linear.shape[1]
    margin = cbf_b - cbf_A @ target[:n]
    satisfied = bool(np.all(margin >= -1e-8))

    if inside and satisfied:
        return CallbackResult.ok(
            bname,
            metadata={
                "bounds": b.tolist(),
                "ee_pos": ee_pos.tolist(),
                "cbf_margin_min": float(np.min(margin)),
            },
        )

    qp_meta = QPTerm(A=cbf_A, b=cbf_b, slack_weight=float(slack_weight))
    clamped = ValidatedAction(
        target_joint_positions=target.copy(),
        target_joint_velocities=action.target_joint_velocities,
        was_clamped=True,
        original_proposal=action,
        timestamp=action.timestamp,
    )
    if inside:
        reason = (
            f"EE action would leave workspace; CBF active (margin_min={float(np.min(margin)):.4f})"
        )
    else:
        reason = f"EE {ee_pos.tolist()} outside workspace {b.tolist()}; CBF pushing back"
    return CallbackResult.clamp(
        bname,
        clamped,
        reason=reason,
        metadata={
            "motion_qp": qp_meta,
            "workspace_bounds": b.tolist(),
            "ee_pos": ee_pos.tolist(),
            "cbf_margin_min": float(np.min(margin)),
            "_units": {"workspace_bounds": "m", "ee_pos": "m"},
        },
    )


@boundary_callback(
    name="keep_out_zone",
    layer="L1",
    category="kinematics",
    description="Keeps the end-effector outside spherical keep-out zones via CBF constraints fed to the QP solver.",
    params={
        "spheres": "Keep-out spheres as [[cx,cy,cz,radius], ...] in metres.",
        "cbf_alpha": "CBF decay rate (0,∞). Higher → repel later, lower → repel earlier. Default 1.0.",
        "slack_weight": "QP soft-constraint penalty.",
    },
    internal_params=("slack_weight",),
)
def keep_out_zone(
    *,
    obs: Observation,
    action: ActionProposal,
    spheres: list[list[float]] | None = None,
    cbf_alpha: float = 1.0,
    slack_weight: float = 1e6,
    ee_pos: np.ndarray | None = None,
    J_linear: np.ndarray | None = None,
    kinematics_resolver: KinematicsResolver | None = None,
    dynamics: Any | None = None,
) -> CallbackResult:
    """CBF constraint that keeps the EE outside spherical keep-out zones.

    Each sphere ``[cx, cy, cz, radius]`` produces one linear inequality
    via CBF linearization, attached as a :class:`QPTerm`.

    Falls back to halt when Jacobian is unavailable and EE is inside a zone.
    """
    bname = "keep_out_zone"
    if not spheres:
        return CallbackResult.ok(bname, "no keep-out zones configured")

    if ee_pos is None:
        ee_pos = _resolve_ee_translation(
            obs, kinematics_resolver=kinematics_resolver, dynamics=dynamics
        )
    if ee_pos is None or obs.joint_positions is None:
        return CallbackResult.ok(bname, "EE pose unavailable; skip keep-out check")

    q = np.asarray(obs.joint_positions, dtype=np.float64)
    target = np.asarray(action.target_joint_positions, dtype=np.float64)

    # Check if EE is currently inside any sphere
    violated_sphere = None
    for sphere in spheres:
        s = np.asarray(sphere, dtype=np.float64)
        dist = float(np.linalg.norm(ee_pos - s[:3]))
        if dist <= float(s[3]):
            violated_sphere = sphere
            break

    if J_linear is None:
        if violated_sphere is None:
            return CallbackResult.ok(
                bname,
                metadata={"ee_pos": ee_pos.tolist(), "n_spheres": len(spheres)},
            )
        halt = ValidatedAction(
            target_joint_positions=q.copy(),
            target_joint_velocities=None,
            was_clamped=True,
            original_proposal=action,
            timestamp=action.timestamp,
        )
        return CallbackResult.clamp(
            bname,
            halt,
            reason=f"EE inside keep-out sphere {violated_sphere} (no Jacobian); halting",
            metadata={"ee_pos": ee_pos.tolist()},
        )

    from dam.runtime import qp_solver

    cbf_A, cbf_b = qp_solver.sphere_keepout_constraints(
        q=q,
        ee_pos=ee_pos,
        J_linear=J_linear,
        spheres=spheres,
        cbf_alpha=cbf_alpha,
    )

    n = J_linear.shape[1]
    margin = cbf_b - cbf_A @ target[:n]
    satisfied = bool(np.all(margin >= -1e-8))

    if violated_sphere is None and satisfied:
        return CallbackResult.ok(
            bname,
            metadata={
                "ee_pos": ee_pos.tolist(),
                "n_spheres": len(spheres),
                "cbf_margin_min": float(np.min(margin)),
            },
        )

    qp_meta = QPTerm(A=cbf_A, b=cbf_b, slack_weight=float(slack_weight))
    clamped = ValidatedAction(
        target_joint_positions=target.copy(),
        target_joint_velocities=action.target_joint_velocities,
        was_clamped=True,
        original_proposal=action,
        timestamp=action.timestamp,
    )
    if violated_sphere is not None:
        reason = f"EE inside keep-out sphere {violated_sphere}; CBF pushing out"
    else:
        reason = f"EE action would enter keep-out zone; CBF active (margin_min={float(np.min(margin)):.4f})"
    return CallbackResult.clamp(
        bname,
        clamped,
        reason=reason,
        metadata={
            "motion_qp": qp_meta,
            "ee_pos": ee_pos.tolist(),
            "n_spheres": len(spheres),
            "cbf_margin_min": float(np.min(margin)),
            "_units": {"ee_pos": "m"},
        },
    )
