import json
import threading
import uuid

import pytest

import dam


@pytest.fixture
def config():
    return {
        "hardware": {"joint_names": ["a"], "degrees_mode": False},
        "boundaries": {
            "bounds": {
                "layer": "L1",
                "type": "single",
                "nodes": [
                    {"callback": "joint_position_limits", "params": {"lower": [-1], "upper": [1]}}
                ],
            }
        },
        "tasks": {"default": {"boundaries": ["bounds"]}},
    }


def test_diagnostics_are_detached_serializable_and_track_reset(config):
    rail = dam.Guardrail(config, quiet=True)
    try:
        before = rail.diagnostics()
        assert before["timings_ms"]["evaluate"] is None
        result = rail.evaluate({"joints": [0], "action": [2]})
        snapshot = rail.diagnostics()
        assert result.decision == dam.GuardDecision.CLAMP
        assert snapshot["risk"]["clamp_rate"] == 1
        assert snapshot["timings_ms"]["evaluate"] > 0
        assert snapshot["timings_ms"]["layers"]["L1"] >= 0
        assert snapshot["resources"]["rss_bytes"] > 0
        json.dumps(snapshot, allow_nan=False)
        snapshot["guards"][0]["metadata"]["changed"] = True
        assert "changed" not in rail.diagnostics()["guards"][0]["metadata"]
        with pytest.raises(dam.InputContractError):
            rail.evaluate({})
        assert rail.diagnostics()["last_error"]["type"] == "InputContractError"
        rail.reset()
        assert rail.diagnostics()["last_error"] is None
        assert rail.diagnostics()["guards"] == []
    finally:
        rail.close()
    assert rail.diagnostics()["session"]["state"] == "closed"


def add_task_check(config, callback, *, slow=False):
    name = f"diagnostic_{uuid.uuid4().hex}"
    dam.register_callback(name, callback, layer="L2")
    config["boundaries"]["task"] = {"layer": "L2", "type": "single", "nodes": [{"callback": name}]}
    config["tasks"]["default"]["boundaries"].append("task")
    if slow:
        config["safety"] = {"slow_lane": {"task_hz": 200, "verdict_timeout_ms": 10}}


def test_timeout_evidence_and_recovery_use_public_session(config):
    release = threading.Event()

    def wait():
        release.wait(1)
        return True

    add_task_check(config, wait, slow=True)
    rail = dam.Guardrail(config, quiet=True)
    try:
        result = rail.evaluate({"joints": [0], "action": [0.5]})
        assert result.decision == dam.GuardDecision.REJECT
        assert result.command == [0]
        assert any("tim" in row["reason"].lower() for row in rail.diagnostics()["guards"])
        release.set()
        rail.reset()
        assert rail.evaluate({"joints": [0], "action": [0.5]}).decision == dam.GuardDecision.PASS
    finally:
        release.set()
        rail.close()


def test_solver_failure_is_fault_and_reset_recovers(config):
    class Solver:
        broken = True

        def check(self):
            if self.broken:
                raise RuntimeError("injected solver failure")
            return True

    solver = Solver()

    def check(solvers):
        return solvers["test_solver"].check()

    add_task_check(config, check)
    with dam.Guardrail(config, solvers={"test_solver": solver}, quiet=True) as rail:
        result = rail.evaluate({"joints": [0], "action": [0.5]})
        assert result.decision == dam.GuardDecision.FAULT
        assert result.command == [0]
        assert any("injected solver" in row["reason"] for row in rail.diagnostics()["guards"])
        solver.broken = False
        rail.reset()
        assert rail.evaluate({"joints": [0], "action": [0.5]}).decision == dam.GuardDecision.PASS


def test_recording_failure_propagates_and_reset_can_recover(config, tmp_path, monkeypatch):
    from dam.logging import loopback_writer

    config["loopback"] = {"output_dir": str(tmp_path)}
    with dam.Guardrail(config, quiet=True) as rail:

        class BrokenWriter:
            def start(self, path):
                pass

            def write_cycle(self, record):
                raise OSError("injected disk full")

            def stop(self, *args, **kwargs):
                pass

            def last_error(self):
                return "injected disk full"

        with monkeypatch.context() as patch:
            patch.setattr(loopback_writer, "_RustMcapWriter", BrokenWriter)
            with pytest.raises(dam.ExecutionError, match="disk full") as error:
                rail.evaluate({"joints": [0], "action": [0.5]})
            assert isinstance(error.value.__cause__, OSError)
            assert "disk full" in rail.diagnostics()["recording"]["error"]
        rail.reset()
        assert rail.evaluate({"joints": [0], "action": [0.5]}).decision == dam.GuardDecision.PASS
    assert len(list(tmp_path.glob("*.mcap"))) == 1


def test_recording_close_timeout_retains_worker_for_retry(config, tmp_path, monkeypatch):
    from dam.logging import loopback_writer

    config["loopback"] = {"output_dir": str(tmp_path)}
    calls = []

    class SlowClose:
        def start(self, path):
            pass

        def write_cycle(self, record):
            pass

        def last_error(self):
            return None

        def stop(self, *args, **kwargs):
            calls.append(self)
            if len(calls) == 1:
                raise TimeoutError("injected recording timeout")

    monkeypatch.setattr(loopback_writer, "_RustMcapWriter", SlowClose)
    rail = dam.Guardrail(config, quiet=True)
    rail.evaluate({"joints": [0], "action": [0.5]})
    with pytest.raises(dam.OperationTimeoutError, match="recording timeout"):
        rail.close()
    assert rail.session_status.state == "recovery_required"
    with pytest.raises(dam.SessionClosedError):
        rail.evaluate({"joints": [0], "action": [0.5]})
    rail.close()
    assert len(calls) == 2 and calls[0] is calls[1]
    assert rail.session_status.state == "closed"
