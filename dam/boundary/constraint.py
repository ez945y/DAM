from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class BoundaryConstraint:
    """Unified constraint for a boundary node.

    All constraint parameters (max_speed, bounds, joint_position_limits, …)
    live in ``params`` as plain key-value pairs, mirroring the Stackfile layout.
    The Guard or callback invoked at runtime reads only the keys it needs.

    Fields
    ------
    params   : Arbitrary parameter dict — injected into the Guard/callback call.
    callback : Name of the registered callback function (or None for layer-native
               guards that don't use an external callback).
    """

    params: dict[str, Any] = field(default_factory=dict)
    callback: str | None = None
