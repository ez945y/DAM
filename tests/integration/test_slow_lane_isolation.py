"""Slow evaluation uses submission-time state and worker-owned resources."""

import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import yaml

from dam.runtime.slow_lane import SlowVerdict
from dam.types.result import GuardDecision
from tests.integration.test_slow_lane_readiness import _rail, _stack


def test_runtime_context_is_captured_before_background_evaluation(tmp_path):
    entered, release = threading.Event(), threading.Event()
    seen = []

    def check(active_task, node_start_times, prev_validated_positions, solvers, limit):
        seen.append(
            (
                active_task,
                dict(node_start_times),
                list(prev_validated_positions),
                solvers["model"].value,
                limit,
            )
        )
        solvers["model"].value += 1
        return True

    path = Path(_stack(tmp_path, check, slow={"first_verdict": "reject"}))
    config = yaml.safe_load(path.read_text())
    config["boundaries"]["task_check"]["nodes"][0]["params"] = {"limit": 2}
    path.write_text(yaml.safe_dump(config))
    with _rail(str(path)) as rail:
        rt = rail.runtime
        rt._solvers["model"] = SimpleNamespace(value=10)
        rt._prev_validated_positions = [0.1]
        node = rt._boundary_containers["task_check"].get_active_node()
        node.constraint.params["limit"] = 2
        old_starts = dict(rt._node_start_times)
        worker = rt._slow_lane
        evaluate = worker._evaluate_fn

        def paused(snapshot):
            entered.set()
            assert release.wait(2)
            return evaluate(snapshot)

        worker._evaluate_fn = paused
        try:
            rail({"joints": [0.25], "action": [1.0]})
            assert entered.wait(1)
            rt._active_task = "changed"
            rt._prev_validated_positions[0] = 9
            rt._node_start_times["task_check"] = 0
            node.constraint.params["limit"] = 99
            rt._solvers["model"].value = 99
        finally:
            release.set()
        verdict = worker.wait_for_verdict(rt._cycle_id, 2)
        assert verdict is not None
        assert seen == [("default", old_starts, [0.1], 10, 2)]
        assert rt._solvers["model"].value == 99
        assert verdict.task_name == "default"
        assert rt.slow_lane_status["state"] == "pending"
        assert (
            rt._slow_lane_extra_results(None)[0].metadata["slow_lane_state"] == "version_mismatch"
        )


@pytest.mark.parametrize(
    "field,value", [("task_name", "other"), ("config_version", 99), ("state_generation", -1)]
)
def test_old_generation_cannot_gate_new_runtime_state(tmp_path, field, value):
    with _rail(_stack(tmp_path, lambda: True)) as rail:
        assert rail({"joints": [0.0], "action": [1.0]}) == [1.0]
        rt = rail.runtime
        worker = rt._slow_lane
        verdict = worker.latest_verdict()
        worker._verdict = replace(verdict, **{field: value})
        rejected = rt._slow_lane_extra_results(None)
        assert rejected[0].decision == GuardDecision.REJECT
        assert rejected[0].metadata["slow_lane_state"] == "version_mismatch"
        assert rail.slow_lane_status["state"] == "pending"
        # A mismatched cached result restores the bounded first-verdict wait.
        assert rail({"joints": [0.0], "action": [1.0]}) == [1.0]
        assert rail.slow_lane_status["state"] == "ready"


def test_container_advance_and_task_restart_invalidate_old_evidence(tmp_path):
    with _rail(_stack(tmp_path, lambda: True)) as rail:
        rail({"joints": [0.0], "action": [1.0]})
        rt = rail.runtime
        old = rt._slow_lane.latest_verdict()
        rt.advance_container("task_check")
        assert not rt._slow_verdict_matches(old)
        rail({"joints": [0.0], "action": [1.0]})
        new = rt._slow_lane.latest_verdict()
        assert new.state_generation > old.state_generation
        rt.start_task("default")
        assert not rt._slow_verdict_matches(new)
        assert rail.slow_lane_status["state"] == "pending"


def test_uncopyable_resource_faults_instead_of_sharing(tmp_path):
    with _rail(_stack(tmp_path, lambda: True)) as rail:
        rail.runtime._solvers["unsafe"] = threading.Lock()
        assert rail({"joints": [0.25], "action": [1.0]}) == [0.25]
        assert any(
            r.guard_name == "slow_lane_snapshot" and r.decision == GuardDecision.FAULT
            for r in rail.last_results
        )


def test_worker_guard_state_persists_without_mutating_control_guard(tmp_path):
    with _rail(_stack(tmp_path, lambda: False)) as rail:
        rt = rail.runtime
        for _ in range(3):
            rail({"joints": [0.25], "action": [1.0]})
        stages = rt._slow_resources[0]
        worker_guard = stages[0].guard_boundary_pairs[0][0]
        original = rt._slow_stages[0].guard_boundary_pairs[0][0]
        assert worker_guard is not original
        assert worker_guard.__dict__["_warn_streaks"]["task_check"] >= 1
        assert not original.__dict__.get("_warn_streaks")


def test_parallel_slow_stage_cannot_leave_callbacks_running_after_stop(tmp_path):
    entered, release = threading.Event(), threading.Event()
    finished = []

    def check():
        entered.set()
        assert release.wait(2)
        finished.append(True)
        return True

    rail = _rail(_stack(tmp_path, check, slow={"verdict_timeout_ms": 20}))
    rt = rail.runtime
    rt._slow_stages[0].parallel = True
    rt._slow_stages[0].timeout_ms = 1
    try:
        assert rail({"joints": [0.25], "action": [1.0]}) == [0.25]
        assert entered.wait(1)
        assert rt._slow_resources[0][0].parallel is False
        with pytest.raises(TimeoutError):
            rt._slow_lane.stop(timeout=0.01)
        assert not finished
    finally:
        release.set()
        rail.close()
    assert finished == [True]


def test_uncopyable_input_metadata_is_a_cycle_fault(tmp_path):
    from dam.types.action import ActionProposal
    from dam.types.observation import Observation

    with _rail(_stack(tmp_path, lambda: True)) as rail:
        obs = Observation(1.0, np.array([0.25]), metadata={"lock": threading.Lock()})
        action = ActionProposal(np.array([1.0]))
        _, results = rail.runtime.validate(obs, action, "copy-fault")
        assert any(
            r.guard_name == "slow_lane_snapshot" and r.decision == GuardDecision.FAULT
            for r in results
        )
