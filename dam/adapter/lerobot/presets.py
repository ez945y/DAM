"""Hardware presets for lerobot robots — joint names, default limits, degrees mode.

Each preset captures the physical configuration of a supported robot arm:
  - Joint names (in the order lerobot enumerates them)
  - Default joint limits [rad]
  - Whether the robot hardware operates in degrees internally (so101/so100/koch all do)

Usage::

    from dam.adapter.lerobot.presets import get_preset

    preset = get_preset("so101_follower")
    print(preset.joint_names)     # ['shoulder_pan', ..., 'gripper']
    print(preset.degrees_mode)    # True — robot speaks degrees
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class JointSpec:
    name: str
    lower_rad: float
    upper_rad: float
    max_vel_rad_s: float = math.pi  # 1 rev/s default
    is_gripper: bool = False  # gripper joint: conversion handled separately


@dataclass
class RobotPreset:
    """Full hardware description for one robot model."""

    name: str
    joints: list[JointSpec]
    control_hz: float = 50.0
    degrees_mode: bool = True  # True → robot HW uses degrees; convert to/from rad

    @property
    def joint_names(self) -> list[str]:
        return [j.name for j in self.joints]

    @property
    def upper_limits(self) -> list[float]:
        return [j.upper_rad for j in self.joints]

    @property
    def lower_limits(self) -> list[float]:
        return [j.lower_rad for j in self.joints]

    @property
    def max_velocities(self) -> list[float]:
        return [j.max_vel_rad_s for j in self.joints]


_d = math.radians  # shorthand: degrees → radians


# ── SO-ARM101 Follower (6-DOF + gripper) ─────────────────────────────────────
SO101_FOLLOWER = RobotPreset(
    name="so101_follower",
    degrees_mode=True,
    joints=[
        JointSpec("shoulder_pan", _d(-110), _d(110), max_vel_rad_s=_d(180)),
        JointSpec("shoulder_lift", _d(-100), _d(100), max_vel_rad_s=_d(180)),
        JointSpec("elbow_flex", _d(-97), _d(97), max_vel_rad_s=_d(180)),
        JointSpec("wrist_flex", _d(-95), _d(95), max_vel_rad_s=_d(180)),
        JointSpec("wrist_roll", _d(-160), _d(160), max_vel_rad_s=_d(180)),
        JointSpec("gripper", 0.0, 0.044, max_vel_rad_s=0.1, is_gripper=True),
    ],
)

# ── SO-ARM100 Follower (same motions, earlier HW revision) ─────────────────
SO100_FOLLOWER = RobotPreset(
    name="so100_follower",
    degrees_mode=True,
    joints=[
        JointSpec("shoulder_pan", _d(-110), _d(110), max_vel_rad_s=_d(180)),
        JointSpec("shoulder_lift", _d(-100), _d(100), max_vel_rad_s=_d(180)),
        JointSpec("elbow_flex", _d(-97), _d(97), max_vel_rad_s=_d(180)),
        JointSpec("wrist_flex", _d(-95), _d(95), max_vel_rad_s=_d(180)),
        JointSpec("wrist_roll", _d(-160), _d(160), max_vel_rad_s=_d(180)),
        JointSpec("gripper", 0.0, 0.044, max_vel_rad_s=0.1, is_gripper=True),
    ],
)

# ── Koch Follower (6-DOF Dynamixel arm) ───────────────────────────────────────
KOCH_FOLLOWER = RobotPreset(
    name="koch_follower",
    degrees_mode=True,
    joints=[
        JointSpec("shoulder_pan", _d(-180), _d(180)),
        JointSpec("shoulder_lift", _d(-180), _d(180)),
        JointSpec("elbow_flex", _d(-180), _d(180)),
        JointSpec("wrist_flex", _d(-180), _d(180)),
        JointSpec("wrist_roll", _d(-180), _d(180)),
        JointSpec("gripper", 0.0, 0.044, is_gripper=True),
    ],
)

# ── Generic 6-DOF (radian-native, no hardware assumption) ─────────────────────
GENERIC_6DOF = RobotPreset(
    name="generic_6dof",
    degrees_mode=False,
    joints=[JointSpec(f"joint_{i}", -math.pi, math.pi) for i in range(6)],
)

# ── Registry ──────────────────────────────────────────────────────────────────

_REGISTRY: dict[str, RobotPreset] = {
    p.name: p for p in [SO101_FOLLOWER, SO100_FOLLOWER, KOCH_FOLLOWER, GENERIC_6DOF]
}


def get_preset(name: str) -> RobotPreset:
    """Look up a robot preset by name.  Raises KeyError for unknown names."""
    key = name.lower().replace("-", "_")
    if key not in _REGISTRY:
        raise KeyError(f"Unknown robot preset '{name}'. Available: {sorted(_REGISTRY)}")
    return _REGISTRY[key]


def list_presets() -> list[str]:
    return sorted(_REGISTRY)
