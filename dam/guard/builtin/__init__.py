from dam.guard.builtin.execution import ExecutionGuard
from dam.guard.builtin.hardware import HardwareGuard
from dam.guard.builtin.motion import MotionGuard
from dam.guard.builtin.ood import OODGuard
from dam.registry.guard import get_guard_registry


def register_all() -> None:
    reg = get_guard_registry()
    reg.register("ood", OODGuard, layer="L0")
    reg.register(
        "preflight",
        ExecutionGuard,
        layer="L1",
        description="Validates trajectories before execution",
    )
    reg.register("motion", MotionGuard, layer="L2")
    reg.register("execution", ExecutionGuard, layer="L3")
    reg.register("hardware", HardwareGuard, layer="L4")


__all__ = ["MotionGuard", "OODGuard", "ExecutionGuard", "HardwareGuard", "register_all"]
