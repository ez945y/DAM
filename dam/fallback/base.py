from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dam.boundary.node import BoundaryNode
    from dam.types.action import ActionProposal, ValidatedAction
    from dam.types.result import GuardResult


@dataclass(frozen=True)
class FallbackContext:
    rejected_proposal: ActionProposal
    guard_result: GuardResult
    current_node: BoundaryNode
    cycle_id: int


@dataclass(frozen=True)
class FallbackResult:
    success: bool
    action: ValidatedAction | None
    reason: str


class Fallback(ABC):
    _fallback_name: str
    _escalates_to: str | None
    _escalation_target_obj: Fallback | None = None  # Set at startup

    @abstractmethod
    def execute(self, context: FallbackContext, bus: Any) -> FallbackResult: ...

    def get_name(self) -> str:
        return self.__class__._fallback_name

    def get_escalation_target(self) -> str | None:
        return self.__class__._escalates_to

    def get_description(self) -> str:
        """Human-readable description of what this fallback does.

        Override in subclasses for richer diagnostic output. The default
        implementation returns the class name.
        """
        return self.__class__.__name__
