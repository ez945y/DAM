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


def test_declared_hardware_and_policy_sections_load_without_input_space():
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
    assert cfg.hardware.preset == "so101_follower"
    assert cfg.policy.type == "act"


def test_hardware_accepts_action_layout_and_solver_overrides():
    path = write_temp_yaml(
        textwrap.dedent("""\
        version: "1"
        hardware:
          preset: so101_follower
          asset:
            type: usd
            path: /robots/franka.usd
          solvers:
            arm_kinematics:
              type: isaac_kinematics
          action_layout:
            - name: arm
              type: ee_pose
              solver: arm_kinematics
            - name: gripper
              type: scalar
        tasks:
          default:
            boundaries: []
        """)
    )
    cfg = StackfileLoader.load(path)
    assert cfg.hardware.asset == {"type": "usd", "path": "/robots/franka.usd"}
    assert cfg.hardware.solvers["arm_kinematics"]["type"] == "isaac_kinematics"
    assert cfg.hardware.action_layout[0]["solver"] == "arm_kinematics"


def test_interfaces_lower_to_runtime_sources_and_sinks():
    path = write_temp_yaml(
        textwrap.dedent("""\
        version: "1"
        hardware:
          preset: so101_follower
          interfaces:
            arm:
              type: motor
              capabilities: [observe_joints, command_joints]
              port: /dev/demo
            current:
              type: current
              capabilities: [robot_telemetry]
              ref: arm
        tasks:
          default:
            boundaries: []
        """)
    )
    cfg = StackfileLoader.load(path)
    assert cfg.hardware.sources is not None
    assert cfg.hardware.sinks is not None
    assert set(cfg.hardware.sources) == {"arm", "current"}
    assert cfg.hardware.sinks["command"].ref == "sources.arm"
