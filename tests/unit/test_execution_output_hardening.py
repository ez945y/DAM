"""Regression checks for guard fusion through the execution boundary."""

from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from dam.bus import PipelineMetricBus
from dam.guard.layer import GuardLayer
from dam.runtime.execution_engine import EnforcementMode, ExecutionEngine
from dam.types.action import ActionProposal, ValidatedAction
from dam.types.observation import Observation
from dam.types.result import GuardDecision, GuardResult


def _clamp(action):
    return GuardResult.clamp(action, "custom", GuardLayer.L1)


@pytest.mark.parametrize("payload", [None, ValidatedAction(np.zeros(1))])
def test_invalid_custom_clamp_reaches_fallback_results(monkeypatch, payload):
    engine = ExecutionEngine(
        enforcement_mode=EnforcementMode.ENFORCE, metric_bus=PipelineMetricBus()
    )
    context = SimpleNamespace(stages=[], guards=[], risk_controller=Mock())
    monkeypatch.setattr(engine, "_build_runtime_pool", lambda *args: {})
    monkeypatch.setattr(engine, "_run_staged", lambda *args: [_clamp(payload)])
    output, results = engine.validate(
        Observation(timestamp=0, joint_positions=np.zeros(2)),
        ActionProposal(np.zeros(2)),
        "trace",
        context,
        now=0,
    )
    assert output is None
    assert any(result.decision == GuardDecision.FAULT for result in results)
    context.risk_controller.record.assert_called_once_with(was_clamped=False, was_rejected=True)


def test_conflicting_phase_clamps_do_not_raise_or_replace_action():
    proposal = ActionProposal(np.zeros(1))
    pool = {"action": proposal}
    results = [
        _clamp(ValidatedAction(np.array([value]), original_proposal=proposal))
        for value in [-1.0, 1.0]
    ]
    ExecutionEngine._accumulate_phase_clamps(results, pool)
    assert pool["action"] is proposal
    assert results[-1].decision == GuardDecision.FAULT


def test_phase_clamp_shape_is_checked_before_downstream_guards():
    proposal = ActionProposal(np.zeros(2))
    pool = {"action": proposal}
    results = [_clamp(ValidatedAction(np.zeros(1)))]
    ExecutionEngine._accumulate_phase_clamps(results, pool)
    assert pool["action"] is proposal
    assert results[-1].decision == GuardDecision.FAULT


def test_pass_preserves_gripper_and_timestamp():
    engine = ExecutionEngine(
        enforcement_mode=EnforcementMode.ENFORCE, metric_bus=PipelineMetricBus()
    )
    result = engine._make_validated_action(
        ActionProposal(np.zeros(2), timestamp=12.0, gripper_action=0.3)
    )
    assert result.gripper_action == 0.3
    assert result.timestamp == 12.0


@pytest.mark.parametrize("reverse", [False, True])
def test_merge_preserves_secondary_velocity_and_gripper(reverse):
    first = ValidatedAction(np.zeros(2))
    second = ValidatedAction(np.zeros(2), target_joint_velocities=np.ones(2), gripper_action=0.2)
    if reverse:
        first, second = second, first
    merged = first.merge_restrictive(second)
    np.testing.assert_array_equal(merged.target_joint_velocities, np.ones(2))
    assert merged.gripper_action == 0.2


@pytest.mark.parametrize(
    "field, first, second",
    [
        ("target_joint_velocities", np.zeros(2), np.ones(2)),
        ("gripper_action", 0.2, 0.7),
    ],
)
def test_conflicting_secondary_commands_cannot_be_silently_dropped(field, first, second):
    with pytest.raises(ValueError, match="conflicting"):
        ValidatedAction(np.zeros(2), **{field: first}).merge_restrictive(
            ValidatedAction(np.zeros(2), **{field: second})
        )


def test_tiny_opposite_corrections_are_not_treated_as_unchanged():
    proposal = ActionProposal(np.ones(1))
    first = ValidatedAction(np.array([1.0 - 1e-7]), original_proposal=proposal)
    second = ValidatedAction(np.array([1.0 + 1e-7]), original_proposal=proposal)
    with pytest.raises(ValueError, match="opposite directions"):
        first.merge_restrictive(second)
