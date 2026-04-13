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
  control_frequency_hz: 50.0
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
  control_frequency_hz: 50.0
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

        rt.register_source(MockSource())
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
