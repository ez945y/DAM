"""Shared boundary-callback execution helpers for guards."""

from __future__ import annotations

import dataclasses
import inspect
import logging
from typing import Any

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
    """Run active boundary callbacks with node.params injected.

    Returns ``(saw_callback, result)``. ``result`` is only set when a callback
    violates or faults; callers can continue with their native guard logic when
    no callback is present.
    """
    if not containers:
        return False, None

    from dam.registry.callback import get_global_registry

    registry = get_global_registry()
    saw_callback = False
    for container in containers:
        node = container.get_active_node()
        constraint = node.constraint
        callback_name = constraint.callback if constraint else None
        if not callback_name:
            continue

        try:
            callback = registry.get(callback_name)
        except KeyError:
            logger.warning("Boundary callback '%s' is not registered", callback_name)
            continue

        if expected_layer is not None and getattr(callback, "_cb_layer", None) != expected_layer:
            continue

        saw_callback = True
        params = dict(constraint.params or {})
        callback_kwargs = {**base_kwargs, **params}

        try:
            raw_result = callback(**_filter_callback_kwargs(callback, callback_kwargs))
        except Exception as exc:
            return True, GuardResult.fault(exc, fault_source, guard_name, guard_layer)

        ok, reason, callback_result = _normalise_callback_result(raw_result)
        if ok:
            continue
        if callback_result is not None:
            return True, dataclasses.replace(
                callback_result,
                guard_name=guard_name,
                layer=guard_layer,
            )
        if not reason:
            detail = ", ".join(f"{k}={v}" for k, v in params.items())
            suffix = f" ({detail})" if detail else ""
            reason = f"callback '{callback_name}' returned False at node '{node.node_id}'{suffix}"
        return True, GuardResult(
            decision=violation_decision,
            guard_name=guard_name,
            layer=guard_layer,
            reason=reason,
            fault_source=fault_source if violation_decision == GuardDecision.FAULT else None,
        )

    return saw_callback, None


def _filter_callback_kwargs(callback: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    sig = inspect.signature(callback)
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
        return kwargs
    return {k: v for k, v in kwargs.items() if k in sig.parameters}


def _normalise_callback_result(result: Any) -> tuple[bool, str, GuardResult | None]:
    if isinstance(result, GuardResult):
        return result.decision == GuardDecision.PASS, result.reason, result
    if isinstance(result, tuple) and len(result) == 2:
        ok, reason = result
        return bool(ok), str(reason or ""), None
    return bool(result), "", None
