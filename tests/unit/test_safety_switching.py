"""Safety switch acknowledgements, stale previews and failure atomicity."""

from __future__ import annotations

import time
from types import SimpleNamespace

import numpy as np
import pytest
import yaml

from dam.config.schema import StackfileConfig
from dam.services.safety_switching import SafetySwitchService
from dam.types.action import ActionProposal
from dam.types.observation import Observation


def stack(upper=1.0):
    return {
        "guards": [{"L1": "motion"}],
        "hardware": {"joint_names": ["j1", "j2"], "degrees_mode": False},
        "boundaries": {
            "limits": {
                "type": "single",
                "layer": "L1",
                "nodes": [
                    {
                        "callback": "joint_position_limits",
                        "params": {"lower": [-1.0, -1.0], "upper": [upper, upper]},
                    }
                ],
            },
        },
        "tasks": {"default": {"boundaries": ["limits"]}},
        "safety": {"enforcement_mode": "enforce"},
    }


CONTRACT = {"observation_keys": ["joints"], "action_units": ["rad", "rad"]}


@pytest.fixture
def running(tmp_path, monkeypatch):
    from dam.api import _register_builtins
    from dam.runtime.guard_runtime import GuardRuntime

    _register_builtins()
    config = StackfileConfig.model_validate(stack())
    runtime = GuardRuntime._from_config(config)
    runtime.start_task("default")
    path = tmp_path / ".dam_stackfile.yaml"
    path.write_text(yaml.safe_dump(stack()))
    control = SimpleNamespace(
        _runner=SimpleNamespace(runtime=runtime), state="running", _stack_path=str(path)
    )
    service = SafetySwitchService(control, tmp_path)
    # Tests explicitly advance control and read receipts; no wall-clock races.
    monkeypatch.setattr(service, "_await_receipt", lambda: None)
    yield service, runtime, control, path
    runtime.shutdown()


def cycle(runtime):
    obs = Observation(timestamp=time.monotonic(), joint_positions=np.zeros(2))
    return runtime.validate(
        obs, ActionProposal(target_joint_positions=np.array([0.8, 0.8])), "switch-test"
    )


def test_switch_acknowledges_actual_limits_preserves_history_and_timers(running):
    service, runtime, _, path = running
    before, _ = cycle(runtime)
    np.testing.assert_allclose(before.target_joint_positions, [0.8, 0.8])
    timers = dict(runtime._node_start_times)
    preview = service.preview(yaml.safe_dump(stack(0.2)), "default", CONTRACT)
    assert preview["mode"] == "hot_reload", preview
    assert preview["changes"]
    row = service.apply(preview["token"])
    assert row["status"] == "pending"
    assert yaml.safe_load(path.read_text())["boundaries"]["limits"]["nodes"][0]["params"][
        "upper"
    ] == [1, 1]
    after, _ = cycle(runtime)
    np.testing.assert_allclose(after.target_joint_positions, [0.2, 0.2], atol=1e-5)
    status = service.status()
    assert status["history"][0]["status"] == "applied"
    assert status["history"][0]["persisted"]
    assert status["active_hash"] == status["saved_hash"] == preview["candidate_hash"]
    assert runtime._node_start_times == timers
    assert runtime._config_version == 1


def test_runtime_failure_retains_old_disk_and_live_config(running, monkeypatch):
    service, runtime, _, path = running
    import dam.runtime.guard_runtime as module

    preview = service.preview(yaml.safe_dump(stack(0.2)), "default", CONTRACT)
    original = path.read_text()
    service.apply(preview["token"])

    def fail(*args):
        raise ValueError("injection preparation failed")

    monkeypatch.setattr(module, "precompute_injection", fail)
    cycle(runtime)
    status = service.status()
    assert status["history"][0]["status"] == "rejected"
    assert "injection" in status["history"][0]["message"]
    assert runtime._config_version == 0
    assert path.read_text() == original


def test_stale_preview_and_duplicate_apply_are_rejected(running):
    service, _, _, path = running
    preview = service.preview(yaml.safe_dump(stack(0.2)), "default", CONTRACT)
    path.write_text(path.read_text() + "\n# other editor\n")
    with pytest.raises(ValueError, match="changed"):
        service.apply(preview["token"])
    preview = service.preview(yaml.safe_dump(stack(0.2)), "default", CONTRACT)
    service.apply(preview["token"])
    with pytest.raises(ValueError, match="expired"):
        service.apply(preview["token"])
    assert service.preview(yaml.safe_dump(stack(0.3)), "default", CONTRACT)["mode"] == "blocked"


def test_structure_change_only_saved_after_stop(running):
    service, runtime, control, path = running
    candidate = stack()
    candidate["safety"]["control_hz"] = 50
    preview = service.preview(yaml.safe_dump(candidate), "default", CONTRACT)
    assert preview["mode"] == "restart_required"
    with pytest.raises(ValueError, match="Stop"):
        service.apply(preview["token"])
    control.state = "stopped"
    preview = service.preview(yaml.safe_dump(candidate), "default", CONTRACT)
    row = service.apply(preview["token"])
    assert row["status"] == "saved_restart_required"
    assert runtime._reload_config.safety.control_hz == 30
    assert yaml.safe_load(path.read_text())["safety"]["control_hz"] == 50
    assert service.status()["active_hash"] != service.status()["saved_hash"]


def test_protected_baseline_and_missing_contract_block_preview(running):
    service, runtime, _, _ = running
    runtime._reload_config.safety.always_active = ["limits"]
    result = service.preview(yaml.safe_dump(stack(0.2)), "default", CONTRACT)
    assert result["mode"] == "blocked"
    assert any("Protected" in reason for reason in result["reasons"])
    result = service.preview(yaml.safe_dump(stack()), "default")
    assert result["mode"] == "blocked"


def test_saved_file_race_does_not_overwrite_other_editor(running):
    service, runtime, _, path = running
    preview = service.preview(yaml.safe_dump(stack(0.2)), "default", CONTRACT)
    service.apply(preview["token"])
    edited = path.read_text() + "\n# independent edit\n"
    path.write_text(edited)
    cycle(runtime)
    row = service.status()["history"][0]
    assert row["status"] == "applied"
    assert not row["persisted"]
    assert path.read_text() == edited


def test_stop_cancels_pending_and_cannot_apply_on_later_start(running):
    service, runtime, control, _ = running
    preview = service.preview(yaml.safe_dump(stack(0.2)), "default", CONTRACT)
    service.apply(preview["token"])
    control.state = "stopped"
    assert service.status()["history"][0]["status"] == "rejected"
    cycle(runtime)
    assert runtime._config_version == 0


def test_task_change_rejects_at_cycle_boundary(running):
    service, runtime, _, _ = running
    preview = service.preview(yaml.safe_dump(stack(0.2)), "default", CONTRACT)
    service.apply(preview["token"])
    runtime._active_task = "different"
    cycle(runtime)
    assert service.status()["history"][0]["status"] == "rejected"
    assert runtime._config_version == 0


def test_rollback_uses_reviewable_previous_snapshot(running):
    service, runtime, _, _ = running
    preview = service.preview(yaml.safe_dump(stack(0.2)), "default", CONTRACT)
    row = service.apply(preview["token"])
    cycle(runtime)
    service.status()
    rollback = service.rollback_preview(row["id"], CONTRACT)
    assert rollback["mode"] == "hot_reload"
    service.apply(rollback["token"])
    after, _ = cycle(runtime)
    np.testing.assert_allclose(after.target_joint_positions, [0.8, 0.8])


def test_receipt_busy_and_version_conflict(running):
    _, runtime, _, _ = running
    candidate = StackfileConfig.model_validate(stack(0.2))
    request_id = runtime.apply_pending_reload(candidate, expected_version=99)
    with pytest.raises(ValueError, match="pending"):
        runtime.apply_pending_reload(candidate)
    cycle(runtime)
    receipt = runtime._hot_reload.reload_receipt(request_id)
    assert receipt["status"] == "rejected"
    assert runtime._config_version == 0


def test_stop_start_invalidates_pending_without_status_poll(running):
    service, runtime, _, _ = running
    preview = service.preview(yaml.safe_dump(stack(0.2)), "default", CONTRACT)
    service.apply(preview["token"])
    runtime.stop_task()
    runtime.start_task("default")
    cycle(runtime)
    assert runtime._config_version == 0
    assert service.status()["history"][0]["status"] == "rejected"


def test_audit_failure_prevents_queueing(running, monkeypatch):
    service, runtime, _, path = running
    original = path.read_text()
    preview = service.preview(yaml.safe_dump(stack(0.2)), "default", CONTRACT)

    def fail():
        raise OSError("disk full")

    monkeypatch.setattr(service, "_persist", fail)
    with pytest.raises(OSError, match="disk full"):
        service.apply(preview["token"])
    assert not runtime._hot_reload.has_pending()
    assert path.read_text() == original


def test_switch_routes_schema_stale_token_and_no_runtime_save(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from dam.services.routers.safety_switching import create_safety_switching_router

    app = FastAPI()
    app.include_router(create_safety_switching_router(root=tmp_path))
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/safety/switch/preview", json={"yaml": "[", "task": "default"}
            ).status_code
            == 422
        )
        result = client.post(
            "/api/safety/switch/preview",
            json={"yaml": yaml.safe_dump(stack()), "task": "default", "contract": CONTRACT},
        )
        assert result.status_code == 200, result.text
        assert result.json()["mode"] == "restart_required"
        applied = client.post("/api/safety/switch/apply", json={"token": result.json()["token"]})
        assert applied.status_code == 200
        assert applied.json()["status"] == "saved_restart_required"
        status = client.get("/api/safety/switch/status").json()
        assert status["active_hash"] is None
        assert status["saved_hash"] == result.json()["candidate_hash"]
        assert client.post("/api/safety/switch/apply", json={"token": "unknown"}).status_code == 409
