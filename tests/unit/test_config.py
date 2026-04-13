import tempfile
import textwrap

import pytest

from dam.config.loader import StackfileLoader

VALID_YAML = textwrap.dedent("""\
    version: "1"
    boundaries:
      motion_guard:
        type: single
        nodes:
          - node_id: default
            callback: joint_position_limits
            params:
              upper: [3.14, 3.14, 3.14, 3.14, 3.14, 3.14]
              lower: [-3.14, -3.14, -3.14, -3.14, -3.14, -3.14]
              velocity_scale: 1.0
    tasks:
      test_task:
        boundaries: [motion_guard]
    safety:
      control_frequency_hz: 50.0
""")

INVALID_YAML_MISSING_TYPE = textwrap.dedent("""\
    boundaries:
      main_boundary:
        nodes:
          - node_id: default
            constraint: {}
""")


def write_temp_yaml(content: str) -> str:
    path = tempfile.mktemp(suffix=".yaml")
    with open(path, "w") as f:
        f.write(content)
    return path


def test_valid_stackfile_loads():
    path = write_temp_yaml(VALID_YAML)
    cfg = StackfileLoader.load(path)
    assert "motion_guard" in cfg.boundaries
    assert cfg.boundaries["motion_guard"].nodes[0].params["velocity_scale"] == 1.0


def test_invalid_stackfile_raises():
    path = write_temp_yaml(INVALID_YAML_MISSING_TYPE)
    with pytest.raises((ValueError, Exception)):
        StackfileLoader.load(path)


def test_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        StackfileLoader.load("/nonexistent/path/stack.yaml")


def test_validate_method():
    path = write_temp_yaml(VALID_YAML)
    StackfileLoader.validate(path)  # Should not raise
