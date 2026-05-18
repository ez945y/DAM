from __future__ import annotations

import logging
from typing import Any

from dam.fallback.base import Fallback, FallbackContext, FallbackResult

logger = logging.getLogger(__name__)
MAX_ESCALATION_DEPTH = 10


class FallbackRegistry:
    def __init__(self) -> None:
        self._strategies: dict[str, Fallback] = {}
        self._templates: dict[str, type[Fallback]] = {}

    def register(self, strategy: Fallback) -> None:
        name = strategy.get_name()
        if name in self._strategies:
            raise ValueError(f"Fallback strategy '{name}' is already registered")
        self._strategies[name] = strategy
        self._templates[strategy.get_type()] = strategy.__class__

    def register_template(self, name: str, cls: type[Fallback]) -> None:
        if name in self._templates and self._templates[name] is not cls:
            raise ValueError(f"Fallback template '{name}' is already registered")
        self._templates[name] = cls

    def configure(
        self,
        *,
        name: str,
        type_name: str,
        params: dict[str, Any] | None = None,
        escalates_to: str | None = None,
    ) -> None:
        if name in self._strategies:
            raise ValueError(f"Fallback strategy '{name}' is already registered")
        if type_name not in self._templates:
            raise ValueError(
                f"Fallback template '{type_name}' not found. Registered: {sorted(self._templates)}"
            )
        strategy = self._templates[type_name]()
        strategy._fallback_name = name
        strategy._fallback_type = type_name
        strategy._params = dict(params or {})
        strategy._escalates_to = escalates_to
        self._strategies[name] = strategy

    def configured_copy(self, configs: dict[str, Any] | None = None) -> FallbackRegistry:
        reg = FallbackRegistry()
        reg._templates = dict(self._templates)
        if configs:
            for name, cfg in configs.items():
                type_name = cfg.get("type") if isinstance(cfg, dict) else cfg.type
                if not isinstance(type_name, str):
                    raise ValueError(f"Fallback '{name}' must define a string type")
                params = cfg.get("params", {}) if isinstance(cfg, dict) else cfg.params
                escalates_to = (
                    cfg.get("escalates_to") if isinstance(cfg, dict) else cfg.escalates_to
                )
                reg.configure(
                    name=name,
                    type_name=type_name,
                    params=params,
                    escalates_to=escalates_to,
                )
        else:
            for type_name, cls in self._templates.items():
                strategy = cls()
                strategy._fallback_name = type_name
                strategy._fallback_type = type_name
                strategy._params = {}
                reg.register(strategy)
        return reg

    def get(self, name: str) -> Fallback:
        if name not in self._strategies:
            regs = sorted(self._strategies.keys())
            raise ValueError(f"Fallback strategy '{name}' not found. Registered: {regs}")
        return self._strategies[name]

    def list_all(self) -> list[str]:
        return sorted(self._strategies.keys())

    def list_templates(self) -> list[str]:
        return sorted(self._templates.keys())

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
