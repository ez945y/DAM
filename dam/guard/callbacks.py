"""Legacy boundary-callback dispatch shim used by ExecutionGuard / HardwareGuard.

The actual logic lives in :mod:`dam.guard.pipeline` (``run_callbacks`` +
``aggregate``).  This shim adapts the old ``(saw_callback, GuardResult|None)``
contract to the new pipeline so the existing guard implementations don't have
to change at the same time as the pipeline is introduced.

New code (and the L1 MotionGuard refactor) should call the pipeline directly.
"""

from __future__ import annotations

import logging
from typing import Any

from dam.guard.pipeline import CallbackResult, run_callbacks
from dam.types.result import GuardDecision, GuardResult

logger = logging.getLogger(__name__)


def evaluate_boundary_callbacks(
    *,
    containers: list[Any] | None,
    base_kwargs: dict[str, Any],
    expected_layer: str | None,
    guard_name: str,
    guard_layer: Any,
    violation_decision: GuardDecision,
    fault_source: str,
) -> tuple[bool, GuardResult | None]:
    """Backwards-compatible wrapper around the unified pipeline.

    Returns ``(saw_callback, result)``. ``result`` is set only when a callback
    violates / faults / clamps so callers can fall through to native logic when
    every callback PASSed.  This preserves the historical contract used by
    ExecutionGuard and HardwareGuard.
    """
    results = run_callbacks(
        containers=containers,
        runtime_pool=base_kwargs,
        expected_layer=expected_layer,
    )
    if not results:
        return False, None

    # Decision priority mirrors aggregate(): FAULT → REJECT/violation → CLAMP →
    # PASS.  We surface the *first* non-PASS result to preserve legacy
    # behaviour (callers expected early-exit on first failure).
    for r in results:
        if r.decision == GuardDecision.FAULT:
            return True, GuardResult(
                decision=GuardDecision.FAULT,
                guard_name=guard_name,
                layer=guard_layer,
                reason=r.reason,
                fault_source=r.fault_source or fault_source,
                metadata=dict(r.metadata),
            )

    for r in results:
        if r.decision == GuardDecision.REJECT:
            # The legacy caller chooses whether REJECT semantics map to
            # GuardDecision.REJECT or GuardDecision.FAULT (Hardware does the
            # latter); honour that via ``violation_decision``.
            return True, GuardResult(
                decision=violation_decision,
                guard_name=guard_name,
                layer=guard_layer,
                reason=r.reason,
                fault_source=fault_source if violation_decision == GuardDecision.FAULT else None,
                metadata=dict(r.metadata),
            )

    for r in results:
        if r.decision == GuardDecision.CLAMP:
            return True, GuardResult(
                decision=GuardDecision.CLAMP,
                guard_name=guard_name,
                layer=guard_layer,
                reason=r.reason,
                clamped_action=r.clamped_action,
                metadata=dict(r.metadata),
            )

    # All PASS — merge metadata from every callback so telemetry
    # (e.g. temperature/voltage/current readings) reaches the inspector.
    merged_meta: dict[str, Any] = {}
    for r in results:
        if r.metadata:
            merged_meta.update(r.metadata)
    if merged_meta:
        return True, GuardResult(
            decision=GuardDecision.PASS,
            guard_name=guard_name,
            layer=guard_layer,
            metadata=merged_meta,
        )
    return True, None


# Re-export for convenience so callers don't have to import from two places.
__all__ = ["CallbackResult", "evaluate_boundary_callbacks", "run_callbacks"]
