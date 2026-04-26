import numpy as np
import pytest

from dam.guard.layer import GuardLayer
from dam.types.action import ActionProposal, ValidatedAction
from dam.types.observation import Observation
from dam.types.result import GuardDecision, GuardResult
from dam.types.risk import RiskLevel


def test_guard_decision_ordering():
    assert GuardDecision.PASS < GuardDecision.CLAMP
    assert GuardDecision.CLAMP < GuardDecision.REJECT
    assert GuardDecision.REJECT < GuardDecision.FAULT


def test_observation_frozen():
    obs = Observation(
        timestamp=1.0,
        joint_positions=np.zeros(6),
        joint_velocities=np.zeros(6),
        end_effector_pose=np.zeros(7),
    )
    from dataclasses import FrozenInstanceError

    with pytest.raises(FrozenInstanceError):
        obs.timestamp = 2.0  # type: ignore


def test_observation_defensive_copy():
    arr = np.zeros(6)
    obs = Observation(
        timestamp=1.0,
        joint_positions=arr,
        joint_velocities=np.zeros(6),
        end_effector_pose=np.zeros(7),
    )
    arr[0] = 99.0
    assert obs.joint_positions[0] == pytest.approx(0.0)  # Not affected by mutation


def test_action_proposal_confidence_validation():
    with pytest.raises(ValueError):
        ActionProposal(
            target_joint_positions=np.zeros(6),
            confidence=1.5,
        )


def test_guard_result_factory_pass():
    r = GuardResult.success(guard_name="test", layer=GuardLayer.L2)
    assert r.decision == GuardDecision.PASS
    assert r.clamped_action is None
    assert r.fault_source is None


def test_guard_result_factory_reject():
    r = GuardResult.reject(reason="out of bounds", guard_name="test", layer=GuardLayer.L2)
    assert r.decision == GuardDecision.REJECT
    assert "out of bounds" in r.reason


def test_guard_result_factory_clamp():
    va = ValidatedAction(target_joint_positions=np.zeros(6), was_clamped=True)
    r = GuardResult.clamp(clamped_action=va, guard_name="test", layer=GuardLayer.L2)
    assert r.decision == GuardDecision.CLAMP
    assert r.clamped_action is va


def test_guard_result_factory_fault():
    exc = ValueError("something went wrong")
    r = GuardResult.fault(exc=exc, source="guard_code", guard_name="test", layer=GuardLayer.L2)
    assert r.decision == GuardDecision.FAULT
    assert "something went wrong" in r.reason
    assert r.fault_source == "guard_code"


def test_risk_level_ordering():
    assert RiskLevel.NORMAL < RiskLevel.ELEVATED
    assert RiskLevel.ELEVATED < RiskLevel.CRITICAL
    assert RiskLevel.CRITICAL < RiskLevel.EMERGENCY
