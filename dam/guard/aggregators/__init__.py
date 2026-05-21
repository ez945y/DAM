"""Optional, user-injectable clamp-fusion strategies for the guard pipeline.

The default fusion (:func:`dam.guard.pipeline.sequential_clamp_aggregator`)
folds each CLAMP into a running action element-wise.  Anything fancier —
e.g. a QP that minimises perturbation subject to all L1 constraints jointly —
lives here as a separate, opt-in aggregator the user wires into a guard via
its ``clamp_aggregator`` argument.  The shared pipeline never imports these.
"""

from __future__ import annotations

from dam.guard.aggregators.motion_qp import MotionQPConstraint, motion_qp_aggregator

__all__ = ["MotionQPConstraint", "motion_qp_aggregator"]
