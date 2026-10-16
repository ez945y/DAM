"""Real MCAP inputs exercise independent A/B guards and executable assertions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from dam.services.safety_comparison import compare, config_snapshot, make_regression, run_regression

pytest.importorskip("mcap.writer")


def stack(limit=1.0, callback="joint_position_limits", **safety):
    params = {"lower": [-limit], "upper": [limit]} if callback == "joint_position_limits" else {}
    return config_snapshot(
        yaml.safe_dump(
            {
                "version": "1",
                "hardware": {"joint_names": ["joint"], "degrees_mode": False},
                "guards": [{"L1": "motion"}]
                if callback.startswith("joint_")
                else [{"L3": "hardware"}],
                "boundaries": {
                    "limit": {
                        "layer": "L1" if callback.startswith("joint_") else "L3",
                        "type": "single",
                        "nodes": [{"callback": callback, "params": params}],
                    }
                },
                "tasks": {"default": {"boundaries": ["limit"]}},
                "safety": safety,
            }
        ),
        "test",
    )


def recording(path: Path, count=3, *, missing=None, task="default", packed=True, ids=None):
    from mcap.writer import Writer

    with path.open("wb") as stream:
        writer = Writer(stream)
        writer.start()
        schema = writer.register_schema("json", "jsonschema", b"{}")
        channels = {
            topic: writer.register_channel(topic, "json", schema)
            for topic in ("/dam/cycle", "/dam/obs", "/dam/action")
        }
        for cid in range(count):
            record = {
                "cycle_id": cid if ids is None else ids[cid],
                "obs_timestamp": 10.0 + cid / 30,
                "obs_joint_positions": [0.0],
                "obs_channels": {},
                "active_task": task,
                "action_positions": [0.25 if cid == 0 else 1.5],
                "action_velocities": None,
            }
            if cid == missing:
                del record["action_positions"]
            messages = [("/dam/cycle", record)]
            if not packed:
                messages = [
                    ("/dam/cycle", {"cycle_id": cid, "active_task": task}),
                    (
                        "/dam/obs",
                        {
                            "cycle_id": cid,
                            "timestamp": record["obs_timestamp"],
                            "joint_positions": [0.0],
                        },
                    ),
                    (
                        "/dam/action",
                        {"cycle_id": cid, "target_positions": record.get("action_positions")},
                    ),
                ]
            for topic, payload in messages:
                writer.add_message(
                    channels[topic], cid * 1_000_000, json.dumps(payload).encode(), cid * 1_000_000
                )
        writer.finish()
    return path


@pytest.mark.parametrize("packed", [True, False])
def test_compares_every_cycle_without_throttling(tmp_path, packed):
    result = compare(recording(tmp_path / "test.mcap", 203, packed=packed), stack(2), stack(1))
    assert result["status"] == "complete", result["cycles"][0]
    assert len(result["cycles"]) == result["compared"] == 203
    assert result["difference_count"] == 202
    assert result["distributions"]["left"] == {"PASS": 203}
    assert result["distributions"]["right"] == {"PASS": 1, "CLAMP": 202}
    assert result["cycles"][1]["right"]["correction_l2"] == pytest.approx(0.5, abs=1e-6)
    assert result["cycles"][1]["output_delta_l2"] == pytest.approx(0.5, abs=1e-6)
    assert len(result["top_differences"]) == 20


def test_missing_action_invalidates_history_without_zero_fill(tmp_path):
    result = compare(recording(tmp_path / "test.mcap", missing=1), stack(), stack())
    assert result["status"] == "partial"
    assert result["compared"] == 1
    assert result["error_cycles"] == 2
    assert "proposed action" in result["cycles"][1]["left"]["issues"][0]
    assert "history" in result["cycles"][2]["left"]["issues"][0]


def test_missing_temperature_is_not_reported_pass(tmp_path):
    result = compare(
        recording(tmp_path / "test.mcap"), stack(callback="temperature_limit"), stack()
    )
    assert result["status"] == "error"
    assert result["cycles"][0]["left"]["decision"] is None
    assert "temperature" in result["cycles"][0]["left"]["issues"][0]


def test_empty_recording_is_error(tmp_path):
    with pytest.raises(ValueError, match="no replayable cycles"):
        compare(recording(tmp_path / "empty.mcap", 0), stack(), stack())


def test_unavailable_task_is_not_silently_remapped(tmp_path):
    result = compare(recording(tmp_path / "test.mcap", task="missing"), stack(), stack())
    assert result["status"] == "error"
    assert "missing" in result["cycles"][0]["left"]["issues"][0]


def test_slow_lane_explicit_synchronous_evaluation(tmp_path):
    candidate = stack(slow_lane={"task_hz": 10})
    result = compare(recording(tmp_path / "test.mcap"), candidate, stack())
    assert result["status"] == "complete"
    assert result["evaluation_mode"] == "synchronous_offline"
    assert "slow_lane" in result["configs"]["left"]["yaml"]
    assert any("synchronously" in note for note in result["limitations"])


@pytest.mark.parametrize("unsupported", ["sequence", "watchdog"])
def test_unrecorded_runtime_state_is_explicitly_unsupported(tmp_path, unsupported):
    candidate = stack()
    if unsupported == "sequence":
        candidate["config"].boundaries["limit"].type = "list"
    else:
        candidate = stack(callback="hardware_watchdog")
    result = compare(recording(tmp_path / "test.mcap"), candidate, stack())
    assert result["cycles"][0]["left"]["status"] == "unsupported"
    assert result["cycles"][0]["left"]["issues"]
    assert result["compared"] == 0


def test_fault_remains_distinct_from_reject(tmp_path, monkeypatch):
    from dam.guard.layer import GuardLayer
    from dam.runtime.guard_runtime import GuardRuntime
    from dam.types.result import GuardDecision, GuardResult

    monkeypatch.setattr(
        GuardRuntime,
        "validate",
        lambda *a, **k: (
            None,
            [
                GuardResult(
                    decision=GuardDecision.FAULT,
                    guard_name="failure",
                    layer=GuardLayer.L1,
                    reason="fault",
                )
            ],
        ),
    )
    result = compare(recording(tmp_path / "test.mcap"), stack(), stack())
    assert result["distributions"]["left"] == {"FAULT": 3}


def test_regression_reexecutes_snapshot_and_detects_failed_expectation(tmp_path):
    path = recording(tmp_path / "test.mcap")
    result = compare(path, stack(2), stack(1))
    case = make_regression(result, name="clamp", side="right", cycle_start=1, cycle_end=2)
    assert run_regression(path, case)["status"] == "pass"
    wrong = make_regression(
        result,
        name="must reject",
        side="right",
        cycle_start=1,
        cycle_end=2,
        expected_decision="REJECT",
    )
    failed = run_regression(path, wrong)
    assert failed["status"] == "fail"
    assert len(failed["mismatches"]) == 2
    case["config"]["yaml"] += "\n# changed"
    assert run_regression(path, case)["status"] == "error"


def test_regression_rejects_changed_mcap(tmp_path):
    path = recording(tmp_path / "test.mcap")
    case = make_regression(
        compare(path, stack(), stack()), name="case", side="left", cycle_start=0, cycle_end=2
    )
    recording(path, 4)
    assert run_regression(path, case)["status"] == "error"


def test_regression_refuses_unknown_cycles(tmp_path):
    result = compare(recording(tmp_path / "test.mcap", missing=1), stack(), stack())
    with pytest.raises(ValueError, match="evaluated cycles"):
        make_regression(result, name="bad", side="left", cycle_start=0, cycle_end=2)


def test_api_persists_and_runs_cases_with_safe_paths(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from dam.services.routers import safety_comparison as routes
    from dam.services.routers import stackfiles
    from examples.safety_comparison_demo import create_demo

    recording_path, left, right = create_demo(tmp_path / "demo")
    monkeypatch.setattr(routes, "_root", lambda: tmp_path)
    monkeypatch.setattr(stackfiles, "_root", lambda: tmp_path)
    library = tmp_path / "data/stackfiles"
    library.mkdir(parents=True)
    for path in (left, right):
        (library / path.name).write_text(path.read_text())

    from dam.services.mcap_sessions import McapSessionService

    sessions = McapSessionService(str(recording_path.parent))
    app = FastAPI()
    app.include_router(routes.create_safety_comparison_router(sessions))
    client = TestClient(app)
    response = client.post(
        "/api/safety/comparisons",
        json={"mcap": recording_path.name, "left_stack": left.stem, "right_stack": right.stem},
    )
    assert response.status_code == 200, response.text
    comparison = response.json()
    assert comparison["status"] == "complete"
    saved = client.post(
        "/api/safety/regressions",
        json={
            "comparison_id": comparison["id"],
            "name": "must clamp",
            "side": "right",
            "cycle_start": 2,
            "cycle_end": 3,
            "expected_decision": "CLAMP",
        },
    ).json()
    assert saved["id"]
    assert (tmp_path / "data/safety_regressions" / f"{saved['id']}.json").is_file()
    assert client.get("/api/safety/regressions").json()["cases"][0]["id"] == saved["id"]
    assert client.post(f"/api/safety/regressions/{saved['id']}/run").json()["status"] == "pass"
    assert client.post("/api/safety/regressions/not-a-uuid/run").status_code == 400
    assert (
        client.post(
            "/api/safety/comparisons",
            json={"mcap": "../secret", "left_stack": left.stem, "right_stack": right.stem},
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/safety/comparisons",
            json={
                "mcap": recording_path.name,
                "left_stack": "../secret",
                "right_stack": right.stem,
            },
        ).status_code
        == 400
    )

    # Stored IDs bind their content to their filename; malformed files cannot
    # crash the UI or execute under the wrong visible identity.
    saved_path = tmp_path / "data/safety_regressions" / f"{saved['id']}.json"
    saved["id"] = "0" * 32
    saved_path.write_text(json.dumps(saved))
    assert client.get("/api/safety/regressions").json()["cases"] == []
    assert client.post(f"/api/safety/regressions/{saved_path.stem}/run").json()["status"] == "error"

    request_body = {"mcap": recording_path.name, "left_stack": left.stem, "right_stack": right.stem}
    for invalid_yaml in ("[", "[]", "null"):
        (library / left.name).write_text(invalid_yaml)
        failure = client.post("/api/safety/comparisons", json=request_body)
        assert failure.status_code == 400, failure.text
    (library / left.name).write_text(left.read_text())
    recording_path.write_bytes(b"not an MCAP file" * 20)
    malformed = client.post("/api/safety/comparisons", json=request_body)
    assert malformed.status_code == 400
    assert "MCAP" in malformed.json()["detail"]
    sessions.close()


def test_missing_cycle_id_invalidates_temporal_history(tmp_path):
    result = compare(recording(tmp_path / "gap.mcap", ids=[0, 2, 3]), stack(), stack())
    assert result["compared"] == 1
    assert "Missing recorded cycles" in result["cycles"][1]["left"]["issues"][0]


def test_duplicate_cycle_is_not_silently_overwritten(tmp_path):
    with pytest.raises(ValueError, match="Duplicate"):
        compare(recording(tmp_path / "duplicate.mcap", ids=[0, 0, 1]), stack(), stack())


def test_acceleration_histories_are_isolated_from_candidates_and_live_cache(tmp_path, monkeypatch):
    import time

    import numpy as np

    from dam.api import _register_builtins
    from dam.boundary.callbacks.kinematics import _prev_vel

    _register_builtins()
    data = yaml.safe_load(stack()["yaml"])
    data["boundaries"]["limit"]["nodes"] = [
        {"callback": "joint_acceleration_limit", "params": {"max_acceleration": 0.1}}
    ]
    candidate = config_snapshot(yaml.safe_dump(data), "acceleration")
    path = recording(tmp_path / "acceleration.mcap")
    # This represents another live runtime using the same public boundary name.
    live_history = (np.array([100.0]), np.array([20.0]), time.monotonic())
    monkeypatch.setitem(_prev_vel, "limit", live_history)
    existing_keys = set(_prev_vel)

    first = compare(path, candidate, candidate)
    second = compare(path, candidate, candidate)
    for comparison in (first, second):
        assert comparison["status"] == "complete", comparison["cycles"]
        assert comparison["difference_count"] == 0
        assert comparison["cycles"][0]["left"]["decision"] == "PASS"
        assert comparison["cycles"][0]["right"]["decision"] == "PASS"
        assert comparison["cycles"][0]["left"]["output"] == [0.25]
        for cycle in comparison["cycles"]:
            assert cycle["left"] == cycle["right"]
            assert {guard["name"] for guard in cycle["left"]["guards"]} == {"limit"}
    assert first["cycles"] == second["cycles"]
    assert _prev_vel["limit"] is live_history
    assert set(_prev_vel) == existing_keys
    assert "boundary_name" not in candidate["config"].boundaries["limit"].nodes[0].params

    # Range assertions still warm up from cycle 0, with a fresh scoped cache.
    case = make_regression(first, name="acceleration", side="left", cycle_start=1, cycle_end=2)
    assert run_regression(path, case)["status"] == "pass"
    assert run_regression(path, case)["status"] == "pass"
    assert _prev_vel["limit"] is live_history
    assert set(_prev_vel) == existing_keys


def test_invalid_yaml_is_reported_as_validation_error():
    with pytest.raises(ValueError, match="Invalid Stackfile YAML"):
        config_snapshot("[", "broken")


@pytest.mark.parametrize("start,end", [(0, 20), (1, 3), (-1, 2)])
def test_regression_range_is_not_silently_truncated(tmp_path, start, end):
    path = recording(tmp_path / "test.mcap")
    result = compare(path, stack(), stack())
    with pytest.raises(ValueError, match="range"):
        make_regression(
            result, name="all requested cycles", side="left", cycle_start=start, cycle_end=end
        )


def test_incomplete_saved_assertions_cannot_pass(tmp_path):
    path = recording(tmp_path / "test.mcap")
    case = make_regression(
        compare(path, stack(), stack()), name="complete", side="left", cycle_start=0, cycle_end=2
    )
    case["expected"].pop(1)
    result = run_regression(path, case)
    assert result["status"] == "error"
    assert any("range" in str(issue) for issue in result["issues"])


def test_invalid_saved_json_is_quarantined_from_list_and_run(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from dam.services.routers import safety_comparison as routes

    monkeypatch.setattr(routes, "_root", lambda: tmp_path)
    directory = tmp_path / "data/safety_regressions"
    directory.mkdir(parents=True)
    case_id = "1234567890abcdef1234567890abcdef"
    (directory / f"{case_id}.json").write_text('{"name":"broken"}')
    app = FastAPI()
    app.include_router(routes.create_safety_comparison_router(None))
    client = TestClient(app)
    listing = client.get("/api/safety/regressions").json()
    assert listing["cases"] == []
    assert listing["errors"][0]["id"] == case_id
    run = client.post(f"/api/safety/regressions/{case_id}/run")
    assert run.status_code == 200
    assert run.json()["status"] == "error"
