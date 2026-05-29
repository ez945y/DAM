"""QP-based clamp fusion for L1 motion constraints.

The mandatory clamp aggregator for MotionGuard.  Each L1 callback attaches
a :class:`QPTerm` under ``metadata["motion_qp"]``; the aggregator collects
them all and solves a single QP that finds the least-perturbing safe action
subject to all constraints jointly.  ``proxsuite`` is required.

``QPTerm`` is deliberately generic: just box bounds (upper/lower) and/or
linear inequalities (A @ u ≤ b).  Each callback is responsible for
linearizing its own constraint into this form — the aggregator never needs
to know what *type* of constraint it is.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np

from dam.guard.pipeline import CallbackResult
from dam.types.action import ActionProposal, ValidatedAction

logger = logging.getLogger(__name__)

#: Metadata key under which an L1 callback advertises its QP constraint.
METADATA_KEY = "motion_qp"


@dataclass(frozen=True)
class QPTerm:
    """One callback's contribution to the joint-space QP.

    A callback fills whichever fields are relevant:

    - **Box bounds** (``upper`` / ``lower``): direct joint-position limits.
    - **Linear inequalities** (``A`` / ``b``): ``A @ u ≤ b`` — used for
      CBF constraints, EE velocity limits, or any linearized constraint.

    The aggregator merges box bounds (element-wise min/max) and stacks
    linear inequalities.  It never needs to know what the constraint means.
    """

    # Joint-position box bounds.
    upper: np.ndarray | None = None
    lower: np.ndarray | None = None
    # Linear inequality: A @ u ≤ b  (m rows, n cols matching joint count).
    A: np.ndarray | None = None
    b: np.ndarray | None = None
    # Slack penalty — stricter boundary should set a higher weight.
    slack_weight: float = 1e6


# Backward compat alias — existing code importing MotionQPConstraint still works.
MotionQPConstraint = QPTerm


def _coerce(value: object) -> QPTerm:
    """Accept a ``QPTerm``, ``MotionQPConstraint``, or an equivalent dict."""
    if isinstance(value, QPTerm):
        return value
    if isinstance(value, dict):
        # Support legacy MotionQPConstraint dict format: convert velocity
        # fields into box bounds, workspace fields into A/b.
        return _coerce_legacy_dict(value)
    raise TypeError(f"metadata['{METADATA_KEY}'] must be a QPTerm or dict, got {type(value)!r}")


def _coerce_legacy_dict(d: dict[str, Any]) -> QPTerm:
    """Convert a dict with legacy MotionQPConstraint fields into QPTerm."""
    upper = _opt_arr(d.get("upper"))
    lower = _opt_arr(d.get("lower"))
    A = _opt_arr(d.get("A"))
    b_vec = _opt_arr(d.get("b"))
    slack_weight = float(d.get("slack_weight", 1e6))

    # Legacy: max_velocity + q + dt → box bounds
    if "max_velocity" in d and "q" in d and "dt" in d:
        vmax = _arr(d["max_velocity"])
        q = _arr(d["q"])
        dt = float(d["dt"])
        m = min(vmax.shape[0], q.shape[0])
        span = vmax[:m] * dt
        vel_upper = q[:m] + span
        vel_lower = q[:m] - span
        if upper is not None:
            upper[:m] = np.minimum(upper[:m], vel_upper)
        else:
            upper = vel_upper
        if lower is not None:
            lower[:m] = np.maximum(lower[:m], vel_lower)
        else:
            lower = vel_lower

    # Legacy: workspace_bounds + ee_pos + J_linear → A/b via CBF
    if "workspace_bounds" in d and "ee_pos" in d and "J_linear" in d:
        from dam.runtime import qp_solver

        gamma = float(d.get("cbf_gamma", 1.0))
        J = np.asarray(d["J_linear"], dtype=np.float64)
        q_cbf = _arr(d["q"]) if "q" in d else np.zeros(J.shape[1])
        ws_A, ws_b = qp_solver.workspace_cbf_constraints(
            q=q_cbf,
            ee_pos=_arr(d["ee_pos"]),
            J_linear=J,
            bounds=np.asarray(d["workspace_bounds"], dtype=np.float64),
            cbf_alpha=gamma,
            dt=1.0,
        )
        if A is not None:
            A = np.vstack([A, ws_A])
            b_vec = np.concatenate([b_vec, _arr(ws_b)])
        else:
            A = ws_A
            b_vec = _arr(ws_b)

    return QPTerm(upper=upper, lower=lower, A=A, b=b_vec, slack_weight=slack_weight)


def _opt_arr(x: object) -> np.ndarray | None:
    if x is None:
        return None
    return np.asarray(x, dtype=np.float64).ravel()


def _arr(x: object) -> np.ndarray:
    return np.asarray(x, dtype=np.float64).ravel()


def _fit_cols(A: np.ndarray, n: int) -> np.ndarray:
    """Pad/truncate a (rows, cols) matrix to ``n`` columns."""
    rows = A.shape[0]
    out = np.zeros((rows, n), dtype=np.float64)
    m = min(n, A.shape[1])
    out[:, :m] = A[:, :m]
    return out


def _resolve_u_nom(
    clamps: list[CallbackResult], action_in: ValidatedAction | None
) -> tuple[np.ndarray | None, ActionProposal | None]:
    """Nominal action the QP minimises distance to, plus its original proposal."""
    for c in clamps:
        ca = c.clamped_action
        if ca is not None and ca.original_proposal is not None:
            return _arr(ca.original_proposal.target_joint_positions), ca.original_proposal
    if action_in is not None:
        return _arr(action_in.target_joint_positions), action_in.original_proposal
    return None, None


def motion_qp_aggregator(
    clamps: list[CallbackResult],
    action_in: ValidatedAction | None,
) -> ValidatedAction | None:
    """Fuse L1 CLAMP results into one least-perturbing action via a QP.

    Each CLAMP's ``metadata['motion_qp']`` is a :class:`QPTerm` with box
    bounds and/or linear inequalities.  The aggregator merges all terms and
    solves a single QP.
    """
    terms = [
        _coerce(c.metadata[METADATA_KEY])
        for c in clamps
        if c.metadata and METADATA_KEY in c.metadata
    ]
    if not terms:
        # CLAMPs without QP metadata — use the first clamped action directly.
        for c in clamps:
            if c.clamped_action is not None:
                return c.clamped_action
        return None

    from dam.runtime import qp_solver

    if not qp_solver.available():
        raise RuntimeError(
            "motion_qp_aggregator requires the 'proxsuite' backend. "
            "Install proxsuite (pip install proxsuite)."
        )

    u_nom, orig = _resolve_u_nom(clamps, action_in)
    if u_nom is None:
        return None
    n = u_nom.shape[0]

    # Merge all terms: box bounds → element-wise, A/b → vstack.
    upper = np.full(n, np.inf)
    lower = np.full(n, -np.inf)
    has_box = False
    ineq_A: list[np.ndarray] = []
    ineq_b: list[np.ndarray] = []
    slack_weight = 0.0

    for term in terms:
        slack_weight = max(slack_weight, float(term.slack_weight))

        if term.upper is not None:
            up = _arr(term.upper)
            m = min(n, up.shape[0])
            upper[:m] = np.minimum(upper[:m], up[:m])
            has_box = True
        if term.lower is not None:
            lo = _arr(term.lower)
            m = min(n, lo.shape[0])
            lower[:m] = np.maximum(lower[:m], lo[:m])
            has_box = True

        if term.A is not None and term.b is not None:
            ineq_A.append(_fit_cols(np.atleast_2d(term.A), n))
            ineq_b.append(_arr(term.b))

    extra_A = np.vstack(ineq_A) if ineq_A else None
    extra_ub = np.concatenate(ineq_b) if ineq_b else None
    if slack_weight <= 0.0:
        slack_weight = 1e6

    u = qp_solver.solve_box_with_slack(
        u_nom,
        upper=upper if has_box else None,
        lower=lower if has_box else None,
        slack_weight=slack_weight,
        extra_A=extra_A,
        extra_ub=extra_ub,
    )
    if u is None:
        logger.error("motion_qp_aggregator: QP solve failed — no safe action found")
        return None

    return ValidatedAction(
        target_joint_positions=u,
        was_clamped=True,
        original_proposal=orig,
        timestamp=max(
            (c.clamped_action.timestamp for c in clamps if c.clamped_action), default=0.0
        ),
    )


# Backward compat for motion_qp_units (no longer needed with QPTerm,
# but existing code may call it).
def motion_qp_units(constraint: QPTerm) -> dict[str, str]:
    """Return unit annotations for populated QPTerm fields."""
    units: dict[str, str] = {}
    if constraint.upper is not None:
        units["motion_qp.upper"] = "rad"
    if constraint.lower is not None:
        units["motion_qp.lower"] = "rad"
    return units


__all__ = ["METADATA_KEY", "MotionQPConstraint", "QPTerm", "motion_qp_aggregator"]
