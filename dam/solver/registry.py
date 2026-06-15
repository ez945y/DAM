from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

# A factory declares the params it needs as keyword arguments; the registry
# injects matching values from the stackfile params + robot context.
SolverFactory = Callable[..., Any]


@dataclass(frozen=True)
class SolverEntry:
    name: str
    solver: Any
    capabilities: tuple[str, ...]
    solver_type: str | None = None


class SolverRegistry:
    """Runtime registry for solver instances and config-driven factories.

    Solvers are embodiment-specific adapters: arm FK/IK, USD articulation
    kinematics, differential-drive rollout, Ackermann constraints, and so on.
    The registry only tracks names and capabilities; solver objects define
    their own concrete methods.
    """

    def __init__(self) -> None:
        self._instances: dict[str, SolverEntry] = {}
        self._factories: dict[str, tuple[SolverFactory, tuple[str, ...]]] = {}

    def register(
        self,
        name: str,
        solver: Any,
        *,
        capabilities: tuple[str, ...] | list[str],
        solver_type: str | None = None,
        replace: bool = False,
    ) -> Any:
        key = _normalize_name(name)
        if not key:
            raise ValueError("Solver name must not be empty")
        if key in self._instances and not replace:
            raise ValueError(f"Solver '{name}' is already registered")
        caps = _normalize_capabilities(capabilities)
        _stamp_solver(solver, key, caps, solver_type)
        self._instances[key] = SolverEntry(
            name=key,
            solver=solver,
            capabilities=caps,
            solver_type=solver_type,
        )
        return solver

    def register_factory(
        self,
        solver_type: str,
        factory: SolverFactory,
        *,
        capabilities: tuple[str, ...] | list[str],
        replace: bool = False,
    ) -> SolverFactory:
        key = _normalize_name(solver_type)
        if not key:
            raise ValueError("Solver type must not be empty")
        if key in self._factories and not replace:
            raise ValueError(f"Solver factory '{solver_type}' is already registered")
        self._factories[key] = (factory, _normalize_capabilities(capabilities))
        return factory

    def build(
        self,
        name: str,
        solver_type: str,
        params: Mapping[str, Any] | None = None,
        *,
        capabilities: tuple[str, ...] | list[str] | None = None,
    ) -> Any:
        type_key = _normalize_name(solver_type)
        if type_key not in self._factories:
            raise KeyError(
                f"Solver factory '{solver_type}' not found. Registered: {sorted(self._factories)}"
            )
        factory, default_caps = self._factories[type_key]
        solver = _invoke_factory(factory, dict(params or {}))
        self.register(
            name,
            solver,
            capabilities=capabilities or default_caps,
            solver_type=type_key,
            replace=True,
        )
        return solver

    def get(self, name: str) -> Any:
        key = _normalize_name(name)
        if key not in self._instances:
            raise KeyError(f"Solver '{name}' not found. Registered: {sorted(self._instances)}")
        return self._instances[key].solver

    def select(self, capability: str) -> Any | None:
        cap = _normalize_name(capability)
        for entry in self._instances.values():
            if cap in entry.capabilities:
                return entry.solver
        return None

    def list_all(self) -> list[str]:
        return sorted(self._instances)

    def entries(self) -> dict[str, SolverEntry]:
        return dict(self._instances)


def _invoke_factory(factory: SolverFactory, params: dict[str, Any]) -> Any:
    """Call a solver factory, injecting only the keyword params it declares.

    The runtime hands every factory the same flat dict (user ``params`` merged
    with robot context like ``asset_path`` / ``observation_joint_names``). A
    factory lists what it wants by name; anything it does not declare is
    dropped, so factories stay free of ``params.get()`` and never receive keys
    they did not ask for. A factory with ``**kwargs`` receives everything.
    """
    try:
        sig = inspect.signature(factory)
    except (TypeError, ValueError):
        # Builtins / C callables without an introspectable signature: pass all.
        return factory(**params)
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
        return factory(**params)
    accepted = {
        p.name
        for p in sig.parameters.values()
        if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }
    return factory(**{k: v for k, v in params.items() if k in accepted})


def _normalize_name(value: str) -> str:
    return str(value).strip().lower().replace("-", "_")


def _normalize_capabilities(capabilities: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    caps = tuple(_normalize_name(c) for c in capabilities if str(c).strip())
    if not caps:
        raise ValueError("Solver must declare at least one capability")
    return caps


def _stamp_solver(
    solver: Any,
    name: str,
    capabilities: tuple[str, ...],
    solver_type: str | None,
) -> None:
    try:
        solver._dam_solver_name = name
        solver._dam_solver_capabilities = capabilities
        solver._dam_solver_type = solver_type
    except Exception:  # noqa: BLE001 - some extension objects disallow attrs
        pass


_registry = SolverRegistry()


def get_global_solver_registry() -> SolverRegistry:
    return _registry
