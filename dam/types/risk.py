from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dam.types.action import ActionProposal, ValidatedAction
    from dam.types.result import GuardResult


class RiskLevel(IntEnum):
    NORMAL = 0
    ELEVATED = 1
    CRITICAL = 2
    EMERGENCY = 3


@dataclass
class CycleResult:
    cycle_id: int
    trace_id: str
    validated_action: ValidatedAction | None
    original_proposal: ActionProposal
    was_clamped: bool
    was_rejected: bool
    guard_results: list[GuardResult] = field(default_factory=list)
    fallback_triggered: str | None = None
    latency_ms: dict[str, float] = field(default_factory=dict)
    risk_level: RiskLevel = RiskLevel.NORMAL
    active_task: str | None = None
    active_boundaries: list[str] = field(default_factory=list)
    mcap_filename: str | None = None
