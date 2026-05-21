"""QP-based clamp fusion for L1 motion constraints.

Phase 1 of the guard-pipeline refactor stripped the inline QP / CBF dispatch
out of ``MotionGuard``.  This module re-introduces it as an *opt-in* clamp
aggregator the user wires in explicitly::

    MotionGuard(clamp_aggregator=motion_qp_aggregator)

Where ``sequential_clamp_aggregator`` folds each CLAMP element-wise (taking the
most restrictive value per joint), ``motion_qp_aggregator`` collects the
constraints the L1 callbacks attach to their CLAMP results and solves a single
QP that finds the least-perturbing safe action subject to all of them jointly.

The contract between callback and aggregator is deliberately narrow: a callback
that wants QP fusion stashes a :class:`MotionQPConstraint` under the
``"motion_qp"`` key of its ``CallbackResult.metadata``.  The framework pipeline
never inspects this — ``MotionQPConstraint`` is solver-specific vocabulary that
belongs to *this* aggregator, not to the generic ``CallbackResult`` schema.

If no callback supplied QP metadata, the aggregator transparently falls back to
the sequential strategy.  If QP metadata *is* present but the ``proxsuite``
backend is unavailable, it raises rather than silently degrading — a guard that
was explicitly configured for QP should fail loudly when its solver is missing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from dam.guard.pipeline import CallbackResult, sequential_clamp_aggregator
from dam.types.action import ActionProposal, ValidatedAction

logger = logging.getLogger(__name__)

#: Metadata key under which an L1 callback advertises its QP constraint.
METADATA_KEY = "motion_qp"


@dataclass(frozen=True)
class MotionQPConstraint:
    """One boundary's contribution to the joint motion QP.

    A callback only fills the fields relevant to the limit it enforces; the
    aggregator merges contributions across boundaries (most-restrictive wins)
    before solving.  Besides the limits themselves the constraint carries the
    runtime state needed to express them in the QP's joint-position decision
    space (``q`` / ``dt`` for velocity, ``ee_pos`` / ``J_linear`` for the
    workspace CBF) because the aggregator only receives the CLAMP results, not
    the runtime pool.
    """

    # Joint-position box (absolute bounds).
    upper: np.ndarray | None = None
    lower: np.ndarray | None = None
    # Joint-velocity limit + the state to turn it into position bounds
    # (vel bound = q ± max_velocity · dt).
    max_velocity: np.ndarray | None = None
    q: np.ndarray | None = None
    dt: float | None = None
    # Workspace CBF: axis-aligned box + decay rate + the state to linearise it.
    workspace_bounds: np.ndarray | None = None
    cbf_gamma: float | None = None
    ee_pos: np.ndarray | None = None
    J_linear: np.ndarray | None = None
    # Slack penalty for this boundary's soft constraints (stricter boundary wins).
    slack_weight: float = 1e6


def _coerce(value: object) -> MotionQPConstraint:
    """Accept either a ``MotionQPConstraint`` or an equivalent dict."""
    if isinstance(value, MotionQPConstraint):
        return value
    if isinstance(value, dict):
        return MotionQPConstraint(**value)
    raise TypeError(
        f"metadata['{METADATA_KEY}'] must be a MotionQPConstraint or dict, got {type(value)!r}"
    )


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
    """Nominal action the QP minimises distance to, plus its original proposal.

    Prefer the unmodified policy proposal recorded on the first CLAMP (the QP
    should pull *that* back, not a value already perturbed by a sibling clamp).
    """
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

    Reads each CLAMP's ``metadata['motion_qp']`` (a :class:`MotionQPConstraint`
    or equivalent dict).  When none are present, defers to the sequential
    strategy.  When present but ``proxsuite`` is unavailable, raises.  On QP
    solver failure, falls back to sequential so the robot still gets a safe
    (if less optimal) clamp rather than no clamp at all.
    """
    constraints = [
        _coerce(c.metadata[METADATA_KEY])
        for c in clamps
        if c.metadata and METADATA_KEY in c.metadata
    ]
    if not constraints:
        return sequential_clamp_aggregator(clamps, action_in)

    from dam.runtime import qp_solver

    if not qp_solver.available():
        raise RuntimeError(
            "motion_qp_aggregator was given QP constraints but the 'proxsuite' "
            "backend is not importable. Install proxsuite, or use the default "
            "sequential_clamp_aggregator if QP fusion is not required."
        )

    u_nom, orig = _resolve_u_nom(clamps, action_in)
    if u_nom is None:
        # No proposal to optimise around; nothing the QP can do.
        return sequential_clamp_aggregator(clamps, action_in)
    n = u_nom.shape[0]

    upper = np.full(n, np.inf)
    lower = np.full(n, -np.inf)
    vel_upper = np.full(n, np.inf)
    vel_lower = np.full(n, -np.inf)
    has_box = has_vel = False
    cbf_A: list[np.ndarray] = []
    cbf_b: list[np.ndarray] = []
    slack_weight = 0.0

    for con in constraints:
        slack_weight = max(slack_weight, float(con.slack_weight))

        if con.upper is not None:
            up = _arr(con.upper)
            m = min(n, up.shape[0])
            upper[:m] = np.minimum(upper[:m], up[:m])
            has_box = True
        if con.lower is not None:
            lo = _arr(con.lower)
            m = min(n, lo.shape[0])
            lower[:m] = np.maximum(lower[:m], lo[:m])
            has_box = True

        if con.max_velocity is not None and con.q is not None and con.dt is not None:
            vmax = _arr(con.max_velocity)
            q = _arr(con.q)
            m = min(n, vmax.shape[0], q.shape[0])
            span = vmax[:m] * float(con.dt)
            vel_upper[:m] = np.minimum(vel_upper[:m], q[:m] + span)
            vel_lower[:m] = np.maximum(vel_lower[:m], q[:m] - span)
            has_vel = True

        if con.workspace_bounds is not None and con.ee_pos is not None and con.J_linear is not None:
            gamma = 1.0 if con.cbf_gamma is None else float(con.cbf_gamma)
            J = np.asarray(con.J_linear, dtype=np.float64)
            q_cbf = _arr(con.q) if con.q is not None else np.zeros(J.shape[1])
            # cbf_gamma is already the discrete decay γ ∈ [0,1]; pass dt=1 so the
            # solver helper uses it verbatim instead of re-multiplying by dt.
            A, b = qp_solver.workspace_cbf_constraints(
                q=q_cbf,
                ee_pos=_arr(con.ee_pos),
                J_linear=J,
                bounds=np.asarray(con.workspace_bounds, dtype=np.float64),
                cbf_alpha=gamma,
                dt=1.0,
            )
            cbf_A.append(_fit_cols(A, n))
            cbf_b.append(_arr(b))

    extra_A = np.vstack(cbf_A) if cbf_A else None
    extra_ub = np.concatenate(cbf_b) if cbf_b else None
    if slack_weight <= 0.0:
        slack_weight = 1e6

    u = qp_solver.solve_box_with_slack(
        u_nom,
        upper=upper if has_box else None,
        lower=lower if has_box else None,
        vel_upper=vel_upper if has_vel else None,
        vel_lower=vel_lower if has_vel else None,
        slack_weight=slack_weight,
        extra_A=extra_A,
        extra_ub=extra_ub,
    )
    if u is None:
        logger.warning("motion_qp_aggregator: QP solve failed; falling back to sequential clamp")
        return sequential_clamp_aggregator(clamps, action_in)

    return ValidatedAction(
        target_joint_positions=u,
        was_clamped=True,
        original_proposal=orig,
        timestamp=max(
            (c.clamped_action.timestamp for c in clamps if c.clamped_action), default=0.0
        ),
    )


__all__ = ["METADATA_KEY", "MotionQPConstraint", "motion_qp_aggregator"]
