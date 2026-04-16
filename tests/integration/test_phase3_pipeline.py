"""Integration tests for Phase 3 pipeline features."""

from __future__ import annotations

import os
import tempfile
import time
from typing import Any

import numpy as np

from dam.types.action import ActionProposal
from dam.types.observation import Observation
from dam.types.risk import CycleResult

# ── Shared YAML template ───────────────────────────────────────────────────

_STACKFILE = """\
version: "1"
boundaries:
  motion_guard:
    type: single
    layer: L2
    nodes:
      - node_id: n1
        constraint:
          upper: [3.14, 3.14, 3.14, 3.14, 3.14, 3.14]
          lower: [-3.14, -3.14, -3.14, -3.14, -3.14, -3.14]
          velocity_scale: 1.0
tasks:
  default:
    boundaries: [motion_guard]
safety:
  control_frequency_hz: 50.0
"""


def _make_mock_source():
    class MockSource:
        def read(self):
            return Observation(
                timestamp=time.monotonic(),
                joint_positions=np.zeros(6),
                joint_velocities=np.zeros(6),
            )

    return MockSource()


def _make_mock_policy():
    class MockPolicy:
        def predict(self, obs):
            return ActionProposal(target_joint_positions=np.zeros(6))

    return MockPolicy()


def _make_mock_sink():
    class MockSink:
        def apply(self, action):
            pass

        def get_hardware_status(self):
            return None

    return MockSink()


# ── Tests ──────────────────────────────���───────────────────────────────────


def test_stage_dag_pipeline():
    """Full GuardRuntime with 2 stages: step() completes successfully."""
    import dam
    from dam.fallback.builtin import EmergencyStop, HoldPosition, SafeRetreat
    from dam.fallback.chain import build_escalation_chain
    from dam.fallback.registry import FallbackRegistry
    from dam.guard.base import Guard
    from dam.guard.stage import Stage
    from dam.injection.static import precompute_injection
    from dam.runtime.guard_runtime import GuardRuntime
    from dam.types.result import GuardResult

    @dam.guard(layer="L2")
    class PipelineGuard1(Guard):
        def check(self, **kwargs: Any) -> GuardResult:
            return GuardResult.success(guard_name=self.get_name(), layer=self.get_layer())

    @dam.guard(layer="L3")
    class PipelineGuard2(Guard):
        def check(self, **kwargs: Any) -> GuardResult:
            return GuardResult.success(guard_name=self.get_name(), layer=self.get_layer())

    g1 = PipelineGuard1()
    g2 = PipelineGuard2()
    precompute_injection(g1, {})
    precompute_injection(g2, {})

    stage1 = Stage(name="s1", guards=[g1], parallel=False)
    stage2 = Stage(name="s2", guards=[g2], parallel=False)

    fallback_registry = FallbackRegistry()
    fallback_registry.register(EmergencyStop())
    fallback_registry.register(HoldPosition())
    fallback_registry.register(SafeRetreat())
    build_escalation_chain(fallback_registry)

    rt = GuardRuntime(
        guards=[g1, g2],
        boundary_containers={},
        fallback_registry=fallback_registry,
        task_config={"default": []},
        always_active=[],
        config_pool={},
    )
    rt.set_stages([stage1, stage2])
    rt.register_source("main", _make_mock_source())
    rt.register_policy(_make_mock_policy())
    rt.register_sink(_make_mock_sink())
    rt.start_task("default")

    result = rt.step()
    assert isinstance(result, CycleResult)
    assert not result.was_rejected
    assert len(result.guard_results) == 2


def test_hot_reload_pipeline():
    """Runtime with watcher: modify stackfile, verify reload applied."""
    import threading

    from dam.config.hot_reload import StackfileWatcher
    from dam.runtime.guard_runtime import GuardRuntime

    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w") as f:
        f.write(_STACKFILE)
        path = f.name

    try:
        rt = GuardRuntime.from_stackfile(path)
        rt.register_source("main", _make_mock_source())
        rt.register_policy(_make_mock_policy())
        rt.register_sink(_make_mock_sink())
        rt.start_task("default")

        reload_fired = threading.Event()

        def on_change(cfg):
            rt.apply_pending_reload(cfg)
            reload_fired.set()

        watcher = StackfileWatcher(path=path, on_change=on_change, poll_interval_s=0.05)
        watcher.start()

        # Modify the stackfile
        time.sleep(0.1)
        modified = _STACKFILE.replace("3.14", "2.0")
        with open(path, "w") as f:
            f.write(modified)

        # Wait for watcher to detect change
        assert reload_fired.wait(timeout=3.0), "Hot reload callback not fired"

        # step() should apply pending reload
        result = rt.step()
        assert isinstance(result, CycleResult)

        # pending config should be cleared after step
        with rt._hot_reload_lock:
            assert rt._pending_config is None
    finally:
        watcher.stop()
        os.unlink(path)


def test_dual_mode_run_n_cycles():
    """runtime.run(n_cycles=3) — verify exactly 3 CycleResults returned."""
    from dam.runtime.guard_runtime import GuardRuntime

    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w") as f:
        f.write(_STACKFILE)
        path = f.name

    try:
        rt = GuardRuntime.from_stackfile(path)
        rt.register_source("main", _make_mock_source())
        rt.register_policy(_make_mock_policy())
        rt.register_sink(_make_mock_sink())
        rt.start_task("default")

        results = rt.run(n_cycles=3, cycle_budget_ms=5.0)
        assert len(results) == 3
        assert all(isinstance(r, CycleResult) for r in results)
    finally:
        os.unlink(path)
