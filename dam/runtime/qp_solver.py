"""Optional ProxSuite-based QP safety filter shared by L1 callbacks.

Why this lives at runtime level (not as a Guard class)
------------------------------------------------------
Every L1 boundary already contributes constraints (``upper``, ``lower``,
``max_velocity``, ``bounds``) via boundary node params — the merge_policy
registry fuses them into one canonical pool.  Treating each constraint as
a CBF candidate just means *swapping the L1 solver*: instead of independent
box-clamps for each constraint, all of them feed into a single QP that
finds the least-perturbing safe action.

So no new ``QPSafetyGuard`` class.  L1's existing ``MotionGuard`` simply
asks this module whether QP is available and dispatches when a boundary
opts in via the ``qp_solver`` param.

The QP
------
::

    min  ½ ‖u − u_nom‖²  +  ½ Σ_i  λ_i · δ_i²
    s.t. lower_i  −  δ_lo,i  ≤  u_i           (joint position lower, soft)
         u_i  −  δ_up,i      ≤  upper_i        (joint position upper, soft)
         q_i − v_max_i·dt − δ_vlo,i ≤ u_i      (velocity lower, soft)
         u_i − δ_vup,i ≤ q_i + v_max_i·dt      (velocity upper, soft)
         A_cbf · u − δ_cbf ≤ b_cbf              (workspace CBF, soft)
         δ_*  ≥  0

Position, velocity and workspace constraints each get independent slack
variables so the optimiser can violate the least-critical constraint
when the feasible region is empty.  ``slack_weight`` (merge: ``take_max``
— stricter boundary wins) controls how aggressively each slack is penalised.
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
    Jq = J_linear @ np.asarray(q, dtype=np.float64)
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
    q_arr = np.asarray(q, dtype=np.float64)
    A_rows: list[np.ndarray] = []
    b_vals: list[float] = []

    for sphere in spheres:
        s = np.asarray(sphere, dtype=np.float64)
        center, radius = s[:3], float(s[3])
        diff = ee - center
        dist = float(np.linalg.norm(diff))
        if dist < 1e-12:
            dist = 1e-12
        n = diff / dist
        h = dist - radius
        # -nᵀ J u ≤ -nᵀ J q + γ h
        nJ = n @ J_linear
        A_rows.append(-nJ)
        b_vals.append(-float(nJ @ q_arr) + gamma * h)

    return np.array(A_rows), np.array(b_vals)


def available() -> bool:
    """True when ``proxsuite`` is importable.  Lets the caller decide whether
    to dispatch to the QP path or fall back to box-clamp."""
    return _PROXSUITE_AVAILABLE


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
