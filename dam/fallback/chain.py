from __future__ import annotations

from dam.fallback.registry import FallbackRegistry


def build_escalation_chain(registry: FallbackRegistry) -> None:
    """Resolve escalation target strings to object pointers at startup."""
    strategies = {name: registry.get(name) for name in registry.list_all()}

    for name in strategies:
        strategy = strategies[name]
        target_name = strategy.get_escalation_target()
        if target_name is None:
            strategy._escalation_target_obj = None
        elif target_name in strategies:
            strategy._escalation_target_obj = strategies[target_name]
        else:
            raise ValueError(
                f"Fallback '{name}' escalates to '{target_name}', which is not registered"
            )

    # Cycle detection: follow each chain, ensure it terminates at a terminal node
    for start_name in strategies:
        visited: set[str] = set()
        current: str | None = start_name
        while current is not None:
            if current in visited:
                raise ValueError(
                    f"Escalation chain starting at '{start_name}' contains a cycle at '{current}'"
                )
            visited.add(current)
            strat = strategies[current]
            current = strat.get_escalation_target()
