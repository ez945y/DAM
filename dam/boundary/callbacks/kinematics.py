"""L1 — Joint-space kinematics boundary callbacks.

Physical invariants enforced every cycle via CLAMP, independent of task
state.  All L1 callbacks produce ``CallbackResult.clamp(...)`` with QP
constraint metadata; the QP aggregator fuses them into a single
least-perturbing safe action.

Callbacks:
    ``joint_position_limits``    — box bounds on joint positions
    ``joint_velocity_limit``     — per-joint velocity cap (motor safety)
    ``joint_acceleration_limit`` — per-joint acceleration cap (smoothing)
    ``ee_velocity_limit``        — EE linear speed cap (environment/human safety)
    ``workspace``                — EE position box (CBF constraint via QP)
    ``keep_out_zone``            — spherical keep-out zones (CBF constraint via QP)
    ``orientation_limit``        — EE tilt limit (CBF constraint via QP)
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from dam.boundary.callbacks._helpers import (
    _all_finite,
    _resolve_ee_rotation,
    _resolve_ee_translation,
)
from dam.boundary.callbacks._registry import boundary_callback
from dam.guard.aggregators.motion_qp import QPTerm, motion_qp_units
from dam.guard.pipeline import CallbackResult
from dam.kinematics.resolver import KinematicsResolver

# Lazy import — resolved on first CBF path entry.  Module-level would create
# a circular import (qp_solver → dam.types → …).
_qp_solver: Any = None


def _get_qp_solver() -> Any:
    global _qp_solver  # noqa: PLW0603
    if _qp_solver is None:
        from dam.runtime import qp_solver as _qs

        _qp_solver = _qs
    return _qp_solver


from dam.types.action import ActionProposal, ValidatedAction
from dam.types.observation import Observation

logger = logging.getLogger(__name__)


# ── Shared CBF helpers ─────────────────────────────────────────────────────────


def _halt_clamp(
    bname: str,
    q: np.ndarray,
    action: ActionProposal,
    reason: str,
    metadata: dict[str, Any] | None = None,
) -> CallbackResult:
    """Freeze action to current joints — fallback when Jacobian is unavailable."""
    halt = ValidatedAction(
        target_joint_positions=q.copy(),
        target_joint_velocities=None,
        was_clamped=True,
        original_proposal=action,
        timestamp=action.timestamp,
    )
    return CallbackResult.clamp(bname, halt, reason=reason, metadata=metadata or {})


def _cbf_clamp(
    bname: str,
    action: ActionProposal,
    target: np.ndarray,
    cbf_A: np.ndarray,
    cbf_b: np.ndarray,
    slack_weight: float,
    reason: str,
    extra_metadata: dict[str, Any] | None = None,
) -> CallbackResult:
    """Build QPTerm from CBF constraints and return a CLAMP with QP metadata."""
    qp_meta = QPTerm(A=cbf_A, b=cbf_b, slack_weight=float(slack_weight))
    clamped = ValidatedAction(
        target_joint_positions=target.copy(),
        target_joint_velocities=action.target_joint_velocities,
        was_clamped=True,
        original_proposal=action,
        timestamp=action.timestamp,
    )
    metadata: dict[str, Any] = {"motion_qp": qp_meta}
    if extra_metadata:
        metadata.update(extra_metadata)
    return CallbackResult.clamp(bname, clamped, reason=reason, metadata=metadata)


def _cbf_margin(
    cbf_A: np.ndarray, cbf_b: np.ndarray, target: np.ndarray, n_joints: int
) -> tuple[np.ndarray, bool]:
    """Check whether the proposed target satisfies CBF constraints.

    Handles dimension mismatch: if ``target`` has fewer elements than
    ``n_joints`` (Jacobian DOF), the missing joints are zero-padded.
    If ``target`` has more elements, the extras are ignored (only the
    Jacobian-covered joints affect the EE).
    """
    u = target[:n_joints]
    if u.shape[0] < n_joints:
        u = np.pad(u, (0, n_joints - u.shape[0]))
    margin = cbf_b - cbf_A @ u
    satisfied = bool(np.all(margin >= -1e-8))
    return margin, satisfied


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
    description="Clamps the action's joint velocities to ±max_velocities (motor safety ceiling).",
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
    """Clamp proposed velocity per joint.

    Per-joint clipping: each joint is independently clamped to its own
    velocity limit.  Target positions are rebuilt from the clamped velocity
    so policy and adapter stay consistent.

    ``dt`` is auto-injected by GuardRuntime.
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

    # ── Derive proposed velocity ──────────────────────────────────────────
    if action.target_joint_velocities is not None:
        velocities = np.asarray(action.target_joint_velocities, dtype=np.float64)[:n]
        if not _all_finite(velocities):
            return CallbackResult.violate(bname, "non-finite joint velocity action")
    else:
        velocities = (target_pos[:n] - cur_pos[:n]) / dt_safe

    v_max_1d = np.atleast_1d(v_max)
    if v_max_1d.shape[0] == 1:
        v_max_1d = np.full(n, v_max_1d[0])
    v_max_1d = v_max_1d[:n]

    # ── Velocity clamp (per-joint) ────────────────────────────────────────
    ratio = np.abs(velocities) / (np.abs(v_max_1d) + 1e-12)
    max_ratio = float(np.max(ratio)) if ratio.size else 0.0
    vel_clamped = max_ratio > 1.0
    if not vel_clamped:
        meta: dict[str, Any] = {
            "max_velocity": v_max_1d.tolist(),
            "current_velocity": velocities.tolist(),
            "scale_ratio": max_ratio,
            "_units": {"max_velocity": "rad/s", "current_velocity": "rad/s"},
        }
        return CallbackResult.ok(bname, metadata=meta)

    velocities = np.clip(velocities, -v_max_1d, v_max_1d)

    # Rebuild positions from the limited velocities
    new_pos = target_pos.copy()
    new_pos[:n] = cur_pos[:n] + velocities * dt_safe

    worst = int(np.argmax(ratio))
    reason = (
        f"velocity ratio={max_ratio:.2f} (worst: J{worst + 1} > {float(v_max_1d[worst]):.3f} rad/s)"
    )

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
    name="joint_acceleration_limit",
    layer="L1",
    category="kinematics",
    description="Clamps the action's joint accelerations to ±max_acceleration (smoothing / jitter suppression).",
    params={
        "max_acceleration": "Per-joint max acceleration in rad/s².",
        "use_degrees": "UI/loader hint: interpret max_acceleration as deg/s². Normalised to rad/s² once at load.",
    },
    unit_params=("max_acceleration",),
)
def joint_acceleration_limit(
    *,
    obs: Observation,
    action: ActionProposal,
    dt: float,
    max_acceleration: list[float] | float,
) -> CallbackResult:
    """Clamp proposed acceleration per joint.

    Tracks the previous cycle's commanded velocity and limits how fast
    velocity can change.  First cycle is always PASS (no history).

    ``dt`` is auto-injected by GuardRuntime.
    """
    bname = "joint_acceleration_limit"
    if obs.joint_positions is None:
        return CallbackResult.ok(bname, "no joint state to act on")

    target_pos = np.asarray(action.target_joint_positions, dtype=np.float64)
    cur_pos = np.asarray(obs.joint_positions, dtype=np.float64)
    if not _all_finite(target_pos) or not _all_finite(cur_pos):
        return CallbackResult.violate(bname, "non-finite joint state or action")
    n = min(target_pos.shape[0], cur_pos.shape[0])
    dt_safe = max(float(dt), 1e-6)

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
    else:
        velocities = (target_pos[:n] - cur_pos[:n]) / dt_safe

    # ── Acceleration clamp ────────────────────────────────────────────────
    prev_v = _prev_vel.get(bname)
    if prev_v is None or prev_v.shape != velocities.shape:
        _prev_vel[bname] = velocities.copy()
        return CallbackResult.ok(
            bname,
            metadata={
                "max_acceleration": a_max.tolist(),
                "current_velocity": velocities.tolist(),
                "_units": {"max_acceleration": "rad/s²", "current_velocity": "rad/s"},
            },
        )

    accel = (velocities - prev_v) / dt_safe
    over_accel = np.abs(accel) > a_max
    if not np.any(over_accel):
        _prev_vel[bname] = velocities.copy()
        return CallbackResult.ok(
            bname,
            metadata={
                "max_acceleration": a_max.tolist(),
                "current_acceleration": accel.tolist(),
                "current_velocity": velocities.tolist(),
                "_units": {
                    "max_acceleration": "rad/s²",
                    "current_acceleration": "rad/s²",
                    "current_velocity": "rad/s",
                },
            },
        )

    clamped_accel = np.clip(accel, -a_max, a_max)
    velocities = prev_v + clamped_accel * dt_safe
    _prev_vel[bname] = velocities.copy()

    new_pos = target_pos.copy()
    new_pos[:n] = cur_pos[:n] + velocities * dt_safe

    worst = int(np.argmax(np.abs(accel) - a_max))
    reason = (
        f"acceleration clamped "
        f"(worst: J{worst + 1} {float(accel[worst]):.1f} > ±{float(a_max[worst]):.1f} rad/s²)"
    )

    clamped_action = ValidatedAction(
        target_joint_positions=new_pos,
        target_joint_velocities=velocities if action.target_joint_velocities is not None else None,
        was_clamped=True,
        original_proposal=action,
        timestamp=action.timestamp,
    )
    return CallbackResult.clamp(bname, clamped_action, reason=reason)


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
    name="ee_velocity_limit",
    layer="L1",
    category="kinematics",
    description="Clamps the end-effector linear velocity magnitude to max_ee_velocity (m/s). Protects environment/humans.",
    params={
        "max_ee_velocity": "Maximum EE linear speed in m/s.",
    },
)
def ee_velocity_limit(
    *,
    obs: Observation,
    action: ActionProposal,
    dt: float,
    max_ee_velocity: float = 0.5,
    J_linear: np.ndarray | None = None,
    kinematics_resolver: KinematicsResolver | None = None,
    dynamics: Any | None = None,
) -> CallbackResult:
    """Clamp EE linear speed to ``max_ee_velocity`` m/s.

    Uses the linear Jacobian to map joint velocities to EE velocity:
    ``v_ee = J_linear @ v_joint``.  If ``||v_ee|| > max``, the joint
    velocity vector is uniformly scaled so the EE speed stays at the limit.
    Uniform scaling preserves the EE direction — all joints slow down
    together to produce the same coordinated Cartesian motion.

    Without a Jacobian, the callback passes (no EE info to check).

    ``dt``, ``J_linear``, ``kinematics_resolver``, ``dynamics`` are
    auto-injected by the guard pipeline.
    """
    bname = "ee_velocity_limit"
    if obs.joint_positions is None:
        return CallbackResult.ok(bname, "no joint state")

    cur_pos = np.asarray(obs.joint_positions, dtype=np.float64)
    target_pos = np.asarray(action.target_joint_positions, dtype=np.float64)
    if not _all_finite(target_pos) or not _all_finite(cur_pos):
        return CallbackResult.violate(bname, "non-finite joint state or action")
    n = min(target_pos.shape[0], cur_pos.shape[0])
    dt_safe = max(float(dt), 1e-6)

    if J_linear is None:
        return CallbackResult.ok(bname, "no Jacobian; skip EE velocity check")

    # ── Derive joint velocity ─────────────────────────────────────────────
    if action.target_joint_velocities is not None:
        v_joint = np.asarray(action.target_joint_velocities, dtype=np.float64)[:n]
    else:
        v_joint = (target_pos[:n] - cur_pos[:n]) / dt_safe

    # ── Map to EE velocity via Jacobian ───────────────────────────────────
    n_jac = J_linear.shape[1]
    v_joint_padded = np.zeros(n_jac, dtype=np.float64)
    v_joint_padded[: min(n, n_jac)] = v_joint[: min(n, n_jac)]
    v_ee = J_linear @ v_joint_padded
    ee_speed = float(np.linalg.norm(v_ee))
    max_v = float(max_ee_velocity)

    meta: dict[str, Any] = {
        "ee_velocity": v_ee.tolist(),
        "ee_speed": ee_speed,
        "max_ee_velocity": max_v,
        "_units": {"ee_velocity": "m/s", "ee_speed": "m/s", "max_ee_velocity": "m/s"},
    }

    if ee_speed <= max_v:
        return CallbackResult.ok(bname, metadata=meta)

    # ── Scale joint velocity uniformly to respect EE speed limit ──────────
    scale = max_v / (ee_speed + 1e-12)
    v_clamped = v_joint * scale
    new_pos = cur_pos[:n] + v_clamped * dt_safe

    # Pad to full action length
    full_pos = target_pos.copy()
    full_pos[:n] = new_pos

    reason = f"EE speed {ee_speed:.3f} m/s > {max_v:.3f} m/s; scaled to {scale:.2%}"
    meta["scale"] = scale

    clamped_action = ValidatedAction(
        target_joint_positions=full_pos,
        target_joint_velocities=v_clamped if action.target_joint_velocities is not None else None,
        was_clamped=True,
        original_proposal=action,
        timestamp=action.timestamp,
    )
    return CallbackResult.clamp(bname, clamped_action, reason=reason, metadata=meta)


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
    dt: float = 0.02,
    ee_pos: np.ndarray | None = None,
    J_linear: np.ndarray | None = None,
    kinematics_resolver: KinematicsResolver | None = None,
    dynamics: Any | None = None,
) -> CallbackResult:
    """CBF constraint that keeps the EE inside ``bounds``.

    Linearises the workspace box into ``A @ u ≤ b`` constraints and attaches
    them as a :class:`QPTerm`.  The QP aggregator fuses this with other L1
    constraints to find the least-perturbing safe action.

    ``dt`` is the control period (seconds), auto-injected by MotionGuard.
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
        return _halt_clamp(
            bname,
            q,
            action,
            reason=f"EE {ee_pos.tolist()} outside workspace (no Jacobian); halting",
            metadata={
                "workspace_bounds": b.tolist(),
                "ee_pos": ee_pos.tolist(),
                "_units": {"workspace_bounds": "m", "ee_pos": "m"},
            },
        )

    cbf_A, cbf_b = _get_qp_solver().workspace_cbf_constraints(
        q=q,
        ee_pos=ee_pos,
        J_linear=J_linear,
        bounds=b,
        cbf_alpha=cbf_alpha,
        dt=dt,
    )
    margin, satisfied = _cbf_margin(cbf_A, cbf_b, target, J_linear.shape[1])

    if inside and satisfied:
        return CallbackResult.ok(
            bname,
            metadata={
                "bounds": b.tolist(),
                "ee_pos": ee_pos.tolist(),
                "cbf_margin_min": float(np.min(margin)),
            },
        )

    if inside:
        reason = (
            f"EE action would leave workspace; CBF active (margin_min={float(np.min(margin)):.4f})"
        )
    else:
        reason = f"EE {ee_pos.tolist()} outside workspace {b.tolist()}; CBF pushing back"
    return _cbf_clamp(
        bname,
        action,
        target,
        cbf_A,
        cbf_b,
        slack_weight,
        reason,
        extra_metadata={
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
    dt: float = 0.02,
    ee_pos: np.ndarray | None = None,
    J_linear: np.ndarray | None = None,
    kinematics_resolver: KinematicsResolver | None = None,
    dynamics: Any | None = None,
) -> CallbackResult:
    """CBF constraint that keeps the EE outside spherical keep-out zones.

    Each sphere ``[cx, cy, cz, radius]`` produces one linear inequality
    via CBF linearization, attached as a :class:`QPTerm`.

    ``dt`` is the control period (seconds), auto-injected by MotionGuard.
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
        return _halt_clamp(
            bname,
            q,
            action,
            reason=f"EE inside keep-out sphere {violated_sphere} (no Jacobian); halting",
            metadata={"ee_pos": ee_pos.tolist()},
        )

    cbf_A, cbf_b = _get_qp_solver().sphere_keepout_constraints(
        q=q,
        ee_pos=ee_pos,
        J_linear=J_linear,
        spheres=spheres,
        cbf_alpha=cbf_alpha,
        dt=dt,
    )
    margin, satisfied = _cbf_margin(cbf_A, cbf_b, target, J_linear.shape[1])

    if violated_sphere is None and satisfied:
        return CallbackResult.ok(
            bname,
            metadata={
                "ee_pos": ee_pos.tolist(),
                "n_spheres": len(spheres),
                "cbf_margin_min": float(np.min(margin)),
            },
        )

    if violated_sphere is not None:
        reason = f"EE inside keep-out sphere {violated_sphere}; CBF pushing out"
    else:
        reason = f"EE action would enter keep-out zone; CBF active (margin_min={float(np.min(margin)):.4f})"
    return _cbf_clamp(
        bname,
        action,
        target,
        cbf_A,
        cbf_b,
        slack_weight,
        reason,
        extra_metadata={
            "ee_pos": ee_pos.tolist(),
            "n_spheres": len(spheres),
            "cbf_margin_min": float(np.min(margin)),
            "_units": {"ee_pos": "m"},
        },
    )


@boundary_callback(
    name="orientation_limit",
    layer="L1",
    category="kinematics",
    description="Limits end-effector tilt from a reference axis via CBF constraint in the QP solver.",
    params={
        "max_tilt_deg": "Maximum allowed tool tilt from the reference axis, in degrees. Default 30.",
        "reference_axis": "World-frame axis to align with. Defaults to [0,0,1] (up).",
        "tool_axis": "Tool-frame axis that should stay aligned. Defaults to [0,0,1].",
        "cbf_alpha": "CBF decay rate (0,∞). Higher → correct later, lower → correct earlier. Default 1.0.",
        "slack_weight": "QP soft-constraint penalty.",
    },
    internal_params=("slack_weight",),
)
def orientation_limit(
    *,
    obs: Observation,
    action: ActionProposal,
    max_tilt_deg: float = 30.0,
    reference_axis: list[float] | None = None,
    tool_axis: list[float] | None = None,
    cbf_alpha: float = 1.0,
    slack_weight: float = 1e6,
    dt: float = 0.02,
    ee_rot: np.ndarray | None = None,
    J_angular: np.ndarray | None = None,
    kinematics_resolver: KinematicsResolver | None = None,
    dynamics: Any | None = None,
) -> CallbackResult:
    """CBF constraint that limits the tool axis tilt from a reference direction.

    Useful for keeping carried payloads upright (open containers, trays).
    Linearises the tilt constraint via the angular Jacobian and contributes
    a :class:`QPTerm`.  ``dt`` is the control period (seconds), auto-injected
    by MotionGuard.  Falls back to halt when angular Jacobian is unavailable.
    """
    bname = "orientation_limit"
    ref = np.asarray(
        reference_axis if reference_axis is not None else [0.0, 0.0, 1.0],
        dtype=np.float64,
    )
    tool = np.asarray(
        tool_axis if tool_axis is not None else [0.0, 0.0, 1.0],
        dtype=np.float64,
    )
    ref_norm = float(np.linalg.norm(ref))
    tool_norm = float(np.linalg.norm(tool))
    if ref_norm < 1e-12 or tool_norm < 1e-12:
        return CallbackResult.ok(bname, "zero-length axis; skip")
    ref = ref / ref_norm
    tool = tool / tool_norm

    if ee_rot is None:
        ee_rot = _resolve_ee_rotation(
            obs, kinematics_resolver=kinematics_resolver, dynamics=dynamics
        )
    if ee_rot is None or obs.joint_positions is None:
        return CallbackResult.ok(bname, "EE orientation unavailable; skip")

    R = np.asarray(ee_rot, dtype=np.float64)
    tool_world = R @ tool
    cos_tilt = float(np.clip(np.dot(tool_world, ref), -1.0, 1.0))
    tilt_deg = float(np.degrees(np.arccos(cos_tilt)))

    q = np.asarray(obs.joint_positions, dtype=np.float64)
    target = np.asarray(action.target_joint_positions, dtype=np.float64)
    violated = tilt_deg > max_tilt_deg

    if J_angular is None:
        if not violated:
            return CallbackResult.ok(
                bname,
                metadata={"tilt_deg": tilt_deg, "max_tilt_deg": max_tilt_deg},
            )
        return _halt_clamp(
            bname,
            q,
            action,
            reason=f"EE tilt {tilt_deg:.1f}° > {max_tilt_deg}° (no angular Jacobian); halting",
            metadata={"tilt_deg": tilt_deg, "max_tilt_deg": max_tilt_deg},
        )

    cbf_A, cbf_b = _get_qp_solver().orientation_tilt_constraint(
        q=q,
        ee_rot=R,
        J_angular=J_angular,
        max_tilt_deg=max_tilt_deg,
        reference_axis=ref,
        tool_axis=tool,
        cbf_alpha=cbf_alpha,
        dt=dt,
    )
    margin, satisfied = _cbf_margin(cbf_A, cbf_b, target, J_angular.shape[1])

    if not violated and satisfied:
        return CallbackResult.ok(
            bname,
            metadata={
                "tilt_deg": tilt_deg,
                "max_tilt_deg": max_tilt_deg,
                "cbf_margin_min": float(np.min(margin)),
            },
        )

    if violated:
        reason = f"EE tilt {tilt_deg:.1f}° > {max_tilt_deg}°; CBF correcting"
    else:
        reason = f"EE action would exceed tilt limit; CBF active (margin_min={float(np.min(margin)):.4f})"
    return _cbf_clamp(
        bname,
        action,
        target,
        cbf_A,
        cbf_b,
        slack_weight,
        reason,
        extra_metadata={
            "tilt_deg": tilt_deg,
            "max_tilt_deg": max_tilt_deg,
            "cbf_margin_min": float(np.min(margin)),
            "_units": {"tilt_deg": "deg", "max_tilt_deg": "deg"},
        },
    )
