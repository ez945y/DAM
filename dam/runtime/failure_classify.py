"""Shared safety-event taxonomy.

The DAM event taxonomy has exactly three categories. Definitions are by guard
*layer* and *fault source* only — no guard-name string heuristics — so the
classification matches the published specification and stays stable when guards
are renamed.

    ood_only            感知異常事件 — only L0 perception-anomaly guards fired
                        (and nothing else).
    guard_triggered     動作風險事件 — any non-hardware safety guard rejected,
                        limited, or fault-arbitrated an action.
    hardware_triggered  硬體風險事件 — any L3 safety guard, or any guard whose
                        fault source is hardware, fired (highest priority).

``host_health`` is an L3 boundary that reports ``fault_source="hardware"``, so a
host CPU/GPU/temperature/memory breach is a硬體風險事件 automatically.
"""

from __future__ import annotations

from collections.abc import Sequence

from dam.types.result import GuardDecision, GuardResult

#: Decisions that make a guard result part of a failure event.
FAILURE_DECISIONS = (GuardDecision.REJECT, GuardDecision.FAULT, GuardDecision.CLAMP)

OOD_ONLY = "ood_only"
GUARD_TRIGGERED = "guard_triggered"
HARDWARE_TRIGGERED = "hardware_triggered"


def select_failure_results(guard_results: Sequence[GuardResult]) -> list[GuardResult]:
    """Return the guard results that constitute a failure (REJECT/FAULT/CLAMP)."""
    return [r for r in guard_results if r.decision in FAILURE_DECISIONS]


def _layer_int(result: GuardResult) -> int:
    """Layer index, tolerant of enum / int / ``"L3"`` / ``"3"`` representations."""
    layer = result.layer
    try:
        return int(layer)  # GuardLayer (IntEnum) and plain ints
    except (TypeError, ValueError):
        return int(str(layer).lstrip("Ll"))


def _is_hardware_risk(result: GuardResult) -> bool:
    return _layer_int(result) == 3 or result.fault_source == "hardware"


def _is_perception(result: GuardResult) -> bool:
    return _layer_int(result) == 0


def classify_failure(failure_results: Sequence[GuardResult]) -> str | None:
    """Classify failing guard results into the DAM event taxonomy.

    ``failure_results`` must already be filtered to failing decisions (see
    :func:`select_failure_results`). Returns ``None`` when empty.
    """
    if not failure_results:
        return None
    if any(_is_hardware_risk(r) for r in failure_results):
        return HARDWARE_TRIGGERED
    if all(_is_perception(r) for r in failure_results):
        return OOD_ONLY
    return GUARD_TRIGGERED
