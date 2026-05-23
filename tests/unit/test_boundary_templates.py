from dam.boundary.templates import get_boundary_templates


def test_task_gripper_sequence_template_shape():
    templates = {t["id"]: t for t in get_boundary_templates()}
    template = templates["task_gripper_sequence"]

    assert template["layer"] == "L2"
    boundary = template["boundary"]
    assert boundary["name"] == "task_gripper_sequence"
    assert boundary["type"] == "list"
    assert [n["node_id"] for n in boundary["nodes"]] == [
        "pick_left",
        "transfer_left_to_right",
        "place_right",
    ]
    assert [n["params"]["allowed_command"] for n in boundary["nodes"]] == [
        "close",
        "none",
        "open",
    ]
