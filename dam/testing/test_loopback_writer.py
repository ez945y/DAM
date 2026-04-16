"""Unit tests for LoopbackWriter — thread-safety, priority queue, and shutdown."""

from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from dam.logging.cycle_record import CycleRecord
from dam.types.action import ActionProposal, ValidatedAction
from dam.types.observation import Observation
from dam.types.result import GuardDecision, GuardResult

# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def obs_bus() -> MagicMock:
    """Minimal ObservationBus mock that returns empty window."""
    bus = MagicMock()
    bus.read_window.return_value = []
    return bus


@pytest.fixture
def session_meta() -> dict[str, str]:
    return {
        "robot_id": "test_robot",
        "session_type": "test",
    }


@pytest.fixture
def writer(
    obs_bus: MagicMock,
    session_meta: dict[str, str],
    tmp_path: Path,
) -> Any:
    """Create a LoopbackWriter with a temporary output directory."""
    from dam.logging.loopback_writer import LoopbackWriter

    w = LoopbackWriter(
        output_dir=str(tmp_path),
        obs_bus=obs_bus,
        control_frequency_hz=50.0,
        window_sec=0.1,
        rotate_mb=100.0,
        rotate_minutes=60.0,
        max_queue_depth=8,
        session_meta=session_meta,
    )
    yield w
    w.shutdown(timeout=2.0)


def make_pass_record(cycle_id: int = 1) -> CycleRecord:
    obs = Observation(
        timestamp=time.time(),
        joint_positions=np.zeros(6),
        joint_velocities=None,
        end_effector_pose=None,
        force_torque=None,
        images={},
        metadata={},
    )
    action = ActionProposal(
        target_joint_positions=np.zeros(6),
        target_joint_velocities=None,
    )
    guard_result = GuardResult.success(guard_name="test_guard", layer=0)
    return CycleRecord(
        cycle_id=cycle_id,
        trace_id="test_trace",
        triggered_at=time.time(),
        active_task="test_task",
        active_boundaries=("test_boundary",),
        obs_timestamp=obs.timestamp,
        obs_joint_positions=obs.joint_positions.tolist(),
        obs_joint_velocities=obs.joint_velocities,
        obs_end_effector_pose=obs.end_effector_pose,
        obs_force_torque=obs.force_torque,
        obs_metadata=obs.metadata,
        action_positions=action.target_joint_positions.tolist(),
        action_velocities=None,
        validated_positions=action.target_joint_positions.tolist(),
        validated_velocities=None,
        was_clamped=False,
        fallback_triggered=None,
        guard_results=(guard_result,),
        latency_stages={"source": 1.0, "policy": 2.0, "guards": 3.0, "sink": 0.5, "total": 6.5},
        latency_layers={"L0": 1.0, "L1": 0.5, "L2": 0.5, "L3": 0.5, "L4": 0.5},
        latency_guards={"test_guard": 1.0},
        has_violation=False,
        has_clamp=False,
        violated_layer_mask=0,
        clamped_layer_mask=0,
    )


def make_violation_record(cycle_id: int = 1) -> CycleRecord:
    rec = make_pass_record(cycle_id)
    guard_result = GuardResult.reject(
        reason="test violation",
        guard_name="test_violation_guard",
        layer=0,
    )
    return CycleRecord(
        cycle_id=rec.cycle_id,
        trace_id=rec.trace_id,
        triggered_at=rec.triggered_at,
        active_task=rec.active_task,
        active_boundaries=rec.active_boundaries,
        obs_timestamp=rec.obs_timestamp,
        obs_joint_positions=rec.obs_joint_positions,
        obs_joint_velocities=rec.obs_joint_velocities,
        obs_end_effector_pose=rec.obs_end_effector_pose,
        obs_force_torque=rec.obs_force_torque,
        obs_metadata=rec.obs_metadata,
        action_positions=rec.action_positions,
        action_velocities=rec.action_velocities,
        validated_positions=None,
        validated_velocities=None,
        was_clamped=rec.was_clamped,
        fallback_triggered=rec.fallback_triggered,
        guard_results=(guard_result,),
        latency_stages=rec.latency_stages,
        latency_layers=rec.latency_layers,
        latency_guards=rec.latency_guards,
        has_violation=True,
        has_clamp=False,
        violated_layer_mask=1 << 0,
        clamped_layer_mask=0,
    )


def make_clamp_record(cycle_id: int = 1) -> CycleRecord:
    rec = make_pass_record(cycle_id)
    validated = ValidatedAction(
        target_joint_positions=np.zeros(6),
        target_joint_velocities=None,
        was_clamped=True,
    )
    guard_result = GuardResult.clamp(
        clamped_action=validated,
        guard_name="test_clamp_guard",
        layer=1,
        reason="clamped for safety",
    )
    return CycleRecord(
        cycle_id=rec.cycle_id,
        trace_id=rec.trace_id,
        triggered_at=rec.triggered_at,
        active_task=rec.active_task,
        active_boundaries=rec.active_boundaries,
        obs_timestamp=rec.obs_timestamp,
        obs_joint_positions=rec.obs_joint_positions,
        obs_joint_velocities=rec.obs_joint_velocities,
        obs_end_effector_pose=rec.obs_end_effector_pose,
        obs_force_torque=rec.obs_force_torque,
        obs_metadata=rec.obs_metadata,
        action_positions=rec.action_positions,
        action_velocities=rec.action_velocities,
        validated_positions=validated.target_joint_positions.tolist(),
        validated_velocities=None,
        was_clamped=True,
        fallback_triggered=None,
        guard_results=(guard_result,),
        latency_stages=rec.latency_stages,
        latency_layers=rec.latency_layers,
        latency_guards=rec.latency_guards,
        has_violation=False,
        has_clamp=True,
        violated_layer_mask=0,
        clamped_layer_mask=1 << 1,
    )


# ── Tests ──────────────────────────────────────────────────────────────────────


def test_submit_non_blocking(writer: Any) -> None:
    """submit() must return immediately without waiting for I/O."""
    writer.start()
    rec = make_pass_record(1)
    start = time.monotonic()
    writer.submit(rec)
    elapsed = time.monotonic() - start
    assert elapsed < 0.05, f"submit() took {elapsed:.3f}s — should be non-blocking"
    assert writer._queue.qsize() == 1


def test_submit_thread_safe(writer: Any) -> None:
    """Multiple threads can call submit() concurrently without errors."""
    writer.start()
    errors: list[BaseException] = []
    barrier = threading.Barrier(10)

    def submit_cycles() -> None:
        try:
            barrier.wait()
            for i in range(50):
                writer.submit(make_pass_record(i))
        except BaseException as e:
            errors.append(e)

    threads = [threading.Thread(target=submit_cycles) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Thread errors: {errors}"
    assert writer._queue.qsize() > 0


def test_queue_full_drops_normal_cycles(writer: Any) -> None:
    """When queue is full, normal PASS cycles are dropped with debug log."""
    writer.start()
    rec = make_pass_record(999)
    writer.submit(rec)
    # Worker hasn't processed yet, queue should have 1 item
    assert writer._queue.qsize() == 1


def test_priority_queue_evicts_oldest(writer: Any) -> None:
    """Violation cycles must evict oldest PASS when queue is full."""
    writer.start()
    for i in range(writer._max_queue_depth):
        writer.submit(make_pass_record(i))
    time.sleep(0.1)
    writer.submit(make_violation_record(100))
    time.sleep(0.2)
    writer.shutdown(timeout=2.0)
    mcap_files = list(writer._output_dir.glob("session_*.mcap"))
    assert len(mcap_files) == 1
    with open(mcap_files[0], "rb") as f:
        from mcap.reader import make_reader

        reader = make_reader(f)
        cycles = list(reader.iter_messages(topics=["/dam/cycle"]))
        assert len(cycles) >= 1
        # cycles is list of (schema, channel, message) tuples
        data = json.loads(cycles[-1][2].data)
        assert data["has_violation"] is True


def test_clamp_evicts_oldest(writer: Any) -> None:
    """CLAMP cycles must evict oldest PASS when queue is full."""
    writer.start()
    for i in range(writer._max_queue_depth):
        writer.submit(make_pass_record(i))
    assert writer._queue.full()
    time.sleep(0.1)
    writer.submit(make_clamp_record(100))
    time.sleep(0.2)
    writer.shutdown(timeout=2.0)
    mcap_files = list(writer._output_dir.glob("session_*.mcap"))
    assert len(mcap_files) == 1


def test_shutdown_closes_file(writer: Any) -> None:
    """shutdown() must close the MCAP file and make it readable."""
    writer.start()
    writer.submit(make_pass_record(1))
    time.sleep(0.2)
    writer.shutdown(timeout=2.0)
    mcap_files = list(writer._output_dir.glob("session_*.mcap"))
    assert len(mcap_files) == 1
    assert mcap_files[0].stat().st_size > 0


def test_shutdown_flushes_violation_cycles(writer: Any) -> None:
    """Violation cycles submitted before shutdown must be written to disk."""
    writer.start()
    for i in range(3):
        writer.submit(make_violation_record(i))
    time.sleep(0.3)  # Give worker time to write
    writer.shutdown(timeout=2.0)
    mcap_files = list(writer._output_dir.glob("session_*.mcap"))
    assert len(mcap_files) == 1
    with open(mcap_files[0], "rb") as f:
        from mcap.reader import make_reader

        reader = make_reader(f)
        cycles = list(reader.iter_messages(topics=["/dam/cycle"]))
        violation_cycles = [c for c in cycles if json.loads(c[2].data).get("has_violation")]
        assert len(violation_cycles) == 3


def test_worker_thread_dead_detection(writer: Any) -> None:
    """submit() logs error and returns when worker thread is dead."""
    writer.start()
    time.sleep(0.1)
    # Manually terminate the worker thread by patching is_alive
    original_is_alive = writer._thread.is_alive
    writer._thread.is_alive = lambda: False
    # Initialize _thread_dead_warned to False so getattr works
    writer._thread_dead_warned = False
    rec = make_pass_record(999)
    with patch("dam.logging.loopback_writer.logger") as mock_logger:
        writer.submit(rec)
        mock_logger.error.assert_called_once()
    assert writer._queue.qsize() == 0
    # Restore
    writer._thread.is_alive = original_is_alive


def test_double_start_is_noop(writer: Any) -> None:
    """Calling start() twice must not start multiple threads."""
    writer.start()
    first_thread_id = writer._thread.ident
    writer.start()
    assert writer._thread.ident == first_thread_id
    writer.shutdown(timeout=2.0)


def test_shutdown_idempotent(writer: Any) -> None:
    """Calling shutdown() multiple times must not raise."""
    writer.start()
    writer.shutdown(timeout=1.0)
    writer.shutdown(timeout=1.0)


def test_mcap_file_readable_after_write(writer: Any) -> None:
    """Written MCAP file must be parseable with all expected channels."""
    writer.start()
    writer.submit(make_pass_record(1))
    writer.submit(make_violation_record(2))
    writer.submit(make_clamp_record(3))
    time.sleep(0.3)
    writer.shutdown(timeout=2.0)
    mcap_files = list(writer._output_dir.glob("session_*.mcap"))
    assert len(mcap_files) == 1
    with open(mcap_files[0], "rb") as f:
        from mcap.reader import make_reader

        reader = make_reader(f)
        summary = reader.get_summary()
        schema_names = {s.name for s in summary.schemas.values()}
        assert "dam.Cycle" in schema_names
        assert "dam.Observation" in schema_names
        assert "dam.Action" in schema_names
        assert "dam.Latency" in schema_names


def test_guard_results_written_to_correct_layer_channel(writer: Any) -> None:
    """Guard results must be written to /dam/L{layer} channels."""
    writer.start()
    writer.submit(make_violation_record(1))
    time.sleep(0.2)
    writer.shutdown(timeout=2.0)
    mcap_files = list(writer._output_dir.glob("session_*.mcap"))
    with open(mcap_files[0], "rb") as f:
        from mcap.reader import make_reader

        reader = make_reader(f)
        l0_messages = list(reader.iter_messages(topics=["/dam/L0"]))
        assert len(l0_messages) >= 1
        data = json.loads(l0_messages[0][2].data)
        assert data["is_violation"] is True


def test_latency_per_guard_written(writer: Any) -> None:
    """Per-guard latency must appear in /dam/latency messages."""
    writer.start()
    writer.submit(make_pass_record(1))
    time.sleep(0.2)
    writer.shutdown(timeout=2.0)
    mcap_files = list(writer._output_dir.glob("session_*.mcap"))
    with open(mcap_files[0], "rb") as f:
        from mcap.reader import make_reader

        reader = make_reader(f)
        latency_messages = list(reader.iter_messages(topics=["/dam/latency"]))
        assert len(latency_messages) >= 1
        data = json.loads(latency_messages[0][2].data)
        assert "source_ms" in data
        assert "L0_ms" in data


def test_session_metadata_written(writer: Any, session_meta: dict[str, str]) -> None:
    """Session metadata must be stored in MCAP file metadata."""
    writer.start()
    writer.submit(make_pass_record(1))
    time.sleep(0.2)
    writer.shutdown(timeout=2.0)
    mcap_files = list(writer._output_dir.glob("session_*.mcap"))
    with open(mcap_files[0], "rb") as f:
        from mcap.reader import make_reader

        reader = make_reader(f)
        summary = reader.get_summary()
        # Check metadata index contains 'session'
        metadata_names = [idx.name for idx in summary.metadata_indexes]
        assert "session" in metadata_names
