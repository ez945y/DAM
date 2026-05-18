from __future__ import annotations

from dam.services.mcap_sessions import (
    _guard_result_from_mapping,
    _guard_result_from_sequence,
    _message_get,
)


def test_message_get_ignores_non_mapping_payloads() -> None:
    assert _message_get(["not", "a", "dict"], "joint_positions", []) == []
    assert _message_get(("not", "a", "dict"), "joint_positions", []) == []


def test_guard_result_from_mapping() -> None:
    parsed = _guard_result_from_mapping(
        {
            "guard_name": "hardware",
            "layer": 3,
            "decision": 3,
            "decision_name": "FAULT",
            "reason": "camera disconnected",
            "is_violation": True,
            "metadata": {"host_health": {"memory_percent": 80.0}},
        }
    )

    assert parsed is not None
    assert parsed["guard_name"] == "hardware"
    assert parsed["layer_name"] == "L3"
    assert parsed["is_violation"] is True
    assert parsed["metadata"]["host_health"]["memory_percent"] == 80.0


def test_guard_result_from_tuple_payload() -> None:
    parsed = _guard_result_from_sequence(
        (
            1,
            123.0,
            "hardware",
            3,
            3,
            "FAULT",
            "camera disconnected",
            1.5,
            True,
            False,
            "source",
        )
    )

    assert parsed is not None
    assert parsed["guard_name"] == "hardware"
    assert parsed["layer_name"] == "L3"
    assert parsed["decision_name"] == "FAULT"
    assert parsed["fault_source"] == "source"
