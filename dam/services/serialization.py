"""Centralized serialization utilities for boundaries (API responses, WebSockets).

Provides a registry-pattern encoder hook for msgspec to dynamically resolve
non-standard types like NumPy arrays/scalars, pathlib Paths, and enums.
"""

from __future__ import annotations

import dataclasses
import enum
import pathlib
from typing import Any

import msgspec
import numpy as np


class EncoderRegistry:
    """A registry mapping unsupported types to their custom JSON serialization logic."""

    def __init__(self) -> None:
        self._registry: list[tuple[type, Any]] = []

    def register(self, type_or_base: type, converter: Any) -> None:
        """Register a serializer callback for a type or any of its subclasses."""
        self._registry.append((type_or_base, converter))

    def enc_hook(self, obj: Any) -> Any:
        """Msgspec custom encoding hook.

        Maps unregistered classes or numpy types to standard JSON-serializable primitives.
        """
        for type_or_base, converter in self._registry:
            if isinstance(obj, type_or_base):
                return converter(obj)

        # Check if the object is a dataclass instance (e.g. MotionQPConstraint)
        if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
            return dataclasses.asdict(obj)

        # Check if the object has a custom to_dict method (like custom dataclasses or models)
        if hasattr(obj, "to_dict") and callable(obj.to_dict):
            return obj.to_dict()

        # Fallback to string representation to avoid hard serialization failures
        return str(obj)


# Initialize global boundary encoder registry
registry = EncoderRegistry()

# 1. NumPy Types (highly frequent in guard metadata / telemetry)
registry.register(np.ndarray, lambda obj: obj.tolist())
registry.register(np.integer, lambda obj: int(obj))
registry.register(np.floating, lambda obj: float(obj))
registry.register(np.bool_, lambda obj: bool(obj))

# 2. Filesystem Paths (frequent in MCAP sessions / stackconfigs)
registry.register(pathlib.PurePath, lambda obj: str(obj))

# 3. Custom / Standard Enums
registry.register(enum.Enum, lambda obj: obj.name if hasattr(obj, "name") else str(obj))

# 4. Raw Bytes / Bytearray (historically converted to integer list for MCAP telemetry)
registry.register(bytes, lambda obj: list(obj))
registry.register(bytearray, lambda obj: list(obj))

# Export the unified hook for msgspec json.encode or to_builtins
msgspec_enc_hook = registry.enc_hook
