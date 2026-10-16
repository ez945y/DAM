"""ProxSuite helpers for motion constraints.

``solve_hard_constraints`` is the enforced MotionGuard fusion path. It projects
onto box bounds and linear inequalities without slack; callers independently
verify every constraint before returning an executable candidate.

``solve_box_with_slack`` is retained as a standalone experimental helper. Its
soft limits can conflict and still produce a compromise, so it must not be
used to establish enforced feasibility. CBF builders provide local linear
models, not proofs about the exact nonlinear robot motion.
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)

try:
    import proxsuite  # type: ignore[import-untyped]

    _PROXSUITE_AVAILABLE = True
except ImportError:
    _PROXSUITE_AVAILABLE = False


def _pad_to(v: np.ndarray, n: int) -> np.ndarray:
    """Zero-pad or truncate a 1-D vector to exactly ``n`` elements."""
    if v.shape[0] >= n:
        return v[:n]
    return np.pad(v, (0, n - v.shape[0]))


def workspace_cbf_constraints(
    *,
    q: np.ndarray,
    ee_pos: np.ndarray,
    J_linear: np.ndarray,
    bounds: np.ndarray,
    cbf_alpha: float = 1.0,
    dt: float = 0.02,
) -> tuple[np.ndarray, np.ndarray]:
    """Build linear CBF constraints for an axis-aligned workspace box.

    Discrete-time CBF: enforce ``h(x_{k+1}) ≥ (1 − α·dt) h(x_k)`` for each
    bound, where ``h_up_i = bounds[i,1] − ee_pos[i]`` (upper) and
    ``h_lo_i = ee_pos[i] − bounds[i,0]`` (lower).

    Linearising ``ee_pos_{k+1} ≈ ee_pos + J·(u − q)`` (joint-position
    control, J is the linear part of the EE Jacobian) yields constraints
    of the form ``A·u ≤ b`` that the QP solver consumes directly.

    Parameters
    ----------
    q
        Current joint configuration, shape (n,).
    ee_pos
        Current EE position [x, y, z].
    J_linear
        Linear part of the EE Jacobian, shape (3, n).
    bounds
        Axis-aligned workspace box, shape (3, 2) — columns ``[min, max]``.
    cbf_alpha
        Discrete CBF decay rate.  ``γ = α·dt`` controls how aggressively
        the constraint must improve; γ = 1 reduces to a one-step hard
        bound, γ → 0 enforces large safety margins by braking early.
    dt
        Control period (seconds).
    """
    gamma = float(cbf_alpha) * float(dt)
    bounds_arr = np.asarray(bounds, dtype=np.float64)
    if bounds_arr.shape != (3, 2):
        raise ValueError(f"bounds must be shape (3,2), got {bounds_arr.shape}")
    b_min = bounds_arr[:, 0]
    b_max = bounds_arr[:, 1]
    n_dof = J_linear.shape[1]
    q_arr = _pad_to(np.asarray(q, dtype=np.float64), n_dof)
    Jq = J_linear @ q_arr
    ee = np.asarray(ee_pos, dtype=np.float64)

    # Upper:  J_i·u ≤ γ·(b_max_i − ee_i) + J_i·q
    A_up = J_linear
    b_up = gamma * (b_max - ee) + Jq
    # Lower (flip sign to ≤):  −J_i·u ≤ γ·(ee_i − b_min_i) − J_i·q
    A_lo = -J_linear
    b_lo = gamma * (ee - b_min) - Jq

    return np.vstack([A_up, A_lo]), np.concatenate([b_up, b_lo])


def sphere_keepout_constraints(
    *,
    q: np.ndarray,
    ee_pos: np.ndarray,
    J_linear: np.ndarray,
    spheres: list[list[float]],
    cbf_alpha: float = 1.0,
    dt: float = 0.02,
) -> tuple[np.ndarray, np.ndarray]:
    """Build linear CBF constraints for spherical keep-out zones.

    Each sphere ``[cx, cy, cz, radius]`` generates one inequality.
    CBF: ``h(q) = ||ee_pos − center|| − radius ≥ 0``, linearised to
    ``A @ u ≤ b`` (one row per sphere).
    """
    gamma = float(cbf_alpha) * float(dt)
    ee = np.asarray(ee_pos, dtype=np.float64)
    n_dof = J_linear.shape[1]
    q_arr = _pad_to(np.asarray(q, dtype=np.float64), n_dof)
    A_rows: list[np.ndarray] = []
    b_vals: list[float] = []

    for sphere in spheres:
        s = np.asarray(sphere, dtype=np.float64)
        center, radius = s[:3], float(s[3])
        diff = ee - center
        dist = float(np.linalg.norm(diff))
        if dist < 1e-12:
            # EE is at/near sphere center — pick an arbitrary escape direction
            # so the CBF constraint has a non-degenerate gradient.
            n = np.array([1.0, 0.0, 0.0])
            dist = 1e-12
        else:
            n = diff / dist
        h = dist - radius
        # -nᵀ J u ≤ -nᵀ J q + γ h
        nJ = n @ J_linear
        A_rows.append(-nJ)
        b_vals.append(-float(nJ @ q_arr) + gamma * h)

    return np.array(A_rows), np.array(b_vals)


def orientation_tilt_constraint(
    *,
    q: np.ndarray,
    ee_rot: np.ndarray,
    J_angular: np.ndarray,
    max_tilt_deg: float,
    reference_axis: np.ndarray,
    tool_axis: np.ndarray,
    cbf_alpha: float = 1.0,
    dt: float = 0.02,
) -> tuple[np.ndarray, np.ndarray]:
    """Build a linear CBF constraint for EE orientation tilt limit.

    CBF: ``h(q) = cos(tilt) - cos(max_tilt) ≥ 0``, where tilt is the angle
    between the tool axis (rotated to world frame) and the reference axis.
    Linearised via the angular Jacobian to ``A @ u ≤ b``.
    """
    gamma = float(cbf_alpha) * float(dt)
    R = np.asarray(ee_rot, dtype=np.float64)
    tool_local = np.asarray(tool_axis, dtype=np.float64)
    tool_local = tool_local / (np.linalg.norm(tool_local) + 1e-12)
    ref = np.asarray(reference_axis, dtype=np.float64)
    ref = ref / (np.linalg.norm(ref) + 1e-12)

    tool_world = R @ tool_local
    cos_tilt = float(np.clip(np.dot(tool_world, ref), -1.0, 1.0))
    cos_max = float(np.cos(np.radians(max_tilt_deg)))
    h = cos_tilt - cos_max

    # ∂h/∂q = (tool_world × ref)ᵀ · J_angular
    cross = np.cross(tool_world, ref)
    dh_dq = cross @ np.asarray(J_angular, dtype=np.float64)
    n_dof = J_angular.shape[1]
    q_arr = _pad_to(np.asarray(q, dtype=np.float64), n_dof)

    # -dh_dq @ u ≤ -dh_dq @ q + γ h
    A = -dh_dq.reshape(1, -1)
    b_val = -float(dh_dq @ q_arr) + gamma * h
    return A, np.array([b_val])


def available() -> bool:
    """True when ``proxsuite`` is importable.  Lets the caller decide whether
    to dispatch to the QP path or fall back to box-clamp."""
    return _PROXSUITE_AVAILABLE


def solve_hard_constraints(
    u_nom: np.ndarray,
    *,
    lower: np.ndarray,
    upper: np.ndarray,
    extra_A: np.ndarray | None = None,
    extra_ub: np.ndarray | None = None,
) -> np.ndarray | None:
    """Project onto hard constraints. Infeasibility never becomes soft slack.

    Callers validate shapes and independently verify the returned residuals.
    The older soft solver remains available for standalone experiments.
    """
    if not _PROXSUITE_AVAILABLE:
        return None
    n = len(u_nom)
    if n == 0:
        return u_nom.copy()
    if np.any(lower > upper):
        return None
    if extra_A is None:
        return np.clip(u_nom, lower, upper)
    C = np.vstack([np.eye(n), extra_A])
    lo = np.concatenate([lower, np.full(len(extra_A), -np.inf)])
    hi = np.concatenate([upper, extra_ub])
    # Normalize rows so numerical tolerance has a stable joint-space scale.
    scales = np.max(np.abs(C), axis=1, initial=0.0)
    scales = np.where(scales > 0, scales, 1.0)
    C = C / scales[:, None]
    lo, hi = lo / scales, hi / scales
    try:
        qp = proxsuite.proxqp.dense.QP(n, 0, len(C))
        qp.settings.eps_abs = 1e-9
        qp.settings.eps_rel = 0.0
        qp.init(H=np.eye(n), g=-u_nom, A=None, b=None, C=C, l=lo, u=hi)
        qp.solve()
        if qp.results.info.status != proxsuite.proxqp.QPSolverOutput.PROXQP_SOLVED:
            return None
        return np.asarray(qp.results.x, dtype=np.float64).copy()
    except Exception:
        logger.exception("Hard-constraint QP solve failed")
        return None


def solve_box_with_slack(
    u_nom: np.ndarray,
    *,
    upper: np.ndarray | None = None,
    lower: np.ndarray | None = None,
    vel_upper: np.ndarray | None = None,
    vel_lower: np.ndarray | None = None,
    slack_weight: float = 1e6,
    extra_A: np.ndarray | None = None,
    extra_ub: np.ndarray | None = None,
) -> np.ndarray | None:
    """Least-perturbation clamp with position + velocity bounds + workspace CBF.

    All constraints are soft (via independent slack variables) so the QP
    stays feasible even when constraints conflict.  Returns the clamped
    vector (same length as *u_nom*), or ``None`` on solver failure.

    Parameters
    ----------
    u_nom
        Nominal control (the policy's proposed action), shape (n,).
    upper, lower
        Per-element joint position bounds, shape (n,).
    vel_upper, vel_lower
        Per-element velocity-derived position bounds, shape (n,).
        Typically ``q_current ± max_velocity * dt``.  Each gets its own
        slack so position and velocity violations are penalised independently.
    slack_weight
        Penalty on each slack variable.
    extra_A, extra_ub
        Optional linear constraints ``extra_A @ u ≤ extra_ub``
        (e.g. workspace CBF).  Same ``slack_weight`` per row.
    """
    if not _PROXSUITE_AVAILABLE:
        return None
    n = int(u_nom.shape[0])
    if n == 0:
        return u_nom.copy()

    upper_arr = np.asarray(upper, dtype=np.float64) if upper is not None else None
    lower_arr = np.asarray(lower, dtype=np.float64) if lower is not None else None
    vu_arr = np.asarray(vel_upper, dtype=np.float64) if vel_upper is not None else None
    vl_arr = np.asarray(vel_lower, dtype=np.float64) if vel_lower is not None else None
    A_extra = np.asarray(extra_A, dtype=np.float64) if extra_A is not None else None
    b_extra = np.asarray(extra_ub, dtype=np.float64) if extra_ub is not None else None
    n_extra = A_extra.shape[0] if A_extra is not None else 0

    # Count constraint groups (each group gets n slack variables, except extra)
    n_pos_up = n if upper_arr is not None else 0
    n_pos_lo = n if lower_arr is not None else 0
    n_vel_up = n if vu_arr is not None else 0
    n_vel_lo = n if vl_arr is not None else 0
    n_slack = n_pos_up + n_pos_lo + n_vel_up + n_vel_lo + n_extra

    # Decision vector: x = [u (n), δ_pos_up, δ_pos_lo, δ_vel_up, δ_vel_lo, δ_extra]
    dim = n + n_slack

    # Hessian:  diag(I_n, λI_n_slack)
    H = np.zeros((dim, dim), dtype=np.float64)
    H[:n, :n] = np.eye(n)
    if n_slack > 0:
        H[n:, n:] = float(slack_weight) * np.eye(n_slack)
    # Gradient: g = [-u_nom, 0, …]
    g = np.zeros(dim, dtype=np.float64)
    g[:n] = -np.asarray(u_nom, dtype=np.float64)

    # Inequality constraints  lb ≤ C·x ≤ ub
    constraint_rows: list[np.ndarray] = []
    lb_parts: list[np.ndarray] = []
    ub_parts: list[np.ndarray] = []
    slack_offset = n  # tracks where the next slack block starts

    if upper_arr is not None:
        # u − δ_pos_up ≤ upper
        row = np.zeros((n, dim))
        row[:, :n] = np.eye(n)
        row[:, slack_offset : slack_offset + n] = -np.eye(n)
        constraint_rows.append(row)
        lb_parts.append(np.full(n, -np.inf))
        ub_parts.append(upper_arr)
        slack_offset += n

    if lower_arr is not None:
        # u + δ_pos_lo ≥ lower  →  lower ≤ u + δ_pos_lo
        row = np.zeros((n, dim))
        row[:, :n] = np.eye(n)
        row[:, slack_offset : slack_offset + n] = np.eye(n)
        constraint_rows.append(row)
        lb_parts.append(lower_arr)
        ub_parts.append(np.full(n, np.inf))
        slack_offset += n

    if vu_arr is not None:
        # u − δ_vel_up ≤ vel_upper  (= q + max_vel * dt)
        row = np.zeros((n, dim))
        row[:, :n] = np.eye(n)
        row[:, slack_offset : slack_offset + n] = -np.eye(n)
        constraint_rows.append(row)
        lb_parts.append(np.full(n, -np.inf))
        ub_parts.append(vu_arr)
        slack_offset += n

    if vl_arr is not None:
        # u + δ_vel_lo ≥ vel_lower  (= q - max_vel * dt)
        row = np.zeros((n, dim))
        row[:, :n] = np.eye(n)
        row[:, slack_offset : slack_offset + n] = np.eye(n)
        constraint_rows.append(row)
        lb_parts.append(vl_arr)
        ub_parts.append(np.full(n, np.inf))
        slack_offset += n

    if A_extra is not None and b_extra is not None and n_extra > 0:
        # A_extra·u − δ_extra ≤ b_extra
        row = np.zeros((n_extra, dim))
        row[:, :n] = A_extra
        row[:, slack_offset : slack_offset + n_extra] = -np.eye(n_extra)
        constraint_rows.append(row)
        lb_parts.append(np.full(n_extra, -np.inf))
        ub_parts.append(b_extra)
        slack_offset += n_extra

    # All slacks ≥ 0
    if n_slack > 0:
        slack_row = np.zeros((n_slack, dim))
        slack_row[:, n:] = np.eye(n_slack)
        constraint_rows.append(slack_row)
        lb_parts.append(np.zeros(n_slack))
        ub_parts.append(np.full(n_slack, np.inf))

    if not constraint_rows:
        return u_nom.copy()

    C = np.vstack(constraint_rows)
    lb = np.concatenate(lb_parts)
    ub = np.concatenate(ub_parts)

    try:
        qp = proxsuite.proxqp.dense.QP(dim, 0, C.shape[0])
        qp.init(H=H, g=g, A=None, b=None, C=C, l=lb, u=ub)
        qp.solve()
        status = qp.results.info.status
        # proxsuite enum names vary slightly across versions; accept anything
        # whose string repr contains "SOLVED".
        if "SOLVED" not in str(status):
            logger.warning("ProxSuite QP did not converge: %s", status)
            return None
        result: np.ndarray = np.asarray(qp.results.x[:n], dtype=np.float64).copy()
        return result
    except Exception:
        logger.error("ProxSuite QP solve failed", exc_info=True)
        return None
