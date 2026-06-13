"""First-class solver registry for kinematics, dynamics, and embodiment logic."""

from dam.solver.registry import (
    SolverFactory,
    SolverRegistry,
    get_global_solver_registry,
)

__all__ = [
    "SolverFactory",
    "SolverRegistry",
    "get_global_solver_registry",
]
