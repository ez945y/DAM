"""Slow-lane evaluator — worker scheduling, verdict gating, lane resolution,
telemetry decimation."""

from __future__ import annotations

import time
from types import SimpleNamespace

import numpy as np
import pytest

from dam.config.schema import SlowLaneConfig
from dam.guard.layer import GuardLayer
from dam.runtime.guard_runtime import GuardRuntime
from dam.runtime.slow_lane import SlowLaneWorker, SlowSnapshot, SlowVerdict
from dam.types.action import ActionProposal
from dam.types.observation import Observation
from dam.types.result import GuardDecision, GuardResult


def _obs(positions=None) -> Observation:
    return Observation(
        timestamp=time.monotonic(),
        joint_positions=np.asarray(positions or [0.0] * 6, dtype=np.float64),
    )


def _snapshot(cycle_id: int = 1) -> SlowSnapshot:
    return SlowSnapshot(
        obs=_obs(),
        action=ActionProposal(target_joint_positions=np.zeros(6)),
        trace_id=f"t-{cycle_id}",
        cycle_id=cycle_id,
        published_at=time.monotonic(),
    )


def _wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


# ── SlowLaneWorker ────────────────────────────────────────────────────────────


class TestSlowLaneWorker:
    def test_evaluates_latest_snapshot_and_publishes_verdict(self):
        seen: list[int] = []

        def evaluate(snapshot):
            seen.append(snapshot.cycle_id)
            return [GuardResult.pass_result("ood", GuardLayer.L0)]

        worker = SlowLaneWorker(evaluate, frequency_hz=200.0)
        worker.start()
        try:
            worker.submit(_snapshot(cycle_id=1))
            assert _wait_until(lambda: worker.latest_verdict() is not None)
            verdict = worker.latest_verdict()
            assert verdict.basis_cycle_id == 1
            assert verdict.results[0].decision == GuardDecision.PASS
        finally:
            worker.stop()

    def test_latest_wins_under_burst(self):
        """A burst of snapshots between evaluations: only the newest is scored."""
        seen: list[int] = []

        def evaluate(snapshot):
            seen.append(snapshot.cycle_id)
            return []

        worker = SlowLaneWorker(evaluate, frequency_hz=50.0)
        # Submit a burst BEFORE starting — mailbox holds only the last one.
        for c in range(1, 6):
            worker.submit(_snapshot(cycle_id=c))
        worker.start()
        try:
            assert _wait_until(lambda: len(seen) >= 1)
            assert seen[0] == 5
        finally:
            worker.stop()

    def test_same_cycle_not_rescored(self):
        calls: list[int] = []

        def evaluate(snapshot):
            calls.append(snapshot.cycle_id)
            return []

        worker = SlowLaneWorker(evaluate, frequency_hz=500.0)
        worker.start()
        try:
            worker.submit(_snapshot(cycle_id=7))
            assert _wait_until(lambda: len(calls) == 1)
            time.sleep(0.05)  # several periods with no new snapshot
            assert calls == [7]
        finally:
            worker.stop()

    def test_evaluation_exception_keeps_worker_alive(self):
        calls: list[int] = []

        def evaluate(snapshot):
            calls.append(snapshot.cycle_id)
            if snapshot.cycle_id == 1:
                raise RuntimeError("boom")
            return [GuardResult.pass_result("ood", GuardLayer.L0)]

        worker = SlowLaneWorker(evaluate, frequency_hz=200.0)
        worker.start()
        try:
            worker.submit(_snapshot(cycle_id=1))
            assert _wait_until(lambda: 1 in calls)
            assert worker.latest_verdict() is None  # failed run publishes nothing
            worker.submit(_snapshot(cycle_id=2))
            assert _wait_until(lambda: worker.latest_verdict() is not None)
            assert worker.latest_verdict().basis_cycle_id == 2
        finally:
            worker.stop()

    def test_verdict_age_falls_back_to_start_time(self):
        worker = SlowLaneWorker(lambda s: [], frequency_hz=100.0)
        assert worker.verdict_age_s(time.monotonic()) is None  # not started
        worker.start()
        try:
            age = worker.verdict_age_s(time.monotonic())
            assert age is not None and age >= 0.0
        finally:
            worker.stop()

    def test_stop_joins_thread(self):
        worker = SlowLaneWorker(lambda s: [], frequency_hz=100.0)
        worker.start()
        assert worker.running
        worker.stop()
        assert not worker.running


# ── Lane resolution ───────────────────────────────────────────────────────────


def _bare_runtime() -> GuardRuntime:
    return GuardRuntime(guards=[], boundary_containers={}, task_config={})


def _fake_guard(layer: GuardLayer, lane: str | None = None):
    g = SimpleNamespace(get_layer=lambda: layer)
    if lane is not None:
        g._lane = lane
    return g


class TestLaneResolution:
    def test_default_by_layer(self):
        rt = _bare_runtime()
        rt._boundary_to_kind = {"b0": "ood", "b1": "motion", "b2": "execution", "b3": "hardware"}
        rt._guards_by_kind = {
            "ood": _fake_guard(GuardLayer.L0),
            "motion": _fake_guard(GuardLayer.L1),
            "execution": _fake_guard(GuardLayer.L2),
            "hardware": _fake_guard(GuardLayer.L3),
        }
        assert rt._lane_of("b0") == "slow"
        assert rt._lane_of("b1") == "fast"
        assert rt._lane_of("b2") == "slow"
        assert rt._lane_of("b3") == "fast"

    def test_guard_override_wins(self):
        rt = _bare_runtime()
        rt._boundary_to_kind = {"b0": "ood", "b1": "motion"}
        rt._guards_by_kind = {
            "ood": _fake_guard(GuardLayer.L0, lane="fast"),
            "motion": _fake_guard(GuardLayer.L1, lane="slow"),
        }
        assert rt._lane_of("b0") == "fast"
        assert rt._lane_of("b1") == "slow"

    def test_unknown_boundary_stays_fast(self):
        rt = _bare_runtime()
        assert rt._lane_of("nope") == "fast"


class TestGuardLaneOverrideParsing:
    def test_stackfile_guards_section_sets_lane(self):
        from dam.config.schema import StackfileConfig
        from dam.runtime._stackfile_builder import _apply_guard_overrides

        config = StackfileConfig(
            **{
                "guards": [{"L0": "ood", "lane": "fast"}],
                "tasks": {},
                "boundaries": {},
            }
        )
        ood = SimpleNamespace(
            is_always_on=lambda: False, get_phase=lambda: 0, set_phase=lambda *a, **k: None
        )
        runtime = SimpleNamespace()
        _apply_guard_overrides(config, {"ood": ood}, runtime)
        assert ood._lane == "fast"

    def test_invalid_lane_raises(self):
        from dam.config.schema import StackfileConfig
        from dam.runtime._stackfile_builder import _apply_guard_overrides

        config = StackfileConfig(
            **{
                "guards": [{"L0": "ood", "lane": "medium"}],
                "tasks": {},
                "boundaries": {},
            }
        )
        with pytest.raises(ValueError, match="lane must be"):
            _apply_guard_overrides(config, {"ood": SimpleNamespace()}, SimpleNamespace())


# ── Verdict gating in validate() ─────────────────────────────────────────────


class _StubWorker:
    def __init__(self, verdict: SlowVerdict | None, age_s: float | None):
        self._verdict = verdict
        self._age = age_s

    def latest_verdict(self):
        return self._verdict

    def verdict_age_s(self, now):
        return self._age


def _gated_runtime(cfg: SlowLaneConfig, worker: _StubWorker) -> GuardRuntime:
    rt = _bare_runtime()
    rt._slow_lane_config = cfg
    rt._slow_lane = worker
    rt._slow_stages = [object()]  # truthy — slow lane active
    return rt


class TestVerdictGating:
    def test_fresh_reject_verdict_gates(self):
        verdict = SlowVerdict(
            results=[GuardResult.reject("ood", GuardLayer.L0, reason="ood detected")],
            basis_cycle_id=3,
            produced_at=time.monotonic(),
        )
        rt = _gated_runtime(SlowLaneConfig(), _StubWorker(verdict, age_s=0.01))
        extra = rt._slow_lane_extra_results(time.monotonic())
        assert extra is not None
        assert extra[0].decision == GuardDecision.REJECT
        assert extra[0].metadata["slow_lane"] is True
        assert "verdict_age_ms" in extra[0].metadata

    def test_clamp_verdict_escalates_to_reject(self):
        from dam.types.action import ValidatedAction

        clamped = ValidatedAction(target_joint_positions=np.zeros(6), was_clamped=True)
        verdict = SlowVerdict(
            results=[GuardResult.clamp(clamped, "execution", GuardLayer.L2, reason="slow clamp")],
            produced_at=time.monotonic(),
        )
        rt = _gated_runtime(SlowLaneConfig(), _StubWorker(verdict, age_s=0.01))
        extra = rt._slow_lane_extra_results(time.monotonic())
        assert extra[0].decision == GuardDecision.REJECT
        assert extra[0].clamped_action is None

    def test_stale_verdict_rejects_by_default(self):
        cfg = SlowLaneConfig(max_staleness_ms=100.0)
        rt = _gated_runtime(cfg, _StubWorker(None, age_s=1.0))
        extra = rt._slow_lane_extra_results(time.monotonic())
        assert extra is not None
        assert extra[-1].guard_name == "slow_lane_watchdog"
        assert extra[-1].decision == GuardDecision.REJECT

    def test_stale_verdict_warn_mode_passes(self):
        cfg = SlowLaneConfig(max_staleness_ms=100.0, stale_action="warn")
        rt = _gated_runtime(cfg, _StubWorker(None, age_s=1.0))
        assert rt._slow_lane_extra_results(time.monotonic()) is None

    def test_fresh_verdict_no_watchdog(self):
        cfg = SlowLaneConfig(max_staleness_ms=500.0)
        verdict = SlowVerdict(
            results=[GuardResult.pass_result("ood", GuardLayer.L0)],
            produced_at=time.monotonic(),
        )
        rt = _gated_runtime(cfg, _StubWorker(verdict, age_s=0.05))
        extra = rt._slow_lane_extra_results(time.monotonic())
        assert all(r.guard_name != "slow_lane_watchdog" for r in extra)

    def test_disabled_slow_lane_returns_none(self):
        rt = _bare_runtime()
        assert rt._slow_lane_extra_results(time.monotonic()) is None


# ── Lane split end-to-end (stackfile → start_task) ───────────────────────────


def _lane_config(slow_lane: dict | None, guards: list | None = None):
    from dam.config.schema import StackfileConfig
    from dam.guard.builtin import register_all

    register_all()

    safety: dict = {"control_hz": 60}
    if slow_lane is not None:
        safety["slow_lane"] = slow_lane
    return StackfileConfig(
        **{
            "version": "1",
            "guards": guards or [],
            "safety": safety,
            "tasks": {"default": {"boundaries": ["vel", "speed_task"]}},
            "boundaries": {
                "vel": {
                    "layer": "L1",
                    "type": "single",
                    "nodes": [
                        {"callback": "joint_velocity_limit", "params": {"max_velocities": [1.0]}}
                    ],
                },
                "speed_task": {
                    "layer": "L2",
                    "type": "single",
                    "nodes": [{"callback": "task_joint_speed_limit", "params": {"max_speed": 2.0}}],
                },
            },
        }
    )


class TestLaneSplitIntegration:
    def test_start_task_splits_lanes_and_starts_worker(self):
        rt = GuardRuntime._from_config(_lane_config({"task_hz": 20}))
        try:
            rt.start_task("default")
            assert rt._slow_active_names == ["speed_task"]
            assert rt._slow_lane is not None and rt._slow_lane.running
            assert rt._slow_stages is not None
            # Fast stages must not contain the L2 guard's stage.
            fast_stage_names = [s.name for s in rt._stages]
            assert "L2" not in fast_stage_names
            slow_stage_names = [s.name for s in rt._slow_stages]
            assert slow_stage_names == ["L2"]
            rt.stop_task()
            assert rt._slow_lane is None
        finally:
            rt.shutdown()

    def test_no_slow_lane_config_keeps_single_pipeline(self):
        rt = GuardRuntime._from_config(_lane_config(None))
        try:
            rt.start_task("default")
            assert rt._slow_lane is None
            stage_names = [s.name for s in rt._stages]
            assert "L1" in stage_names and "L2" in stage_names
        finally:
            rt.shutdown()

    def test_guard_lane_override_moves_l2_to_fast(self):
        rt = GuardRuntime._from_config(
            _lane_config({"task_hz": 20}, guards=[{"L2": "execution", "lane": "fast"}])
        )
        try:
            rt.start_task("default")
            assert rt._slow_active_names == []
            assert rt._slow_lane is None  # nothing slow → no worker
            stage_names = [s.name for s in rt._stages]
            assert "L1" in stage_names and "L2" in stage_names
        finally:
            rt.shutdown()


# ── Schema ────────────────────────────────────────────────────────────────────


class TestSlowLaneConfigSchema:
    def test_defaults(self):
        cfg = SlowLaneConfig()
        assert cfg.task_hz == 10.0
        assert cfg.stale_action == "reject"

    def test_invalid_stale_action(self):
        with pytest.raises(ValueError):
            SlowLaneConfig(stale_action="ignore")

    def test_invalid_frequency(self):
        with pytest.raises(ValueError):
            SlowLaneConfig(task_hz=0)


# ── Telemetry decimation (read_telemetry ABI) ────────────────────────────────


class _CountingBusRobot:
    """Modern-API mock whose bus counts sync_read calls."""

    def __init__(self):
        self.sync_read_calls = 0
        robot = self

        class _Bus:
            def sync_read(self, register):
                robot.sync_read_calls += 1
                return {"shoulder_pan": 40.0, "gripper": 38.0}

        self.bus = _Bus()

    def get_observation(self):
        names = [
            "shoulder_pan",
            "shoulder_lift",
            "elbow_flex",
            "wrist_flex",
            "wrist_roll",
            "gripper",
        ]
        return {f"{n}.pos": 10.0 for n in names}


class TestTelemetryDecimation:
    def _adapter(self, telemetry_hz):
        from dam.adapter.lerobot.adapter import LeRobotAdapter

        adapter = LeRobotAdapter(_CountingBusRobot(), telemetry_hz=telemetry_hz)
        adapter.set_observation_channels(["temperature"])
        return adapter

    def test_legacy_reads_every_cycle(self):
        adapter = self._adapter(telemetry_hz=None)
        robot = adapter._robot
        adapter.read_telemetry(now=0.0)
        adapter.read_telemetry(now=0.001)
        assert robot.sync_read_calls == 2

    def test_decimated_reads_serve_cache_between(self):
        adapter = self._adapter(telemetry_hz=2.0)  # period 0.5 s
        robot = adapter._robot
        _, st1, ts1 = adapter.read_telemetry(now=0.0)
        _, st2, ts2 = adapter.read_telemetry(now=0.1)  # within period → cache
        assert robot.sync_read_calls == 1
        assert ts1 == ts2 == 0.0
        assert st2["temperatures"] == st1["temperatures"]
        _, _, ts3 = adapter.read_telemetry(now=0.6)  # past period → fresh read
        assert robot.sync_read_calls == 2
        assert ts3 == 0.6

    def test_observation_carries_telemetry_timestamp(self):
        adapter = self._adapter(telemetry_hz=2.0)
        obs = adapter.read()
        assert obs.metadata.get("telemetry_timestamp") is not None
        assert "hardware_status" in obs.metadata
