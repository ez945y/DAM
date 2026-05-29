"""L1 — Joint-space kinematics guard.

A thin shell over :mod:`dam.guard.pipeline`: ``check()`` runs every active
L1 boundary's ``@boundary_callback`` and aggregates the results into a single
``GuardResult``.  All constraint semantics live in the callbacks (see
``dam/boundary/callbacks/kinematics.py``); MotionGuard owns no constraint
logic of its own — adding a new L1 limit means writing one callback, not
editing this file.

CLAMP fusion uses the QP aggregator by default: each callback contributes
``MotionQPConstraint`` metadata, and the aggregator solves a single QP
that satisfies all constraints with minimum perturbation to the policy
proposal.  ``proxsuite`` is required at runtime.
"""

from __future__ import annotations

import logging
from typing import Any

import dam
from dam.guard.aggregators.motion_qp import motion_qp_aggregator
from dam.guard.base import Guard
from dam.guard.pipeline import ClampAggregator, run_and_aggregate
from dam.types.action import ActionProposal
from dam.types.observation import Observation
from dam.types.result import GuardDecision, GuardResult

logger = logging.getLogger(__name__)


@dam.guard(layer="L1")
class MotionGuard(Guard):
    """L1 motion safety guard. Pipeline-driven; constraint logic lives in
    @boundary_callback functions registered for layer "L1".
    """

    _guard_kind = "motion"

    expected_decisions = frozenset(
        {GuardDecision.PASS, GuardDecision.CLAMP, GuardDecision.REJECT, GuardDecision.FAULT}
    )

    def __init__(self, clamp_aggregator: ClampAggregator | None = None) -> None:
        # QP aggregator is the default and only supported fusion strategy.
        self._clamp_aggregator: ClampAggregator = clamp_aggregator or motion_qp_aggregator

    def check(
        self,
        obs: Observation,
        action: ActionProposal,
        active_containers: list[Any] | None = None,
        dt: float = 0.02,
        dynamics: Any | None = None,
    ) -> GuardResult:
        runtime_pool: dict[str, Any] = {
            "obs": obs,
            "action": action,
            "dt": dt,
            "dynamics": dynamics,
        }
        _results, final = run_and_aggregate(
            containers=active_containers,
            runtime_pool=runtime_pool,
            expected_layer="L1",
            guard_name=self.get_name(),
            guard_layer=self.get_layer(),
            action_in=None,
            clamp_aggregator=self._clamp_aggregator,
        )
        return final
