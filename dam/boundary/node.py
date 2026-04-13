from __future__ import annotations

from dataclasses import dataclass

from dam.boundary.constraint import BoundaryConstraint


@dataclass(frozen=True)
class BoundaryNode:
    node_id: str
    constraint: BoundaryConstraint
    fallback: str = "emergency_stop"
    timeout_sec: float | None = None
