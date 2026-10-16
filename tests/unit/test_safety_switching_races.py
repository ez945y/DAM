"""Task lifecycle races must invalidate a reviewed safety switch."""

from __future__ import annotations

import pytest
import yaml

from tests.unit import test_safety_switching as switch_tests
from tests.unit.test_safety_switching import CONTRACT, cycle, stack

running = switch_tests.running


def test_same_task_restart_after_preview_invalidates_token(running):
    service, runtime, _, _ = running
    preview = service.preview(yaml.safe_dump(stack(0.2)), "default", CONTRACT)
    runtime.stop_task()
    runtime.start_task("default")
    with pytest.raises(ValueError, match="changed|preview"):
        service.apply(preview["token"])


def test_same_task_restart_during_prepare_rejects_inflight_switch(running, monkeypatch):
    service, runtime, _, _ = running
    preview = service.preview(yaml.safe_dump(stack(0.2)), "default", CONTRACT)
    service.apply(preview["token"])
    original = runtime._build_config_pool

    def restart_during_prepare(config):
        result = original(config)
        runtime.stop_task()
        runtime.start_task("default")
        return result

    monkeypatch.setattr(runtime, "_build_config_pool", restart_during_prepare)
    cycle(runtime)
    assert runtime._config_version == 0
    assert service.status()["history"][0]["status"] == "rejected"


def test_restart_save_rechecks_file_after_audit_persistence(running, monkeypatch):
    service, _, control, path = running
    control.state = "stopped"
    candidate = stack()
    candidate["safety"]["control_hz"] = 50
    preview = service.preview(yaml.safe_dump(candidate), "default", CONTRACT)
    original_persist = service._persist
    edited = path.read_text() + "\n# independent edit during audit persistence\n"
    first = True

    def persist_and_edit():
        nonlocal first
        original_persist()
        if first:
            first = False
            path.write_text(edited)

    monkeypatch.setattr(service, "_persist", persist_and_edit)
    with pytest.raises(ValueError, match="changed|preview"):
        service.apply(preview["token"])
    assert path.read_text() == edited


def test_hot_spatial_switch_requires_initialized_runtime_jacobian(running, monkeypatch):
    from dam.config.schema import StackfileConfig

    service, runtime, _, path = running
    baseline = stack()
    baseline["solvers"] = {"pinocchio_kinematics": {"params": {"asset_path": "robot.urdf"}}}
    baseline["boundaries"]["workspace"] = {
        "type": "single",
        "layer": "L1",
        "nodes": [{"callback": "workspace", "params": {"bounds": [[-1, 1]] * 3}}],
    }
    baseline["tasks"]["default"]["boundaries"].append("workspace")
    # Represent a configured runtime whose solver construction failed: the
    # Stackfile declares FK, but no usable runtime capability was produced.
    runtime._reload_config = StackfileConfig.model_validate(baseline)
    path.write_text(yaml.safe_dump(baseline))
    monkeypatch.setattr(runtime, "_select_dynamics", lambda: None)
    monkeypatch.setattr(runtime, "_select_solver", lambda _: None)
    candidate = yaml.safe_load(yaml.safe_dump(baseline))
    candidate["boundaries"]["workspace"]["nodes"][0]["params"]["bounds"] = [[-0.5, 0.5]] * 3
    result = service.preview(
        yaml.safe_dump(candidate), "default", {**CONTRACT, "frame": "base_link"}
    )
    assert result["mode"] == "blocked"
    assert "runtime_capability" in {item["code"] for item in result["diagnostics"]}


def test_restart_candidate_can_add_solver_before_runtime_preflight(running):
    service, _, control, _ = running
    control.state = "stopped"
    candidate = stack()
    candidate["solvers"] = {"pinocchio_kinematics": {"params": {"asset_path": "robot.urdf"}}}
    candidate["boundaries"]["workspace"] = {
        "type": "single",
        "layer": "L1",
        "nodes": [{"callback": "workspace", "params": {"bounds": [[-1, 1]] * 3}}],
    }
    candidate["tasks"]["default"]["boundaries"].append("workspace")
    result = service.preview(
        yaml.safe_dump(candidate), "default", {**CONTRACT, "frame": "base_link"}
    )
    assert result["mode"] == "restart_required"
    assert "runtime_capability" not in {item["code"] for item in result["diagnostics"]}


def test_initialized_source_dynamics_allows_spatial_preview_without_invoking_it(
    running, monkeypatch
):
    from types import SimpleNamespace

    from dam.config.schema import StackfileConfig

    service, runtime, _, path = running
    baseline = stack()
    baseline["solvers"] = {"pinocchio_kinematics": {"params": {"asset_path": "robot.urdf"}}}
    baseline["boundaries"]["workspace"] = {
        "type": "single",
        "layer": "L1",
        "nodes": [{"callback": "workspace", "params": {"bounds": [[-1, 1]] * 3}}],
    }
    baseline["tasks"]["default"]["boundaries"].append("workspace")
    runtime._reload_config = StackfileConfig.model_validate(baseline)
    path.write_text(yaml.safe_dump(baseline))

    def no_calls(*_):
        raise AssertionError("Preview must never evaluate or mutate dynamics")

    dynamics = SimpleNamespace(
        available=True,
        default_frame_id=0,
        update=no_calls,
        frame_placement=no_calls,
        frame_jacobian=no_calls,
    )
    monkeypatch.setattr(runtime, "_select_dynamics", lambda: dynamics)
    candidate = yaml.safe_load(yaml.safe_dump(baseline))
    candidate["boundaries"]["workspace"]["nodes"][0]["params"]["bounds"] = [[-0.5, 0.5]] * 3
    result = service.preview(
        yaml.safe_dump(candidate), "default", {**CONTRACT, "frame": "base_link"}
    )
    assert result["mode"] == "hot_reload"
