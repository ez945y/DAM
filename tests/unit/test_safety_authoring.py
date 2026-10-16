"""Safety authoring preserves protection and rejects incomplete declarations."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from dam.config.safety_plan import SafetyDraftRequest, SafetyPlan
from dam.services.routers import safety_authoring as routes
from dam.services.safety_authoring import compile_plan, draft_plan


def rule(callback="joint_velocity_limit", params=None, layer="L1"):
    return {
        "type": "single",
        "layer": layer,
        "nodes": [
            {
                "callback": callback,
                "params": params if params is not None else {"max_velocities": 0.5},
            }
        ],
    }


def base():
    return {
        "hardware": {"joint_names": ["j1", "j2"], "degrees_mode": False},
        "guards": [{"L1": "motion"}, {"L3": "hardware"}],
        "boundaries": {"base_limit": rule()},
        "tasks": {"pick": {"boundaries": []}, "other": {"boundaries": []}},
        "safety": {"always_active": ["base_limit"]},
    }


def plan(data=None, components=None, contract=None):
    return SafetyPlan(
        name="table",
        task="pick",
        base_yaml=yaml.safe_dump(data or base()),
        components=components or [],
        contract=contract or {"observation_keys": ["joints"]},
    )


def component(name="precise", kind="mode", boundary=None):
    return {"name": name, "kind": kind, "boundaries": {name: boundary or rule()}}


def test_composition_preserves_base_and_only_adds_selected_task():
    source = base()
    result = compile_plan(plan(source, [component(), component("hard", "baseline")]))
    compiled = yaml.safe_load(result["yaml"])
    assert result["valid"] is True
    assert result["runtime_verified"] is False
    assert compiled["safety"]["always_active"] == ["base_limit", "hard"]
    assert compiled["tasks"]["pick"]["boundaries"] == ["precise"]
    assert compiled["tasks"]["other"]["boundaries"] == []
    assert compiled["boundaries"]["base_limit"] == source["boundaries"]["base_limit"]
    assert result["sources"]["hard"] == {"component": "hard", "kind": "baseline"}
    assert all(item["status"] == "declaration-only" for item in result["diagnostics"])
    assert "contract" not in compiled and "components" not in compiled


def test_collision_never_replaces_base_protection():
    with pytest.raises(ValueError, match="collision"):
        compile_plan(plan(components=[component("base_limit")]))


def test_collision_between_components_is_rejected():
    one = component("one")
    two = component("two")
    two["boundaries"] = copy.deepcopy(one["boundaries"])
    with pytest.raises(ValueError, match="collision"):
        compile_plan(plan(components=[one, two]))


def test_task_must_exist_and_unknown_fields_rejected():
    body = plan().model_dump()
    body["task"] = "unconfigured"
    with pytest.raises(ValueError, match="does not exist"):
        compile_plan(SafetyPlan(**body))
    body["remove_baseline"] = True
    with pytest.raises(ValidationError):
        SafetyPlan(**body)


@pytest.mark.parametrize(
    ("change", "code"),
    [
        (lambda d: d["hardware"].pop("degrees_mode"), "units"),
        (lambda d: d["hardware"].update(joint_names=[]), "joint_names"),
        (
            lambda d: d["hardware"].update(action_layout=[{"keys": ["v", "omega"]}]),
            "action_semantics",
        ),
        (
            lambda d: d["boundaries"]["base_limit"]["nodes"][0].update(callback="not_registered"),
            "callback",
        ),
        (lambda d: d["boundaries"]["base_limit"].update(layer="L3"), "layer"),
        (
            lambda d: d["boundaries"]["base_limit"]["nodes"][0].update(
                params={"max_velocities": [1]}
            ),
            "dimension",
        ),
        (
            lambda d: d["boundaries"]["base_limit"]["nodes"][0].update(
                params={"max_velocities": -1}
            ),
            "limit",
        ),
        (
            lambda d: d["boundaries"]["base_limit"]["nodes"][0].update(
                params={"max_velocities": float("nan")}
            ),
            "finite",
        ),
        (lambda d: d["safety"].update(enforcement_mode="monitor"), "enforcement"),
        (lambda d: d["safety"].update(always_active=["missing"]), "boundary"),
    ],
)
def test_missing_or_incompatible_declarations_block_compile(change, code):
    data = base()
    change(data)
    result = compile_plan(plan(data))
    assert result["valid"] is False
    assert code in {item["code"] for item in result["diagnostics"]}


def test_missing_observations_and_fk_are_not_success():
    result = compile_plan(
        plan(
            components=[component("scene", "scene", rule("workspace", {"bounds": [[0, 1]] * 3}))],
            contract={"observation_keys": []},
        )
    )
    assert not result["valid"]
    codes = {item["code"] for item in result["diagnostics"]}
    assert {"observation", "fk", "frame"} <= codes


def test_temperature_channel_must_be_declared_and_never_reported_healthy():
    item = component("heat", "baseline", rule("temperature_limit", {"max_temperature_c": 50}, "L3"))
    assert not compile_plan(plan(components=[item]))["valid"]
    result = compile_plan(
        plan(components=[item], contract={"observation_keys": ["joints", "temperature"]})
    )
    assert result["valid"] and not result["runtime_verified"]


def test_inactive_rule_is_visible_without_claiming_execution():
    data = base()
    data["boundaries"]["unused"] = rule("not_registered")
    result = compile_plan(plan(data))
    assert result["valid"]
    assert {item["boundary"]: item["status"] for item in result["diagnostics"]}[
        "unused"
    ] == "inactive"


def test_unknown_parameter_rejected_instead_of_silently_ignored():
    data = base()
    data["boundaries"]["base_limit"]["nodes"][0]["params"]["max_velocity"] = 100
    assert not compile_plan(plan(data))["valid"]


@pytest.mark.parametrize(
    "text",
    [
        "靠近放置區慢一點",
        "joint velocity 1",
        "joint velocity 1 rad/s when near table",
        "不要超過桌面",
        "workspace x[0,1]m y[0,1]m z[0,1]m",
    ],
)
def test_ambiguous_draft_stays_unresolved(text):
    result = draft_plan(SafetyDraftRequest(text=text))
    assert result["components"] == [] and result["unresolved"]


def test_explicit_bilingual_draft_preserves_units():
    result = draft_plan(
        SafetyDraftRequest(
            text="關節速度上限 20 deg/s；joint acceleration 2 rad/s^2；workspace x[-1,1]m y[0,1]m z[0,2]m",
            frame="base_link",
        )
    )
    assert result["unresolved"] == []
    assert len(result["components"]) == 3
    first = next(iter(result["components"][0]["boundaries"].values()))
    assert first["nodes"][0]["params"] == {"max_velocities": 20, "use_degrees": True}
    assert not result["runtime_verified"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "_library_dir", lambda: tmp_path)
    app = FastAPI()
    app.include_router(routes.create_safety_authoring_router())
    return TestClient(app)


def test_library_round_trip_and_delete(client, tmp_path):
    body = plan().model_dump()
    assert client.put("/api/safety/plans/table", json=body).status_code == 200
    assert client.get("/api/safety/plans/table").json() == body
    assert client.get("/api/safety/plans").json()["plans"][0]["name"] == "table"
    assert list(tmp_path.glob(".plan-*")) == []
    assert client.post("/api/safety/compile", json=body).json()["valid"] is True
    assert client.delete("/api/safety/plans/table").status_code == 204
    assert client.get("/api/safety/plans/table").status_code == 404


def test_library_name_mismatch_and_symlink_rejected(client, tmp_path):
    assert client.put("/api/safety/plans/other", json=plan().model_dump()).status_code == 400
    (tmp_path / "table.json").symlink_to(Path("/tmp/outside-safety-plan.json"))
    assert client.put("/api/safety/plans/table", json=plan().model_dump()).status_code == 400
    assert client.get("/api/safety/plans").json() == {"plans": []}


@pytest.mark.parametrize("name", ["../escape", "bad/name", "bad\\name", "a..b", ""])
def test_invalid_library_names(name):
    with pytest.raises(ValidationError):
        SafetyPlan(name=name, task="pick", base_yaml="{}")


def test_callback_parameters_cannot_spoof_runtime_inputs():
    data = base()
    data["boundaries"]["base_limit"]["nodes"][0]["params"]["obs"] = {"joint_positions": [0, 0]}
    result = compile_plan(plan(data))
    assert not result["valid"]
    assert "runtime_parameter" in {item["code"] for item in result["diagnostics"]}


def test_solver_capability_without_registered_implementation_is_missing():
    data = base()
    data["solvers"] = {"invented": {"capabilities": ["fk"]}}
    result = compile_plan(
        plan(
            data,
            [component("table", "scene", rule("workspace", {"bounds": [[0, 1]] * 3}))],
            {"observation_keys": ["joints"], "frame": "base_link"},
        )
    )
    assert not result["valid"]
    assert "fk" in {item["code"] for item in result["diagnostics"]}


def test_declared_builtin_fk_still_never_claims_runtime_verified():
    data = base()
    data["solvers"] = {"pinocchio_kinematics": {"params": {"asset_path": "robot.urdf"}}}
    result = compile_plan(
        plan(
            data,
            [component("table", "scene", rule("workspace", {"bounds": [[0, 1]] * 3}))],
            {"observation_keys": ["joints"], "frame": "base_link"},
        )
    )
    assert result["valid"]
    assert not result["runtime_verified"]
    assert "runtime inputs" in result["diagnostics"][-1]["message"]


def test_draft_empty_input_does_not_appear_resolved():
    result = draft_plan(SafetyDraftRequest(text="  ; \n "))
    assert result["unresolved"] and not result["components"]


@pytest.mark.parametrize("guards", [[{"L1": "hardware"}], [{"L3": "motion"}], ["motion"]])
def test_missing_or_misrouted_guard_declaration(guards):
    data = base()
    data["guards"] = guards
    result = compile_plan(plan(data))
    assert not result["valid"]
    assert "guard_declaration" in {item["code"] for item in result["diagnostics"]}


def test_nonfinite_safety_rate_is_invalid():
    data = base()
    data["safety"]["control_hz"] = float("inf")
    result = compile_plan(plan(data))
    assert not result["valid"]
    assert "finite_safety" in {item["code"] for item in result["diagnostics"]}


@pytest.mark.parametrize(
    "callback,first,second",
    [
        ("workspace", {"bounds": [[0, 1]] * 3}, {"bounds": [[2, 3]] * 3}),
        (
            "joint_position_limits",
            {"lower": [0, 0], "upper": [1, 1]},
            {"lower": [2, 2], "upper": [3, 3]},
        ),
    ],
)
def test_disjoint_active_boxes_are_reported_as_conflicts(callback, first, second):
    data = base()
    data["boundaries"]["base_limit"] = rule(callback, first)
    result = compile_plan(plan(data, [component("narrow", "mode", rule(callback, second))]))
    assert not result["valid"]
    assert "conflict" in {item["code"] for item in result["diagnostics"]}


def test_phase_specific_boxes_are_not_falsely_intersected():
    data = base()
    data["boundaries"]["sequence"] = {
        "type": "list",
        "layer": "L1",
        "nodes": [
            {"callback": "joint_position_limits", "params": {"lower": [0, 0], "upper": [1, 1]}},
            {"callback": "joint_position_limits", "params": {"lower": [2, 2], "upper": [3, 3]}},
        ],
    }
    data["tasks"]["pick"]["boundaries"] = ["sequence"]
    result = compile_plan(plan(data))
    assert "conflict" not in {item["code"] for item in result["diagnostics"]}


def test_fk_solver_override_precedence_matches_runtime(monkeypatch):
    from dam.runtime import _stackfile_builder

    monkeypatch.setattr(
        _stackfile_builder,
        "_preset_solver_configs",
        lambda _: {"arm": {"type": "pinocchio_kinematics", "params": {"asset_path": "robot.urdf"}}},
    )
    data = base()
    workspace = component("table", "scene", rule("workspace", {"bounds": [[0, 1]] * 3}))
    contract = {"observation_keys": ["joints"], "frame": "base_link"}
    assert compile_plan(plan(data, [workspace], contract))["valid"]
    data["solvers"] = {"arm": {"type": "ackermann", "capabilities": ["rollout"]}}
    result = compile_plan(plan(data, [workspace], contract))
    assert not result["valid"]
    assert "fk" in {item["code"] for item in result["diagnostics"]}


def test_guards_are_automatically_routed_when_override_list_is_empty():
    data = base()
    data["guards"] = []
    assert compile_plan(plan(data))["valid"]


@pytest.mark.parametrize("kind,nodes", [("graph", 1), ("single", 2), ("single", 0)])
def test_unsupported_or_partially_ignored_containers_are_invalid(kind, nodes):
    data = base()
    data["boundaries"]["base_limit"] = {
        "type": kind,
        "layer": "L1",
        "nodes": rule()["nodes"] * nodes,
    }
    result = compile_plan(plan(data))
    assert not result["valid"]
    assert "container" in {item["code"] for item in result["diagnostics"]}


def test_command_space_requires_explicit_action_units():
    data = base()
    data["hardware"]["action_layout"] = [{"keys": ["v", "omega"]}]
    result = compile_plan(plan(data))
    assert "action_units" in {item["code"] for item in result["diagnostics"]}


@pytest.mark.parametrize("limit", [-1, 0, float("nan"), float("inf"), [0.5], "0.5", True, None])
def test_ee_speed_requires_positive_finite_scalar(limit):
    data = base()
    data["solvers"] = {"pinocchio_kinematics": {"params": {"asset_path": "robot.urdf"}}}
    result = compile_plan(
        plan(
            data,
            [component("ee_cap", "mode", rule("ee_velocity_limit", {"max_ee_velocity": limit}))],
            {"observation_keys": ["joints"], "frame": "base_link"},
        )
    )
    assert not result["valid"]
    assert any(
        item["code"] == "limit" and "max_ee_velocity" in item["message"]
        for item in result["diagnostics"]
    )


def test_positive_ee_speed_and_registered_fallbacks_compile():
    data = base()
    data["solvers"] = {"pinocchio_kinematics": {"params": {"asset_path": "robot.urdf"}}}
    data["fallbacks"] = {
        "hold_position": {"escalate_to": "emergency_stop", "escalate_after_seconds": 2}
    }
    data["safety"]["no_task_behavior"] = "hold_position"
    data["boundaries"]["base_limit"]["nodes"][0]["fallback"] = "hold_position"
    result = compile_plan(
        plan(
            data,
            [component("ee_cap", "mode", rule("ee_velocity_limit", {"max_ee_velocity": 0.2}))],
            {"observation_keys": ["joints"], "frame": "base_link"},
        )
    )
    assert result["valid"]


@pytest.mark.parametrize("location", ["node", "default", "configured", "escalation"])
def test_unknown_fallback_contexts_are_diagnosed(location):
    data = base()
    if location == "node":
        data["boundaries"]["base_limit"]["nodes"][0]["fallback"] = "nonexistent_stop"
    elif location == "default":
        data["safety"]["no_task_behavior"] = "nonexistent_stop"
    elif location == "configured":
        data["fallbacks"] = {"nonexistent_stop": {}}
    else:
        data["fallbacks"] = {"hold_position": {"escalate_to": "nonexistent_stop"}}
    result = compile_plan(plan(data))
    assert not result["valid"]
    assert any(
        item["code"] == "fallback" and "nonexistent_stop" in item["message"]
        for item in result["diagnostics"]
    )


def spatial_plan(callback, params, layer="L1"):
    data = base()
    data["solvers"] = {"pinocchio_kinematics": {"params": {"asset_path": "robot.urdf"}}}
    return plan(
        data,
        [component("geometry", "scene", rule(callback, params, layer))],
        {"observation_keys": ["joints"], "frame": "base_link"},
    )


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"bounds": [[0, 0], [0, 1], [0, 1]]},
        {"bounds": [[0, 1]]},
        {"bounds": [[False, True]] * 3},
    ],
)
def test_task_workspace_requires_complete_nondegenerate_box(params):
    result = compile_plan(spatial_plan("task_workspace_bounds", params, "L2"))
    assert not result["valid"]
    assert any(item["code"] in {"bounds", "parameter"} for item in result["diagnostics"])


@pytest.mark.parametrize(
    "params",
    [
        {"reference_axis": [0, 0, 0]},
        {"tool_axis": [0, 0, 1e-14]},
        {"reference_axis": [1, 0]},
        {"tool_axis": [False, False, True]},
        {"max_tilt_deg": -1},
        {"max_tilt_deg": 181},
        {"cbf_alpha": 0},
        {"cbf_alpha": -2},
    ],
)
def test_orientation_rejects_skipped_or_invalid_geometry(params):
    result = compile_plan(spatial_plan("orientation_limit", params))
    assert not result["valid"]
    assert any(item["code"] in {"axis", "angle", "cbf_alpha"} for item in result["diagnostics"])


def test_gripper_zone_rejects_degenerate_geometry():
    result = compile_plan(
        spatial_plan(
            "task_gripper_command_guard",
            {"allowed_command": "close", "zone": [[0, 0], [0, 1], [0, 1]]},
            "L2",
        )
    )
    assert not result["valid"]
    assert "bounds" in {item["code"] for item in result["diagnostics"]}


def test_joint_bounds_do_not_treat_booleans_as_coordinates():
    data = base()
    data["boundaries"]["base_limit"] = rule(
        "joint_position_limits", {"lower": [False, False], "upper": [True, True]}
    )
    assert not compile_plan(plan(data))["valid"]


@pytest.mark.parametrize("keys", [[["j1"], "j2"], None, "j1", [], ["j1", " "]])
def test_malformed_action_layout_returns_diagnostics(keys):
    data = base()
    data["hardware"]["action_layout"] = [{"keys": keys}]
    result = compile_plan(plan(data))
    assert not result["valid"]
    assert "action_layout" in {item["code"] for item in result["diagnostics"]}


@pytest.mark.parametrize("names", [["j1", "j1"], ["j1", " "]])
def test_hardware_joint_names_must_be_unique_and_nonblank(names):
    data = base()
    data["hardware"]["joint_names"] = names
    assert not compile_plan(plan(data))["valid"]


def test_contract_cannot_declare_reserved_runtime_inputs_as_observations():
    result = compile_plan(plan(contract={"observation_keys": ["joints", "action", "dt"]}))
    assert not result["valid"]
    assert "observation_contract" in {item["code"] for item in result["diagnostics"]}


def test_unresolved_clause_cannot_turn_conditional_draft_into_unconditional_limit():
    result = draft_plan(SafetyDraftRequest(text="joint velocity 1 rad/s; only near humans"))
    assert result["unresolved"]
    assert result["components"] == []


def test_unresolved_workspace_does_not_leave_other_partial_rules_ready_to_add():
    result = draft_plan(SafetyDraftRequest(text="joint velocity 1 rad/s; workspace x[0,1]m"))
    assert result["unresolved"]
    assert result["components"] == []


def test_joint_intersection_uses_runtime_degree_normalization():
    data = base()
    data["boundaries"]["base_limit"] = rule(
        "joint_position_limits", {"lower": [1, 1], "upper": [2, 2]}
    )
    degrees = component(
        "degrees",
        "mode",
        rule("joint_position_limits", {"lower": [1, 1], "upper": [2, 2], "use_degrees": True}),
    )
    result = compile_plan(plan(data, [degrees]))
    assert not result["valid"]
    assert "conflict" in {item["code"] for item in result["diagnostics"]}


def test_unit_hints_cannot_silently_be_ignored_by_spatial_callbacks():
    result = compile_plan(spatial_plan("workspace", {"bounds": [[0, 1]] * 3, "use_degrees": True}))
    assert not result["valid"]
    assert "units" in {item["code"] for item in result["diagnostics"]}


def test_explicit_empty_action_unit_contract_is_a_dimension_mismatch():
    result = compile_plan(plan(contract={"observation_keys": ["joints"], "action_units": []}))
    assert not result["valid"]
    assert "units_dimension" in {item["code"] for item in result["diagnostics"]}


@pytest.mark.parametrize(
    "callback,params,layer",
    [
        ("task_workspace_bounds", {"bounds": [[0, 1]] * 3}, "L2"),
        ("task_gripper_command_guard", {"allowed_command": "close", "zone": [[0, 1]] * 3}, "L2"),
        (
            "orientation_limit",
            {"reference_axis": [0, 0, 2], "tool_axis": [1, 0, 0], "max_tilt_deg": 30},
            "L1",
        ),
        ("orientation_limit", {}, "L1"),
    ],
)
def test_well_formed_geometry_remains_declaration_only(callback, params, layer):
    result = compile_plan(spatial_plan(callback, params, layer))
    assert result["valid"]
    assert not result["runtime_verified"]


def test_nonfinite_raw_fallback_params_are_not_hidden_by_serialization():
    data = base()
    data["fallbacks"] = {"hold_position": {"params": {"extra": float("nan")}}}
    result = compile_plan(plan(data))
    assert not result["valid"]
    assert any(
        item["code"] == "finite" and item["boundary"] == "*" for item in result["diagnostics"]
    )


def test_compile_api_handles_malformed_action_layout_without_server_error(client):
    data = base()
    data["hardware"]["action_layout"] = [{"keys": [["j1"], "j2"]}]
    response = client.post("/api/safety/compile", json=plan(data).model_dump())
    assert response.status_code == 200
    assert not response.json()["valid"]


def test_draft_api_never_returns_applicable_components_for_partial_request(client):
    response = client.post(
        "/api/safety/draft", json={"text": "joint velocity 1 rad/s; only near humans"}
    )
    assert response.status_code == 200
    assert response.json()["unresolved"]
    assert response.json()["components"] == []
