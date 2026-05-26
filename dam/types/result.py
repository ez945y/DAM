from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dam.guard.layer import GuardLayer
    from dam.types.action import ValidatedAction


class GuardDecision(IntEnum):
    PASS = 0
    CLAMP = 1
    REJECT = 2
    FAULT = 3


@dataclass(frozen=True)
class GuardResult:
    decision: GuardDecision
    guard_name: str
    layer: GuardLayer
    reason: str = ""
    clamped_action: ValidatedAction | None = None
    fault_source: str | None = None  # "environment", "guard_code", "timeout"
    metadata: dict[str, Any] = field(default_factory=dict)
    event_class: str | None = None  # None → derived from layer at MCAP write time

    def resolved_event_class(self) -> str:
        """event_class explicit if set, else layer default (perception/motion/task/hardware)."""
        if self.event_class is not None:
            return self.event_class
        from dam.guard.layer import LAYER_TO_EVENT_CLASS

        return LAYER_TO_EVENT_CLASS[self.layer]

    @classmethod
    def success(
        cls,
        guard_name: str,
        layer: GuardLayer,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> GuardResult:
        return cls(
            decision=GuardDecision.PASS,
            guard_name=guard_name,
            layer=layer,
            reason=reason,
            metadata=metadata or {},
        )

    @classmethod
    def reject(cls, guard_name: str, layer: GuardLayer, reason: str = "") -> GuardResult:
        return cls(decision=GuardDecision.REJECT, guard_name=guard_name, layer=layer, reason=reason)

    @classmethod
    def clamp(
        cls, clamped_action: ValidatedAction, guard_name: str, layer: GuardLayer, reason: str = ""
    ) -> GuardResult:
        return cls(
            decision=GuardDecision.CLAMP,
            guard_name=guard_name,
            layer=layer,
            reason=reason,
            clamped_action=clamped_action,
        )

    @classmethod
    def fault(cls, exc: Exception, source: str, guard_name: str, layer: GuardLayer) -> GuardResult:
        return cls(
            decision=GuardDecision.FAULT,
            guard_name=guard_name,
            layer=layer,
            reason=str(exc),
            fault_source=source,
        )
