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
from dam.boundary.callbacks.hardware import collect_host_health
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
        min_voltage_v: float = 6.0,
        max_voltage_v: float = 8.5,
        monitor_temperature: bool = True,
        monitor_current: bool = True,
        monitor_voltage: bool = False,
        consecutive_fault_frames: int = 1,
        peak_action: str = "warn",
        exception_joints: list[int] | None = None,
        **kwargs: Any,
    ) -> GuardResult:
        layer = self.get_layer()
        name = self.get_name()
        host_health = self._collect_host_health_if_active(active_containers)

        # 1. Boundary callbacks — L3 nodes own their params in constraint.params.
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
        if callback_res:
            return callback_res

        if host_health is not None and self._has_only_host_health_boundary(active_containers):
            return GuardResult.success(
                guard_name=name,
                layer=layer,
                metadata={"host_health": host_health},
                reason="host health ok",
            )

        # 2. Health telemetry checks
        if hardware_status is None and hasattr(obs, "metadata") and obs.metadata:
            hardware_status = obs.metadata.get("hardware_status")

        exceptions = set(exception_joints or [])
        if hardware_status is not None:
            health_res = self._check_health_telemetry(
                hardware_status=hardware_status,
                max_temp=max_temperature_c,
                max_curr=max_current_a,
                min_voltage=min_voltage_v,
                max_voltage=max_voltage_v,
                monitor_temperature=monitor_temperature,
                monitor_current=monitor_current,
                monitor_voltage=monitor_voltage,
                consecutive_fault_frames=consecutive_fault_frames,
                peak_action=peak_action,
                exceptions=exceptions,
                name=name,
                layer=layer,
            )
            if health_res:
                return health_res

        reason = self._telemetry_summary(hardware_status, exceptions)
        telemetry = self._extract_telemetry(hardware_status)
        if host_health is not None:
            telemetry["host_health"] = host_health
        if exceptions:
            telemetry["exception_joints"] = sorted(exceptions)
        return GuardResult.success(guard_name=name, layer=layer, metadata=telemetry, reason=reason)

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
    def _has_only_host_health_boundary(active_containers: list[Any] | None) -> bool:
        if not active_containers:
            return False
        for container in active_containers:
            node = container.get_active_node()
            callback = node.constraint.callback if node.constraint else None
            if callback != "host_health_limit":
                return False
        return True

    def _check_health_telemetry(
        self,
        hardware_status: dict[str, Any],
        max_temp: float,
        max_curr: float,
        min_voltage: float,
        max_voltage: float,
        monitor_temperature: bool,
        monitor_current: bool,
        monitor_voltage: bool,
        consecutive_fault_frames: int,
        peak_action: str,
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

        violations: list[str] = []

        # Per-motor temperature check
        temps: dict[str, float] | None = hardware_status.get("temperatures")
        if monitor_temperature and temps:
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
                violations.append(f"Temp>{max_temp}°: {detail} ({all_str})")
        elif monitor_temperature:
            temp = hardware_status.get("temperature_c")
            if temp is not None and temp > max_temp:
                violations.append(f"Temp {temp:.1f}° > {max_temp}° limit")

        # Per-motor current check (overcurrent only — zero current is normal when idle)
        currents: dict[str, float] | None = hardware_status.get("currents")
        if monitor_current and currents:
            keys = list(currents.keys())
            over = {
                m: c
                for i, (m, c) in enumerate(currents.items())
                if c > max_curr and (i + 1) not in exceptions
            }
            if over:
                detail = ", ".join(f"{_jlabel(keys, m)}={c:.2f}A" for m, c in over.items())
                violations.append(f"Current>{max_curr}A: {detail}")
        elif monitor_current:
            curr_a = hardware_status.get("current_a")
            if curr_a is not None and curr_a > max_curr:
                violations.append(f"Current {curr_a:.2f}A > {max_curr}A limit")

        voltages: dict[str, float] | None = hardware_status.get("voltages")
        if monitor_voltage and voltages:
            keys = list(voltages.keys())
            out_of_band = {
                m: v
                for i, (m, v) in enumerate(voltages.items())
                if (v < min_voltage or v > max_voltage) and (i + 1) not in exceptions
            }
            if out_of_band:
                detail = ", ".join(f"{_jlabel(keys, m)}={v:.2f}V" for m, v in out_of_band.items())
                violations.append(f"Voltage outside [{min_voltage}, {max_voltage}]V: {detail}")

        if not violations:
            self._hardware_violation_streak = 0
            return None

        frames = max(1, int(consecutive_fault_frames))
        self._hardware_violation_streak = getattr(self, "_hardware_violation_streak", 0) + 1
        reason = "; ".join(violations)
        if self._hardware_violation_streak < frames:
            # A single motor telemetry spike is useful signal, but should not
            # immediately stop the robot unless the operator configured one-frame
            # escalation. Persist the peak in metadata via the PASS result.
            return GuardResult.success(
                guard_name=name,
                layer=layer,
                reason=(
                    f"{peak_action} peak: {reason} "
                    f"(streak {self._hardware_violation_streak}/{frames})"
                ),
                metadata={
                    **self._extract_telemetry(hardware_status),
                    "hardware_peak": {
                        "reason": reason,
                        "streak": self._hardware_violation_streak,
                        "required_frames": frames,
                        "action": peak_action,
                    },
                },
            )

        return GuardResult(
            decision=GuardDecision.FAULT,
            guard_name=name,
            layer=layer,
            reason=f"{reason} (streak {self._hardware_violation_streak}/{frames})",
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
