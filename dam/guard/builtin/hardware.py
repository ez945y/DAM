"""HardwareGuard (L3) — hardware health and heartbeat monitoring.

Checks observation freshness (watchdog), actuator temperature, current draw,
and error codes reported by the hardware_status dict.

Following error (Goal vs Present position) is a *motion control* concern that
belongs at L1, not here.  Use boundary callbacks (e.g. ``joint_position_limits``)
for positional safety.
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


def _jlabel(keys: Any, motor: str) -> str:
    try:
        idx = list(keys).index(motor)
        return f"J{idx + 1}"
    except ValueError:
        return motor


def _jmap(readings: dict[str, float]) -> dict[str, float]:
    return {f"J{i + 1}": v for i, (_, v) in enumerate(readings.items())}


@dam.guard(layer="L3")
class HardwareGuard(Guard):
    """L3 hardware safety guard: watchdog (heartbeat) + health telemetry.

    Injection keys
    --------------
    obs : Observation
        The current observation to check for freshness.
    hardware_status : dict | None
        Optional telemetry from ActionAdapter/SensorAdapter.
    now : float | None
        Current monotonic time (passed from runtime to avoid redundant calls).

    Config-pool keys (optional)
    ---------------------------
    max_temperature_c : float       default 80.0
    max_current_a     : float       default 5.0
    exception_joints  : list[int]   default []
        1-based joint indices to skip in the per-motor temperature and
        current checks (e.g. ``[3]`` excludes J3). Use this when a sensor
        is known faulty — its readings still appear in the telemetry
        summary, but won't trigger a FAULT.
    """

    def check(
        self,
        obs: Observation,
        hardware_status: dict[str, Any] | None = None,
        active_containers: list[Any] | None = None,
        node_start_times: dict[str, float] | None = None,
        now: float | None = None,
        max_temperature_c: float = 80.0,
        max_current_a: float = 5.0,
        exception_joints: list[int] | None = None,
        **kwargs: Any,
    ) -> GuardResult:
        layer = self.get_layer()
        name = self.get_name()

        # 1. Boundary callbacks — L3 nodes own their params in constraint.params.
        _, callback_res = evaluate_boundary_callbacks(
            containers=active_containers,
            base_kwargs={
                "obs": obs,
                "now": now,
                "hardware_status": hardware_status,
                "node_start_times": node_start_times or {},
            },
            expected_layer=layer.name,
            guard_name=name,
            guard_layer=layer,
            violation_decision=GuardDecision.FAULT,
            fault_source="hardware",
        )
        if callback_res:
            return callback_res

        # 2. Health telemetry checks
        if hardware_status is None and hasattr(obs, "metadata") and obs.metadata:
            hardware_status = obs.metadata.get("hardware_status")

        exceptions = set(exception_joints or [])
        if hardware_status is not None:
            health_res = self._check_health_telemetry(
                hardware_status, max_temperature_c, max_current_a, exceptions, name, layer
            )
            if health_res:
                return health_res

        reason = self._telemetry_summary(hardware_status, exceptions)
        telemetry = self._extract_telemetry(hardware_status)
        if exceptions:
            telemetry["exception_joints"] = sorted(exceptions)
        return GuardResult.success(guard_name=name, layer=layer, metadata=telemetry, reason=reason)

    def _check_health_telemetry(
        self,
        hardware_status: dict[str, Any],
        max_temp: float,
        max_curr: float,
        exceptions: set[int],
        name: str,
        layer: str,
    ) -> GuardResult | None:
        # Error codes
        error_codes: list[int] = hardware_status.get("error_codes", [])
        non_zero = [c for c in error_codes if c != 0]
        if non_zero:
            return GuardResult(
                decision=GuardDecision.FAULT,
                guard_name=name,
                layer=layer,
                reason=f"Hardware error codes: {non_zero}",
                fault_source="hardware",
            )

        # Per-motor temperature check
        temps: dict[str, float] | None = hardware_status.get("temperatures")
        if temps:
            keys = list(temps.keys())
            over = {
                m: t
                for i, (m, t) in enumerate(temps.items())
                if t > max_temp and (i + 1) not in exceptions
            }
            if over:
                all_str = " ".join(
                    f"{_jlabel(keys, m)}:{t:.0f}°{'·skip' if (i + 1) in exceptions else ''}"
                    for i, (m, t) in enumerate(temps.items())
                )
                detail = ", ".join(f"{_jlabel(keys, m)}={t:.1f}°" for m, t in over.items())
                return GuardResult(
                    decision=GuardDecision.FAULT,
                    guard_name=name,
                    layer=layer,
                    reason=f"Temp>{max_temp}°: {detail} ({all_str})",
                    fault_source="hardware",
                )
        else:
            temp = hardware_status.get("temperature_c")
            if temp is not None and temp > max_temp:
                return GuardResult(
                    decision=GuardDecision.FAULT,
                    guard_name=name,
                    layer=layer,
                    reason=f"Temp {temp:.1f}° > {max_temp}° limit",
                    fault_source="hardware",
                )

        # Per-motor current check (overcurrent only — zero current is normal when idle)
        currents: dict[str, float] | None = hardware_status.get("currents")
        if currents:
            keys = list(currents.keys())
            over = {
                m: c
                for i, (m, c) in enumerate(currents.items())
                if c > max_curr and (i + 1) not in exceptions
            }
            if over:
                detail = ", ".join(f"{_jlabel(keys, m)}={c:.2f}A" for m, c in over.items())
                return GuardResult(
                    decision=GuardDecision.FAULT,
                    guard_name=name,
                    layer=layer,
                    reason=f"Current>{max_curr}A: {detail}",
                    fault_source="hardware",
                )
        else:
            curr_a = hardware_status.get("current_a")
            if curr_a is not None and curr_a > max_curr:
                return GuardResult(
                    decision=GuardDecision.FAULT,
                    guard_name=name,
                    layer=layer,
                    reason=f"Current {curr_a:.2f}A > {max_curr}A limit",
                    fault_source="hardware",
                )

        return None

    @staticmethod
    def _telemetry_summary(
        hardware_status: dict[str, Any] | None, exceptions: set[int] | None = None
    ) -> str:
        if not hardware_status:
            return ""
        excl = exceptions or set()
        parts = []
        temps = hardware_status.get("temperatures")
        if temps:
            parts.append(
                "T["
                + " ".join(
                    f"J{i + 1}:{t:.0f}°{'·skip' if (i + 1) in excl else ''}"
                    for i, (_, t) in enumerate(temps.items())
                )
                + "]"
            )
        currents = hardware_status.get("currents")
        if currents:
            parts.append(
                "I["
                + " ".join(
                    f"J{i + 1}:{c:.2f}A{'·skip' if (i + 1) in excl else ''}"
                    for i, (_, c) in enumerate(currents.items())
                )
                + "]"
            )
        voltages = hardware_status.get("voltages")
        if voltages:
            jv = _jmap(voltages)
            parts.append("V[" + " ".join(f"{j}:{v:.1f}V" for j, v in jv.items()) + "]")
        return " ".join(parts)

    @staticmethod
    def _extract_telemetry(hardware_status: dict[str, Any] | None) -> dict[str, Any]:
        if not hardware_status:
            return {}
        out: dict[str, Any] = {}
        for key in ("temperatures", "currents", "voltages"):
            if key in hardware_status:
                out[key] = _jmap(hardware_status[key])
        return out
