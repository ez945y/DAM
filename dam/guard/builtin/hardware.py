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
from dam.boundary.callbacks.hardware import collect_host_health
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
    hardware_status : dict | None
    now : float | None
    active_containers : list
    node_start_times : dict
    """

    expected_decisions = frozenset({GuardDecision.PASS, GuardDecision.CLAMP, GuardDecision.FAULT})

    def check(
        self,
        obs: Observation,
        hardware_status: dict[str, Any] | None = None,
        active_containers: list[Any] | None = None,
        node_start_times: dict[str, float] | None = None,
        now: float | None = None,
        **kwargs: Any,
    ) -> GuardResult:
        layer = self.get_layer()
        name = self.get_name()
        host_health = self._collect_host_health_if_active(active_containers)

        # Inject hardware_status from obs.metadata if adapter didn't provide it directly.
        if hardware_status is None and hasattr(obs, "metadata") and obs.metadata:
            hardware_status = obs.metadata.get("hardware_status")

        # Run L3 boundary callbacks.
        _, callback_res = evaluate_boundary_callbacks(
            containers=active_containers,
            base_kwargs={
                "obs": obs,
                "now": now,
                "hardware_status": hardware_status,
                "host_health": host_health,
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
            return callback_res

        # All clear — reset streaks for active boundaries.
        if active_containers:
            for c in active_containers:
                bname = getattr(c, "_runtime_boundary_name", None)
                if bname:
                    self.reset_streak(bname)

        if host_health is not None:
            telemetry: dict[str, Any] = {"host_health": host_health}
            reason = ""
        else:
            telemetry = self._extract_telemetry(hardware_status)
            reason = self._telemetry_summary(hardware_status)
        return GuardResult.success(guard_name=name, layer=layer, metadata=telemetry, reason=reason)

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

    @staticmethod
    def _collect_host_health_if_active(
        active_containers: list[Any] | None,
    ) -> dict[str, Any] | None:
        if not active_containers:
            return None
        for container in active_containers:
            node = container.get_active_node()
            callback = node.constraint.callback if node.constraint else None
            if callback == "host_health_limit":
                return collect_host_health()
        return None

    @staticmethod
    def _extract_telemetry(hardware_status: dict[str, Any] | None) -> dict[str, Any]:
        if not hardware_status:
            return {}
        out: dict[str, Any] = {}
        for key in ("temperatures", "currents", "voltages", "error_codes"):
            if key in hardware_status:
                out[key] = hardware_status[key]
        return out

    @staticmethod
    def _telemetry_summary(hardware_status: dict[str, Any] | None) -> str:
        if not hardware_status:
            return ""
        parts: list[str] = []
        temps = hardware_status.get("temperatures")
        if temps:
            vals = list(temps.values())
            parts.append(f"T:{max(vals):.0f}°")
        currents = hardware_status.get("currents")
        if currents:
            vals = list(currents.values())
            parts.append(f"I:{max(vals):.2f}A")
        return " ".join(parts)
