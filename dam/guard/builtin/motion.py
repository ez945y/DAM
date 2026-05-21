"""L1 — Physical kinematics guard.

A thin shell over :mod:`dam.guard.pipeline`: ``check()`` runs every active
L1 boundary's ``@boundary_callback`` and aggregates the results into a single
``GuardResult``.  All constraint semantics live in the callbacks (see
``dam/boundary/callbacks/kinematics.py``); MotionGuard owns no constraint
logic of its own — adding a new L1 limit means writing one callback, not
editing this file.

Custom solvers (QP, CBF, learned safety filters, …) plug in via the
``clamp_aggregator`` argument: instead of the default element-wise restrictive
merge, the aggregator can read per-callback ``metadata`` to assemble a joint
QP and return the fused least-perturbing action.
"""

from __future__ import annotations

import logging
from typing import Any

from dam.guard.base import Guard
from dam.guard.pipeline import ClampAggregator, run_and_aggregate, sequential_clamp_aggregator
from dam.types.action import ActionProposal
from dam.types.observation import Observation
from dam.types.result import GuardDecision, GuardResult

logger = logging.getLogger(__name__)


class MotionGuard(Guard):
    """L1 motion safety guard. Pipeline-driven; constraint logic lives in
    @boundary_callback functions registered for layer "L1".
    """

    _guard_kind = "motion"

    expected_decisions = frozenset(
        {GuardDecision.PASS, GuardDecision.CLAMP, GuardDecision.REJECT, GuardDecision.FAULT}
    )

    def __init__(self, clamp_aggregator: ClampAggregator | None = None) -> None:
        # Default fusion uses ValidatedAction.merge_restrictive (per-joint most
        # restrictive value).  Users with a QP / CBF stack inject their own.
        self._clamp_aggregator: ClampAggregator = clamp_aggregator or sequential_clamp_aggregator

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
