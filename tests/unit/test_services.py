"""Tests for DAM services layer (Telemetry, RiskLog, BoundaryConfig, RuntimeControl)."""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from dam.guard.layer import GuardLayer
from dam.runner.base import RunnerStatus, SimulationRunner
from dam.services.boundary_config import BoundaryConfigService
from dam.services.mcap_sessions import McapSessionService
from dam.services.risk_log import RiskLogService
from dam.services.runtime_control import BackendState, RuntimeControlService, RuntimeState
from dam.services.telemetry import TelemetryService, _serialise_cycle
from dam.types.action import ActionProposal
from dam.types.result import GuardDecision, GuardResult
from dam.types.risk import CycleResult, RiskLevel

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_cycle_result(
    cycle_id: int = 0,
    rejected: bool = False,
    clamped: bool = False,
    risk: RiskLevel = RiskLevel.NORMAL,
) -> CycleResult:
    proposal = ActionProposal(target_joint_positions=np.zeros(6))
    return CycleResult(
        cycle_id=cycle_id,
        trace_id=f"trace-{cycle_id}",
        validated_action=None,
        original_proposal=proposal,
        was_clamped=clamped,
        was_rejected=rejected,
        guard_results=[],
        fallback_triggered=None,
        latency_ms={"obs": 1.0, "policy": 2.0, "validate": 3.0, "sink": 0.5, "total": 6.5},
        risk_level=risk,
    )


# ── McapSessionService ─────────────────────────────────────────────────────────


def test_mcap_archive_session_deletes_file(tmp_path):
    path = tmp_path / "session_demo.mcap"
    path.write_bytes(b"0" * 128)
    svc = McapSessionService(str(tmp_path))
    try:
        result = svc.archive_session(path.name)
        assert result is not None
        assert not path.exists()
        assert not (tmp_path / "_trash").exists()
        assert svc.list_sessions() == []
    finally:
        svc.close()


def test_risk_log_filters_failure_type():
    svc = RiskLogService(max_events=10)
    result = _make_cycle_result(1, rejected=True, risk=RiskLevel.EMERGENCY)
    result.guard_results = [
        GuardResult(
            decision=GuardDecision.FAULT,
            guard_name="hardware_watchdog",
            layer=GuardLayer.L3,
            reason="Voltage high",
            fault_source="hardware",
        )
    ]

    svc.record(result)

    hardware = svc.query(failure_type="hardware_triggered")
    assert len(hardware) == 1
    assert hardware[0].failure_type == "hardware_triggered"
    assert svc.query(failure_type="ood_only") == []


# ── TelemetryService ──────────────────────────────────────────────────────────


class TestTelemetryService:
    def test_push_increments_counter(self):
        svc = TelemetryService()
        assert svc.total_pushed == 0
        svc.push(_make_cycle_result(0))
        assert svc.total_pushed == 1
        svc.push(_make_cycle_result(1))
        assert svc.total_pushed == 2

    def test_history_stores_events(self):
        svc = TelemetryService(history_size=10)
        for i in range(5):
            svc.push(_make_cycle_result(i))
        hist = svc.get_history()
        assert len(hist) == 5

    def test_history_respects_size_limit(self):
        svc = TelemetryService(history_size=3)
        for i in range(10):
            svc.push(_make_cycle_result(i))
        assert len(svc.get_history()) == 3

    def test_history_n_param(self):
        svc = TelemetryService()
        for i in range(20):
            svc.push(_make_cycle_result(i))
        assert len(svc.get_history(5)) == 5

    def test_subscriber_count(self):
        svc = TelemetryService()
        assert svc.subscriber_count == 0

    def test_subscribe_unsubscribe(self):
        import asyncio

        svc = TelemetryService()
        loop = asyncio.new_event_loop()
        svc.attach_loop(loop)

        q = svc.subscribe()
        assert svc.subscriber_count == 1
        svc.unsubscribe(q)
        assert svc.subscriber_count == 0

        loop.close()

    def test_serialise_cycle_fields(self):
        result = _make_cycle_result(42, rejected=True, risk=RiskLevel.CRITICAL)
        d = _serialise_cycle(result)
        assert d["cycle_id"] == 42
        assert d["was_rejected"] is True
        assert d["risk_level"] == "CRITICAL"
        assert d["type"] == "cycle"
        assert "latency_ms" in d

    def test_serialise_cycle_hardware_metadata_is_json_safe_and_deduped(self):
        result = _make_cycle_result(43)
        result.guard_results = [
            GuardResult.success(
                guard_name="HardwareGuard",
                layer=GuardLayer.L3,
                reason="ok",
                metadata={
                    "host_health": {"cpu_percent": np.float32(12.5)},
                    "temperatures": {"m1": np.float64(31.2)},
                    "currents": np.array([0.1, 0.2]),
                    "voltages": {"m1": np.float32(7.4)},
                    "_latency_ms": np.float64(201.0),
                },
            ),
            GuardResult.success(
                guard_name="host_health",
                layer="L3",
                reason="ok",
                metadata={"host_health": {"memory_percent": np.float64(44.0)}},
            ),
        ]

        d = _serialise_cycle(result)

        import msgspec

        from dam.services.serialization import msgspec_enc_hook

        serialized = msgspec.json.encode(d, enc_hook=msgspec_enc_hook)
        parsed = json.loads(serialized)

        assert parsed["hardware"]["host_health"] == {"memory_percent": 44.0}
        assert parsed["hardware"]["guards"]["hardware_watchdog"]["currents"] == [0.1, 0.2]
        assert "host_health" not in parsed["hardware"]["guards"]["hardware_watchdog"]
        assert "_latency_ms" not in parsed["hardware"]["guards"]["hardware_watchdog"]
        assert "host_health" not in parsed["hardware"]["guards"]

    def test_push_raw(self):
        svc = TelemetryService()
        svc.push_raw({"type": "custom", "data": 42})
        hist = svc.get_history()
        assert hist[-1]["type"] == "custom"

    # ── perf enrichment via MetricBus ──────────────────────────────────────

    def _make_metric_bus_stub(
        self,
        stages: dict | None = None,
        layers: dict | None = None,
        guards: dict | None = None,
    ):
        """Return a minimal object that satisfies the MetricBus.snapshot() contract."""
        from unittest.mock import MagicMock

        mb = MagicMock()
        mb.snapshot.return_value = {
            "stages": stages
            or {"source": 1.0, "policy": 2.0, "guards": 3.0, "sink": 0.5, "total": 6.5},
            "layers": layers or {"L2": 3.0},
            "guards": guards or {"motion_guard": 1.5},
        }
        return mb

    def test_push_without_metric_bus_has_no_perf_key(self):
        svc = TelemetryService()  # no metric_bus
        svc.push(_make_cycle_result(0))
        event = svc.get_history()[-1]
        assert "perf" not in event

    def test_push_with_metric_bus_adds_perf_key(self):
        mb = self._make_metric_bus_stub()
        svc = TelemetryService(metric_bus=mb, cycle_budget_ms=20.0)
        svc.push(_make_cycle_result(0))
        event = svc.get_history()[-1]
        assert "perf" in event

    def test_perf_contains_expected_sub_keys(self):
        mb = self._make_metric_bus_stub()
        svc = TelemetryService(metric_bus=mb, cycle_budget_ms=20.0)
        svc.push(_make_cycle_result(0))
        perf = svc.get_history()[-1]["perf"]
        assert "stages" in perf
        assert "layers" in perf
        assert "guards" in perf
        assert "deadline_ms" in perf
        assert "slack_ms" in perf

    def test_perf_deadline_and_slack_computed_correctly(self):
        mb = self._make_metric_bus_stub(stages={"total": 8.0})
        svc = TelemetryService(metric_bus=mb, cycle_budget_ms=20.0)
        svc.push(_make_cycle_result(0))
        perf = svc.get_history()[-1]["perf"]
        assert abs(perf["deadline_ms"] - 20.0) < 1e-9
        assert abs(perf["slack_ms"] - 12.0) < 1e-9  # 20 - 8

    def test_perf_negative_slack_when_over_budget(self):
        mb = self._make_metric_bus_stub(stages={"total": 25.0})
        svc = TelemetryService(metric_bus=mb, cycle_budget_ms=20.0)
        svc.push(_make_cycle_result(0))
        perf = svc.get_history()[-1]["perf"]
        assert perf["slack_ms"] < 0

    def test_perf_stages_data_matches_metric_bus(self):
        expected_stages = {"source": 1.1, "policy": 3.2, "guards": 5.8, "sink": 0.7, "total": 10.8}
        mb = self._make_metric_bus_stub(stages=expected_stages)
        svc = TelemetryService(metric_bus=mb, cycle_budget_ms=20.0)
        svc.push(_make_cycle_result(0))
        perf = svc.get_history()[-1]["perf"]
        for k, v in expected_stages.items():
            assert abs(perf["stages"][k] - v) < 1e-9

    def test_perf_does_not_pollute_cycle_result(self):
        """CycleResult object must not be modified by TelemetryService."""
        mb = self._make_metric_bus_stub()
        svc = TelemetryService(metric_bus=mb, cycle_budget_ms=20.0)
        result = _make_cycle_result(0)
        original_latency = dict(result.latency_ms)
        svc.push(result)
        assert result.latency_ms == original_latency
        assert not hasattr(result, "perf")

    def test_snapshot_called_once_per_push(self):
        mb = self._make_metric_bus_stub()
        svc = TelemetryService(metric_bus=mb, cycle_budget_ms=20.0)
        for _ in range(3):
            svc.push(_make_cycle_result(0))
        assert mb.snapshot.call_count == 3


# ── RiskLogService ────────────────────────────────────────────────────────────


class TestRiskLogService:
    def test_record_and_query(self):
        svc = RiskLogService()
        svc.record(_make_cycle_result(0, risk=RiskLevel.ELEVATED))
        events = svc.query()
        assert len(events) == 1
        assert events[0].risk_level == "ELEVATED"

    def test_query_rejected_only(self):
        svc = RiskLogService()
        svc.record(_make_cycle_result(0, rejected=False))
        svc.record(_make_cycle_result(1, rejected=True))
        events = svc.query(rejected_only=True)
        assert len(events) == 1
        assert events[0].cycle_id == 1

    def test_query_clamped_only(self):
        svc = RiskLogService()
        svc.record(_make_cycle_result(0, clamped=False))
        svc.record(_make_cycle_result(1, clamped=True))
        events = svc.query(clamped_only=True)
        assert len(events) == 1

    def test_query_min_risk_level(self):
        svc = RiskLogService()
        svc.record(_make_cycle_result(0, risk=RiskLevel.NORMAL))
        svc.record(_make_cycle_result(1, risk=RiskLevel.CRITICAL))
        events = svc.query(min_risk_level="CRITICAL")
        assert all(e.risk_level == "CRITICAL" for e in events)

    def test_query_limit(self):
        svc = RiskLogService()
        for i in range(20):
            svc.record(_make_cycle_result(i))
        events = svc.query(limit=5)
        assert len(events) == 5

    def test_get_by_id(self):
        svc = RiskLogService()
        svc.record(_make_cycle_result(0))
        svc.record(_make_cycle_result(1))
        ev = svc.get_by_id(0)
        assert ev is not None
        assert ev.cycle_id == 0

    def test_get_by_id_missing(self):
        svc = RiskLogService()
        assert svc.get_by_id(999) is None

    def test_export_json(self):
        import json

        svc = RiskLogService()
        svc.record(_make_cycle_result(5))
        data = json.loads(svc.export_json())
        assert isinstance(data, list)
        assert data[0]["cycle_id"] == 5

    def test_export_csv(self):
        svc = RiskLogService()
        svc.record(_make_cycle_result(3))
        csv_str = svc.export_csv()
        assert "event_id" in csv_str
        assert "risk_level" in csv_str

    def test_stats_empty(self):
        svc = RiskLogService()
        s = svc.stats()
        assert s["total"] == 0

    def test_stats_populated(self):
        svc = RiskLogService()
        svc.record(_make_cycle_result(0, rejected=True, risk=RiskLevel.CRITICAL))
        svc.record(_make_cycle_result(1, clamped=True, risk=RiskLevel.ELEVATED))
        svc.record(_make_cycle_result(2, risk=RiskLevel.NORMAL))
        s = svc.stats()
        assert s["total"] == 3
        assert s["rejected"] == 1
        assert s["clamped"] == 1

    def test_clear(self):
        svc = RiskLogService()
        svc.record(_make_cycle_result(0))
        svc.clear()
        assert svc.stats()["total"] == 0

    def test_max_events_eviction(self):
        svc = RiskLogService(max_events=5)
        for i in range(10):
            svc.record(_make_cycle_result(i))
        assert svc.stats()["total"] == 5

    def test_query_since_until(self):
        svc = RiskLogService()
        svc.record(_make_cycle_result(0))
        now = time.time()
        events = svc.query(until=now + 10)
        assert len(events) >= 1

    def test_event_to_dict(self):
        svc = RiskLogService()
        svc.record(_make_cycle_result(7))
        ev = svc.query()[0]
        d = ev.to_dict()
        assert d["cycle_id"] == 7
        assert "timestamp" in d

    def test_event_to_dict_and_export_json_handle_numpy_metadata(self):
        svc = RiskLogService()
        result = _make_cycle_result(8, clamped=True, risk=RiskLevel.ELEVATED)
        result.guard_results = [
            GuardResult(
                decision=GuardDecision.CLAMP,
                guard_name="motion",
                layer=GuardLayer.L1,
                reason="qp clamp",
                metadata={
                    "joint_position_limits": {
                        "upper": np.array([1.0, 2.0]),
                        "scale": np.float32(0.5),
                    }
                },
            )
        ]
        svc.record(result, perf={"layers": {"L1": np.float64(1.25)}})

        ev = svc.query()[0]
        d = ev.to_dict()
        json.dumps(d)
        assert d["guard_results"][0]["metadata"]["joint_position_limits"]["upper"] == [
            1.0,
            2.0,
        ]
        assert d["guard_results"][0]["metadata"]["joint_position_limits"]["scale"] == 0.5
        exported = json.loads(svc.export_json())
        assert exported[0]["perf"]["layers"]["L1"] == 1.25


# ── BoundaryConfigService ──────────────────────────────────────────────────────


class TestBoundaryConfigService:
    def _sample(self, name="ws") -> dict:
        return {
            "name": name,
            "type": "single",
            "nodes": [{"node_id": "default", "constraint": {"max_speed": 1.0}}],
        }

    def test_create_and_list(self):
        svc = BoundaryConfigService()
        svc.create(self._sample("ws"))
        cfgs = svc.list()
        assert len(cfgs) == 1
        assert cfgs[0]["name"] == "ws"

    def test_get(self):
        svc = BoundaryConfigService()
        svc.create(self._sample("ws"))
        cfg = svc.get("ws")
        assert cfg is not None
        assert cfg["type"] == "single"

    def test_get_missing(self):
        svc = BoundaryConfigService()
        assert svc.get("nonexistent") is None

    def test_create_duplicate_raises(self):
        svc = BoundaryConfigService()
        svc.create(self._sample("ws"))
        with pytest.raises(ValueError, match="already exists"):
            svc.create(self._sample("ws"))

    def test_update(self):
        svc = BoundaryConfigService()
        svc.create(self._sample("ws"))
        updated = svc.update("ws", {"name": "ws", "type": "list"})
        assert updated["type"] == "list"

    def test_update_missing_raises(self):
        svc = BoundaryConfigService()
        with pytest.raises(KeyError):
            svc.update("nonexistent", {"name": "nonexistent"})

    def test_delete(self):
        svc = BoundaryConfigService()
        svc.create(self._sample("ws"))
        assert svc.delete("ws") is True
        assert svc.get("ws") is None

    def test_delete_missing_returns_false(self):
        svc = BoundaryConfigService()
        assert svc.delete("nonexistent") is False

    def test_upsert_creates(self):
        svc = BoundaryConfigService()
        svc.upsert(self._sample("ws"))
        assert svc.get("ws") is not None

    def test_upsert_replaces(self):
        svc = BoundaryConfigService()
        svc.create(self._sample("ws"))
        svc.upsert({"name": "ws", "type": "graph"})
        assert svc.get("ws")["type"] == "graph"

    def test_create_no_name_raises(self):
        svc = BoundaryConfigService()
        with pytest.raises(ValueError, match="non-empty"):
            svc.create({"type": "single"})

    def test_load_from_stackfile(self):
        svc = BoundaryConfigService()
        n = svc.load_from_stackfile(
            {
                "workspace": {"type": "single", "nodes": []},
                "approach": {"type": "list", "nodes": []},
            }
        )
        assert n == 2
        assert svc.get("workspace") is not None


# ── RuntimeControlService ──────────────────────────────────────────────────────


class TestRuntimeControlService:
    def _mock_runtime(self):
        rt = MagicMock()
        rt.step.return_value = _make_cycle_result(0)
        rt._task_config = {"default": []}
        rt._active_task = None
        rt._active_container_names = []
        rt._control_frequency_hz = 30.0
        return rt

    def _mock_runner(self, rt=None):
        rt = rt or self._mock_runtime()
        return SimulationRunner(rt, control_frequency_hz=getattr(rt, "_control_frequency_hz", 30.0))

    def test_initial_state(self):
        svc = RuntimeControlService()
        assert svc.state == RuntimeState.IDLE

    def test_status_no_runtime(self):
        svc = RuntimeControlService()
        s = svc.status()
        assert s["state"] == "idle"
        assert s["has_runtime"] is False

    def test_start_without_runtime_raises(self):
        svc = RuntimeControlService()
        with pytest.raises(RuntimeError, match="No Runner"):
            svc.start()

    def test_attach_runner(self):
        svc = RuntimeControlService()
        svc.attach_runner(self._mock_runner())
        assert svc.status()["has_runtime"] is True

    def test_start_and_stop(self):
        svc = RuntimeControlService()
        svc.attach_runner(self._mock_runner())
        with svc._lock:
            svc._backend_state = BackendState.READY
        svc.start(n_cycles=2, cycle_budget_ms=1.0)
        assert svc.state == RuntimeState.RUNNING
        # Give the background thread a moment
        time.sleep(0.05)
        svc.stop()
        time.sleep(0.05)
        assert svc.state in (RuntimeState.STOPPED, RuntimeState.RUNNING)

    def test_pause_when_not_running_returns_false(self):
        svc = RuntimeControlService()
        assert svc.pause() is False

    def test_resume_when_not_paused_returns_false(self):
        svc = RuntimeControlService()
        assert svc.resume() is False

    def test_stop_when_idle_returns_false(self):
        svc = RuntimeControlService()
        assert svc.stop() is False

    def test_emergency_stop(self):
        svc = RuntimeControlService()
        svc.attach_runner(self._mock_runner())
        with svc._lock:
            svc._backend_state = BackendState.READY
        svc.start(n_cycles=100, cycle_budget_ms=1.0)
        time.sleep(0.02)
        svc.emergency_stop()
        assert svc.state == RuntimeState.EMERGENCY

    def test_reset_from_stopped(self):
        svc = RuntimeControlService()
        svc.attach_runner(self._mock_runner())
        with svc._lock:
            svc._backend_state = BackendState.READY
        svc.start(n_cycles=1, cycle_budget_ms=1.0)
        time.sleep(0.1)
        svc.stop()
        time.sleep(0.05)
        ok = svc.reset()
        assert ok is True
        assert svc.state == RuntimeState.IDLE

    def test_state_change_callback(self):
        svc = RuntimeControlService()
        states = []
        svc.on_state_change(lambda s: states.append(s))
        svc.emergency_stop()
        assert RuntimeState.EMERGENCY in states

    def test_cycle_count_increments(self):
        svc = RuntimeControlService()
        svc.attach_runner(self._mock_runner())
        with svc._lock:
            svc._backend_state = BackendState.READY
        svc.start(n_cycles=3, cycle_budget_ms=1.0)
        time.sleep(0.2)
        assert svc.status()["cycle_count"] >= 0  # may have run some cycles

    # ── New status() fields ────────────────────────────────────────────────────

    def test_status_no_runtime_new_fields(self):
        """status() returns safe defaults for all new fields when no runtime attached."""
        svc = RuntimeControlService()
        s = svc.status()
        assert s["active_task"] is None
        assert s["active_boundaries"] == []
        assert s["control_frequency_hz"] == pytest.approx(30.0)
        assert s["available_tasks"] == []
        assert s["planned_task"] is None
        assert s["planned_boundaries"] == []

    def test_status_with_runtime_exposes_task_config(self):
        """status() reads _task_config from runtime and populates planned_task/boundaries."""
        svc = RuntimeControlService()
        rt = self._mock_runtime()
        rt._task_config = {
            "default": ["workspace", "approach"],
            "pick": ["workspace"],
        }
        rt._active_task = None
        rt._active_container_names = []
        rt._control_frequency_hz = 30.0
        svc.attach_runner(self._mock_runner(rt))
        s = svc.status()
        assert s["available_tasks"] == ["default", "pick"]
        assert s["planned_task"] == "default"
        assert s["planned_boundaries"] == ["workspace", "approach"]
        assert s["control_frequency_hz"] == pytest.approx(30.0)

    def test_status_planned_task_fallback_to_first(self):
        """When no 'default' task, planned_task falls back to first key."""
        svc = RuntimeControlService()
        rt = self._mock_runtime()
        rt._task_config = {"pick": ["workspace"], "place": []}
        rt._active_task = None
        rt._active_container_names = []
        rt._control_frequency_hz = 30.0
        svc.attach_runner(self._mock_runner(rt))
        s = svc.status()
        assert s["planned_task"] == "pick"
        assert s["planned_boundaries"] == ["workspace"]

    def test_status_active_task_propagates(self):
        """When runtime is running, active_task/boundaries reflect live state."""
        svc = RuntimeControlService()
        rt = self._mock_runtime()
        rt._task_config = {"default": ["ws"]}
        rt._active_task = "default"
        rt._active_container_names = ["ws"]
        rt._control_frequency_hz = 30.0
        svc.attach_runner(self._mock_runner(rt))
        s = svc.status()
        assert s["active_task"] == "default"
        assert s["active_boundaries"] == ["ws"]

    # ── startup_error ──────────────────────────────────────────────────────────

    def test_startup_error_none_by_default(self):
        svc = RuntimeControlService()
        assert svc.status()["startup_error"] is None

    def test_set_startup_error_appears_in_status(self):
        svc = RuntimeControlService()
        svc.set_startup_error("port /dev/ttyUSB0 not accessible")
        s = svc.status()
        assert s["startup_error"] == "port /dev/ttyUSB0 not accessible"

    def test_start_blocked_when_startup_error_set(self):
        svc = RuntimeControlService()
        svc.set_startup_error("hardware missing")
        with pytest.raises(RuntimeError, match="Cannot start"):
            svc.start()

    def test_start_allowed_when_no_startup_error(self):
        """start() without startup_error falls through to normal checks.

        (no runtime → RuntimeError).
        """
        svc = RuntimeControlService()
        with pytest.raises(RuntimeError, match="No Runner"):
            svc.start()

    def test_runner_start_failure_rolls_back_status(self):
        rt = self._mock_runtime()
        rt.start_task.side_effect = ValueError("unknown task")
        runner = self._mock_runner(rt)

        with pytest.raises(ValueError, match="unknown task"):
            runner.start("missing")

        assert runner.status == RunnerStatus.IDLE
        assert runner.error == "unknown task"

    def test_runner_start_task_does_not_start_recording(self):
        rt = self._mock_runtime()
        rt.start_recording = MagicMock()
        runner = self._mock_runner(rt)

        runner.start_task("default")

        rt.start_task.assert_called_once_with("default")
        rt.start_recording.assert_not_called()
        assert runner.status == RunnerStatus.IDLE

    def test_runner_start_task_then_start_records_one_segment(self):
        rt = self._mock_runtime()
        rt.start_recording = MagicMock()
        rt.stop_recording = MagicMock()
        rt.stop_task.side_effect = rt.stop_recording
        runner = self._mock_runner(rt)

        runner.start_task("default")

        assert runner.start("default", n_cycles=1, cycle_budget_ms=1.0) is True
        time.sleep(0.05)

        rt.start_recording.assert_called_once_with()
        rt.stop_recording.assert_called_once_with()

    def test_runner_start_starts_recording(self):
        rt = self._mock_runtime()
        rt.start_recording = MagicMock()
        rt.stop_recording = MagicMock()
        runner = self._mock_runner(rt)

        assert runner.start("default", n_cycles=1, cycle_budget_ms=1.0) is True

        rt.start_recording.assert_called_once_with()
        runner.stop()

    def test_runner_stop_closes_recording_before_next_start(self):
        rt = self._mock_runtime()
        rt.start_recording = MagicMock()
        rt.stop_recording = MagicMock()
        rt.stop_task.side_effect = rt.stop_recording
        runner = self._mock_runner(rt)

        assert runner.start("default", n_cycles=-1, cycle_budget_ms=1.0) is True
        assert runner.stop() is True
        assert runner.status == RunnerStatus.STOPPED

        assert runner.start("default", n_cycles=1, cycle_budget_ms=1.0) is True
        time.sleep(0.05)

        assert rt.start_recording.call_count == 2
        assert rt.stop_recording.call_count == 2

    def test_recheck_preserves_ros2_node(self, monkeypatch, tmp_path):
        import importlib

        import yaml

        from dam.runtime.factory import RuntimeFactory

        path = tmp_path / "ros2.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "version": "1",
                    "hardware": {
                        "preset": "generic_6dof",
                        "sources": {"arm": {"type": "ros2", "topic": "/joint_states"}},
                        "sinks": {"cmd": {"ref": "sources.arm", "topic": "/cmd"}},
                    },
                    "guards": [],
                    "tasks": {"default": {"description": "", "boundaries": []}},
                    "boundaries": {},
                }
            )
        )

        node = object()
        captured: dict[str, object] = {}
        new_runner = MagicMock()

        def fake_build_from_stackfile(stack_path: str, *, ros2_node=None):
            captured["stack_path"] = stack_path
            captured["ros2_node"] = ros2_node
            return new_runner

        monkeypatch.setattr(importlib, "reload", lambda module: module)
        monkeypatch.setattr(
            RuntimeFactory,
            "build_from_stackfile",
            staticmethod(fake_build_from_stackfile),
        )

        svc = RuntimeControlService()
        svc._ros2_node = node
        assert svc.recheck_hardware(str(path)) is True

        assert captured["stack_path"] == str(path)
        assert captured["ros2_node"] is node
        new_runner.connect.assert_called_once()
        new_runner.verify.assert_called_once()

    def test_opencv_source_rejects_camera_fields_under_params(self):
        from dam.adapter.lerobot.builder import LeRobotBuilder
        from dam.config.schema import StackfileConfig
        from dam.runtime.factory import RuntimeFactory

        cfg = StackfileConfig(
            **{
                "version": "1",
                "hardware": {
                    "preset": "generic_6dof",
                    "sources": {
                        "arm": {"type": "motor"},
                        "top": {"type": "opencv", "params": {"index": 0}},
                    },
                    "sinks": {"cmd": {"ref": "sources.arm"}},
                },
                "guards": [],
                "tasks": {"default": {"description": "", "boundaries": []}},
                "boundaries": {},
            }
        )

        with (
            patch.object(LeRobotBuilder, "build_robot", return_value=object()),
            patch.object(LeRobotBuilder, "build_policy", return_value=None),
            pytest.raises(ValueError, match="source top level"),
        ):
            RuntimeFactory.build_from_config(cfg)
