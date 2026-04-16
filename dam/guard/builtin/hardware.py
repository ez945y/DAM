"""HardwareGuard (L4) — hardware health and heartbeat monitoring.

Checks actuator temperature, current draw, and error codes reported by the
hardware status, as well as the freshness of the latest observation.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import dam
from dam.guard.base import Guard
from dam.types.observation import Observation
from dam.types.result import GuardDecision, GuardResult

logger = logging.getLogger(__name__)


@dam.guard(layer="L4")
class HardwareGuard(Guard):
    """L4 hardware safety guard: health (temp/current) and watchdog (heartbeat).

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
    max_staleness_ms  : float   default 500.0
    max_temperature_c : float   default 70.0
    max_current_a     : float   default 5.0
    """

    def check(
        self,
        obs: Observation,
        hardware_status: dict[str, Any] | None = None,
        now: float | None = None,
        cycle_id: int = 1,
        max_staleness_ms: float = 500.0,
        max_temperature_c: float = 70.0,
        max_current_a: float = 5.0,
        **kwargs: Any,
    ) -> GuardResult:
        layer = self.get_layer()
        name = self.get_name()

        # 1. Watchdog: Heartbeat Check
        current = now if now is not None else time.monotonic()

        # Apply a generous grace period for the very first cycle to allow for
        # hardware warmup/init (especially slow USB cameras).
        effective_limit = max_staleness_ms
        if cycle_id == 0:
            effective_limit = max(effective_limit, 5000.0)

        staleness_s = current - obs.timestamp
        staleness_ms = staleness_s * 1000.0

        if staleness_ms > effective_limit:
            return GuardResult(
                decision=GuardDecision.FAULT,
                guard_name=name,
                layer=layer,
                reason=(
                    f"Hardware heartbeat lost: data is {staleness_ms:.1f}ms stale "
                    f"(limit {effective_limit}ms)"
                ),
                fault_source="hardware",
            )

        # 2. Health: Telemetry Checks
        if hardware_status is None and hasattr(obs, "metadata") and obs.metadata:
            hardware_status = obs.metadata.get("hardware_status")

        if hardware_status is None:
            return GuardResult.success(guard_name=name, layer=layer)

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

        # Temperature
        temperature = hardware_status.get("temperature_c")
        if temperature is not None and temperature > max_temperature_c:
            return GuardResult(
                decision=GuardDecision.FAULT,
                guard_name=name,
                layer=layer,
                reason=f"Temperature {temperature:.1f}°C exceeds limit",
                fault_source="hardware",
            )

        # Current
        current_a = hardware_status.get("current_a")
        if current_a is not None and current_a > max_current_a:
            return GuardResult(
                decision=GuardDecision.FAULT,
                guard_name=name,
                layer=layer,
                reason=f"Current {current_a:.2f}A exceeds limit",
                fault_source="hardware",
            )

        return GuardResult.success(guard_name=name, layer=layer)
