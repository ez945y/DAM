from __future__ import annotations

from typing import Any

from dam.guard.base import Guard
from dam.injection.static import precompute_injection
from dam.types.result import GuardDecision, GuardResult


def inject_and_call(
    guard_instance: Guard, config_pool: dict[str, Any], **runtime_kwargs: Any
) -> GuardResult:
    """Initialize a guard with config and call it with runtime kwargs."""
    precompute_injection(guard_instance, config_pool)
    runtime_pool = dict(runtime_kwargs)
    kwargs = dict(guard_instance._static_kwargs)
    kwargs.update({k: runtime_pool[k] for k in guard_instance._runtime_keys if k in runtime_pool})
    # For direct testing, also pass through any explicitly provided kwargs
    for k, v in runtime_kwargs.items():
        if k not in kwargs and k in guard_instance.__class__._cached_param_names:
            kwargs[k] = v
    return guard_instance.check(**kwargs)


def assert_rejects(result: GuardResult) -> None:
    assert result.decision == GuardDecision.REJECT, (
        f"Expected REJECT, got {result.decision.name}. Reason: {result.reason}"
    )


def assert_clamps(result: GuardResult) -> None:
    assert result.decision == GuardDecision.CLAMP, (
        f"Expected CLAMP, got {result.decision.name}. Reason: {result.reason}"
    )


def assert_passes(result: GuardResult) -> None:
    assert result.decision == GuardDecision.PASS, (
        f"Expected PASS, got {result.decision.name}. Reason: {result.reason}"
    )
