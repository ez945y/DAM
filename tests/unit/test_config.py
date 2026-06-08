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
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(content)
        return f.name


def test_valid_stackfile_loads():
    path = write_temp_yaml(VALID_YAML)
    cfg = StackfileLoader.load(path)
    assert "motion_guard" in cfg.boundaries
    assert abs(cfg.boundaries["motion_guard"].nodes[0].params["velocity_scale"] - 1.0) < 1e-9


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


def test_input_space_defaults_to_joint_when_declared_sections_exist():
    path = write_temp_yaml(
        textwrap.dedent("""\
        version: "1"
        hardware:
          preset: so101_follower
        policy:
          type: act
        tasks:
          default:
            boundaries: []
        """)
    )
    cfg = StackfileLoader.load(path)
    assert cfg.hardware.input_space == "joint"
    assert cfg.policy.input_space == "joint"


def test_input_space_accepts_ee_when_policy_and_hardware_match():
    path = write_temp_yaml(
        textwrap.dedent("""\
        version: "1"
        hardware:
          preset: franka_emika_panda
          input_space: EE
        policy:
          type: custom_rl
          input_space: ee
        tasks:
          default:
            boundaries: []
        """)
    )
    cfg = StackfileLoader.load(path)
    assert cfg.hardware.input_space == "ee"
    assert cfg.policy.input_space == "ee"


def test_input_space_mismatch_raises():
    path = write_temp_yaml(
        textwrap.dedent("""\
        version: "1"
        hardware:
          preset: so101_follower
          input_space: joint
        policy:
          type: custom_rl
          input_space: ee
        tasks:
          default:
            boundaries: []
        """)
    )
    with pytest.raises(ValueError, match="hardware.input_space and policy.input_space"):
        StackfileLoader.load(path)


def test_invalid_input_space_raises():
    path = write_temp_yaml(
        textwrap.dedent("""\
        version: "1"
        hardware:
          preset: so101_follower
          input_space: task
        tasks:
          default:
            boundaries: []
        """)
    )
    with pytest.raises(ValueError, match="input_space"):
        StackfileLoader.load(path)
