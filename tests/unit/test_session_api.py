import copy
from unittest.mock import Mock

import pytest

import dam
from dam.runtime.guard_runtime import GuardRuntime


@pytest.fixture
def config():
    return {
        "hardware": {"joint_names": ["a", "b"], "degrees_mode": False},
        "guards": [{"L1": "motion"}],
        "boundaries": {
            "bounds": {
                "layer": "L1",
                "type": "single",
                "nodes": [
                    {
                        "callback": "joint_position_limits",
                        "params": {"lower": [-1, -1], "upper": [1, 1]},
                    }
                ],
            }
        },
        "tasks": {"default": {"boundaries": ["bounds"]}, "other": {"boundaries": ["bounds"]}},
    }


@pytest.fixture
def rail(config):
    with dam.Guardrail(config, quiet=True) as instance:
        yield instance


def inputs():
    return {"joints": [0, 0], "action": [2, 2]}


def changed(config):
    new = copy.deepcopy(config)
    new["boundaries"]["bounds"]["nodes"][0]["params"]["upper"] = [0.2, 0.2]
    return new


def test_reload_requires_cycle_receipt_and_updates_output(rail, config):
    assert rail.evaluate(inputs()).command == [1, 1]
    receipt = rail.request_reload(changed(config))
    assert receipt.accepted and receipt.status == "pending"
    assert receipt.config_version is None
    assert rail.session_status.config_version == 0
    result = rail.evaluate(inputs())
    assert result.command == [0.2, 0.2]
    assert result.config_version == 1
    applied = rail.reload_receipt(receipt.id)
    assert applied.status == "applied" and applied.config_version == 1
    assert receipt.status == "pending"  # detached snapshot


def test_structural_reload_rejection_keeps_previous_configuration(rail, config):
    config["hardware"]["joint_names"] = ["x", "y"]
    receipt = rail.request_reload(config)
    result = rail.evaluate(inputs())
    rejected = rail.reload_receipt(receipt.id)
    assert rejected.status == "rejected" and not rejected.accepted
    assert "Structural" in rejected.message
    assert result.command == [1, 1]
    assert result.config_version == 0


def test_stale_version_is_rejected_and_retry_can_apply(rail, config):
    receipt = rail.request_reload(changed(config), expected_version=4)
    rail.evaluate(inputs())
    assert rail.reload_receipt(receipt.id).status == "rejected"
    retry = rail.request_reload(changed(config))
    rail.evaluate(inputs())
    assert rail.reload_receipt(retry.id).status == "applied"


def test_duplicate_request_does_not_replace_pending_update(rail, config):
    first = rail.request_reload(changed(config))
    with pytest.raises(dam.ExecutionError, match="already pending"):
        rail.request_reload(config)
    rail.evaluate(inputs())
    assert rail.reload_receipt(first.id).status == "applied"


def test_reset_clears_history_retains_applied_version_and_receipts(rail, config):
    receipt = rail.request_reload(changed(config))
    previous = rail.evaluate(inputs())
    old = rail.runtime
    before = rail.session_status
    status = rail.reset()
    assert old._shutdown_complete
    assert status.state == "open"
    assert status.config_version == 1
    assert status.cycle_id == before.cycle_id
    assert status.state_generation > before.state_generation
    assert rail.last_results == []
    assert rail.runtime._prev_validated_positions is None
    assert rail.reload_receipt(receipt.id).status == "applied"
    next_result = rail.evaluate(inputs())
    assert next_result.cycle_id > previous.cycle_id
    assert next_result.command == [0.2, 0.2]
    assert rail.reset().state == "open"


def test_task_switch_cancels_pending_reload_and_changes_task(rail, config):
    receipt = rail.request_reload(changed(config))
    status = rail.switch_task("other")
    assert status.task == "other"
    assert rail.reload_receipt(receipt.id).status == "rejected"
    result = rail.evaluate(inputs())
    assert result.task == "other" and result.command == [1, 1]


def test_unknown_task_leaves_original_session_available(rail):
    before = rail.session_status
    with pytest.raises(dam.ConfigurationError, match="Unknown task"):
        rail.switch_task("missing")
    assert rail.session_status == before
    assert rail.evaluate(inputs()).command == [1, 1]


def test_close_rejects_pending_and_reset_reopens(rail, config):
    receipt = rail.request_reload(changed(config))
    rail.close()
    rail.close()
    assert rail.session_status.state == "closed"
    assert rail.reload_receipt(receipt.id).status == "rejected"
    with pytest.raises(dam.SessionClosedError):
        rail.request_reload(config)
    assert rail.reset().state == "open"


def test_reset_timeout_does_not_construct_replacement_or_allow_evaluation(rail, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(
            rail.runtime, "_stop_slow_lane", Mock(side_effect=TimeoutError("worker busy"))
        )
        build = Mock(side_effect=AssertionError("must stop old worker first"))
        patch.setattr(GuardRuntime, "_from_config", build)
        with pytest.raises(dam.OperationTimeoutError):
            rail.reset()
        build.assert_not_called()
        assert rail.session_status.state == "recovery_required"
        with pytest.raises(dam.SessionClosedError):
            rail.evaluate(inputs())
    assert rail.reset().state == "open"


def test_failed_reconstruction_retains_receipts_and_can_retry(rail, config, monkeypatch):
    receipt = rail.request_reload(changed(config))
    rail.evaluate(inputs())
    with monkeypatch.context() as patch:
        patch.setattr(GuardRuntime, "start_task", Mock(side_effect=RuntimeError("startup failed")))
        with pytest.raises(dam.ConfigurationError, match="startup failed"):
            rail.reset()
        assert rail.session_status.state == "recovery_required"
        assert rail.session_status.config_version == 1
        assert rail.reload_receipt(receipt.id).status == "applied"
    status = rail.reset()
    assert status.state == "open" and status.config_version == 1
    assert rail.evaluate(inputs()).command == [0.2, 0.2]


def test_unknown_receipt_is_explicit(rail):
    with pytest.raises(dam.InputContractError, match="Unknown or expired"):
        rail.reload_receipt("missing")
