"""A detached, serializable description of one SDK evaluation."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from enum import Enum
from typing import Any

import numpy as np

from dam.types.result import GuardDecision, GuardResult


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.name
    if hasattr(value, "detach"):
        return _json_value(value.detach().cpu().double().numpy())
    if isinstance(value, np.ndarray):
        return _json_value(value.tolist())
    if isinstance(value, np.generic):
        return _json_value(value.item())
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return repr(value)


@dataclass(frozen=True)
class GuardrailEvaluation:
    """Decision evidence and the returned command; no sink dispatch is implied.

    ``verdict_age_ms`` is the oldest slow verdict actually consumed by this
    call, or None when no aged verdict was used. Individual basis-cycle IDs,
    versions and ages remain in each guard result's metadata.
    """

    command: Any
    decision: GuardDecision
    guard_results: tuple[GuardResult, ...]
    fallback: str | None
    config_version: int
    task: str
    cycle_id: int
    verdict_age_ms: float | None
    output_status: str = "returned"

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": _json_value(self.command),
            "decision": self.decision.name,
            "fallback": self.fallback,
            "config_version": self.config_version,
            "task": self.task,
            "cycle_id": self.cycle_id,
            "verdict_age_ms": self.verdict_age_ms,
            "output_status": self.output_status,
            "guard_results": [
                {
                    "decision": r.decision.name,
                    "guard_name": r.guard_name,
                    "layer": r.layer.name,
                    "reason": r.reason,
                    "fault_source": r.fault_source,
                    "metadata": _json_value(r.metadata),
                }
                for r in self.guard_results
            ],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, allow_nan=False)
