"""Tests for built-in boundary callbacks."""

from __future__ import annotations

import numpy as np
import pytest

from dam.boundary.builtin_callbacks import (
    _CALLBACKS,
    base_geofence,
    cartesian_velocity_limit,
    check_force_torque_safe,
    check_joints_not_moving,
    check_velocity_smooth,
    get_catalog,
    joint_position_limits,
    joint_velocity_limit,
    keep_out_zone,
    orientation_limit,
    register_all,
)
from dam.registry.callback import CallbackRegistry
from dam.types.action import ActionProposal
from dam.types.observation import Observation
from dam.types.result import GuardDecision


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


def _action(positions, velocities=None) -> ActionProposal:
    return ActionProposal(
        target_joint_positions=np.array(positions, dtype=np.float64),
        target_joint_velocities=np.array(velocities, dtype=np.float64)
        if velocities is not None
        else None,
    )


# ── joint_position_limits (action-CLAMP) ──────────────────────────────────────


class TestJointLimits:
    def test_within_limits_pass(self):
        result = joint_position_limits(
            action=_action([0.0] * 6),
            upper=np.ones(6),
            lower=-np.ones(6),
        )
        assert result.decision == GuardDecision.PASS

    def test_exceeds_upper_clamps_down(self):
        result = joint_position_limits(
            action=_action([2.0, 0, 0, 0, 0, 0]),
            upper=np.ones(6),
            lower=-np.ones(6),
        )
        assert result.decision == GuardDecision.CLAMP
        assert result.clamped_action is not None
        assert result.clamped_action.target_joint_positions[0] == 1.0

    def test_exceeds_lower_clamps_up(self):
        result = joint_position_limits(
            action=_action([-2.0, 0, 0, 0, 0, 0]),
            upper=np.ones(6),
            lower=-np.ones(6),
        )
        assert result.decision == GuardDecision.CLAMP
        assert result.clamped_action.target_joint_positions[0] == -1.0

    def test_loader_normalises_degrees_so_callback_sees_radians(self):
        # Runtime never receives use_degrees; the loader strips it and
        # pre-converts the listed unit_params to radians.
        from dam.boundary.callbacks._registry import normalize_unit_params

        normalised = normalize_unit_params(
            "joint_position_limits",
            {"upper": [90.0] * 6, "lower": [-90.0] * 6, "use_degrees": True},
        )
        assert "use_degrees" not in normalised
        assert normalised["upper"] == pytest.approx([np.pi / 2] * 6)
        result = joint_position_limits(
            action=_action([2.0, 0, 0, 0, 0, 0]),
            **normalised,
        )
        assert result.decision == GuardDecision.CLAMP
        assert result.clamped_action is not None
        assert result.clamped_action.target_joint_positions[0] == pytest.approx(np.pi / 2)


# ── joint_velocity_limit (action-CLAMP) ───────────────────────────────────────


class TestJointVelocityLimit:
    def test_within_limit_pass(self):
        # Action wants positions [0.1] * 6 from obs [0,...,0] at dt=0.1 → v=1.0
        result = joint_velocity_limit(
            obs=_obs(positions=[0.0] * 6),
            action=_action([0.1] * 6),
            dt=0.1,
            max_velocities=[2.0] * 6,
        )
        assert result.decision == GuardDecision.PASS

    def test_exceeds_limit_scales_down(self):
        # Action wants positions [1.0]*6 from obs [0,...,0] at dt=0.1 → v=10.0,
        # limit 2.0/s → scale ratio 5×, expect clamped positions = 0.2
        result = joint_velocity_limit(
            obs=_obs(positions=[0.0] * 6),
            action=_action([1.0] * 6),
            dt=0.1,
            max_velocities=[2.0] * 6,
        )
        assert result.decision == GuardDecision.CLAMP
        np.testing.assert_allclose(
            result.clamped_action.target_joint_positions, [0.2] * 6, atol=1e-9
        )

    def test_loader_normalises_velocity_limit_degrees_to_radians(self):
        from dam.boundary.callbacks._registry import normalize_unit_params

        normalised = normalize_unit_params(
            "joint_velocity_limit",
            {"max_velocities": [90.0] * 6, "use_degrees": True},
        )
        assert "use_degrees" not in normalised
        assert normalised["max_velocities"] == pytest.approx([np.pi / 2] * 6)
        result = joint_velocity_limit(
            obs=_obs(positions=[0.0] * 6),
            action=_action([1.0] * 6),
            dt=0.1,
            **normalised,
        )
        assert result.decision == GuardDecision.CLAMP
        np.testing.assert_allclose(
            result.clamped_action.target_joint_positions,
            [np.pi / 20] * 6,
            atol=1e-9,
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
        r = check_force_torque_safe(obs=obs, max_force_n=50.0, max_torque_nm=10.0)
        assert r.decision.name == "PASS"
        assert "force_n" in r.metadata

    def test_excessive_force_fail(self):
        obs = _obs(force_torque=[100.0, 0, 0, 0, 0, 0])
        r = check_force_torque_safe(obs=obs, max_force_n=50.0, max_torque_nm=10.0)
        assert r.decision.name == "REJECT"

    def test_excessive_torque_fail(self):
        obs = _obs(force_torque=[0, 0, 0, 20.0, 0, 0])
        r = check_force_torque_safe(obs=obs, max_force_n=50.0, max_torque_nm=10.0)
        assert r.decision.name == "REJECT"

    def test_no_force_torque_pass(self):
        obs = _obs()
        r = check_force_torque_safe(obs=obs)
        assert r.decision.name == "PASS"


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

    def test_register_all_auto_captures_decorated(self):
        """Every @boundary_callback fn must be auto-registered (no manual list)."""
        import dam.registry.callback as rcmod

        orig = rcmod._registry
        rcmod._registry = CallbackRegistry()
        try:
            register_all()
            registered = set(rcmod._registry.list_all())
            assert set(_CALLBACKS) == registered
            assert {c["name"] for c in get_catalog()} == registered
        finally:
            rcmod._registry = orig

    def test_catalog_hides_runtime_injected_params(self):
        by_name = {c["name"]: c for c in get_catalog()}

        workspace_params = by_name["workspace"]["params"]
        assert "bounds" in workspace_params
        assert "action" not in workspace_params
        assert "kinematics_resolver" not in workspace_params
        assert "dynamics" not in workspace_params
        assert workspace_params["cbf_gamma"]["internal"] is True
        assert workspace_params["cbf_alpha"]["internal"] is True
        assert workspace_params["slack_weight"]["internal"] is True
        assert workspace_params["cbf_gamma"]["description"]

        host_params = by_name["host_health_limit"]["params"]
        assert "host_health" not in host_params
        assert host_params["max_cpu_percent"]["default"] == 95.0
        assert host_params["max_cpu_percent"]["description"]

    def test_catalog_param_descriptions_are_declared_by_callbacks(self):
        by_name = {c["name"]: c for c in get_catalog()}

        assert "metres" in by_name["task_gripper_command_guard"]["params"]["zone"]["description"]
        assert set(by_name["joint_position_limits"]["unit_params"]) == {"upper", "lower"}
        assert by_name["joint_velocity_limit"]["unit_params"] == ["max_velocities"]
        ood_params = by_name["ood_detector"]["params"]
        assert ood_params["temporal_smoothing_frames"].get("internal") is True
        assert ood_params["nn_threshold"].get("internal") is True
        assert ood_params["sigma"]["default"] == 3.0
        assert ood_params["sigma"].get("internal") is not True
        assert "ood_context" not in ood_params
        assert not {"ood_welford", "ood_memory_bank", "ood_normalizing_flow"} & set(by_name)


# ── Fake pinocchio-like dynamics context ──────────────────────────────────────


class _FakePlacement:
    def __init__(self, translation, rotation):
        self.translation = np.asarray(translation, dtype=float)
        self.rotation = np.asarray(rotation, dtype=float)


class _FakeDynamics:
    def __init__(self, jac=None, translation=None, rotation=None, available=True):
        self._jac = jac
        self._placement = _FakePlacement(
            translation if translation is not None else [0.0, 0.0, 0.0],
            rotation if rotation is not None else np.eye(3),
        )
        self.available = available
        self.default_frame_id = 0
        self.updated_with = None

    def update(self, q):
        self.updated_with = np.asarray(q, dtype=float)

    def frame_jacobian(self, _fid):
        return self._jac

    def frame_placement(self, _fid):
        return self._placement


# ── cartesian_velocity_limit ──────────────────────────────────────────────────


class TestCartesianVelocityLimit:
    def test_within_limit_pass(self):
        obs = _obs(positions=[0.0] * 6, velocities=[0.2, 0, 0, 0, 0, 0])
        dyn = _FakeDynamics(jac=np.eye(6))
        assert cartesian_velocity_limit(obs=obs, dynamics=dyn, max_linear_speed=0.25) is True

    def test_exceeds_linear_fail(self):
        obs = _obs(positions=[0.0] * 6, velocities=[0.5, 0, 0, 0, 0, 0])
        dyn = _FakeDynamics(jac=np.eye(6))
        result = cartesian_velocity_limit(obs=obs, dynamics=dyn, max_linear_speed=0.25)
        assert result is not True
        assert result[0] is False

    def test_exceeds_angular_fail(self):
        obs = _obs(positions=[0.0] * 6, velocities=[0, 0, 0, 5.0, 0, 0])
        dyn = _FakeDynamics(jac=np.eye(6))
        result = cartesian_velocity_limit(
            obs=obs, dynamics=dyn, max_linear_speed=10.0, max_angular_speed=1.0
        )
        assert result is not True
        assert result[0] is False

    def test_no_dynamics_pass(self):
        obs = _obs(velocities=[9.0] * 6)
        assert cartesian_velocity_limit(obs=obs, dynamics=None) is True

    def test_unavailable_dynamics_pass(self):
        obs = _obs(velocities=[9.0] * 6)
        dyn = _FakeDynamics(jac=np.eye(6), available=False)
        assert cartesian_velocity_limit(obs=obs, dynamics=dyn) is True

    def test_no_velocities_pass(self):
        obs = _obs(positions=[0.0] * 6)
        dyn = _FakeDynamics(jac=np.eye(6))
        assert cartesian_velocity_limit(obs=obs, dynamics=dyn) is True


# ── keep_out_zone ─────────────────────────────────────────────────────────────


class TestKeepOutZone:
    def test_outside_box_pass(self):
        obs = _obs(ee_pose=[1.0, 1.0, 1.0, 0, 0, 0, 1])
        boxes = [[[-0.1, 0.1], [-0.1, 0.1], [-0.1, 0.1]]]
        assert keep_out_zone(obs=obs, boxes=boxes) is True

    def test_inside_box_fail(self):
        obs = _obs(ee_pose=[0.0, 0.0, 0.0, 0, 0, 0, 1])
        boxes = [[[-0.1, 0.1], [-0.1, 0.1], [-0.1, 0.1]]]
        result = keep_out_zone(obs=obs, boxes=boxes)
        assert result is not True
        assert result[0] is False

    def test_inside_sphere_fail(self):
        obs = _obs(ee_pose=[0.05, 0.0, 0.0, 0, 0, 0, 1])
        result = keep_out_zone(obs=obs, spheres=[[0.0, 0.0, 0.0, 0.1]])
        assert result is not True
        assert result[0] is False

    def test_outside_sphere_pass(self):
        obs = _obs(ee_pose=[0.5, 0.0, 0.0, 0, 0, 0, 1])
        assert keep_out_zone(obs=obs, spheres=[[0.0, 0.0, 0.0, 0.1]]) is True

    def test_no_pose_pass(self):
        obs = _obs()
        assert keep_out_zone(obs=obs, boxes=[[[-1, 1], [-1, 1], [-1, 1]]]) is True

    def test_uses_dynamics_translation(self):
        obs = _obs(positions=[0.1] * 6)
        dyn = _FakeDynamics(translation=[0.0, 0.0, 0.0])
        result = keep_out_zone(
            obs=obs, boxes=[[[-0.1, 0.1], [-0.1, 0.1], [-0.1, 0.1]]], dynamics=dyn
        )
        assert result is not True
        assert result[0] is False


# ── orientation_limit ─────────────────────────────────────────────────────────


class TestOrientationLimit:
    def test_upright_pass(self):
        obs = _obs(ee_pose=[0, 0, 0.3, 0, 0, 0, 1])  # identity quat
        assert orientation_limit(obs=obs, max_tilt_deg=30.0) is True

    def test_tilted_fail(self):
        # 90° rotation about x: tool +z maps away from world +z.
        s = float(np.sin(np.pi / 4))
        obs = _obs(ee_pose=[0, 0, 0.3, s, 0, 0, s])
        result = orientation_limit(obs=obs, max_tilt_deg=30.0)
        assert result is not True
        assert result[0] is False

    def test_tilt_within_generous_limit_pass(self):
        s = float(np.sin(np.pi / 4))
        obs = _obs(ee_pose=[0, 0, 0.3, s, 0, 0, s])
        assert orientation_limit(obs=obs, max_tilt_deg=120.0) is True

    def test_no_pose_pass(self):
        obs = _obs()
        assert orientation_limit(obs=obs) is True

    def test_uses_dynamics_rotation(self):
        obs = _obs(positions=[0.0] * 6)
        # rotation that flips tool z to -z → 180° tilt
        rot = np.diag([1.0, -1.0, -1.0])
        dyn = _FakeDynamics(rotation=rot)
        result = orientation_limit(obs=obs, max_tilt_deg=30.0, dynamics=dyn)
        assert result is not True
        assert result[0] is False


# ── base_geofence ─────────────────────────────────────────────────────────────


def _obs_base(xy) -> Observation:
    return Observation(
        timestamp=0.0,
        joint_positions=np.zeros(6),
        channels={"base_pose": np.asarray(xy, dtype=float)},
    )


class TestBaseGeofence:
    BOX = [[-1.0, 1.0], [-1.0, 1.0]]

    def test_inside_box_pass(self):
        assert base_geofence(obs=_obs_base([0.0, 0.0]), bounds=self.BOX) is True

    def test_outside_box_fail(self):
        result = base_geofence(obs=_obs_base([2.0, 0.0]), bounds=self.BOX)
        assert result is not True
        assert result[0] is False

    def test_inside_polygon_pass(self):
        poly = [[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0]]
        assert base_geofence(obs=_obs_base([1.0, 1.0]), polygon=poly) is True

    def test_outside_polygon_fail(self):
        poly = [[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0]]
        result = base_geofence(obs=_obs_base([5.0, 5.0]), polygon=poly)
        assert result is not True
        assert result[0] is False

    def test_no_channel_pass(self):
        obs = Observation(timestamp=0.0, joint_positions=np.zeros(6))
        assert base_geofence(obs=obs, bounds=self.BOX) is True

    def test_custom_channel_name(self):
        obs = Observation(
            timestamp=0.0,
            joint_positions=np.zeros(6),
            channels={"odom": np.array([9.0, 9.0, 0.0])},
        )
        result = base_geofence(obs=obs, bounds=self.BOX, channel="odom")
        assert result is not True
        assert result[0] is False
