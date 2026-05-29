"""QP-based clamp fusion for the L1 guard pipeline.

Each L1 callback contributes a :class:`QPTerm` (box bounds and/or linear
inequalities); the aggregator solves a single QP to find the least-perturbing
safe action.  ``MotionQPConstraint`` is a backward-compat alias for ``QPTerm``.
"""

from __future__ import annotations

from dam.guard.aggregators.motion_qp import (
    MotionQPConstraint,
    QPTerm,
    motion_qp_aggregator,
)

__all__ = ["MotionQPConstraint", "QPTerm", "motion_qp_aggregator"]
