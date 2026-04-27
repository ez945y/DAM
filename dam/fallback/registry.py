from __future__ import annotations

import logging
from typing import Any

from dam.fallback.base import Fallback, FallbackContext, FallbackResult

logger = logging.getLogger(__name__)
MAX_ESCALATION_DEPTH = 10


class FallbackRegistry:
    def __init__(self) -> None:
        self._strategies: dict[str, Fallback] = {}

    def register(self, strategy: Fallback) -> None:
        name = strategy.get_name()
        if name in self._strategies:
            raise ValueError(f"Fallback strategy '{name}' is already registered")
        self._strategies[name] = strategy

    def get(self, name: str) -> Fallback:
        if name not in self._strategies:
            regs = sorted(self._strategies.keys())
            raise ValueError(f"Fallback strategy '{name}' not found. Registered: {regs}")
        return self._strategies[name]

    def list_all(self) -> list[str]:
        return sorted(self._strategies.keys())

    def execute_with_escalation(
        self, name: str, context: FallbackContext, bus: Any
    ) -> FallbackResult:
        strategy = self.get(name)
        depth = 0
        while strategy is not None and depth < MAX_ESCALATION_DEPTH:
            try:
                result = strategy.execute(context, bus)
                if result.success:
                    return result
                # Not successful — escalate
                logger.warning("Fallback '%s' failed, escalating", strategy.get_name())
            except Exception as e:
                logger.error(
                    "Fallback '%s' raised exception: %s, escalating", strategy.get_name(), e
                )

            escalation_target = strategy._escalation_target_obj
            if escalation_target is None:
                # Already at terminal — return failure
                return FallbackResult(
                    success=False,
                    action=None,
                    reason=f"terminal fallback {strategy.get_name()} failed",
                )
            strategy = escalation_target
            depth += 1

        # Force emergency_stop as last resort
        if "emergency_stop" in self._strategies:
            try:
                return self._strategies["emergency_stop"].execute(context, bus)
            except Exception:  # noqa: BLE001 — swallow; caller handles the failure result below
                pass
        return FallbackResult(success=False, action=None, reason="all fallbacks failed")


# Module-level singleton instance
_registry = FallbackRegistry()


def get_global_registry() -> FallbackRegistry:
    return _registry
