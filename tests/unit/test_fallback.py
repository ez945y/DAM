import numpy as np
import pytest

from dam.boundary.constraint import BoundaryConstraint
from dam.boundary.node import BoundaryNode
from dam.fallback.base import FallbackContext
from dam.fallback.builtin import EmergencyStop, HoldPosition, SafeRetreat
from dam.fallback.chain import build_escalation_chain
from dam.fallback.registry import FallbackRegistry
from dam.guard.layer import GuardLayer
from dam.types.action import ActionProposal
from dam.types.result import GuardResult


def make_context() -> FallbackContext:
    return FallbackContext(
        rejected_proposal=ActionProposal(target_joint_positions=np.zeros(6)),
        guard_result=GuardResult.reject("test", "test_guard", GuardLayer.L2),
        current_node=BoundaryNode("n0", BoundaryConstraint()),
        cycle_id=0,
    )


def test_fallback_registry_register_and_get():
    reg = FallbackRegistry()
    reg.register(EmergencyStop())
    strat = reg.get("emergency_stop")
    assert strat is not None


def test_fallback_registry_duplicate_raises():
    reg = FallbackRegistry()
    reg.register(EmergencyStop())
    with pytest.raises(ValueError, match="already registered"):
        reg.register(EmergencyStop())


def test_fallback_registry_unknown_raises():
    reg = FallbackRegistry()
    with pytest.raises(ValueError, match="not found"):
        reg.get("nonexistent")


def test_emergency_stop_is_terminal():
    s = EmergencyStop()
    assert s.get_escalation_target() is None


def test_escalation_chain():
    reg = FallbackRegistry()
    reg.register(EmergencyStop())
    reg.register(HoldPosition())
    reg.register(SafeRetreat())
    build_escalation_chain(reg)
    retreat = reg.get("safe_retreat")
    hold = reg.get("hold_position")
    estop = reg.get("emergency_stop")
    assert retreat._escalation_target_obj is hold
    assert hold._escalation_target_obj is estop
    assert estop._escalation_target_obj is None


def test_execute_with_escalation_success():
    reg = FallbackRegistry()
    reg.register(EmergencyStop())
    reg.register(HoldPosition())
    build_escalation_chain(reg)
    ctx = make_context()
    result = reg.execute_with_escalation("hold_position", ctx, bus=None)
    assert result.success


def test_fallback_terminal_never_escalates():
    reg = FallbackRegistry()
    reg.register(EmergencyStop())
    build_escalation_chain(reg)
    ctx = make_context()
    result = reg.execute_with_escalation("emergency_stop", ctx, bus=None)
    assert result.success
