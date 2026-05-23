"""Built-in boundary container templates for authoring stackfiles/UI configs."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

LEFT_PICK_ZONE = [[-0.175, -0.025], [-0.075, 0.075], [0.075, 0.225]]
RIGHT_PLACE_ZONE = [[0.025, 0.175], [-0.075, 0.075], [0.075, 0.225]]

_BOUNDARY_TEMPLATES: list[dict[str, Any]] = [
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
                {
                    "node_id": "pick_left",
                    "callback": "task_gripper_command_guard",
                    "fallback": "hold_position",
                    "timeout_sec": None,
                    "params": {
                        "allowed_command": "close",
                        "zone": LEFT_PICK_ZONE,
                    },
                },
                {
                    "node_id": "transfer_left_to_right",
                    "callback": "task_gripper_command_guard",
                    "fallback": "hold_position",
                    "timeout_sec": None,
                    "params": {"allowed_command": "none"},
                },
                {
                    "node_id": "place_right",
                    "callback": "task_gripper_command_guard",
                    "fallback": "hold_position",
                    "timeout_sec": None,
                    "params": {
                        "allowed_command": "open",
                        "zone": RIGHT_PLACE_ZONE,
                    },
                },
            ],
        },
    }
]


def get_boundary_templates() -> list[dict[str, Any]]:
    """Return built-in boundary templates as JSON-serialisable dictionaries."""
    return deepcopy(_BOUNDARY_TEMPLATES)
