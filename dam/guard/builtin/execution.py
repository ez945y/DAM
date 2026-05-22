"""Execution Guard (L2) — task-level boundary enforcement.

A thin shell over the boundary-callback pipeline plus a temporal watchdog.
Task-level constraints (joint speed cap, workspace box, …) are ordinary L2
``@boundary_callback`` functions (see ``dam/boundary/callbacks/execution.py``);
the guard itself owns no constraint logic — adding a new task limit means
writing one callback, not editing this file.

The only thing the guard does beyond dispatching callbacks is enforce
``node.timeout_sec``, which is a property of the boundary-node lifecycle
(how long a node may stay active) rather than an observation-level constraint,
so it can't be expressed as a stateless callback.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import dam
from dam.guard.base import Guard
from dam.guard.callbacks import evaluate_boundary_callbacks
from dam.types.action import ActionProposal
from dam.types.observation import Observation
from dam.types.result import GuardDecision, GuardResult

logger = logging.getLogger(__name__)


@dam.guard(layer="L2")
class ExecutionGuard(Guard):
    """L2: evaluates the ACTIVE boundary node's callback + timeout each cycle.

    Checks (in order):
    1. callback: the node's registered L2 callback; if it violates → REJECT.
    2. timeout_sec: if the node has been active > timeout_sec → REJECT.

    Injection keys:
        obs: Observation (runtime)
        active_containers: List[BoundaryContainer] (runtime)
        node_start_times: Dict[str, float] (runtime)
    """

    expected_decisions = frozenset(
        {GuardDecision.PASS, GuardDecision.CLAMP, GuardDecision.REJECT, GuardDecision.FAULT}
    )

    def check(
        self,
        obs: Observation,
        action: ActionProposal | None = None,
        active_containers: list[Any] | None = None,
        node_start_times: dict[str, float] | None = None,
    ) -> GuardResult:
        layer = self.get_layer()
        name = self.get_name()

        if not active_containers:
            return GuardResult.success(guard_name=name, layer=layer)

        for container in active_containers:
            node = container.get_active_node()
            constraint = node.constraint

            # 1. callback check
            if constraint.callback:
                _, callback_res = evaluate_boundary_callbacks(
                    containers=[container],
                    base_kwargs={"obs": obs, "action": action},
                    expected_layer="L2",
                    guard_name=name,
                    guard_layer=layer,
                    violation_decision=GuardDecision.REJECT,
                    fault_source="guard_code",
                )
                if callback_res:
                    return callback_res

            # 2. timeout_sec check (Temporal watchdog)
            if node.timeout_sec is not None and node_start_times:
                boundary_name = getattr(container, "_runtime_boundary_name", None)
                start_time = node_start_times.get(node.node_id)
                if start_time is None and isinstance(boundary_name, str):
                    start_time = node_start_times.get(boundary_name)
                if start_time is not None:
                    elapsed = time.monotonic() - start_time
                    if elapsed > node.timeout_sec:
                        return GuardResult.reject(
                            reason=(
                                f"node '{node.node_id}' timed out "
                                f"({elapsed:.3f}s > {node.timeout_sec}s)"
                            ),
                            guard_name=name,
                            layer=layer,
                        )

        return GuardResult.success(guard_name=name, layer=layer)
