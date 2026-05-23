"""Built-in boundary container templates for authoring stackfiles/UI configs."""

from __future__ import annotations

from copy import deepcopy
from typing import Literal, NotRequired, TypedDict


class BoundaryNodeTemplate(TypedDict):
    node_id: str
    callback: str
    fallback: str
    timeout_sec: float | None
    params: dict[str, object]


class BoundaryTemplateBody(TypedDict):
    name: str
    layer: str
    type: Literal["single", "list"]
    nodes: list[BoundaryNodeTemplate]


class BoundaryTemplate(TypedDict):
    id: str
    name: str
    layer: str
    description: NotRequired[str]
    boundary: BoundaryTemplateBody


LEFT_PICK_ZONE = [[-0.175, -0.025], [-0.075, 0.075], [0.075, 0.225]]
RIGHT_PLACE_ZONE = [[0.025, 0.175], [-0.075, 0.075], [0.075, 0.225]]


def _gripper_node(
    node_id: str,
    allowed_command: Literal["close", "open", "none"],
    zone: list[list[float]] | None = None,
) -> BoundaryNodeTemplate:
    params: dict[str, object] = {"allowed_command": allowed_command}
    if zone is not None:
        params["zone"] = zone
    return {
        "node_id": node_id,
        "callback": "task_gripper_command_guard",
        "fallback": "hold_position",
        "timeout_sec": None,
        "params": params,
    }


_BOUNDARY_TEMPLATES: list[BoundaryTemplate] = [
    {
        "id": "task_gripper_sequence",
        "name": "task_gripper_sequence",
        "layer": "L2",
        "description": "Left-to-right pick/transfer/place gripper command gate.",
        "boundary": {
            "name": "task_gripper_sequence",
            "layer": "L2",
            "type": "list",
            "nodes": [
                _gripper_node("pick_left", "close", LEFT_PICK_ZONE),
                _gripper_node("transfer_left_to_right", "none"),
                _gripper_node("place_right", "open", RIGHT_PLACE_ZONE),
            ],
        },
    }
]


def get_boundary_templates() -> list[BoundaryTemplate]:
    """Return built-in boundary templates as JSON-serialisable dictionaries."""
    return deepcopy(_BOUNDARY_TEMPLATES)
