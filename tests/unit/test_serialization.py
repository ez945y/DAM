"""Unit tests for the boundary serialization hook."""

from __future__ import annotations

import enum
import pathlib

import msgspec
import numpy as np
import pytest

from dam.services.serialization import MsgspecJSONResponse, msgspec_enc_hook


class _Color(enum.Enum):
    RED = "red"
    BLUE = "blue"


def test_enc_hook_converts_numpy_scalars_and_arrays():
    assert msgspec_enc_hook(np.int32(7)) == 7
    assert msgspec_enc_hook(np.float64(1.5)) == 1.5
    assert msgspec_enc_hook(np.bool_(True)) is True
    assert msgspec_enc_hook(np.array([1.0, 2.0])) == [1.0, 2.0]


def test_enc_hook_converts_paths_and_enums():
    assert msgspec_enc_hook(pathlib.PurePosixPath("/var/log/dam")) == "/var/log/dam"
    assert msgspec_enc_hook(_Color.RED) == "RED"


def test_enc_hook_converts_bytes_to_int_list():
    assert msgspec_enc_hook(b"\x01\x02\x03") == [1, 2, 3]
    assert msgspec_enc_hook(bytearray(b"\xff\x00")) == [255, 0]


def test_enc_hook_raises_typeerror_on_unknown_type():
    """Unknown types must fail loudly so a stray object in guard metadata
    doesn't silently become a useless ``str(obj)`` blob in MCAP/telemetry."""

    class _Custom:
        pass

    with pytest.raises(TypeError, match="_Custom"):
        msgspec_enc_hook(_Custom())


def test_enc_hook_integrates_with_msgspec_encode():
    """Only types msgspec can't natively encode reach the hook. bytes/Enum
    are msgspec-native, so this test focuses on numpy + Path which msgspec
    rejects without our hook."""
    payload = {
        "scale": np.float32(0.5),
        "arr": np.array([1, 2, 3]),
        "path": pathlib.PurePosixPath("/tmp/x"),
    }
    encoded = msgspec.json.encode(payload, enc_hook=msgspec_enc_hook)
    decoded = msgspec.json.decode(encoded)
    assert decoded == {"scale": 0.5, "arr": [1, 2, 3], "path": "/tmp/x"}


def test_enc_hook_not_called_for_msgspec_native_types():
    """msgspec encodes bytes as base64 and Enum by .value natively; the hook
    is bypassed for those even when registered. Document the observed
    behaviour so a future contributor doesn't try to override it via the
    registry (it won't work)."""
    assert msgspec.json.encode(_Color.RED, enc_hook=msgspec_enc_hook) == b'"red"'
    assert msgspec.json.encode(b"\xab\xcd", enc_hook=msgspec_enc_hook) == b'"q80="'


def test_msgspec_json_response_renders_via_enc_hook():
    response = MsgspecJSONResponse(content={"v": np.float32(2.0)})
    assert response.body == b'{"v":2.0}'
    assert response.media_type == "application/json"
