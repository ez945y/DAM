"""Centralized serialization utilities for boundaries (API responses, WebSockets).

Provides a dict-dispatched encoder hook for ``msgspec`` covering the
non-standard types that show up in guard metadata: NumPy arrays/scalars,
filesystem paths, enums, and raw bytes. Unknown types raise ``TypeError``
so accidental object leaks surface during development instead of being
silently stringified into telemetry / MCAP records.
"""

from __future__ import annotations

import enum
import pathlib
from collections.abc import Callable
from typing import Any

import numpy as np
from fastapi import Response
from msgspec import json as msgspec_json

# Exact-type dispatch first (O(1)); falls back to a small subclass scan.
# msgspec already handles dataclasses, msgspec.Struct, and standard
# containers natively, so we only register the types it can't.
_EXACT: dict[type, Callable[[Any], Any]] = {
    bytes: list,
    bytearray: list,
}

# Order matters here only for subclass coverage; each entry must be a base
# class whose subclasses share a single conversion rule.
_SUBCLASS: tuple[tuple[type, Callable[[Any], Any]], ...] = (
    (np.ndarray, lambda obj: obj.tolist()),
    (np.integer, int),
    (np.floating, float),
    (np.bool_, bool),
    (pathlib.PurePath, str),
    (enum.Enum, lambda obj: obj.name),
)


def msgspec_enc_hook(obj: Any) -> Any:
    """``msgspec`` encoder hook: convert non-native types to JSON primitives.

    Raises ``TypeError`` for unknown types so a stray object in guard
    metadata surfaces as a loud failure during development rather than a
    silent ``str(obj)`` blob in production MCAP/telemetry records.
    """
    converter = _EXACT.get(type(obj))
    if converter is not None:
        return converter(obj)
    for base, sub_converter in _SUBCLASS:
        if isinstance(obj, base):
            return sub_converter(obj)
    raise TypeError(f"Cannot serialize object of type {type(obj).__name__!r}")


class MsgspecJSONResponse(Response):
    """FastAPI response that encodes via ``msgspec`` with our enc_hook.

    Routes that return raw dicts/lists containing numpy / Path / Enum can
    declare ``response_class=MsgspecJSONResponse`` instead of hand-rolling
    ``Response(content=msgspec.json.encode(...), media_type=...)`` at every
    endpoint.
    """

    media_type = "application/json"

    def render(self, content: Any) -> bytes:
        return msgspec_json.encode(content, enc_hook=msgspec_enc_hook)
