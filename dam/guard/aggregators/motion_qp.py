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
from dataclasses import dataclass, replace
from typing import Any

import numpy as np

from dam.guard.pipeline import CallbackResult
from dam.types.action import ActionProposal, ValidatedAction
from dam.types.result import GuardDecision, GuardResult

logger = logging.getLogger(__name__)

#: Metadata key under which an L1 callback advertises its QP constraint.
METADATA_KEY = "motion_qp"
FEASIBILITY_TOLERANCE = 1e-7
_BASIS_KEY = "_motion_checked_command"


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
    # Compatibility field; the enforced fusion solver does not relax constraints.
    slack_weight: float = 1e6
    # Optional exact norm check for a candidate: ||norm_A @ u - norm_b|| <= norm_limit.
    # Linear terms guide the QP; this check rejects tangent-plane escape.
    norm_A: np.ndarray | None = None
    norm_b: np.ndarray | None = None
    norm_limit: float | None = None


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
    A = np.asarray(d["A"], dtype=np.float64) if d.get("A") is not None else None
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

    return QPTerm(
        upper=upper,
        lower=lower,
        A=A,
        b=b_vec,
        slack_weight=slack_weight,
        norm_A=d.get("norm_A"),
        norm_b=d.get("norm_b"),
        norm_limit=d.get("norm_limit"),
    )


def _opt_arr(x: object) -> np.ndarray | None:
    if x is None:
        return None
    return np.asarray(x, dtype=np.float64)


def _arr(x: object) -> np.ndarray:
    return np.asarray(x, dtype=np.float64).ravel()


def _fit_cols(A: np.ndarray, n: int) -> np.ndarray:
    """Pad a leading-joint constraint; never silently drop constrained joints."""
    if A.ndim != 2 or A.shape[1] > n or not np.all(np.isfinite(A)):
        raise ValueError("QP matrix must be finite, two-dimensional, and fit the action")
    rows = A.shape[0]
    out = np.zeros((rows, n), dtype=np.float64)
    m = min(n, A.shape[1])
    out[:, :m] = A[:, :m]
    return out


def _box(value: np.ndarray, n: int, *, upper: bool) -> np.ndarray:
    array = np.asarray(value, dtype=np.float64)
    if array.ndim != 1 or len(array) > n or np.any(np.isnan(array)):
        raise ValueError("QP bounds must be one-dimensional and fit the action without NaN")
    if np.any(np.isneginf(array) if upper else np.isposinf(array)):
        raise ValueError("QP bound excludes every finite command")
    return array


def validate_term(term: QPTerm, u: np.ndarray) -> None:
    """Pure candidate validation, without callback execution or state changes."""
    u = np.asarray(u, dtype=np.float64)
    if u.ndim != 1 or not np.all(np.isfinite(u)):
        raise ValueError("QP candidate must be a finite vector")
    n = len(u)
    tol = FEASIBILITY_TOLERANCE
    if term.upper is not None:
        upper = _box(term.upper, n, upper=True)
        if np.any(u[: len(upper)] > upper + tol):
            raise ValueError("Candidate violates motion upper bound")
    if term.lower is not None:
        lower = _box(term.lower, n, upper=False)
        if np.any(u[: len(lower)] < lower - tol):
            raise ValueError("Candidate violates motion lower bound")
    if (term.A is None) != (term.b is None):
        raise ValueError("QP linear constraints require both A and b")
    if term.A is not None:
        A = _fit_cols(np.asarray(term.A, dtype=np.float64), n)
        b = np.asarray(term.b, dtype=np.float64)
        if b.shape != (len(A),) or not np.all(np.isfinite(b)):
            raise ValueError("QP b must be finite and match the matrix rows")
        scale = np.max(np.abs(A), axis=1, initial=0.0)
        scale = np.where(scale > 0, scale, 1.0)
        with np.errstate(over="ignore", invalid="ignore"):
            residual = (A / scale[:, None]) @ u - b / scale
        if not np.all(np.isfinite(residual)) or np.any(residual > tol):
            raise ValueError("Candidate violates coupled motion constraint")
    norm_fields = (term.norm_A, term.norm_b, term.norm_limit)
    if any(v is not None for v in norm_fields):
        if any(v is None for v in norm_fields):
            raise ValueError("Norm constraint requires norm_A, norm_b and norm_limit")
        A = _fit_cols(np.asarray(term.norm_A, dtype=np.float64), n)
        b = np.asarray(term.norm_b, dtype=np.float64)
        assert term.norm_limit is not None
        limit = float(term.norm_limit)
        if (
            b.shape != (len(A),)
            or not np.all(np.isfinite(b))
            or not np.isfinite(limit)
            or limit < 0
        ):
            raise ValueError("Invalid motion norm constraint")
        if np.linalg.norm(A @ u - b) > limit + tol:
            raise ValueError("Candidate violates motion norm constraint")


def bind_motion_evidence(
    result: GuardResult, action: ActionProposal | ValidatedAction | None
) -> GuardResult:
    """Bind opaque L1/L2 checks to the command they actually checked.

    A callback without a declarative constraint cannot certify a different
    command. Keep its accepted command instead of calling stateful code twice.
    """
    from dam.guard.pipeline import PER_BOUNDARY_KEY

    if (
        action is None
        or result.layer.value not in (1, 2)
        or result.decision not in (GuardDecision.PASS, GuardDecision.CLAMP)
    ):
        return result

    def bind(metadata: dict[str, Any], decision: GuardDecision) -> dict[str, Any]:
        if METADATA_KEY in metadata:
            return metadata
        accepted = result.clamped_action if decision == GuardDecision.CLAMP else action
        if accepted is None:
            raise ValueError("Motion CLAMP has no accepted command")
        return {
            **metadata,
            _BASIS_KEY: {
                "positions": accepted.target_joint_positions.copy(),
                "velocities": accepted.target_joint_velocities.copy()
                if accepted.target_joint_velocities is not None
                else None,
                "gripper": accepted.gripper_action,
            },
        }

    metadata = dict(result.metadata)
    per_boundary = metadata.get(PER_BOUNDARY_KEY)
    if per_boundary:
        metadata[PER_BOUNDARY_KEY] = {
            name: {
                **entry,
                "metadata": bind(entry.get("metadata", {}), GuardDecision[entry["decision"]]),
            }
            for name, entry in per_boundary.items()
        }
    else:
        metadata = bind(metadata, result.decision)
    return replace(result, metadata=metadata)


def validate_motion_evidence(results: list[GuardResult], candidate: ValidatedAction) -> None:
    """Validate current motion constraints and opaque L1/L2 command bindings."""
    from dam.guard.pipeline import PER_BOUNDARY_KEY

    def validate(metadata: dict[str, Any]) -> None:
        if METADATA_KEY in metadata:
            validate_term(_coerce(metadata[METADATA_KEY]), candidate.target_joint_positions)
        basis = metadata.get(_BASIS_KEY)
        velocity_matches = (
            basis is None
            or (basis["velocities"] is None and candidate.target_joint_velocities is None)
            or (
                basis["velocities"] is not None
                and candidate.target_joint_velocities is not None
                and np.array_equal(basis["velocities"], candidate.target_joint_velocities)
            )
        )
        if basis is not None and (
            not np.array_equal(basis["positions"], candidate.target_joint_positions)
            or not velocity_matches
            or basis["gripper"] != candidate.gripper_action
        ):
            raise ValueError("Motion check has no constraint metadata for the modified command")
        for entry in metadata.get(PER_BOUNDARY_KEY, {}).values():
            validate(entry.get("metadata", {}))

    for result in results:
        cached_slow = result.metadata.get("slow_lane") and not result.metadata.get(
            "current_cycle_checked"
        )
        if result.layer.value in (1, 2) and not cached_slow:
            try:
                validate(result.metadata)
            except Exception as exc:
                raise ValueError(f"{result.guard_name}: {exc}") from exc


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
    results: list[CallbackResult],
    action_in: ValidatedAction | None,
) -> ValidatedAction | None:
    """Fuse L1 CLAMP results into one least-perturbing action via a QP.

    Every active PASS and CLAMP contributes ``metadata['motion_qp']``.
    Constraints are hard; all final residuals are independently checked.
    A standalone opaque CLAMP can pass through unchanged, but cannot be
    fused with other constraints without a declarative model.
    """
    clamps = [r for r in results if r.decision == GuardDecision.CLAMP]
    terms = [
        _coerce(c.metadata[METADATA_KEY])
        for c in results
        if c.metadata and METADATA_KEY in c.metadata
    ]
    if len(results) > 1 and len(terms) != len(results):
        raise ValueError(
            "Every CLAMP needs QP metadata, and every PASS needs constraint metadata when fusing motion constraints"
        )
    if not terms:
        # A single custom CLAMP does not require a QP solve.
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
    ineq_A: list[np.ndarray] = []
    ineq_b: list[np.ndarray] = []

    for term in terms:
        if term.upper is not None:
            up = _box(term.upper, n, upper=True)
            m = min(n, up.shape[0])
            upper[:m] = np.minimum(upper[:m], up[:m])
        if term.lower is not None:
            lo = _box(term.lower, n, upper=False)
            m = min(n, lo.shape[0])
            lower[:m] = np.maximum(lower[:m], lo[:m])

        if (term.A is None) != (term.b is None):
            raise ValueError("QP linear constraints require both A and b")
        if term.A is not None:
            A = _fit_cols(np.asarray(term.A, dtype=np.float64), n)
            b = np.asarray(term.b, dtype=np.float64)
            if b.shape != (len(A),) or not np.all(np.isfinite(b)):
                raise ValueError("QP b must be finite and match the matrix rows")
            ineq_A.append(A)
            ineq_b.append(b)

    extra_A = np.vstack(ineq_A) if ineq_A else None
    extra_ub = np.concatenate(ineq_b) if ineq_b else None
    u = qp_solver.solve_hard_constraints(
        u_nom,
        upper=upper,
        lower=lower,
        extra_A=extra_A,
        extra_ub=extra_ub,
    )
    if u is None:
        logger.error("motion_qp_aggregator: QP solve failed — no safe action found")
        return None

    for term in terms:
        validate_term(term, u)

    return ValidatedAction(
        target_joint_positions=u,
        was_clamped=True,
        original_proposal=orig,
        gripper_action=orig.gripper_action if orig is not None else None,
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
