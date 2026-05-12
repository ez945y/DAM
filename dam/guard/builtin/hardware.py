"""HardwareGuard (L3) — hardware health and heartbeat monitoring.

Checks observation freshness (watchdog), actuator temperature, current draw,
and error codes reported by the hardware_status dict.

Following error (Goal vs Present position) is a *motion control* concern that
belongs at L1, not here.  Use boundary callbacks (e.g. ``joint_position_limits``)
for positional safety.
"""

from __future__ import annotations

import logging
import statistics
import time
from typing import Any

import dam
from dam.guard.base import Guard
from dam.types.observation import Observation
from dam.types.result import GuardDecision, GuardResult

logger = logging.getLogger(__name__)

_SENSOR_OUTLIER_RATIO = 2.0
_SENSOR_OUTLIER_ABS = 30.0
_WATCHDOG_MS = 500.0
_FIRST_CYCLE_GRACE_MS = 5000.0


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
    max_temperature_c : float   default 80.0
    max_current_a     : float   default 5.0
    """

    def check(
        self,
        obs: Observation,
        hardware_status: dict[str, Any] | None = None,
        now: float | None = None,
        cycle_id: int = 1,
        max_temperature_c: float = 80.0,
        max_current_a: float = 5.0,
        **kwargs: Any,
    ) -> GuardResult:
        layer = self.get_layer()
        name = self.get_name()

        # 1. Watchdog — fixed 500ms, generous first-cycle grace
        watchdog_res = self._check_watchdog(obs, now, cycle_id, name, layer)
        if watchdog_res:
            return watchdog_res

        # 2. Health telemetry checks
        if hardware_status is None and hasattr(obs, "metadata") and obs.metadata:
            hardware_status = obs.metadata.get("hardware_status")

        if hardware_status is not None:
            health_res = self._check_health_telemetry(
                hardware_status, max_temperature_c, max_current_a, name, layer
            )
            if health_res:
                return health_res

        reason = self._telemetry_summary(hardware_status)
        telemetry = self._extract_telemetry(hardware_status)
        return GuardResult.success(guard_name=name, layer=layer, metadata=telemetry, reason=reason)

    def _check_watchdog(
        self,
        obs: Observation,
        now: float | None,
        cycle_id: int,
        name: str,
        layer: str,
    ) -> GuardResult | None:
        current = now if now is not None else time.monotonic()
        limit_ms = _FIRST_CYCLE_GRACE_MS if cycle_id == 0 else _WATCHDOG_MS

        staleness_ms = (current - obs.timestamp) * 1000.0
        if staleness_ms > limit_ms:
            return GuardResult(
                decision=GuardDecision.FAULT,
                guard_name=name,
                layer=layer,
                reason=(f"Heartbeat lost: {staleness_ms:.0f}ms stale (limit {limit_ms:.0f}ms)"),
                fault_source="hardware",
            )
        return None

    def _check_health_telemetry(
        self,
        hardware_status: dict[str, Any],
        max_temp: float,
        max_curr: float,
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
            over = {m: t for m, t in temps.items() if t > max_temp}
            if over:
                suspects = _detect_sensor_outliers(temps)
                real = {m: t for m, t in over.items() if m not in suspects}
                keys = temps.keys()
                all_str = " ".join(
                    f"{_jlabel(keys, m)}:{t:.0f}°{'⚠' if m in suspects else ''}"
                    for m, t in temps.items()
                )
                if suspects:
                    sus_str = ", ".join(f"{_jlabel(keys, m)}={temps[m]:.0f}°" for m in suspects)
                    logger.warning("Suspect temp sensor: %s (all: %s)", sus_str, all_str)
                if real:
                    detail = ", ".join(f"{_jlabel(keys, m)}={t:.1f}°" for m, t in real.items())
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
            over = {m: c for m, c in currents.items() if c > max_curr}
            if over:
                keys = currents.keys()
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
    def _telemetry_summary(hardware_status: dict[str, Any] | None) -> str:
        if not hardware_status:
            return ""
        parts = []
        temps = hardware_status.get("temperatures")
        if temps:
            jt = _jmap(temps)
            parts.append("T[" + " ".join(f"{j}:{t:.0f}°" for j, t in jt.items()) + "]")
        currents = hardware_status.get("currents")
        if currents:
            jc = _jmap(currents)
            parts.append("I[" + " ".join(f"{j}:{c:.2f}A" for j, c in jc.items()) + "]")
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


def _detect_sensor_outliers(readings: dict[str, float]) -> set[str]:
    """Flag motors whose reading is far above the median — likely sensor faults."""
    if len(readings) < 3:
        return set()
    vals = list(readings.values())
    med = statistics.median(vals)
    if med <= 0:
        return set()
    return {
        m
        for m, v in readings.items()
        if v > med * _SENSOR_OUTLIER_RATIO and v - med > _SENSOR_OUTLIER_ABS
    }
