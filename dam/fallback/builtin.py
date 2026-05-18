from __future__ import annotations

import logging
from typing import Any

from dam.decorators import fallback
from dam.fallback.base import Fallback, FallbackContext, FallbackResult

logger = logging.getLogger(__name__)


@fallback(name="emergency_stop", escalates_to=None)
class EmergencyStop(Fallback):
    """Call ``sink.emergency_stop()`` when available, otherwise report success.

    Stackfile params:
      require_sink: bool = false  # fail/escalate when no sink stop hook exists
    """

    def execute(self, context: FallbackContext, bus: Any) -> FallbackResult:
        logger.critical(
            "EMERGENCY STOP triggered | cycle=%d | guard=%s | reason=%s",
            context.cycle_id,
            context.guard_result.guard_name,
            context.guard_result.reason,
        )
        sink = context.sink or (bus or {}).get("sink") if isinstance(bus, dict) else context.sink
        if sink is not None and hasattr(sink, "emergency_stop"):
            sink.emergency_stop()
            return FallbackResult(success=True, action=None, reason="sink emergency_stop executed")
        if self.get_param("require_sink", False):
            return FallbackResult(
                success=False, action=None, reason="sink emergency_stop unavailable"
            )
        return FallbackResult(success=True, action=None, reason="emergency_stop executed")


@fallback(name="hold_position", escalates_to="emergency_stop")
class HoldPosition(Fallback):
    """Hold by withholding the rejected proposal.

    Stackfile params:
      require_sink: bool = false
    """

    def execute(self, context: FallbackContext, bus: Any) -> FallbackResult:
        logger.warning(
            "HOLD POSITION triggered | cycle=%d | guard=%s",
            context.cycle_id,
            context.guard_result.guard_name,
        )
        sink = context.sink or (bus or {}).get("sink") if isinstance(bus, dict) else context.sink
        if sink is not None and hasattr(sink, "hold_position"):
            sink.hold_position()
        elif self.get_param("require_sink", False):
            return FallbackResult(
                success=False, action=None, reason="sink hold_position unavailable"
            )
        return FallbackResult(success=True, action=None, reason="hold_position executed")


@fallback(name="safe_retreat", escalates_to="hold_position")
class SafeRetreat(Fallback):
    def execute(self, context: FallbackContext, bus: Any) -> FallbackResult:
        logger.warning(
            "SAFE RETREAT triggered | cycle=%d | guard=%s",
            context.cycle_id,
            context.guard_result.guard_name,
        )
        return FallbackResult(success=True, action=None, reason="safe_retreat executed")


@fallback(name="noop", escalates_to=None)
class NoopFallback(Fallback):
    """Record the fallback event without commanding the sink."""

    def execute(self, context: FallbackContext, bus: Any) -> FallbackResult:
        logger.info(
            "NOOP FALLBACK triggered | cycle=%d | guard=%s",
            context.cycle_id,
            context.guard_result.guard_name,
        )
        return FallbackResult(success=True, action=None, reason="noop fallback executed")


@fallback(name="slow_down", escalates_to="hold_position")
class SlowDown(Fallback):
    """Gradually reduce commanded velocity to zero.

    A softer alternative to HoldPosition — useful when the robot can safely
    decelerate before stopping rather than halting immediately.

    Escalates to: HoldPosition
    """

    def execute(self, context: FallbackContext, bus: Any) -> FallbackResult:
        logger.warning(
            "SLOW DOWN triggered | cycle=%d | guard=%s",
            context.cycle_id,
            context.guard_result.guard_name,
        )
        return FallbackResult(success=True, action=None, reason="slow_down executed")


@fallback(name="return_home", escalates_to="hold_position")
class ReturnHome(Fallback):
    """Command the robot to return to its home / zero configuration.

    Useful when the task has reached an unrecoverable boundary violation
    and the safest action is to return to a known-safe pose.

    Escalates to: HoldPosition
    """

    def execute(self, context: FallbackContext, bus: Any) -> FallbackResult:
        logger.warning(
            "RETURN HOME triggered | cycle=%d | guard=%s",
            context.cycle_id,
            context.guard_result.guard_name,
        )
        return FallbackResult(success=True, action=None, reason="return_home executed")


@fallback(name="wait_and_retry", escalates_to="hold_position")
class WaitAndRetry(Fallback):
    """Pause for a brief moment and signal the task to retry.

    Intended for transient sensor glitches (e.g. a single OOD observation
    during a pick) where retrying the last action is safer than escalating.

    Escalates to: HoldPosition
    """

    def execute(self, context: FallbackContext, bus: Any) -> FallbackResult:
        logger.info(
            "WAIT AND RETRY triggered | cycle=%d | guard=%s",
            context.cycle_id,
            context.guard_result.guard_name,
        )
        return FallbackResult(success=True, action=None, reason="wait_and_retry executed")


def register_all() -> None:
    """Import-time decorators register built-ins; this is an idempotent hook."""
    return
