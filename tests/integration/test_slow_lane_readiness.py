"""First-cycle slow checks must participate before an enforced SDK return."""

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
import yaml

import dam
from dam.types.result import GuardDecision


def _stack(tmp_path, callback, *, slow=None, mode="enforce"):
    name = f"readiness_{uuid.uuid4().hex}"
    dam.register_callback(name, callback, layer="L2")
    config = {
        "version": "1",
        "safety": {
            "enforcement_mode": mode,
            "slow_lane": {"task_hz": 200, **(slow or {})},
        },
        "tasks": {"default": {"boundaries": ["task_check"]}},
        "boundaries": {
            "task_check": {"layer": "L2", "type": "single", "nodes": [{"callback": name}]},
        },
    }
    path = tmp_path / "readiness.yaml"
    path.write_text(yaml.safe_dump(config))
    return str(path)


def _rail(path, **kwargs):
    return dam.Guardrail(path, joint_names=["j1"], degrees_mode=False, quiet=True, **kwargs)


@pytest.mark.parametrize("one_shot", [False, True])
def test_first_reject_is_applied_before_return(tmp_path, one_shot):
    path = _stack(tmp_path, lambda: False)
    inputs = {"joints": [0.25], "action": [1.0]}
    if one_shot:
        output = dam.guardrail(inputs, path, joint_names=["j1"], degrees_mode=False)
        assert output == [0.25]
    else:
        with _rail(path) as rail:
            assert rail(inputs) == [0.25]
            assert any(r.decision == GuardDecision.REJECT for r in rail.last_results)


def test_first_callback_fault_is_applied_before_return(tmp_path):
    def fail():
        raise RuntimeError("first callback failed")

    with _rail(_stack(tmp_path, fail)) as rail:
        assert rail({"joints": [0.25], "action": [1.0]}) == [0.25]
        assert any(r.decision == GuardDecision.FAULT for r in rail.last_results)


def test_wait_does_not_return_while_first_check_is_pending(tmp_path):
    entered, release = threading.Event(), threading.Event()

    def check():
        entered.set()
        assert release.wait(2)
        return False

    with _rail(_stack(tmp_path, check, slow={"verdict_timeout_ms": 1500})) as rail:
        assert rail.slow_lane_status["state"] == "pending"
        with ThreadPoolExecutor(max_workers=1) as executor:
            call = executor.submit(rail, {"joints": [0.25], "action": [1.0]})
            try:
                assert entered.wait(1)
                assert not call.done()
                assert rail.slow_lane_status["state"] == "pending"
            finally:
                release.set()
            assert call.result(timeout=2) == [0.25]
        assert rail.slow_lane_status["state"] == "ready"
        result = next(r for r in rail.last_results if r.metadata.get("slow_lane"))
        assert result.metadata["current_cycle_checked"] is True


@pytest.mark.parametrize("policy,state", [("wait", "timeout"), ("reject", "pending")])
def test_pending_or_timeout_never_passes_even_with_warn(tmp_path, policy, state):
    release = threading.Event()

    def check():
        assert release.wait(2)
        return True

    rail = _rail(
        _stack(
            tmp_path,
            check,
            slow={"first_verdict": policy, "verdict_timeout_ms": 20, "stale_action": "warn"},
        )
    )
    try:
        start = time.monotonic()
        assert rail({"joints": [0.25], "action": [1.0]}) == [0.25]
        assert time.monotonic() - start < 1
        result = rail.last_results[-1]
        assert result.decision == GuardDecision.REJECT
        assert result.metadata["slow_lane_state"] == state
        assert rail.runtime._prev_validated_positions is None
    finally:
        release.set()
        rail.close()


def test_one_shot_overrides_nonblocking_first_policy(tmp_path):
    calls = []

    def check():
        calls.append(True)
        return True

    path = _stack(tmp_path, check, slow={"first_verdict": "reject"})
    assert dam.guardrail(
        {"joints": [0.25], "action": [1.0]}, path, joint_names=["j1"], degrees_mode=False
    ) == [1.0]
    assert calls == [True]


def test_one_shot_timeout_returns_fallback_and_joins_worker(tmp_path):
    finished = threading.Event()
    workers = []

    def check():
        workers.append(threading.current_thread())
        time.sleep(0.05)
        finished.set()
        return True

    path = _stack(tmp_path, check, slow={"verdict_timeout_ms": 10})
    assert dam.guardrail(
        {"joints": [0.25], "action": [1.0]}, path, joint_names=["j1"], degrees_mode=False
    ) == [0.25]
    assert finished.is_set()
    assert workers and all(not worker.is_alive() for worker in workers)


def test_session_latest_verdict_is_explicitly_from_an_older_cycle(tmp_path):
    release = threading.Event()
    calls = []

    def check():
        calls.append(True)
        if len(calls) > 1:
            assert release.wait(2)
        return True

    rail = _rail(_stack(tmp_path, check))
    try:
        assert rail({"joints": [0.25], "action": [1.0]}) == [1.0]
        basis = rail.slow_lane_status["basis_cycle_id"]
        assert rail({"joints": [0.25], "action": [2.0]}) == [2.0]
        result = next(r for r in rail.last_results if r.metadata.get("slow_lane"))
        assert result.metadata["basis_cycle_id"] == basis
        assert result.metadata["current_cycle_checked"] is False
    finally:
        release.set()
        rail.close()


def test_wait_for_slow_session_checks_each_command(tmp_path):
    calls = []

    def check(*, action):
        calls.append(float(action.target_joint_positions[0]))
        return len(calls) == 1

    with _rail(_stack(tmp_path, check), wait_for_slow=True) as rail:
        assert rail({"joints": [0.25], "action": [1.0]}) == [1.0]
        assert rail({"joints": [0.25], "action": [2.0]}) == [0.25]
        assert calls == [1.0, 2.0]
        assert rail.last_results[-1].metadata["current_cycle_checked"] is True


def test_expired_first_pass_is_rejected_even_with_warn(tmp_path):
    def check():
        time.sleep(0.03)
        return True

    with _rail(
        _stack(tmp_path, check, slow={"max_staleness_ms": 10, "stale_action": "warn"})
    ) as rail:
        assert rail({"joints": [0.25], "action": [1.0]}) == [0.25]
        assert rail.last_results[-1].guard_name == "slow_lane_watchdog"
        assert rail.slow_lane_status["state"] == "stale"


def test_slow_check_sees_fast_clamp_and_fast_callback_runs_once(tmp_path):
    from dam.guard.layer import GuardLayer
    from dam.types.action import ValidatedAction
    from dam.types.result import GuardResult

    fast_calls, slow_values = [], []

    def fast(*, action):
        fast_calls.append(True)
        return GuardResult.clamp(
            ValidatedAction(
                target_joint_positions=np.array([0.5]), was_clamped=True, original_proposal=action
            ),
            "fast",
            GuardLayer.L1,
        )

    def slow(*, action):
        slow_values.append(float(action.target_joint_positions[0]))
        return True

    path = _stack(tmp_path, slow)
    name = f"fast_{uuid.uuid4().hex}"
    dam.register_callback(name, fast)
    with open(path) as stream:
        config = yaml.safe_load(stream)
    config["boundaries"]["fast"] = {"layer": "L1", "type": "single", "nodes": [{"callback": name}]}
    config["tasks"]["default"]["boundaries"].append("fast")
    with open(path, "w") as stream:
        yaml.safe_dump(config, stream)
    with _rail(path) as rail:
        assert rail({"joints": [0.25], "action": [1.0]}) == [0.5]
        assert fast_calls == [True]
        assert slow_values == [0.5]


@pytest.mark.parametrize("mode", ["monitor", "log_only"])
def test_observation_modes_do_not_claim_enforcement(tmp_path, mode):
    calls = []

    def check():
        calls.append(True)
        return False

    with _rail(_stack(tmp_path, check, mode=mode)) as rail:
        assert rail({"joints": [0.25], "action": [1.0]}) == [1.0]
        if mode == "monitor":
            assert any(r.decision == GuardDecision.REJECT for r in rail.last_results)
            assert calls == [True]
        else:
            assert rail.last_results == []
            assert calls == []
            assert rail.slow_lane_status["state"] == "bypassed"


def test_synchronous_pipeline_has_no_slow_evidence(tmp_path):
    path = _stack(tmp_path, lambda: False)
    with open(path) as stream:
        config = yaml.safe_load(stream)
    del config["safety"]["slow_lane"]
    with open(path, "w") as stream:
        yaml.safe_dump(config, stream)
    with _rail(path) as rail:
        assert rail({"joints": [0.25], "action": [1.0]}) == [0.25]
        assert rail.slow_lane_status["state"] == "disabled"
        assert not any(r.metadata.get("slow_lane") for r in rail.last_results)


def test_task_restart_resets_readiness(tmp_path):
    with _rail(_stack(tmp_path, lambda: True)) as rail:
        rail({"joints": [0.25], "action": [1.0]})
        assert rail.slow_lane_status["state"] == "ready"
        rail.runtime.start_task("default")
        assert rail.slow_lane_status["state"] == "pending"


def test_runtime_step_gates_sink_before_first_slow_verdict(tmp_path):
    from dam.testing.mocks import MockPolicyAdapter, MockSinkAdapter, MockSourceAdapter
    from dam.types.action import ActionProposal
    from dam.types.observation import Observation

    with _rail(_stack(tmp_path, lambda: False)) as rail:
        runtime = rail.runtime
        runtime._sources = {
            "mock": MockSourceAdapter([Observation(timestamp=0, joint_positions=np.array([0.25]))])
        }
        runtime._policy = MockPolicyAdapter(
            [ActionProposal(target_joint_positions=np.array([1.0]))]
        )
        sink = MockSinkAdapter()
        runtime._sink = sink
        result = runtime.step()
        assert result.was_rejected
        assert any(r.decision == GuardDecision.REJECT for r in result.guard_results)
        assert sink.received == []
