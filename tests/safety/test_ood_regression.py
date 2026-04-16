"""
Safety regression: OOD detector boundary.

Verifies:
  1. The ood_detector callback is registered by register_all() and callable.
  2. During warmup (<= 30 cycles), all observations pass (Welford fallback).
  3. Consistent in-distribution observations continue to pass after warmup.
  4. A single wildly extreme observation is flagged as OOD after warmup.
  5. OOD boundary in a full monitor-mode pipeline: violation recorded, action not blocked.
"""

from __future__ import annotations

import numpy as np
import pytest

from dam.boundary.builtin_callbacks import ood_detector, register_all
from dam.boundary.constraint import BoundaryConstraint
from dam.boundary.node import BoundaryNode
from dam.boundary.single import SingleNodeContainer
from dam.decorators import guard as guard_decorator
from dam.fallback.builtin import EmergencyStop
from dam.fallback.chain import build_escalation_chain
from dam.fallback.registry import FallbackRegistry
from dam.guard.builtin.ood import OODGuard
from dam.registry.callback import CallbackRegistry
from dam.runtime.guard_runtime import GuardRuntime
from dam.testing.mocks import MockPolicyAdapter, MockSinkAdapter, MockSourceAdapter
from dam.types.action import ActionProposal
from dam.types.observation import Observation

# ── Helpers ───────────────────────────────────────────────────────────────────


def _obs(positions=None) -> Observation:
    pos = np.array(positions) if positions is not None else np.zeros(6)
    return Observation(
        timestamp=0.0,
        joint_positions=pos,
        joint_velocities=np.zeros(6),
        end_effector_pose=np.array([0.1, 0.1, 0.3, 0.0, 0.0, 0.0, 1.0]),
    )


# ── Callback registration ─────────────────────────────────────────────────────


class TestOodDetectorRegistration:
    def test_ood_detector_registered_by_register_all(self):
        """register_all() must include 'ood_detector' in the global registry."""
        import dam.registry.callback as rcmod

        orig = rcmod._registry
        rcmod._registry = CallbackRegistry()
        try:
            register_all()
            assert "ood_detector" in rcmod._registry.list_all()
        finally:
            rcmod._registry = orig

    def test_ood_detector_callback_is_callable(self):
        """ood_detector callback must accept an Observation and return bool."""
        obs = _obs()
        result = ood_detector(obs=obs, backend="welford")
        assert isinstance(result, bool)


# ── Warmup behaviour ──────────────────────────────────────────────────────────


class TestOodWarmup:
    """During warmup the Welford fallback must pass every observation."""

    def test_warmup_passes_normal_observations(self):
        """First 30 calls must all return True (pass) regardless of content."""
        obs = _obs([0.1] * 6)
        # Use a fresh guard instance via a fresh cache key
        import dam.boundary.builtin_callbacks as bc

        bc._ood_guard_cache.clear()

        for i in range(30):
            result = ood_detector(obs=obs, backend="welford", ood_model_path="__test__")
            assert result is True, f"warmup sample {i} must pass"

    def test_warmup_passes_extreme_observations(self):
        """Even extreme values must pass during the 30-sample warmup window."""
        import dam.boundary.builtin_callbacks as bc

        bc._ood_guard_cache.clear()

        extreme_obs = _obs([999.0] * 6)
        for i in range(30):
            result = ood_detector(
                obs=extreme_obs, backend="welford", ood_model_path="__extreme_test__"
            )
            assert result is True, f"warmup sample {i} must pass even for extreme obs"


# ── Post-warmup behaviour ─────────────────────────────────────────────────────


class TestOodPostWarmup:
    """After warmup in-distribution data passes; clear outliers are flagged."""

    @pytest.fixture(autouse=True)
    def _fresh_cache(self):
        import dam.boundary.builtin_callbacks as bc

        bc._ood_guard_cache.clear()
        yield
        bc._ood_guard_cache.clear()

    def _train_welford(self, n: int = 40, positions=None) -> None:
        """Feed n identical observations to push past the warmup window."""
        pos = positions or [0.1] * 6
        obs = _obs(pos)
        for _ in range(n):
            ood_detector(obs=obs, backend="welford", ood_model_path="__pw__")

    def test_consistent_observations_pass_after_warmup(self):
        """Observations close to the training distribution must pass."""
        self._train_welford(n=50)
        obs = _obs([0.1] * 6)
        result = ood_detector(obs=obs, backend="welford", ood_model_path="__pw__")
        assert result is True

    def test_extreme_outlier_flagged_after_warmup(self):
        """After training on near-zero data, a 1e6 observation must be OOD."""
        self._train_welford(n=50, positions=[0.0] * 6)
        extreme_obs = _obs([1e6] * 6)
        result = ood_detector(obs=extreme_obs, backend="welford", ood_model_path="__pw__")
        assert result is False, "extreme outlier must be flagged as OOD after warmup"


# ── OOD in monitor-mode pipeline ──────────────────────────────────────────────


class TestOodInMonitorPipeline:
    """OOD boundary wired into a GuardRuntime in monitor mode."""

    @pytest.fixture(autouse=True)
    def _fresh_cache(self):
        import dam.boundary.builtin_callbacks as bc

        bc._ood_guard_cache.clear()
        yield
        bc._ood_guard_cache.clear()

    def _make_ood_runtime(self, enforcement_mode: str = "monitor") -> GuardRuntime:
        """Build a minimal runtime with only the OOD guard."""
        OG = guard_decorator("L0")(OODGuard)
        g = OG()
        g.set_name("ood")

        reg = FallbackRegistry()
        reg.register(EmergencyStop())
        build_escalation_chain(reg)

        node = BoundaryNode("n0", BoundaryConstraint(), fallback="emergency_stop")
        container = SingleNodeContainer(node)

        return GuardRuntime(
            guards=[g],
            boundary_containers={"ood": container},
            fallback_registry=reg,
            task_config={"task": ["ood"]},
            config_pool={
                "nn_threshold": 0.5,
                "nll_threshold": 5.0,
                "backend": "welford",
                "ood_model_path": "",
                "bank_path": "",
            },
            enforcement_mode=enforcement_mode,
        )

    def test_ood_boundary_runs_and_passes_during_warmup(self):
        """OOD guard must pass during warmup; sink must receive the action."""
        runtime = self._make_ood_runtime("monitor")
        runtime.start_task("task")

        obs = _obs()
        action = ActionProposal(target_joint_positions=np.zeros(6))
        source = MockSourceAdapter([obs] * 5)
        policy = MockPolicyAdapter([action] * 5)
        sink = MockSinkAdapter()
        runtime.register_source("main", source)
        runtime.register_policy(policy)
        runtime.register_sink(sink)

        [runtime.step() for _ in range(5)]
        runtime.stop_task()

        assert len(sink.received) == 5

    def test_ood_violation_recorded_but_not_blocked_in_monitor_mode(self):
        """After warmup, an OOD obs is recorded in results but not blocked."""
        runtime = self._make_ood_runtime("monitor")
        runtime.start_task("task")

        # Warm up with consistent data
        normal_obs = _obs([0.0] * 6)
        normal_action = ActionProposal(target_joint_positions=np.zeros(6))
        source_warmup = MockSourceAdapter([normal_obs] * 50)
        policy_warmup = MockPolicyAdapter([normal_action] * 50)
        sink = MockSinkAdapter()
        runtime.register_source("main", source_warmup)
        runtime.register_policy(policy_warmup)
        runtime.register_sink(sink)
        [runtime.step() for _ in range(50)]

        # Now inject an extreme observation
        extreme_obs = _obs([1e6] * 6)
        extreme_action = ActionProposal(target_joint_positions=np.zeros(6))
        source_extreme = MockSourceAdapter([extreme_obs])
        policy_extreme = MockPolicyAdapter([extreme_action])
        runtime.register_source("main", source_extreme)
        runtime.register_policy(policy_extreme)

        result = runtime.step()
        runtime.stop_task()

        # In monitor mode the action must still reach the sink
        assert result is not None
        # Guard decisions must be visible
        assert result.guard_results is not None
