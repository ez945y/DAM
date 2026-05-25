"""Targeted tests for the Runtime Context state machine — push/pop, severity
preemption, escalation, and the ``requires_proposal`` gate that skips
policy.predict() when the active Context doesn't need it."""

from __future__ import annotations

import time

import numpy as np

from dam.decorators import fallback
from dam.runtime.context import (
    SEVERITY_EMERGENCY_STOP,
    SEVERITY_HOLD_POSITION,
    SEVERITY_NORMAL,
    SEVERITY_RETREAT,
    SEVERITY_SLOW_DOWN,
    ContextEvent,
    NormalContext,
    StepContext,
    StepResult,
    get_context_class,
)
from dam.runtime.contexts import (
    EmergencyStopContext,
    HoldPositionContext,
    RetreatContext,
    SlowDownContext,
    WaitAndRetryContext,
    make_context,
)
from dam.types.result import GuardResult


class _FakeRuntime:
    """Minimal duck-typed runtime stand-in for Context unit tests."""

    def __init__(self) -> None:
        self._prev_validated_positions: list[float] | None = [0.0] * 6
        self._control_frequency_hz: float = 50.0
        self._sink = None
        self._policy = None


def _fault(name: str, frames: int | None = None) -> GuardResult:
    from dam.guard.layer import GuardLayer

    metadata: dict[str, object] = {}
    if frames is not None:
        metadata["required_frames"] = frames
    return GuardResult(
        decision=__import__("dam.types.result", fromlist=["GuardDecision"]).GuardDecision.FAULT,
        guard_name=name,
        layer=GuardLayer.L3,
        reason="test",
        fault_source="hardware",
        metadata=metadata,
    )


# ── requires_proposal gate ────────────────────────────────────────────────────


def test_requires_proposal_defaults_match_design():
    """Bypass / re-source Contexts opt out of policy.predict; only Normal and
    SlowDown actually consume the proposal."""
    assert NormalContext.requires_proposal is True
    assert SlowDownContext.requires_proposal is True
    assert HoldPositionContext.requires_proposal is False
    assert EmergencyStopContext.requires_proposal is False
    assert WaitAndRetryContext.requires_proposal is False
    assert RetreatContext.requires_proposal is False


def test_monitors_hardware_defaults_match_design():
    assert NormalContext.monitors_hardware is True
    assert SlowDownContext.monitors_hardware is True
    assert HoldPositionContext.monitors_hardware is True
    assert WaitAndRetryContext.monitors_hardware is True
    assert RetreatContext.monitors_hardware is True
    assert EmergencyStopContext.monitors_hardware is False


def test_fallback_decorator_registers_step_context():
    @fallback("_test_context_decorator", monitors_hardware=False)
    class _DecoratedContext(StepContext):
        severity = 7
        requires_proposal = False

        def step(self, obs, runtime, *, proposal, trace_id, now):
            return StepResult(action=None)

    assert get_context_class("_test_context_decorator") is _DecoratedContext
    assert _DecoratedContext.name == "_test_context_decorator"
    assert _DecoratedContext.monitors_hardware is False


# ── Stack semantics ───────────────────────────────────────────────────────────


def test_severity_ladder_strictly_increasing():
    """Preemption depends on a well-ordered severity ladder."""
    assert (
        SEVERITY_NORMAL
        < SlowDownContext.severity
        == SEVERITY_SLOW_DOWN
        < RetreatContext.severity
        == SEVERITY_RETREAT
        < HoldPositionContext.severity
        == SEVERITY_HOLD_POSITION
        < EmergencyStopContext.severity
        == SEVERITY_EMERGENCY_STOP
    )


# ── SlowDown is_done semantics ────────────────────────────────────────────────


def test_slow_down_is_done_requires_hysteresis_clearance():
    sd = SlowDownContext(scale=0.5, hysteresis_seconds=0.1)  # 5 cycles @ 50Hz
    sd.on_enter(_FakeRuntime(), trigger=_fault("motor_temp"))
    assert sd._effective_hysteresis == 5

    # Trigger still firing → streak stays 0 → never done
    sd._last_results = [_fault("motor_temp")]
    for _ in range(10):
        assert sd.is_done(None, _FakeRuntime()) is False

    # Trigger clears → streak counts up
    sd._last_results = []  # no rejects
    for i in range(1, 6):
        done = sd.is_done(None, _FakeRuntime())
        if i < 5:
            assert done is False, f"streak {i}/5: must not be done"
        else:
            assert done is True, "streak ≥ effective_hysteresis must yield"


def test_slow_down_hysteresis_inherits_from_trigger_metadata():
    """When user doesn't set hysteresis_seconds, SlowDown reads the boundary's
    required_frames from trigger.metadata."""
    sd = SlowDownContext()  # hysteresis_seconds=None
    sd.on_enter(_FakeRuntime(), trigger=_fault("motor_temp", frames=12))
    assert sd._effective_hysteresis == 12


def test_slow_down_hysteresis_falls_back_to_one_when_no_metadata():
    sd = SlowDownContext()
    sd.on_enter(_FakeRuntime(), trigger=_fault("motor_temp"))
    assert sd._effective_hysteresis == 1


# ── Auto-escalation ───────────────────────────────────────────────────────────


def test_should_escalate_requires_elapsed_and_not_resolving():
    sd = SlowDownContext()
    sd.escalate_after_seconds = 3.0
    sd.escalate_to = "emergency_stop"
    sd.on_enter(_FakeRuntime(), trigger=_fault("motor"))

    # Not enough elapsed
    assert sd.should_escalate(elapsed_seconds=1.0) is False

    # Elapsed AND trigger still violating (healthy_streak = 0)
    assert sd.should_escalate(elapsed_seconds=5.0) is True

    # Elapsed BUT trigger is starting to clear
    sd._healthy_streak = 2
    assert sd.should_escalate(elapsed_seconds=5.0) is False


def test_escalate_disabled_when_unset():
    sd = SlowDownContext()
    # escalate_after_seconds/escalate_to default None
    sd.on_enter(_FakeRuntime(), trigger=_fault("motor"))
    assert sd.should_escalate(elapsed_seconds=10_000.0) is False


# ── HoldPosition snapshot ─────────────────────────────────────────────────────


def test_hold_position_snapshots_on_enter_and_replays():
    rt = _FakeRuntime()
    rt._prev_validated_positions = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]
    hp = HoldPositionContext()
    hp.on_enter(rt)
    # Mutate runtime's positions — snapshot should NOT change
    rt._prev_validated_positions = [9.9] * 6
    result = hp.step(None, rt, proposal=None, trace_id="t", now=0.0)
    assert result.action is not None
    np.testing.assert_array_almost_equal(
        result.action.target_joint_positions,
        [0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
    )


def test_hold_position_with_no_prev_does_not_command_sink():
    rt = _FakeRuntime()
    rt._prev_validated_positions = None
    hp = HoldPositionContext()
    hp.on_enter(rt)
    result = hp.step(None, rt, proposal=None, trace_id="t", now=0.0)
    assert result.action is None


# ── WaitAndRetry seconds conversion ──────────────────────────────────────────


def test_wait_and_retry_converts_seconds_to_cycles():
    rt = _FakeRuntime()
    rt._control_frequency_hz = 50.0
    w = WaitAndRetryContext(wait_seconds=0.4)  # → 20 cycles
    w.on_enter(rt)
    assert w._remaining == 20
    for _ in range(19):
        w.step(None, rt, proposal=None, trace_id="t", now=0.0)
    assert w.is_done(None, rt) is False
    w.step(None, rt, proposal=None, trace_id="t", now=0.0)
    assert w.is_done(None, rt) is True


# ── Retreat plan + chassis ────────────────────────────────────────────────────


def test_retreat_plan_derived_from_duration_and_fps():
    rt = _FakeRuntime()
    rt._control_frequency_hz = 50.0
    r = RetreatContext(target_positions=[0.0] * 6, duration_seconds=2.0)
    r.on_enter(rt)
    assert r._plan is not None
    assert len(r._plan) == 100  # 2.0s * 50Hz


def test_retreat_arrival_detection_within_tolerance():
    rt = _FakeRuntime()
    rt._prev_validated_positions = [0.005, 0.005, 0.0, 0.0, 0.0, 0.0]  # near zero
    r = RetreatContext(target_positions=[0.0] * 6, arrival_tol=0.01)
    r.on_enter(rt)
    # prev within tol of target → is_done
    assert r.is_done(None, rt) is True


# ── make_context factory + builtin coverage ───────────────────────────────────


def test_make_context_round_trip_for_each_builtin():
    for name, cls in [
        ("emergency_stop", EmergencyStopContext),
        ("hold_position", HoldPositionContext),
        ("wait_and_retry", WaitAndRetryContext),
        ("slow_down", SlowDownContext),
        ("retreat", RetreatContext),
    ]:
        ctx = make_context(name)
        assert isinstance(ctx, cls)
        assert ctx.name == name


def test_context_event_dataclass_fields():
    ev = ContextEvent(
        event="preempt",
        ctx_name="emergency_stop",
        ctx_severity=100,
        from_ctx_name="slow_down",
        from_ctx_severity=20,
        trigger_guard="motor_3",
        trigger_reason="overheat",
    )
    assert ev.event == "preempt"
    assert ev.extra == {}  # default factory


def test_stop_task_resets_context_stack_to_normal():
    """stop_task must collapse any fallback context back to NormalContext so a
    fresh start begins in normal mode."""
    from dam.runtime.guard_runtime import GuardRuntime

    rt = GuardRuntime(
        guards=[],
        boundary_containers={},
        task_config={"default": []},
        always_active=[],
        config_pool={},
    )
    rt.start_task("default")

    hp = HoldPositionContext()
    rt._push_context(hp, trigger=None, event="enter")
    assert isinstance(rt._active_context, HoldPositionContext)
    assert len(rt._context_stack) == 2

    rt.stop_task()
    assert isinstance(rt._active_context, NormalContext)
    assert len(rt._context_stack) == 1
