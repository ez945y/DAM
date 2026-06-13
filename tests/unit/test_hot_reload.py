"""Unit tests for StackfileWatcher hot-reload and GuardRuntime config swap."""

from __future__ import annotations

import os
import tempfile
import threading
import time

import numpy as np

from dam.config.hot_reload import StackfileWatcher

# ── Minimal YAML templates ─────────────────────────────────────────────────

_STACKFILE_V1 = """\
version: "1"
guards:
  builtin:
    motion:
      enabled: true
boundaries:
  b1:
    layer: L2
    type: single
    nodes:
      - node_id: n1
        fallback: emergency_stop
        params:
          upper: [1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
          lower: [-1.0, -1.0, -1.0, -1.0, -1.0, -1.0]
          max_speed: 2.0
tasks:
  default:
    boundaries: [b1]
safety:
  control_hz: 50.0
"""

_STACKFILE_V2 = """\
version: "1"
guards:
  builtin:
    motion:
      enabled: true
boundaries:
  b1:
    layer: L2
    type: single
    nodes:
      - node_id: n1
        fallback: emergency_stop
        params:
          upper: [2.0, 2.0, 2.0, 2.0, 2.0, 2.0]
          lower: [-2.0, -2.0, -2.0, -2.0, -2.0, -2.0]
          max_speed: 5.0
tasks:
  default:
    boundaries: [b1]
safety:
  control_hz: 50.0
"""


def _write_stackfile(path: str, content: str) -> None:
    with open(path, "w") as f:
        f.write(content)
    # Small sleep to ensure mtime changes
    time.sleep(0.02)


# ── Tests ──────────────────────────────────────────────────────────────────


def test_watcher_detects_file_change():
    """Write YAML, modify it, verify callback fires."""
    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w") as f:
        f.write(_STACKFILE_V1)
        path = f.name

    try:
        fired = threading.Event()
        received_configs = []

        def on_change(cfg):
            received_configs.append(cfg)
            fired.set()

        watcher = StackfileWatcher(path=path, on_change=on_change, poll_interval_s=0.05)
        watcher.start()

        # Modify the file
        time.sleep(0.1)
        _write_stackfile(path, _STACKFILE_V2)

        # Wait up to 2 seconds for callback
        assert fired.wait(timeout=2.0), "Callback was not fired within 2 seconds"
        assert len(received_configs) >= 1
    finally:
        watcher.stop()
        os.unlink(path)


def test_watcher_stops_cleanly():
    """start() then stop(): verify thread exits within reasonable time."""
    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w") as f:
        f.write(_STACKFILE_V1)
        path = f.name

    try:
        watcher = StackfileWatcher(path=path, on_change=lambda cfg: None, poll_interval_s=0.05)
        watcher.start()
        assert watcher.is_running()

        watcher.stop()
        assert not watcher.is_running()
    finally:
        os.unlink(path)


def test_runtime_apply_pending_reload_swaps_config():
    """apply_pending_reload() stores config; step() applies it before running guards."""
    from dam.config.loader import StackfileLoader
    from dam.runtime.guard_runtime import GuardRuntime

    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w") as f:
        f.write(_STACKFILE_V1)
        path = f.name

    try:
        rt = GuardRuntime.from_stackfile(path)

        # Load v2 config and apply pending reload
        new_config = StackfileLoader.load(path)
        rt.apply_pending_reload(new_config)

        # Verify pending config is stored
        with rt._hot_reload_lock:
            assert rt._pending_config is not None
    finally:
        os.unlink(path)


def test_hot_reload_not_mid_cycle():
    """Pending reload is applied BEFORE step(), not during guard execution."""
    from dam.config.loader import StackfileLoader
    from dam.runtime.guard_runtime import GuardRuntime
    from dam.types.action import ActionProposal
    from dam.types.observation import Observation

    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w") as f:
        f.write(_STACKFILE_V1)
        path = f.name

    try:
        rt = GuardRuntime.from_stackfile(path)

        class MockSource:
            def read(self):
                return Observation(
                    timestamp=time.monotonic(),
                    joint_positions=np.zeros(6),
                    joint_velocities=np.zeros(6),
                )

        class MockPolicy:
            def predict(self, obs):
                return ActionProposal(target_joint_positions=np.zeros(6))

        class MockSink:
            def apply(self, action):
                pass

            def get_hardware_status(self):
                return None

        rt.register_source("main", MockSource())
        rt.register_policy(MockPolicy())
        rt.register_sink(MockSink())
        rt.start_task("default")

        # Set a pending reload
        new_config = StackfileLoader.load(path)
        rt.apply_pending_reload(new_config)

        # step() should apply the reload before guard execution
        rt.step()

        # After step, pending config should be cleared
        with rt._hot_reload_lock:
            assert rt._pending_config is None
    finally:
        os.unlink(path)


# ── Validate-then-commit + config_version tests ───────────────────────────


def test_config_version_starts_at_zero_and_bumps_on_swap():
    """config_version is 0 until first swap, then increments by 1 each commit."""
    from dam.config.loader import StackfileLoader
    from dam.runtime.guard_runtime import GuardRuntime

    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w") as f:
        f.write(_STACKFILE_V1)
        path = f.name

    try:
        rt = GuardRuntime.from_stackfile(path)
        assert rt._config_version == 0

        new_config = StackfileLoader.load(path)
        rt._apply_config_swap(new_config)
        assert rt._config_version == 1

        rt._apply_config_swap(new_config)
        assert rt._config_version == 2
    finally:
        os.unlink(path)


def test_failed_swap_keeps_previous_config(caplog):
    """A swap that throws during pool build leaves runtime untouched."""
    from unittest.mock import patch

    from dam.config.loader import StackfileLoader
    from dam.runtime.guard_runtime import GuardRuntime

    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w") as f:
        f.write(_STACKFILE_V1)
        path = f.name

    try:
        rt = GuardRuntime.from_stackfile(path)
        prior_pool = dict(rt._config_pool)
        prior_version = rt._config_version

        new_config = StackfileLoader.load(path)
        # Inject a failure in the pure builder
        with patch.object(
            GuardRuntime, "_build_config_pool", side_effect=ValueError("shape mismatch")
        ):
            rt._apply_config_swap(new_config)

        # Pool and version should be unchanged
        assert rt._config_pool == prior_pool
        assert rt._config_version == prior_version
        assert any("REJECTED" in r.message for r in caplog.records)
    finally:
        os.unlink(path)


def test_cycle_record_carries_config_version():
    """CycleRecord defaults config_version to 0 and propagates the runtime value."""
    from dam.guard.layer import GuardLayer
    from dam.logging.cycle_record import CycleRecord
    from dam.types.result import GuardResult

    base = CycleRecord(
        cycle_id=0,
        trace_id="t",
        triggered_at=0.0,
        active_task=None,
        active_boundaries=(),
        active_cameras=(),
        obs_timestamp=0.0,
        obs_joint_positions=[0.0],
        obs_channels={},
        obs_metadata={},
        action_positions=[0.0],
        action_velocities=None,
        validated_positions=None,
        validated_velocities=None,
        was_clamped=False,
        fallback_triggered=None,
        guard_results=(GuardResult.success(guard_name="g", layer=GuardLayer.L0),),
        latency_stages={},
        latency_layers={},
        latency_guards={},
        has_violation=False,
        has_clamp=False,
        violated_layer_mask=0,
        clamped_layer_mask=0,
    )
    # Default
    assert base.config_version == 0

    bumped = CycleRecord(**{**base.__dict__, "config_version": 7})
    assert bumped.config_version == 7


def test_event_loop_retries_on_transient_failure(monkeypatch):
    """A transient watchfiles error doesn't permanently demote to polling."""
    import dam.config.hot_reload as hr
    from dam.config.hot_reload import StackfileWatcher

    call_count = {"n": 0}

    # First two calls raise, third returns an empty iterable (clean stop).
    def fake_watch(path, **kwargs):
        call_count["n"] += 1
        stop_event = kwargs.get("stop_event")
        if call_count["n"] <= 2:
            raise RuntimeError(f"simulated transient failure {call_count['n']}")
        if stop_event is not None:
            stop_event.set()
        return iter([])

    # Inject a fake watchfiles module via sys.modules so the lazy import
    # inside _event_loop pulls our stub instead of the real lib.
    import sys
    import types

    fake_module = types.SimpleNamespace(watch=fake_watch)
    monkeypatch.setitem(sys.modules, "watchfiles", fake_module)

    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w") as f:
        f.write(_STACKFILE_V1)
        path = f.name

    try:
        w = StackfileWatcher(path=path, on_change=lambda _cfg: None, poll_interval_s=0.05)
        w._stop_event.clear()
        w._event_loop()  # blocks until stop_event set inside fake_watch
        # Two crashes + one clean exit = 3 attempts before stop
        assert call_count["n"] == 3
    finally:
        os.unlink(path)
