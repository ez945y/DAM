"""HardwareGuard (L3) — hardware health and heartbeat monitoring.

A thin shell over L3 boundary callbacks (``hardware_watchdog``,
``temperature_limit``, ``current_limit``, ``voltage_limit``, etc.).

All constraint logic lives in boundary callbacks registered in
``dam/boundary/callbacks/hardware.py``.  Adding a new hardware check
means writing one callback, not editing this file.

The node's ``warn_frames`` controls how many consecutive violation
cycles before the decision is promoted from warning to FAULT.
"""

from __future__ import annotations

import logging
from typing import Any

import dam
from dam.guard.base import Guard
from dam.guard.callbacks import evaluate_boundary_callbacks
from dam.types.observation import Observation
from dam.types.result import GuardDecision, GuardResult

logger = logging.getLogger(__name__)


@dam.guard(layer="L3")
class HardwareGuard(Guard):
    """L3 hardware safety guard — dispatches to boundary callbacks.

    Injection keys
    --------------
    obs : Observation
    now : float | None
    active_containers : list
    node_start_times : dict
    """

    expected_decisions = frozenset({GuardDecision.PASS, GuardDecision.CLAMP, GuardDecision.FAULT})

    def check(
        self,
        obs: Observation,
        active_containers: list[Any] | None = None,
        node_start_times: dict[str, float] | None = None,
        now: float | None = None,
        **kwargs: Any,
    ) -> GuardResult:
        layer = self.get_layer()
        name = self.get_name()

        # Run L3 boundary callbacks.
        _, callback_res = evaluate_boundary_callbacks(
            containers=active_containers,
            base_kwargs={
                "obs": obs,
                "now": now,
                "node_start_times": node_start_times or {},
            },
            expected_layer=layer.name,
            guard_name=name,
            guard_layer=layer,
            violation_decision=GuardDecision.FAULT,
            fault_source="hardware",
        )

        if callback_res and callback_res.decision in (
            GuardDecision.FAULT,
            GuardDecision.REJECT,
        ):
            # Use the first active container's boundary name for streak tracking,
            # since evaluate_boundary_callbacks merges results under guard_name.
            streak_key = name
            threshold = 1
            if active_containers:
                bname = getattr(active_containers[0], "_runtime_boundary_name", None)
                if bname:
                    streak_key = bname
                threshold = self._warn_frames_for(active_containers, streak_key)
            streak = self.bump_streak(streak_key)
            if streak < threshold:
                return GuardResult(
                    decision=GuardDecision.CLAMP,
                    guard_name=name,
                    layer=layer,
                    reason=f"{callback_res.reason} (warn {streak}/{threshold})",
                    metadata={
                        **callback_res.metadata,
                        "warn_streak": streak,
                        "warn_frames": threshold,
                    },
                )
            return GuardResult(
                decision=callback_res.decision,
                guard_name=name,
                layer=layer,
                reason=f"{callback_res.reason} (warn {streak}/{threshold})",
                fault_source=callback_res.fault_source,
                metadata={
                    **callback_res.metadata,
                    "warn_streak": streak,
                    "warn_frames": threshold,
                    "required_frames": threshold,
                },
            )

        if callback_res:
            # Reset streaks on PASS — violations cleared.
            if callback_res.decision == GuardDecision.PASS and active_containers:
                for c in active_containers:
                    bname = getattr(c, "_runtime_boundary_name", None)
                    if bname:
                        self.reset_streak(bname)
            return callback_res

        # No callbacks ran — reset streaks and return empty success.
        if active_containers:
            for c in active_containers:
                bname = getattr(c, "_runtime_boundary_name", None)
                if bname:
                    self.reset_streak(bname)

        return GuardResult.success(guard_name=name, layer=layer)

    @staticmethod
    def _warn_frames_for(active_containers: list[Any] | None, boundary_name: str) -> int:
        """Read warn_frames from the matching boundary node (default 1)."""
        if not active_containers:
            return 1
        for c in active_containers:
            bname = getattr(c, "_runtime_boundary_name", None)
            if bname == boundary_name or boundary_name == bname:
                node = c.get_active_node()
                return max(1, int(getattr(node, "warn_frames", 1)))
        # Fallback: check first container.
        node = active_containers[0].get_active_node()
        return max(1, int(getattr(node, "warn_frames", 1)))
