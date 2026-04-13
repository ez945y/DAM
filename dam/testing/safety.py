from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dam.types.result import GuardDecision


@dataclass
class SafetyScenario:
    name: str
    guard_instance: Any
    config_pool: dict[str, Any]
    runtime_kwargs: dict[str, Any]
    expected: GuardDecision


def safety_regression(scenarios: list[SafetyScenario]) -> None:
    from dam.testing.helpers import inject_and_call

    failures: list[str] = []
    for scenario in scenarios:
        try:
            result = inject_and_call(
                scenario.guard_instance,
                scenario.config_pool,
                **scenario.runtime_kwargs,
            )
            if result.decision != scenario.expected:
                failures.append(
                    f"[{scenario.name}] expected {scenario.expected.name}, "
                    f"got {result.decision.name} (reason: {result.reason})"
                )
        except Exception as e:
            failures.append(f"[{scenario.name}] raised exception: {e}")
    if failures:
        raise AssertionError("Safety regression failures:\n" + "\n".join(failures))
