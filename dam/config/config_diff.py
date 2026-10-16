"""Canonical configuration identity and reviewable changes."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from dam.config.schema import StackfileConfig


def _require_finite(value: Any) -> None:
    # Pydantic's JSON serialization can turn non-finite numbers into null.
    # Validate the original values before computing a configuration identity.
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Configuration contains NaN or infinity")
    if isinstance(value, dict):
        for item in value.values():
            _require_finite(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _require_finite(item)


def config_hash(config: StackfileConfig) -> str:
    _require_finite(config.model_dump(mode="python"))
    payload = json.dumps(config.model_dump(mode="json"), sort_keys=True, allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def config_structure(config: StackfileConfig) -> dict[str, Any]:
    """Fields that cannot change through a parameter-only runtime reload."""
    data = config.model_dump(mode="json")
    for boundary in data["boundaries"].values():
        for node in boundary["nodes"]:
            node.pop("params", None)
    return data


def config_changes(before: Any, after: Any, path: str = "") -> list[dict[str, Any]]:
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        changes = []
        for key in sorted(before.keys() | after.keys()):
            child = f"{path}.{key}" if path else key
            if key not in before or key not in after:
                changes.append({"path": child, "before": before.get(key), "after": after.get(key)})
            else:
                changes.extend(config_changes(before[key], after[key], child))
        return changes
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        return [
            change
            for i, (old, new) in enumerate(zip(before, after, strict=True))
            for change in config_changes(old, new, f"{path}[{i}]")
        ]
    return [{"path": path, "before": before, "after": after}]
