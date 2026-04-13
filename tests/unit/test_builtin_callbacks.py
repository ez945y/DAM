"""Tests for built-in boundary callbacks."""

from __future__ import annotations

import numpy as np

from dam.boundary.builtin_callbacks import (
    check_force_torque_safe,
    check_gripper_clear,
    check_joints_not_moving,
    check_velocity_smooth,
    joint_position_limits,
    register_all,
)
from dam.registry.callback import CallbackRegistry
from dam.types.observation import Observation


def _obs(
    positions=None,
    velocities=None,
    ee_pose=None,
    force_torque=None,
    metadata=None,
) -> Observation:
    return Observation(
        timestamp=0.0,
        joint_positions=np.array(positions or [0.0] * 6),
        joint_velocities=np.array(velocities) if velocities is not None else None,
        end_effector_pose=np.array(ee_pose) if ee_pose is not None else None,
        force_torque=np.array(force_torque) if force_torque is not None else None,
        metadata=metadata or {},
    )


# ── joint_position_limits ───────────────────────────────────────────────────────────


class TestJointLimits:
    def test_within_limits_pass(self):
        obs = _obs(positions=[0.0] * 6)
        assert (
            joint_position_limits(
                obs=obs,
                upper=np.ones(6),
                lower=-np.ones(6),
            )
            is True
        )

    def test_exceeds_upper_fail(self):
        obs = _obs(positions=[2.0, 0, 0, 0, 0, 0])
        assert (
            joint_position_limits(
                obs=obs,
                upper=np.ones(6),
                lower=-np.ones(6),
            )
            is False
        )

    def test_exceeds_lower_fail(self):
        obs = _obs(positions=[-2.0, 0, 0, 0, 0, 0])
        assert (
            joint_position_limits(
                obs=obs,
                upper=np.ones(6),
                lower=-np.ones(6),
            )
            is False
        )


# ── check_velocity_smooth ─────────────────────────────────────────────────────


class TestCheckVelocitySmooth:
    def test_low_velocity_pass(self):
        obs = _obs(velocities=[0.1] * 6)
        assert check_velocity_smooth(obs=obs, max_jerk_norm=10.0) is True

    def test_high_velocity_fail(self):
        obs = _obs(velocities=[5.0] * 6)
        assert check_velocity_smooth(obs=obs, max_jerk_norm=1.0) is False

    def test_no_velocities_pass(self):
        obs = _obs()
        assert check_velocity_smooth(obs=obs) is True


# ── check_force_torque_safe ───────────────────────────────────────────────────


class TestCheckForceTorqueSafe:
    def test_safe_force_pass(self):
        obs = _obs(force_torque=[1.0, 0, 0, 0, 0, 0])
        assert check_force_torque_safe(obs=obs, max_force_n=50.0, max_torque_nm=10.0) is True

    def test_excessive_force_fail(self):
        obs = _obs(force_torque=[100.0, 0, 0, 0, 0, 0])
        assert check_force_torque_safe(obs=obs, max_force_n=50.0, max_torque_nm=10.0) is False

    def test_excessive_torque_fail(self):
        obs = _obs(force_torque=[0, 0, 0, 20.0, 0, 0])
        assert check_force_torque_safe(obs=obs, max_force_n=50.0, max_torque_nm=10.0) is False

    def test_no_force_torque_pass(self):
        obs = _obs()
        assert check_force_torque_safe(obs=obs) is True


# ── check_joints_not_moving ───────────────────────────────────────────────────


class TestCheckJointsNotMoving:
    def test_stationary_pass(self):
        obs = _obs(velocities=[0.001] * 6)
        assert check_joints_not_moving(obs=obs, max_speed_rad_s=0.01) is True

    def test_moving_fail(self):
        obs = _obs(velocities=[0.1] * 6)
        assert check_joints_not_moving(obs=obs, max_speed_rad_s=0.01) is False

    def test_no_velocities_pass(self):
        obs = _obs()
        assert check_joints_not_moving(obs=obs) is True


# ── check_gripper_clear ───────────────────────────────────────────────────────


class TestCheckGripperClear:
    def test_open_pass(self):
        obs = _obs(metadata={"gripper_pos": 0.05})
        assert check_gripper_clear(obs=obs, min_gripper_opening_m=0.005) is True

    def test_closed_fail(self):
        obs = _obs(metadata={"gripper_pos": 0.001})
        assert check_gripper_clear(obs=obs, min_gripper_opening_m=0.005) is False

    def test_no_metadata_pass(self):
        obs = _obs()
        assert check_gripper_clear(obs=obs) is True


# ── register_all ──────────────────────────────────────────────────────────────


class TestRegisterAll:
    def test_register_all_no_crash(self):
        # Use a fresh registry to avoid conflicts with global
        import dam.registry.callback as rcmod

        orig = rcmod._registry
        rcmod._registry = CallbackRegistry()
        try:
            register_all()
            reg = rcmod._registry
            assert "check_force_torque_safe" in reg.list_all()
            assert "joint_position_limits" in reg.list_all()
        finally:
            rcmod._registry = orig

    def test_register_all_idempotent(self):
        """Calling register_all twice should not raise."""
        import dam.registry.callback as rcmod

        orig = rcmod._registry
        rcmod._registry = CallbackRegistry()
        try:
            register_all()
            register_all()  # second call must not crash
        finally:
            rcmod._registry = orig
