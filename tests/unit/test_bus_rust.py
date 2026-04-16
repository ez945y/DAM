"""Bus tests — verifies the Rust extension (dam_rs) via the canonical dam.bus import.

dam_rs is a mandatory dependency.  If it is not compiled, importing dam.bus
raises ImportError and every test in this module fails immediately with a clear
error message (build the extension first).

Run:
    pytest tests/unit/test_bus_rust.py -v
"""

import time

from dam.bus import (
    ActionBus,
    MetricBus,
    ObservationBus,
    PipelineMetricBus,
    RiskController,
    WatchdogTimer,
)

# ── ObservationBus ────────────────────────────────────────────────────────


class TestObservationBus:
    def test_write_read_latest(self):
        bus = ObservationBus(10)
        bus.write(b"hello")
        bus.write(b"world")
        assert bus.read_latest() == b"world"

    def test_empty_returns_none(self):
        bus = ObservationBus(10)
        assert bus.read_latest() is None
        assert bus.is_empty()

    def test_len(self):
        bus = ObservationBus(10)
        bus.write(b"a")
        bus.write(b"b")
        assert len(bus) == 2
        assert bus.len() == 2

    def test_capacity_evicts(self):
        bus = ObservationBus(3)
        for i in range(5):
            bus.write(bytes([i]))
        assert bus.len() == 3
        assert bus.read_latest() == bytes([4])

    def test_read_window_chronological(self):
        bus = ObservationBus(10)
        for i in range(5):
            bus.write(bytes([i]))
        w = bus.read_window(3)
        assert [bytes([b]) for b in [2, 3, 4]] == w

    def test_read_window_larger_than_buf(self):
        bus = ObservationBus(10)
        bus.write(b"x")
        assert len(bus.read_window(100)) == 1

    def test_capacity_property(self):
        bus = ObservationBus(42)
        assert bus.capacity == 42


# ── WatchdogTimer ─────────────────────────────────────────────────────────


class TestWatchdogTimer:
    def test_not_emergency_on_create(self):
        w = WatchdogTimer(500.0)
        assert not w.is_emergency()

    def test_ping_prevents_emergency(self):
        w = WatchdogTimer(60.0)
        w.arm()
        for _ in range(6):
            time.sleep(0.012)
            w.ping()
        w.disarm()
        assert not w.is_emergency()

    def test_missed_ping_triggers(self):
        w = WatchdogTimer(20.0)
        w.arm()
        time.sleep(0.100)
        assert w.is_emergency()
        w.disarm()

    def test_clear_emergency(self):
        w = WatchdogTimer(10.0)
        w.arm()
        time.sleep(0.060)
        assert w.is_emergency()
        w.clear_emergency()
        assert not w.is_emergency()

    def test_deadline_ms_property(self):
        w = WatchdogTimer(77.5)
        assert abs(w.deadline_ms - 77.5) < 1e-6

    def test_elapsed_since_ping(self):
        w = WatchdogTimer(1000.0)
        w.ping()
        time.sleep(0.030)
        assert w.elapsed_since_ping_ms() >= 20.0


# ── RiskController ────────────────────────────────────────────────────────


class TestRiskController:
    def _rc(self):
        return RiskController(10, 3, 2)

    def test_fresh_is_normal(self):
        assert self._rc().risk_level() == 0

    def test_rejects_elevate(self):
        rc = self._rc()
        rc.record(False, True)
        rc.record(False, True)
        assert rc.risk_level() == 1

    def test_emergency_overrides(self):
        rc = self._rc()
        rc.trigger_emergency()
        assert rc.risk_level() == 3

    def test_clear_emergency(self):
        rc = self._rc()
        rc.trigger_emergency()
        rc.clear_emergency()
        assert not rc.is_emergency()

    def test_stats_dict(self):
        rc = self._rc()
        rc.record(True, False)
        rc.record(False, True)
        s = rc.stats()
        assert s["clamp_count"] == 1
        assert s["reject_count"] == 1
        assert "risk_level" in s
        assert "is_emergency" in s


# ── MetricBus (Rust base — flat push API) ────────────────────────────────


class TestMetricBus:
    # ── Base API ──────────────────────────────────────────────────────────

    def test_push_latest(self):
        mb = MetricBus()
        mb.push("g", 1.5)
        mb.push("g", 2.5)
        assert abs(mb.latest("g") - 2.5) < 1e-9

    def test_missing_is_none(self):
        mb = MetricBus()
        assert mb.latest("missing") is None

    def test_all_latest(self):
        mb = MetricBus()
        mb.push("a", 1.0)
        mb.push("b", 2.0)
        all_l = mb.all_latest()
        assert set(all_l.keys()) == {"a", "b"}

    def test_mean(self):
        mb = MetricBus()
        for v in [1.0, 2.0, 3.0, 4.0]:
            mb.push("g", v)
        assert abs(mb.mean("g") - 2.5) < 1e-9

    def test_max(self):
        mb = MetricBus()
        for v in [3.0, 1.0, 4.0]:
            mb.push("g", v)
        assert abs(mb.max("g") - 4.0) < 1e-9

    def test_guard_names(self):
        mb = MetricBus()
        mb.push("x", 1.0)
        mb.push("y", 2.0)
        assert sorted(mb.guard_names()) == ["x", "y"]

    def test_clear(self):
        mb = MetricBus()
        mb.push("g", 42.0)
        mb.clear()
        assert mb.latest("g") is None


# ── PipelineMetricBus (Python adapter — structured API) ───────────────────


class TestPipelineMetricBus:
    # ── push_guard — per-guard with layer info ────────────────────────────

    def test_push_guard_updates_per_guard_latest(self):
        mb = PipelineMetricBus()
        mb.push_guard("joint_limit", 2, 1.5)
        mb.push_guard("joint_limit", 2, 3.0)
        assert abs(mb.latest("joint_limit") - 3.0) < 1e-9

    def test_push_guard_multiple_guards_and_layers(self):
        mb = PipelineMetricBus()
        mb.push_guard("ood", 0, 2.0)
        mb.push_guard("motion", 2, 1.0)
        mb.push_guard("workspace", 2, 1.5)
        assert set(mb.guard_names()) >= {"ood", "motion", "workspace"}

    # ── push_stage — pipeline stages ──────────────────────────────────────

    def test_push_stage_appears_in_snapshot_stages(self):
        mb = PipelineMetricBus()
        mb.push_stage("source", 1.1)
        mb.push_stage("policy", 3.2)
        mb.push_stage("total", 5.0)
        snap = mb.snapshot()
        assert abs(snap["stages"]["source"] - 1.1) < 1e-9
        assert abs(snap["stages"]["policy"] - 3.2) < 1e-9
        assert abs(snap["stages"]["total"] - 5.0) < 1e-9

    def test_push_stage_updates_on_second_call(self):
        mb = PipelineMetricBus()
        mb.push_stage("source", 1.0)
        mb.push_stage("source", 2.5)
        snap = mb.snapshot()
        assert abs(snap["stages"]["source"] - 2.5) < 1e-9

    # ── commit_cycle + layer aggregation ──────────────────────────────────

    def test_layers_empty_before_commit(self):
        mb = PipelineMetricBus()
        mb.push_guard("g", 2, 1.0)
        snap = mb.snapshot()
        # Layer history is empty until first commit_cycle().
        assert snap["layers"] == {}

    def test_commit_cycle_publishes_layer_sums(self):
        mb = PipelineMetricBus()
        mb.push_guard("joint_limit", 2, 1.5)
        mb.push_guard("workspace", 2, 2.0)
        mb.push_guard("ood", 0, 3.0)
        mb.commit_cycle()
        snap = mb.snapshot()
        assert abs(snap["layers"]["L2"] - 3.5) < 1e-9  # 1.5 + 2.0
        assert abs(snap["layers"]["L0"] - 3.0) < 1e-9

    def test_commit_cycle_resets_accumulator(self):
        """Second cycle with no guards → layer sum should be 0."""
        mb = PipelineMetricBus()
        mb.push_guard("g", 1, 5.0)
        mb.commit_cycle()
        mb.commit_cycle()  # no guards this cycle
        snap = mb.snapshot()
        assert abs(snap["layers"]["L1"] - 0.0) < 1e-9

    def test_layer_key_format_is_L_prefixed(self):
        mb = PipelineMetricBus()
        mb.push_guard("g", 4, 1.0)
        mb.commit_cycle()
        snap = mb.snapshot()
        assert "L4" in snap["layers"]

    # ── snapshot — guards field ────────────────────────────────────────────

    def test_snapshot_guards_reflects_push_guard(self):
        mb = PipelineMetricBus()
        mb.push_guard("velocity_guard", 2, 0.8)
        snap = mb.snapshot()
        assert abs(snap["guards"]["velocity_guard"] - 0.8) < 1e-9

    def test_snapshot_guards_reflects_push(self):
        mb = PipelineMetricBus()
        mb.push_guard("legacy_guard", 2, 1.2)
        snap = mb.snapshot()
        assert abs(snap["guards"]["legacy_guard"] - 1.2) < 1e-9

    # ── snapshot — structure ───────────────────────────────────────────────

    def test_snapshot_returns_all_three_keys(self):
        mb = PipelineMetricBus()
        snap = mb.snapshot()
        assert set(snap.keys()) == {"stages", "layers", "guards"}

    # ── clear wipes everything ────────────────────────────────────────────

    def test_clear_removes_stages_and_layers(self):
        mb = PipelineMetricBus()
        mb.push_guard("g", 2, 1.0)
        mb.push_stage("source", 1.0)
        mb.commit_cycle()
        mb.clear()
        snap = mb.snapshot()
        assert snap["stages"] == {}
        assert snap["layers"] == {}
        assert snap["guards"] == {}
        assert mb.latest("g") is None


# ── ActionBus ─────────────────────────────────────────────────────────────


class TestActionBus:
    def test_write_read(self):
        bus = ActionBus(1)
        bus.write(b"hello")
        assert bus.read() == b"hello"

    def test_empty_returns_none(self):
        assert ActionBus(1).read() is None

    def test_overwrite_latest_wins(self):
        bus = ActionBus(1)
        bus.write(b"old")
        bus.write(b"new")
        assert bus.read() == b"new"

    def test_is_empty(self):
        bus = ActionBus(1)
        assert bus.is_empty()
        bus.write(b"x")
        assert not bus.is_empty()
        bus.read()
        assert bus.is_empty()
