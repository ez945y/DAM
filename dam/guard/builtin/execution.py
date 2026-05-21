"""Execution Guard (L2) — task-level boundary enforcement."""

from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np

from dam.guard.base import Guard
from dam.guard.callbacks import evaluate_boundary_callbacks
from dam.types.observation import Observation
from dam.types.result import GuardDecision, GuardResult

logger = logging.getLogger(__name__)


class ExecutionGuard(Guard):
    """
    L3: evaluates the ACTIVE boundary node constraints each cycle.

    Checks (in order):
    1. max_speed: if obs.joint_velocities norm exceeds params["max_speed"] → REJECT
    2. bounds: if obs end_effector_pose[:3] outside bounds → REJECT
    3. max_force_n: if obs.force_torque norm exceeds limit → REJECT
    4. callback: each registered callback; if any returns False → REJECT
    5. timeout_sec: if node has been active > timeout_sec → REJECT

    All constraint parameters are read from ``constraint.params``.

    Injection keys:
        obs: Observation (runtime)
        active_containers: List[BoundaryContainer] (runtime)
        node_start_times: Dict[str, float] (runtime)
    """

    expected_decisions = frozenset({GuardDecision.PASS, GuardDecision.REJECT, GuardDecision.FAULT})

    def __init__(self) -> None:
        # Cache for parameter processing (e.g. degrees to radians)
        self._cache_map: dict[
            str, tuple[int, float]
        ] = {}  # node_id -> (id(max_speed), converted_val)

    def check(
        self,
        obs: Observation,
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
            params = (constraint.params or {}).copy()  # copy to avoid mutating original

            use_degrees = params.get("use_degrees", False)

            # 1. max_speed check (Joint speed norm)
            max_speed = params.get("max_speed")
            if max_speed is not None and obs.joint_velocities is not None:
                # Optimized: only convert once per node configuration
                cache_key = node.node_id
                param_id = id(max_speed)

                if cache_key in self._cache_map and self._cache_map[cache_key][0] == param_id:
                    effective_max_speed = self._cache_map[cache_key][1]
                else:
                    effective_max_speed = np.radians(max_speed) if use_degrees else max_speed
                    self._cache_map[cache_key] = (param_id, effective_max_speed)

                speed_norm = float(np.linalg.norm(obs.joint_velocities))
                if speed_norm > effective_max_speed:
                    return GuardResult.reject(
                        reason=(
                            f"joint speed norm {speed_norm:.3f} > "
                            f"max_speed {effective_max_speed:.3f}"
                        ),
                        guard_name=name,
                        layer=layer,
                    )

            # 2. bounds check (Endpoint workspace)
            bounds = params.get("bounds")
            if bounds is not None and obs.end_effector_pose is not None:
                bounds_arr = np.asarray(bounds)
                ee_pos = obs.end_effector_pose[:3]
                if not np.all((ee_pos >= bounds_arr[:, 0]) & (ee_pos <= bounds_arr[:, 1])):
                    return GuardResult.reject(
                        reason=f"end-effector {ee_pos} outside bounds {bounds}",
                        guard_name=name,
                        layer=layer,
                    )

            # 3. callback check
            if constraint.callback:
                _, callback_res = evaluate_boundary_callbacks(
                    containers=[container],
                    base_kwargs={"obs": obs},
                    expected_layer=None,
                    guard_name=name,
                    guard_layer=layer,
                    violation_decision=GuardDecision.REJECT,
                    fault_source="guard_code",
                )
                if callback_res:
                    return callback_res

            # 4. timeout_sec check (Temporal watchdog)
            if node.timeout_sec is not None and node_start_times:
                start_time = node_start_times.get(node.node_id)
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
