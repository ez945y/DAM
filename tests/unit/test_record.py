"""Regression tests for scripts/record.py hardware → lerobot CLI arg mapping.

The redesign moved stackfile IO under ``interfaces:`` (``sources:`` is the
lowered runtime form). record.py reads the raw YAML, so it must find the motor
robot / cameras / teleop from ``interfaces:`` — not the absent ``sources:`` key.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RECORD_PATH = _REPO_ROOT / "scripts" / "record.py"

_spec = importlib.util.spec_from_file_location("dam_record_script", _RECORD_PATH)
assert _spec and _spec.loader
record = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(record)


def _args_from(yaml_text: str) -> list[str]:
    return record._build_hardware_args(yaml.safe_load(yaml_text))


def test_build_hardware_args_reads_interfaces():
    args = _args_from(
        """
        hardware:
          preset: so101_follower
          interfaces:
            arm:
              type: motor
              capabilities: [observe_joints, command_joints]
              port: /dev/ttyDEMO
              robot_type: so101_follower
              id: follower_arm
            top:
              type: opencv
              index_or_path: 0
              width: 640
              height: 480
              fps: 30
            temperature:
              capabilities: [robot_telemetry]
              ref: arm
          teleop:
            type: so101_leader
            port: /dev/ttyLEAD
            id: leader_arm
        """
    )
    assert "--robot.type=so101_follower" in args
    assert "--robot.port=/dev/ttyDEMO" in args
    assert "--robot.id=follower_arm" in args
    # Cameras collected from the opencv interface; telemetry channel ignored.
    assert any(a.startswith("--robot.cameras=") for a in args)
    assert "--teleop.type=so101_leader" in args


def test_build_hardware_args_still_reads_legacy_sources():
    """Stackfiles that use the lowered ``sources:`` form keep working."""
    args = _args_from(
        """
        hardware:
          preset: so101_follower
          sources:
            arm:
              type: motor
              port: /dev/ttyOLD
              robot_type: so101_follower
        """
    )
    assert "--robot.type=so101_follower" in args
    assert "--robot.port=/dev/ttyOLD" in args
