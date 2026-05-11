"""Optional ProxSuite-based QP safety filter shared by L1 callbacks.

Why this lives at runtime level (not as a Guard class)
------------------------------------------------------
Every L1 boundary already contributes constraints (``upper``, ``lower``,
``max_velocity``) via boundary node params — the merge_policy registry
fuses them into one canonical pool.  Treating each constraint as a CBF
candidate just means *swapping the L1 solver*: instead of independent
box-clamps for each constraint, all of them feed into a single QP that
finds the least-perturbing safe action.

So no new ``QPSafetyGuard`` class.  L1's existing ``MotionGuard`` simply
asks this module whether QP is available and dispatches when a boundary
opts in via the ``qp_solver`` param.

The QP
------
::

    min  ½ ‖u − u_nom‖²  +  ½ Σ_i  λ_i · δ_i²
    s.t. lower_i  −  δ_lo,i  ≤  u_i   (joint position lower bound, soft)
         u_i  −  δ_up,i      ≤  upper_i      (joint position upper bound, soft)
         δ_*  ≥  0

Slack permits otherwise-infeasible combinations of constraints to still
yield a clamped action; ``slack_weight`` (merge: ``take_max`` — stricter
boundary wins) controls how aggressively each slack is penalised.
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)

try:
    import proxsuite  # type: ignore[import-not-found]

    _PROXSUITE_AVAILABLE = True
except ImportError:
    _PROXSUITE_AVAILABLE = False


def available() -> bool:
    """True when ``proxsuite`` is importable.  Lets the caller decide whether
    to dispatch to the QP path or fall back to box-clamp."""
    return _PROXSUITE_AVAILABLE


def solve_box_with_slack(
    u_nom: np.ndarray,
    *,
    upper: np.ndarray | None = None,
    lower: np.ndarray | None = None,
    slack_weight: float = 1e6,
) -> np.ndarray | None:
    """Least-perturbation clamp to a box [lower, upper] with slack variables.

    Returns the QP-clamped vector (same length as *u_nom*), or ``None`` when
    proxsuite is unavailable, the problem is infeasible, or the solver
    fails — the caller should fall back to its naive clamp logic.

    Parameters
    ----------
    u_nom
        Nominal control (the policy's proposed action), shape (n,).
    upper, lower
        Per-element bounds, shape (n,).  Either or both may be ``None`` to
        omit that side.
    slack_weight
        Penalty on each slack variable.  Larger → constraint enforced more
        strictly.  ``1e6`` is a reasonable default for ``rad``-scale joint
        limits; safety-critical bounds may want ``1e8``+.
    """
    if not _PROXSUITE_AVAILABLE:
        return None
    n = int(u_nom.shape[0])
    if n == 0:
        return u_nom.copy()

    upper_arr = np.asarray(upper, dtype=np.float64) if upper is not None else None
    lower_arr = np.asarray(lower, dtype=np.float64) if lower is not None else None

    # Decision vector x = [u (n), δ_up (n), δ_lo (n)]; total dim 3n
    dim = 3 * n

    # Hessian:  diag(I_n, λI_2n)
    H = np.zeros((dim, dim), dtype=np.float64)
    H[:n, :n] = np.eye(n)
    H[n:, n:] = float(slack_weight) * np.eye(2 * n)
    # Gradient: g = [-u_nom, 0, 0]
    g = np.zeros(dim, dtype=np.float64)
    g[:n] = -np.asarray(u_nom, dtype=np.float64)

    # Inequality constraints  lb ≤ C·x ≤ ub
    constraint_rows: list[np.ndarray] = []
    lb_parts: list[np.ndarray] = []
    ub_parts: list[np.ndarray] = []
    if upper_arr is not None:
        # u − δ_up ≤ upper
        row = np.zeros((n, dim))
        row[:, :n] = np.eye(n)
        row[:, n : 2 * n] = -np.eye(n)
        constraint_rows.append(row)
        lb_parts.append(np.full(n, -np.inf))
        ub_parts.append(upper_arr)
    if lower_arr is not None:
        # u + δ_lo ≥ lower
        row = np.zeros((n, dim))
        row[:, :n] = np.eye(n)
        row[:, 2 * n :] = np.eye(n)
        constraint_rows.append(row)
        lb_parts.append(lower_arr)
        ub_parts.append(np.full(n, np.inf))
    # δ ≥ 0 (both up and lo slacks)
    slack_row = np.zeros((2 * n, dim))
    slack_row[:, n:] = np.eye(2 * n)
    constraint_rows.append(slack_row)
    lb_parts.append(np.zeros(2 * n))
    ub_parts.append(np.full(2 * n, np.inf))

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
