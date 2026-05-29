"""Execution Guard (L2) — task-level boundary enforcement.

A thin shell over the boundary-callback pipeline plus a temporal watchdog.
Task-level constraints (joint speed cap, workspace box, …) are ordinary L2
``@boundary_callback`` functions (see ``dam/boundary/callbacks/execution.py``);
the guard itself owns no constraint logic — adding a new task limit means
writing one callback, not editing this file.

Timeout emits CLAMP (warning) first.  The boundary node's ``warn_frames``
controls how many consecutive warning cycles before the decision is promoted
to REJECT, which triggers the node's configured fallback.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import dam
from dam.guard.base import Guard
from dam.guard.pipeline import aggregate, run_callbacks
from dam.types.action import ActionProposal
from dam.types.observation import Observation
from dam.types.result import GuardDecision, GuardResult

logger = logging.getLogger(__name__)


@dam.guard(layer="L2")
class ExecutionGuard(Guard):
    """L2: evaluates the ACTIVE boundary node's callback + timeout each cycle.

    Checks (in order):
    1. callback: the node's registered L2 callback; if it violates → REJECT.
    2. timeout_sec: if the node has been active > timeout_sec → CLAMP first,
       REJECT after ``warn_frames`` consecutive cycles.

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
            boundary_name = getattr(container, "_runtime_boundary_name", None) or name
            threshold = max(1, node.warn_frames)

            # 1. callback check
            if constraint.callback:
                cb_results = run_callbacks(
                    containers=[container],
                    runtime_pool={"obs": obs, "action": action},
                    expected_layer="L2",
                )
                if cb_results:
                    callback_res = aggregate(
                        cb_results,
                        guard_name=boundary_name,
                        guard_layer=layer,
                    )
                    if callback_res.decision in (GuardDecision.REJECT, GuardDecision.FAULT):
                        streak = self.bump_streak(boundary_name)
                        if streak < threshold:
                            return GuardResult(
                                decision=GuardDecision.CLAMP,
                                guard_name=boundary_name,
                                layer=layer,
                                reason=f"{callback_res.reason} (warn {streak}/{threshold})",
                                metadata={
                                    **callback_res.metadata,
                                    "warn_streak": streak,
                                    "warn_frames": threshold,
                                },
                            )
                    if callback_res.decision != GuardDecision.PASS:
                        return callback_res

            # 2. timeout_sec check
            if node.timeout_sec is not None and node_start_times:
                bname_key = getattr(container, "_runtime_boundary_name", None)
                start_time = node_start_times.get(node.node_id)
                if start_time is None and isinstance(bname_key, str):
                    start_time = node_start_times.get(bname_key)
                if start_time is not None:
                    elapsed = time.monotonic() - start_time
                    if elapsed > node.timeout_sec:
                        reject_name = bname_key or name
                        streak = self.bump_streak(f"_timeout:{reject_name}")
                        reason = (
                            f"node '{node.node_id}' timeout "
                            f"({elapsed:.3f}s > {node.timeout_sec}s, "
                            f"warn {streak}/{threshold})"
                        )
                        if streak >= threshold:
                            return GuardResult(
                                decision=GuardDecision.REJECT,
                                guard_name=reject_name,
                                layer=layer,
                                reason=reason,
                                metadata={
                                    "warn_streak": streak,
                                    "warn_frames": threshold,
                                    "required_frames": threshold,
                                },
                            )
                        return GuardResult(
                            decision=GuardDecision.CLAMP,
                            guard_name=reject_name,
                            layer=layer,
                            reason=reason,
                            metadata={
                                "warn_streak": streak,
                                "warn_frames": threshold,
                            },
                        )
                    else:
                        bname_clear = bname_key or name
                        self.reset_streak(f"_timeout:{bname_clear}")

            self.reset_streak(boundary_name)

        return GuardResult.success(guard_name=name, layer=layer)
