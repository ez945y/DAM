from __future__ import annotations

from dam.services.mcap_sessions import (
    _decode_msg,
    _parse_guard_result,
)


def test_decode_msg_json() -> None:
    data = b'{"cycle_id": 42, "has_violation": true}'
    result = _decode_msg(data, "json")
    assert result == {"cycle_id": 42, "has_violation": True}


def test_decode_msg_msgpack() -> None:
    import msgpack

    payload = {"cycle_id": 7, "obs_timestamp": 1.0}
    data = msgpack.packb(payload)
    result = _decode_msg(data, "application/x-msgpack")
    assert result is not None
    assert result["cycle_id"] == 7


def test_decode_msg_returns_none_for_array() -> None:
    import msgpack

    data = msgpack.packb([1, 2, 3])
    assert _decode_msg(data, "msgpack") is None


def test_decode_msg_returns_none_for_unknown_encoding() -> None:
    assert _decode_msg(b"hello", "text/plain") is None


def test_parse_guard_result() -> None:
    parsed = _parse_guard_result(
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


def test_parse_guard_result_defaults() -> None:
    parsed = _parse_guard_result({"guard_name": "motion"})
    assert parsed is not None
    assert parsed["layer"] == 0
    assert parsed["layer_name"] == "L0"
    assert parsed["decision_name"] == "PASS"
    assert parsed["is_violation"] is False
    assert parsed["metadata"] == {}


def test_parse_guard_result_rejects_non_dict() -> None:
    assert _parse_guard_result([1, 2, 3]) is None
    assert _parse_guard_result("not a dict") is None
    assert _parse_guard_result(None) is None
