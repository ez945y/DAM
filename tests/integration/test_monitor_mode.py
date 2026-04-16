"""
Integration tests for enforcement_mode="monitor" and policy-to-guard I/O wiring.

Key invariants verified:
  1. Monitor mode: guard violation does NOT block the action (validate returns non-None).
  2. Monitor mode: guard decisions ARE recorded in the results list (observable).
  3. Policy wiring: the ActionProposal from the policy is what the guards receive.
  4. Full step cycle: CycleResult in monitor mode contains guard decisions.
"""

from __future__ import annotations

import numpy as np

from dam.boundary.constraint import BoundaryConstraint
from dam.boundary.node import BoundaryNode
from dam.boundary.single import SingleNodeContainer
from dam.decorators import guard as guard_decorator
from dam.fallback.builtin import EmergencyStop, HoldPosition
from dam.fallback.chain import build_escalation_chain
from dam.fallback.registry import FallbackRegistry
from dam.guard.builtin.motion import MotionGuard
from dam.runtime.guard_runtime import GuardRuntime
from dam.testing.mocks import MockPolicyAdapter, MockSinkAdapter, MockSourceAdapter
from dam.types.action import ActionProposal
from dam.types.observation import Observation
from dam.types.result import GuardDecision
from dam.types.risk import CycleResult

# ── Helpers ───────────────────────────────────────────────────────────────────


def _obs(ee_x: float = 0.1) -> Observation:
    return Observation(
        timestamp=0.0,
        joint_positions=np.zeros(6),
        joint_velocities=np.zeros(6),
        end_effector_pose=np.array([ee_x, 0.1, 0.1, 0.0, 0.0, 0.0, 1.0]),
    )


def _action(pos: float = 0.0) -> ActionProposal:
    return ActionProposal(target_joint_positions=np.full(6, pos))


def _make_runtime(
    *,
    enforcement_mode: str = "monitor",
) -> GuardRuntime:
    """Runtime with a MotionGuard wired to a workspace that rejects ee_x < 0."""
    KG = guard_decorator("L2")(MotionGuard)
    g = KG()
    g.set_name("main")

    reg = FallbackRegistry()
    reg.register(EmergencyStop())
    reg.register(HoldPosition())
    build_escalation_chain(reg)

    node = BoundaryNode("n0", BoundaryConstraint(), fallback="emergency_stop")
    container = SingleNodeContainer(node)

    runtime = GuardRuntime(
        guards=[g],
        boundary_containers={"main": container},
        fallback_registry=reg,
        task_config={"task": ["main"]},
        config_pool={
            "upper": np.full(6, 5.0),
            "lower": np.full(6, -5.0),
            # Workspace requires ee_x in [0, 0.5]; negative x triggers REJECT.
            "bounds": np.array([[0.0, 0.5], [0.0, 0.5], [0.0, 0.5]]),
        },
        enforcement_mode=enforcement_mode,
    )
    return runtime


# ── Monitor mode: action passthrough ──────────────────────────────────────────


def test_monitor_mode_passes_action_on_guard_violation():
    """In monitor mode a REJECT from a guard must NOT block the action."""
    runtime = _make_runtime(enforcement_mode="monitor")
    runtime.start_task("task")

    obs = _obs(ee_x=-1.0)  # ee_x=-1 is outside [0, 0.5] → REJECT
    action = _action()
    validated, results, fallback = runtime.validate(obs, action, "trace-001")

    assert validated is not None, "monitor mode must pass the action through"
    assert fallback is None, "monitor mode must not trigger fallback"


def test_monitor_mode_records_violation():
    """Guard decisions are still captured in results even in monitor mode."""
    runtime = _make_runtime(enforcement_mode="monitor")
    runtime.start_task("task")

    obs = _obs(ee_x=-1.0)
    action = _action()
    _validated, results, _fallback = runtime.validate(obs, action, "trace-002")

    decisions = [r.decision for r in results]
    assert GuardDecision.REJECT in decisions, (
        "monitor mode must still record the violation in guard results"
    )


def test_monitor_mode_passes_original_positions():
    """The action positions passed through in monitor mode must equal the original proposal."""
    runtime = _make_runtime(enforcement_mode="monitor")
    runtime.start_task("task")

    target = np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6])
    obs = _obs(ee_x=-1.0)
    action = ActionProposal(target_joint_positions=target)
    validated, _results, _fallback = runtime.validate(obs, action, "trace-003")

    assert validated is not None
    np.testing.assert_array_equal(
        validated.target_joint_positions,
        target,
        err_msg="monitor mode must forward original positions unmodified",
    )


# ── Policy I/O wiring ─────────────────────────────────────────────────────────


def test_policy_output_is_what_guards_receive():
    """The ActionProposal from the policy must be the proposal that guards evaluate."""
    runtime = _make_runtime(enforcement_mode="enforce")
    runtime.start_task("task")

    # Policy proposes positions clearly inside limits → should PASS
    safe_pos = np.full(6, 0.0)
    obs = _obs(ee_x=0.2)
    action = ActionProposal(target_joint_positions=safe_pos)

    validated, results, fallback = runtime.validate(obs, action, "trace-005")

    assert validated is not None
    np.testing.assert_array_equal(validated.target_joint_positions, safe_pos)
    assert all(r.decision in (GuardDecision.PASS, GuardDecision.CLAMP) for r in results)


def test_full_step_cycle_monitor_records_decisions():
    """Full step() in monitor mode: CycleResult contains guard decisions."""
    runtime = _make_runtime(enforcement_mode="monitor")
    runtime.start_task("task")

    # Obs with EE outside workspace so the guard fires
    obs = _obs(ee_x=-1.0)
    action = _action()

    source = MockSourceAdapter([obs] * 5)
    policy = MockPolicyAdapter([action] * 5)
    sink = MockSinkAdapter()
    runtime.register_source("main", source)
    runtime.register_policy(policy)
    runtime.register_sink(sink)

    results = [runtime.step() for _ in range(5)]
    runtime.stop_task()

    assert all(isinstance(r, CycleResult) for r in results)
    # Monitor mode: sink receives actions even though guard rejected
    assert len(sink.received) == 5, "monitor mode must still dispatch to sink"
    # Guard decisions must be visible in every cycle
    for cycle in results:
        assert cycle.guard_results, "CycleResult must carry guard_results"
        decisions = [r.decision for r in cycle.guard_results]
        assert GuardDecision.REJECT in decisions


def test_enforce_mode_blocks_sink_on_violation():
    """Sanity check: in enforce mode the same scenario blocks the action."""
    runtime = _make_runtime(enforcement_mode="enforce")
    runtime.start_task("task")

    obs = _obs(ee_x=-1.0)
    action = _action()

    source = MockSourceAdapter([obs] * 3)
    policy = MockPolicyAdapter([action] * 3)
    sink = MockSinkAdapter()
    runtime.register_source("main", source)
    runtime.register_policy(policy)
    runtime.register_sink(sink)

    [runtime.step() for _ in range(3)]
    runtime.stop_task()

    assert len(sink.received) == 0, "enforce mode must not dispatch rejected actions to sink"
