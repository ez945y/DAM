import contextlib
import json
from unittest.mock import Mock

import numpy as np
import pytest
import yaml

import dam


@pytest.fixture
def make_rail(tmp_path):
    rails = []

    def make(callback="joint_position_limits", mode="enforce"):
        path = tmp_path / "evaluation.yaml"
        config = {
            "hardware": {"joint_names": ["a", "b"], "degrees_mode": False},
            "guards": [{"L1": "motion"}],
            "safety": {"enforcement_mode": mode},
            "boundaries": {
                "bounds": {
                    "layer": "L1",
                    "type": "single",
                    "nodes": [
                        {"callback": callback, "params": {"lower": [-1, -1], "upper": [1, 1]}}
                    ],
                }
            },
            "tasks": {"default": {"boundaries": ["bounds"]}},
        }
        path.write_text(yaml.safe_dump(config))
        rail = dam.Guardrail(str(path), quiet=True)
        rails.append(rail)
        return rail

    yield make
    for rail in rails:
        rail.close()


@pytest.mark.parametrize(
    "value,decision", [(0.2, dam.GuardDecision.PASS), (2.0, dam.GuardDecision.CLAMP)]
)
def test_detailed_result_matches_callable_and_serializes(make_rail, value, decision):
    rail = make_rail()
    inputs = {"joints": [0, 0], "action": [value, value]}
    result = rail.evaluate(inputs)
    assert isinstance(result, dam.GuardrailEvaluation)
    assert result.decision == decision
    assert result.fallback is None
    assert result.task == "default"
    assert result.output_status == "returned"
    assert result.verdict_age_ms is None
    assert [r.decision for r in result.guard_results] == [r.decision for r in rail.last_results]
    np.testing.assert_allclose(result.command, rail(inputs))
    serialized = json.loads(result.to_json())
    assert serialized["decision"] == decision.name
    assert serialized["command"] == result.command
    assert serialized["guard_results"][0]["guard_name"] == "bounds"


@pytest.mark.parametrize("fault", [False, True])
def test_rejection_and_fault_are_results_with_explicit_fallback(make_rail, fault):
    name = f"evaluation_failure_{fault}"
    calls = []

    def callback(action):
        calls.append(1)
        if fault:
            raise RuntimeError("callback unavailable")
        return False

    with contextlib.suppress(ValueError):
        dam.register_callback(name, callback, layer="L1")
    result = make_rail(name).evaluate({"joints": [0.1, 0.2], "action": [0.3, 0.4]})
    assert len(calls) == 1
    assert result.decision == (dam.GuardDecision.FAULT if fault else dam.GuardDecision.REJECT)
    assert result.fallback == "hold"
    assert result.command == [0.1, 0.2]


def test_consumed_evidence_is_detached_and_age_is_not_latest_worker_age(make_rail, monkeypatch):
    rail = make_rail()
    metadata = {"slow_lane": True, "verdict_age_ms": 12.5, "basis_cycle_id": 7}
    guard_result = dam.GuardResult.pass_result("slow", dam.GuardLayer.L2, metadata=metadata)
    monkeypatch.setattr(
        rail.runtime,
        "validate",
        Mock(return_value=(dam.ValidatedAction(np.zeros(2)), [guard_result])),
    )
    result = rail.evaluate({"joints": [0, 0], "action": [0, 0]})
    metadata["verdict_age_ms"] = 99
    assert result.verdict_age_ms == 12.5
    assert result.guard_results[0].metadata["verdict_age_ms"] == 12.5
    assert result.to_dict()["guard_results"][0]["metadata"]["basis_cycle_id"] == 7


def test_monitor_reports_rejection_without_claiming_fallback(make_rail):
    with contextlib.suppress(ValueError):
        dam.register_callback("evaluation_monitor_reject", lambda action: False, layer="L1")
    rail = make_rail("evaluation_monitor_reject", mode="monitor")
    result = rail.evaluate({"joints": [0, 0], "action": [0.3, 0.4]})
    assert result.decision == dam.GuardDecision.REJECT
    assert result.fallback is None
    assert result.command == [0.3, 0.4]


def test_configuration_failure_has_public_category_and_cause():
    with pytest.raises(dam.ConfigurationError) as error:
        dam.Guardrail("/nonexistent/evaluation.yaml", quiet=True)
    assert isinstance(error.value.__cause__, FileNotFoundError)


def test_input_failure_has_public_category_and_session_recovers(make_rail):
    rail = make_rail()
    with pytest.raises(dam.InputContractError) as error:
        rail.evaluate({})
    assert isinstance(error.value.__cause__, KeyError)
    assert rail.evaluate({"joints": [0, 0], "action": [0, 0]}).decision == dam.GuardDecision.PASS


def test_closed_session_is_distinct(make_rail):
    rail = make_rail()
    rail.close()
    with pytest.raises(dam.SessionClosedError):
        rail.evaluate({"joints": [0, 0], "action": [0, 0]})


@pytest.mark.parametrize(
    "cause,category",
    [
        (RuntimeError("execution failed"), dam.ExecutionError),
        (TimeoutError("wait expired"), dam.OperationTimeoutError),
    ],
)
def test_runtime_failures_preserve_original_cause(make_rail, monkeypatch, cause, category):
    rail = make_rail()
    monkeypatch.setattr(rail.runtime, "validate", Mock(side_effect=cause))
    with pytest.raises(category) as error:
        rail.evaluate({"joints": [0, 0], "action": [0, 0]})
    assert error.value.__cause__ is cause
