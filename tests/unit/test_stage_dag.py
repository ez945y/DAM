"""Unit tests for Stage DAG execution in GuardRuntime."""

from __future__ import annotations

import time
from typing import Any

import numpy as np

import dam
from dam.guard.base import Guard
from dam.guard.stage import Stage
from dam.injection.static import precompute_injection
from dam.types.action import ActionProposal
from dam.types.observation import Observation
from dam.types.result import GuardDecision, GuardResult

# ── Simple test guards ─────────────────────────────────────────────────────


@dam.guard(layer="L2")
class AlwaysPassGuard(Guard):
    def check(self, **kwargs: Any) -> GuardResult:
        return GuardResult.success(guard_name=self.get_name(), layer=self.get_layer())


@dam.guard(layer="L2")
class AlwaysPassGuard2(Guard):
    def check(self, **kwargs: Any) -> GuardResult:
        return GuardResult.success(guard_name=self.get_name(), layer=self.get_layer())


@dam.guard(layer="L2")
class AlwaysRaiseGuard(Guard):
    def check(self, **kwargs: Any) -> GuardResult:
        raise RuntimeError("intentional exception")


def make_obs():
    return Observation(
        timestamp=time.monotonic(),
        joint_positions=np.zeros(6),
        joint_velocities=np.zeros(6),
    )


def make_action():
    return ActionProposal(target_joint_positions=np.zeros(6))


def _make_runtime_with_stages(stages):
    """Build a minimal GuardRuntime with stages (no boundaries/task required)."""
    from dam.fallback.builtin import EmergencyStop, HoldPosition, SafeRetreat
    from dam.fallback.chain import build_escalation_chain
    from dam.fallback.registry import FallbackRegistry
    from dam.runtime.guard_runtime import GuardRuntime

    # Collect all guards from stages
    all_guards = []
    for stage in stages:
        all_guards.extend(stage.guards)

    for g in all_guards:
        precompute_injection(g, {})

    fallback_registry = FallbackRegistry()
    fallback_registry.register(EmergencyStop())
    fallback_registry.register(HoldPosition())
    fallback_registry.register(SafeRetreat())
    build_escalation_chain(fallback_registry)

    rt = GuardRuntime(
        guards=all_guards,
        boundary_containers={},
        fallback_registry=fallback_registry,
        task_config={"default": []},
        always_active=[],
        config_pool={},
    )
    rt.set_stages(stages)
    return rt


# ── Tests ──────────────────────────────────────────────────────────────────


def test_sequential_stage_runs_all_guards():
    """2 guards in a sequential stage: both are called and both return PASS."""
    g1 = AlwaysPassGuard()
    g2 = AlwaysPassGuard2()
    stage = Stage(name="motion", guards=[g1, g2], parallel=False)

    rt = _make_runtime_with_stages([stage])
    rt.start_task("default")

    obs = make_obs()
    action = make_action()
    validated, results, _ = rt.validate(obs, action, "trace-1")

    assert len(results) == 2
    assert all(r.decision == GuardDecision.PASS for r in results)


def test_parallel_stage_runs_all_guards():
    """2 guards in a parallel stage: both are called and both return PASS."""
    g1 = AlwaysPassGuard()
    g2 = AlwaysPassGuard2()
    stage = Stage(name="parallel_stage", guards=[g1, g2], parallel=True)

    rt = _make_runtime_with_stages([stage])
    rt.start_task("default")

    obs = make_obs()
    action = make_action()
    validated, results, _ = rt.validate(obs, action, "trace-2")

    assert len(results) == 2
    assert all(r.decision == GuardDecision.PASS for r in results)


def test_stage_respects_fail_to_reject():
    """Guard that raises an exception → FAULT result in the stage output."""
    g_raise = AlwaysRaiseGuard()
    precompute_injection(g_raise, {})
    stage = Stage(name="fault_stage", guards=[g_raise], parallel=False)

    rt = _make_runtime_with_stages([stage])
    rt.start_task("default")

    obs = make_obs()
    action = make_action()
    validated, results, fallback = rt.validate(obs, action, "trace-3")

    # Should get a FAULT result from the raising guard
    fault_results = [r for r in results if r.decision == GuardDecision.FAULT]
    assert len(fault_results) >= 1
    # Runtime treats FAULT as REJECT → action is None
    assert validated is None


def test_runtime_uses_stages_when_set():
    """set_stages() on runtime; step() routes through staged execution."""
    # Create a mock source, policy, sink
    obs_val = make_obs()
    action_val = make_action()

    class MockSource:
        def read(self):
            return obs_val

    class MockPolicy:
        def predict(self, obs):
            return action_val

    class MockSink:
        def apply(self, action):
            pass

        def get_hardware_status(self):
            return None

    g1 = AlwaysPassGuard()
    g2 = AlwaysPassGuard2()
    stage = Stage(name="main", guards=[g1, g2], parallel=False)

    rt = _make_runtime_with_stages([stage])
    rt.register_source("main", MockSource())
    rt.register_policy(MockPolicy())
    rt.register_sink(MockSink())
    rt.start_task("default")

    from dam.types.risk import CycleResult

    result = rt.step()

    assert isinstance(result, CycleResult)
    assert len(result.guard_results) == 2
    assert not result.was_rejected
