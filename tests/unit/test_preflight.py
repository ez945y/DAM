import copy
import threading
from unittest.mock import Mock

import pytest
import yaml

import dam
from dam.runtime.context import StepContext
from dam.runtime.guard_runtime import GuardRuntime
from dam.solver.registry import SolverRegistry


@pytest.fixture
def config():
    return {
        "hardware": {"joint_names": ["a", "b"], "degrees_mode": False},
        "guards": [],
        "tasks": {"default": {"boundaries": []}},
    }


def test_path_mapping_and_model_have_identical_public_behavior(config, tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    sources = [str(path), path, config, dam.StackfileConfig.model_validate(config)]
    reports = [dam.preflight(source).to_dict() for source in sources]
    assert all(report == reports[0] for report in reports)
    assert reports[0]["valid"]
    for source in sources:
        with dam.Guardrail(source, quiet=True) as rail:
            result = rail.evaluate({"joints": [0, 0], "action": [0.1, 0.2]})
            assert result.command == [0.1, 0.2]
            assert result.decision == dam.GuardDecision.PASS


def test_config_is_owned_by_session(config):
    original = copy.deepcopy(config)
    with dam.Guardrail(config, quiet=True) as rail:
        config["hardware"]["joint_names"].append("later")
        assert rail({"joints": [0, 0], "action": [0.1, 0.2]}) == [0.1, 0.2]
    assert original["hardware"]["joint_names"] == ["a", "b"]


def test_preflight_does_not_construct_resources_or_invoke_user_code(config, monkeypatch):
    bomb = Mock(side_effect=AssertionError("preflight invoked runtime resources"))
    monkeypatch.setattr(GuardRuntime, "_from_config", bomb)
    monkeypatch.setattr(GuardRuntime, "start_recording", bomb)
    monkeypatch.setattr(SolverRegistry, "build", bomb)
    monkeypatch.setattr(threading.Thread, "start", bomb)

    @dam.solver_factory("preflight_no_construct", capabilities=["test"])
    def factory():
        return bomb()

    @dam.callback("preflight_no_evaluate", layer="L1")
    def callback(action, sensor):
        return bomb()

    @dam.fallback("preflight_no_context")
    class NoContext(StepContext):
        def __init__(self):
            bomb()

    config["solvers"] = {"test": {"type": "preflight_no_construct"}}
    config["fallbacks"] = {"preflight_no_context": {}}
    config["boundaries"] = {
        "inspect": {
            "layer": "L1",
            "type": "single",
            "nodes": [{"callback": "preflight_no_evaluate"}],
        }
    }
    config["tasks"]["default"]["boundaries"] = ["inspect"]
    before = copy.deepcopy(config)
    report = dam.preflight(config)
    assert report.valid, report.issues
    assert report.required_observations == ("sensor",)
    assert config == before
    bomb.assert_not_called()


def test_unknown_callback_and_solver_point_to_fields(config):
    config["solvers"] = {"arm": {"type": "not_registered"}}
    config["boundaries"] = {
        "check": {"layer": "L1", "type": "single", "nodes": [{"callback": "not_registered"}]}
    }
    config["tasks"]["default"]["boundaries"] = ["check"]
    report = dam.preflight(config)
    fields = {issue.field for issue in report.issues}
    assert "solvers.arm.type" in fields
    assert "boundaries.check.nodes.0.callback" in fields
    with pytest.raises(dam.ConfigurationError, match="solvers.arm.type"):
        report.raise_for_errors()


@pytest.mark.parametrize(
    "segment",
    [{"size": "bad"}, {"size": 1.5}, {"size": True}, {"keys": ["a", "a"]}, {"size": -2}, {}],
)
def test_bad_layout_is_reported_without_throwing(config, segment):
    config["hardware"]["action_layout"] = [segment]
    report = dam.preflight(config, safe_action="zero")
    assert not report.valid
    assert any(issue.field.startswith("hardware.action_layout") for issue in report.issues)


def test_command_units_and_fallback_contract(config):
    config["hardware"]["action_layout"] = [{"keys": ["v", "omega"], "units": ["m/s", "rad/s"]}]
    report = dam.preflight(config, safe_action=[0, 0])
    assert report.valid and not report.issues
    assert report.command_space and report.command_size == 2
    assert not dam.preflight(config).valid
    config["hardware"]["action_layout"][0]["units"] = ["m/s"]
    assert not dam.preflight(config, safe_action="zero").valid


def test_unspecified_command_units_are_explicit_warning(config):
    config["hardware"]["action_layout"] = [{"keys": ["v", "omega"]}]
    report = dam.preflight(config, safe_action="zero")
    assert report.valid
    assert any(
        issue.field.endswith("units") and issue.severity == "warning" for issue in report.issues
    )


def test_schema_errors_keep_field_locations(config):
    config["hardware"]["degrees_mode"] = "invalid"
    report = dam.preflight(config)
    assert not report.valid
    assert report.issues[0].field == "hardware.degrees_mode"


def test_direct_retreat_reference_requires_configured_target(config):
    config["safety"] = {"no_task_behavior": "retreat"}
    report = dam.preflight(config)
    assert not report.valid
    assert any(
        issue.field == "fallbacks.retreat.params.target_positions" for issue in report.issues
    )
    config["fallbacks"] = {"retreat": {"params": {"target_positions": [0.2, 0.3]}}}
    assert dam.preflight(config).valid


def test_unknown_task_and_nonjoint_position_fallback(config):
    assert not dam.preflight(config, task="missing").valid
    config["hardware"]["action_layout"] = [{"keys": ["v", "omega"]}]
    config["fallbacks"] = {"hold_position": {}}
    report = dam.preflight(config, safe_action="zero")
    assert not report.valid
    assert any(issue.field == "fallbacks.hold_position" for issue in report.issues)


@pytest.mark.parametrize("solver", [["bad"], 1, "missing"])
def test_bad_solver_reference_is_a_report_issue(config, solver):
    config["hardware"]["action_layout"] = [{"keys": ["a", "b"], "solver": solver}]
    report = dam.preflight(config)
    assert not report.valid
    assert any(issue.field.endswith(".solver") for issue in report.issues)


def test_nonfinite_fallback_parameters_are_reported(config):
    config["fallbacks"] = {
        "slow_down": {"params": {"scale": float("nan"), "hysteresis_seconds": float("inf")}}
    }
    report = dam.preflight(config)
    assert not report.valid
    assert {issue.field for issue in report.issues} >= {
        "fallbacks.slow_down.params.scale",
        "fallbacks.slow_down.params.hysteresis_seconds",
    }
