"""R04: invalid outputs never reach a sink, in any enforcement mode."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from dam.config.schema import StackfileConfig
from dam.runtime.builtin_contexts import HoldPositionContext
from dam.runtime.context import StepContext, StepResult
from dam.runtime.guard_runtime import GuardRuntime
from dam.testing.mocks import MockPolicyAdapter, MockSinkAdapter, MockSourceAdapter
from dam.types.action import ActionProposal, ValidatedAction
from dam.types.observation import Observation
from dam.types.result import GuardDecision


@pytest.fixture
def runtime(request):
    mode = getattr(request, "param", "enforce")
    config = StackfileConfig.model_validate(
        {
            "hardware": {"joint_names": ["a", "b"], "degrees_mode": False},
            "guards": [],
            "tasks": {"default": {"boundaries": []}},
            "safety": {"enforcement_mode": mode},
        }
    )
    r = GuardRuntime._from_config(config)
    r.start_task("default")
    r._sources = {
        "mock": MockSourceAdapter([Observation(timestamp=1, joint_positions=np.zeros(2))])
    }
    r._policy = MockPolicyAdapter([ActionProposal(np.ones(2))])
    r._sink = MockSinkAdapter()
    yield r
    r.shutdown()


INVALID = [
    {"target_joint_positions": np.array([np.nan, 0])},
    {"target_joint_positions": np.array([np.inf, 0])},
    {"target_joint_positions": np.zeros((1, 2))},
    {"target_joint_positions": np.zeros(3)},
    {"target_joint_positions": np.array([])},
    {"target_joint_velocities": np.array([0.0])},
    {"target_joint_velocities": np.array([np.inf, 0])},
    {"gripper_action": float("nan")},
    {"timestamp": float("inf")},
]


@pytest.mark.parametrize("runtime", ["enforce", "monitor", "log_only"], indirect=True)
@pytest.mark.parametrize("patch", INVALID)
def test_invalid_policy_never_reaches_sink(runtime, patch):
    runtime._policy = MockPolicyAdapter([replace(ActionProposal(np.zeros(2)), **patch)])
    result = runtime.step()
    assert runtime._sink.received == []
    assert result.validated_action is None
    assert result.was_rejected
    assert any(r.decision == GuardDecision.FAULT for r in result.guard_results)
    assert runtime._prev_validated_positions is None


@pytest.mark.parametrize("runtime", ["enforce", "monitor", "log_only"], indirect=True)
@pytest.mark.parametrize("patch", INVALID)
def test_custom_fallback_output_is_checked(runtime, patch):
    class Custom(StepContext):
        requires_proposal = False
        monitors_hardware = False

        def step(self, *args, **kwargs):
            return StepResult(replace(ValidatedAction(np.zeros(2)), **patch))

    runtime._ctx_sm._context_stack.append(Custom())
    result = runtime.step()
    assert result.validated_action is None
    assert runtime._sink.received == []
    assert any(r.decision == GuardDecision.FAULT for r in result.guard_results)


def test_sink_exception_is_recorded_without_committing_history(runtime):
    runtime._sink = SimpleNamespace(
        apply=Mock(side_effect=OSError("transport failed")), emergency_stop=Mock()
    )
    runtime._prev_validated_positions = [0.25, 0.25]
    result = runtime.step()
    assert result.validated_action is None
    assert result.was_rejected
    assert runtime._prev_validated_positions == [0.25, 0.25]
    assert any("transport failed" in r.reason for r in result.guard_results)


def test_context_exception_is_recorded(runtime):
    class Broken(StepContext):
        requires_proposal = False

        def step(self, *args, **kwargs):
            raise RuntimeError("fallback failed")

    runtime._ctx_sm._context_stack.append(Broken())
    result = runtime.step()
    assert result.validated_action is None
    assert runtime._sink.received == []
    assert any("fallback failed" in r.reason for r in result.guard_results)


def test_hold_missing_baseline_remains_explicitly_suppressed(runtime):
    runtime._push_context(HoldPositionContext(), trigger=None, event="enter")
    result = runtime.step()
    assert result.validated_action is None
    assert runtime._sink.received == []


@pytest.mark.parametrize("kind", ["hold", "retreat", "slow_down"])
def test_joint_fallback_rejects_non_joint_command_layout(runtime, kind):
    from dam.runtime.builtin_contexts import RetreatContext, SlowDownContext

    runtime._hot_reload.config_pool["action_layout"] = [{"keys": ["v", "omega"]}]
    runtime._prev_validated_positions = [1, 1]
    context = {
        "hold": HoldPositionContext(),
        "retreat": RetreatContext(target_positions=[0, 0]),
        "slow_down": SlowDownContext(),
    }[kind]
    with pytest.raises(ValueError, match="joint-position action layout"):
        context.on_enter(runtime)
    assert runtime._sink.received == []


@pytest.mark.parametrize(
    "previous,target",
    [(None, [0, 0]), ([1, 1], None), ([1, 1], [0]), ([1, 1], [float("nan"), 0])],
)
def test_retreat_invalid_prerequisites_fault_without_implicit_target(runtime, previous, target):
    from dam.guard.layer import GuardLayer
    from dam.runtime.builtin_contexts import RetreatContext
    from dam.types.result import GuardResult

    runtime._prev_validated_positions = previous
    runtime._engine.validate = Mock(
        return_value=(None, [GuardResult.reject("custom", GuardLayer.L1)])
    )
    runtime._ctx_sm.pick_context_for = Mock(return_value=RetreatContext(target_positions=target))
    result = runtime.step()
    assert result.output_status == "suppressed"
    assert runtime._sink.received == []
    assert runtime._active_context.name == "emergency_stop"
    assert any(r.fault_source == "fallback" for r in result.guard_results)


def test_valid_payload_preserves_optional_fields(runtime):
    runtime._policy = MockPolicyAdapter(
        [
            ActionProposal(
                np.ones(2), target_joint_velocities=np.zeros(2), timestamp=5, gripper_action=0.3
            )
        ]
    )
    result = runtime.step()
    sent = runtime._sink.received[0]
    assert sent is result.validated_action
    assert sent.timestamp == 5 and sent.gripper_action == 0.3
    np.testing.assert_equal(sent.target_joint_velocities, [0, 0])


def test_failed_emergency_stop_is_visible_and_terminal(runtime):
    runtime._policy = MockPolicyAdapter([ActionProposal(np.array([np.nan, 0]))])
    runtime._sink = SimpleNamespace(
        apply=Mock(), emergency_stop=Mock(side_effect=OSError("stop failed"))
    )
    result = runtime.step()
    assert result.validated_action is None
    runtime._sink.apply.assert_not_called()
    assert any("stop failed" in r.reason for r in result.guard_results)
    assert runtime._active_context.name == "emergency_stop"
    runtime._sink.emergency_stop.assert_called_once()


def test_policy_exception_is_a_cycle_fault(runtime):
    runtime._policy = SimpleNamespace(predict=Mock(side_effect=ValueError("policy output failed")))
    result = runtime.step()
    assert result.output_status == "suppressed"
    assert any(r.fault_source == "policy" for r in result.guard_results)
    assert runtime._sink.received == []


def test_command_layout_takes_priority_over_observation_joint_count(runtime):
    runtime._hot_reload.config_pool["action_layout"] = [{"keys": ["v", "omega", "aux"]}]
    runtime._policy = MockPolicyAdapter([ActionProposal(np.zeros(3))])
    result = runtime.step()
    assert result.output_status == "sent"
    assert len(runtime._sink.received[0].target_joint_positions) == 3


def test_direct_validate_checks_payload_in_log_only(runtime):
    runtime._engine._enforcement_mode = __import__(
        "dam.types.enforcement", fromlist=["EnforcementMode"]
    ).EnforcementMode.LOG_ONLY
    output, results = runtime.validate(
        Observation(timestamp=1, joint_positions=np.zeros(2)),
        ActionProposal(np.array([np.inf, 0])),
        "direct",
    )
    assert output is None
    assert results[0].fault_source == "input_contract"


def test_sink_success_is_the_only_history_commit(runtime):
    result = runtime.step()
    assert result.output_status == "sent"
    assert runtime._prev_validated_positions == [1, 1]


def test_no_sink_is_not_reported_as_sent(runtime):
    runtime._sink = None
    result = runtime.step()
    assert result.output_status == "not_configured"
    assert runtime._prev_validated_positions is None


def test_fallback_transition_does_not_repeat_guard_execution(runtime):
    from dam.guard.layer import GuardLayer
    from dam.types.result import GuardResult

    calls = []

    class Next(StepContext):
        severity = 20

        def step(self, *args, **kwargs):
            calls.append("fallback-step")
            return StepResult(None)

    runtime._engine.validate = Mock(
        return_value=(None, [GuardResult.reject("custom", GuardLayer.L1)])
    )
    runtime._ctx_sm.pick_context_for = Mock(return_value=Next())
    first = runtime.step()
    assert calls == []
    assert first.output_status == "suppressed"
    runtime._engine.validate.assert_called_once()
    runtime._sources["mock"].reset()
    runtime._policy.reset()
    runtime.step()
    assert calls == ["fallback-step"]


def test_fallback_entry_exception_is_reported(runtime):
    from dam.guard.layer import GuardLayer
    from dam.types.result import GuardResult

    class BrokenEntry(StepContext):
        severity = 40

        def on_enter(self, *args, **kwargs):
            raise ValueError("entry failed")

        def step(self, *args, **kwargs):
            raise AssertionError("must not execute failed context")

    runtime._engine.validate = Mock(
        return_value=(None, [GuardResult.reject("custom", GuardLayer.L1)])
    )
    runtime._ctx_sm.pick_context_for = Mock(return_value=BrokenEntry())
    result = runtime.step()
    assert result.output_status == "suppressed"
    assert any(
        r.fault_source == "fallback" and "entry failed" in r.reason for r in result.guard_results
    )


def test_slow_down_preserves_timestamp_and_gripper():
    from dam.runtime.builtin_contexts import SlowDownContext

    proposal = ActionProposal(np.ones(2), timestamp=7, gripper_action=0.4)
    checked = ValidatedAction(
        np.ones(2), timestamp=7, gripper_action=0.4, original_proposal=proposal
    )
    r = SimpleNamespace(_prev_validated_positions=[0, 0], validate=Mock(return_value=(checked, [])))
    result = SlowDownContext(scale=0.5).step(
        Observation(timestamp=1, joint_positions=np.zeros(2)),
        r,
        proposal=proposal,
        trace_id="slow",
        now=1,
    )
    assert result.action.timestamp == 7
    assert result.action.gripper_action == 0.4
    np.testing.assert_equal(result.action.target_joint_positions, [0.5, 0.5])


def test_slow_down_missing_baseline_does_not_send_unscaled_command(runtime):
    from dam.runtime.builtin_contexts import SlowDownContext

    runtime._ctx_sm._context_stack.append(SlowDownContext(hysteresis_seconds=1))
    runtime._ctx_sm.active_context._effective_hysteresis = 50
    result = runtime.step()
    assert result.output_status == "suppressed"
    assert runtime._sink.received == []


@pytest.mark.parametrize("scale", [float("nan"), float("inf"), -0.1, 1.1])
def test_invalid_slow_down_scale_is_not_silently_clamped(scale):
    from dam.runtime.builtin_contexts import SlowDownContext

    with pytest.raises(ValueError, match="scale"):
        SlowDownContext(scale=scale)


@pytest.mark.parametrize("runtime", ["enforce", "monitor", "log_only"], indirect=True)
def test_malformed_engine_result_cannot_escape_direct_validation(runtime, monkeypatch):
    monkeypatch.setattr(
        runtime._engine,
        "_validate_checked",
        lambda *args: (ValidatedAction(np.array([np.nan, 0])), []),
    )
    output, results = runtime.validate(
        Observation(timestamp=1, joint_positions=np.zeros(2)),
        ActionProposal(np.zeros(2)),
        "bad-result",
    )
    assert output is None
    assert results[-1].fault_source == "output_contract"
    assert runtime._prev_validated_positions is None
