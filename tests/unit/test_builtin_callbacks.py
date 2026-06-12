"""Tests for built-in boundary callbacks."""

from __future__ import annotations

import numpy as np
import pytest

from dam.boundary.builtin_callbacks import (
    current_limit,
    ee_velocity_limit,
    force_torque_limit,
    get_catalog,
    joint_acceleration_limit,
    joint_position_limits,
    joint_velocity_limit,
    keep_out_zone,
    orientation_limit,
    register_all,
    temperature_limit,
    voltage_limit,
    workspace,
)
from dam.boundary.callbacks._registry import _CALLBACKS
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
    def setup_method(self):
        from dam.boundary.callbacks.kinematics import _prev_vel

        _prev_vel.clear()

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

    def test_explicit_velocity_clamp_also_rebuilds_position(self):
        """When target_joint_velocities is provided and clamped, positions must
        be rebuilt from the clamped velocity to stay consistent."""
        result = joint_velocity_limit(
            obs=_obs(positions=[0.0] * 6),
            action=_action([1.0] * 6, velocities=[10.0] * 6),
            dt=0.1,
            max_velocities=[2.0] * 6,
        )
        assert result.decision == GuardDecision.CLAMP
        clamped_pos = result.clamped_action.target_joint_positions
        clamped_vel = result.clamped_action.target_joint_velocities
        np.testing.assert_allclose(clamped_vel, [2.0] * 6, atol=1e-9)
        np.testing.assert_allclose(clamped_pos, [0.2] * 6, atol=1e-9)

    def test_velocity_limit_no_acceleration_param(self):
        """Velocity limit no longer accepts max_acceleration."""
        result = joint_velocity_limit(
            obs=_obs(positions=[0.0] * 6),
            action=_action([0.15] * 6),
            dt=0.1,
        )
        assert result.decision == GuardDecision.PASS


class TestJointAccelerationLimit:
    """Tests for the standalone joint_acceleration_limit callback."""

    def setup_method(self):
        from dam.boundary.callbacks.kinematics import _prev_vel

        _prev_vel.clear()

    def test_first_cycle_always_passes(self):
        """First cycle has no previous velocity → always PASS."""
        result = joint_acceleration_limit(
            obs=_obs(positions=[0.0] * 6),
            action=_action([1.0] * 6),
            dt=0.02,
            max_acceleration=[5.0] * 6,
        )
        assert result.decision == GuardDecision.PASS

    def test_acceleration_clamped_on_sudden_jump(self):
        """From stationary to max velocity in one cycle should be accel-clamped."""
        # Cycle 1: establish zero velocity baseline
        joint_acceleration_limit(
            obs=_obs(positions=[0.0] * 6),
            action=_action([0.0] * 6),
            dt=0.02,
            max_acceleration=[5.0] * 6,
        )
        # Cycle 2: jump to v=50 rad/s (pos delta=1.0 in 0.02s)
        result = joint_acceleration_limit(
            obs=_obs(positions=[0.0] * 6),
            action=_action([1.0] * 6),
            dt=0.02,
            max_acceleration=[5.0] * 6,
        )
        assert result.decision == GuardDecision.CLAMP
        # Clamped velocity = 0 + 5.0 * 0.02 = 0.1 rad/s → pos = 0 + 0.1 * 0.02 = 0.002
        np.testing.assert_allclose(
            result.clamped_action.target_joint_positions, [0.002] * 6, atol=1e-6
        )

    def test_acceleration_uses_prev_validated_velocity_when_available(self):
        """Runtime path uses the last safe command velocity, not callback-local history."""
        result = joint_acceleration_limit(
            obs=_obs(positions=[0.0] * 6),
            action=_action([1.0] * 6),
            dt=0.02,
            max_acceleration=[5.0] * 6,
            prev_validated_velocities=[0.0] * 6,
        )
        assert result.decision == GuardDecision.CLAMP
        assert result.metadata["acceleration_basis"] == "prev_validated_velocities"
        np.testing.assert_allclose(
            result.clamped_action.target_joint_positions, [0.002] * 6, atol=1e-6
        )

    def test_tracking_lag_does_not_create_phantom_momentum(self):
        """A lagging follower must not inflate velocity (teleop inertia bug).

        Operator stationary at 1.0, previous command 1.0 with zero command
        velocity, follower physically lagging at 0.8.  In command domain the
        proposed velocity is 0 — the shrinking tracking gap must not read as
        motion, so no clamp and no command thrown past the operator.
        """
        result = joint_acceleration_limit(
            obs=_obs(positions=[0.8] * 6),
            action=_action([1.0] * 6),
            dt=1.0 / 30.0,
            max_acceleration=[15.0] * 6,
            prev_validated_positions=[1.0] * 6,
            prev_validated_velocities=[0.0] * 6,
        )
        assert result.decision == GuardDecision.PASS

    def test_clamp_rebuilds_from_previous_command_not_measured_position(self):
        """Deceleration clamp anchors at the command trajectory.

        Previous command 2.0 moving at 5 rad/s; operator stops (target stays
        2.0) while the follower lags at 1.5.  Clamped velocity = 5 - 10*0.1
        = 4 rad/s; the rebuilt position must extend the *command* (2.0 + 0.4
        = 2.4), not the measured position (1.5 + 0.4 = 1.9), so observation
        lag never bends the smoothed trajectory.
        """
        result = joint_acceleration_limit(
            obs=_obs(positions=[1.5] * 6),
            action=_action([2.0] * 6),
            dt=0.1,
            max_acceleration=[10.0] * 6,
            prev_validated_positions=[2.0] * 6,
            prev_validated_velocities=[5.0] * 6,
        )
        assert result.decision == GuardDecision.CLAMP
        np.testing.assert_allclose(
            result.clamped_action.target_joint_positions, [2.4] * 6, atol=1e-6
        )

    def test_prev_validated_velocity_avoids_position_following_false_spike(self):
        """If the robot reached last target, use command velocity, not target-current."""
        result = joint_acceleration_limit(
            obs=_obs(positions=[1.0] * 6),
            action=_action([2.0] * 6),
            dt=0.1,
            max_acceleration=[1.0] * 6,
            prev_validated_positions=[1.0] * 6,
            prev_validated_velocities=[10.0] * 6,
        )
        assert result.decision == GuardDecision.PASS
        assert result.metadata["acceleration_basis"] == "prev_validated_velocities"

    def test_smooth_acceleration_passes(self):
        """Gradual velocity increase within accel limit should pass."""
        # Cycle 1: v=1.0
        joint_acceleration_limit(
            obs=_obs(positions=[0.0] * 6),
            action=_action([0.1] * 6),
            dt=0.1,
            max_acceleration=[20.0] * 6,
        )
        # Cycle 2: v=1.1 (accel = 0.1/0.1 = 1.0 rad/s² < 20.0)
        result = joint_acceleration_limit(
            obs=_obs(positions=[0.1] * 6),
            action=_action([0.21] * 6),
            dt=0.1,
            max_acceleration=[20.0] * 6,
        )
        assert result.decision == GuardDecision.PASS

    def test_deceleration_also_clamped(self):
        """Sudden stop (deceleration) should also be clamped."""
        # Cycle 1: moving at v=10 (pos delta=1.0 in dt=0.1)
        joint_acceleration_limit(
            obs=_obs(positions=[0.0] * 6),
            action=_action([1.0] * 6),
            dt=0.1,
            max_acceleration=[5.0] * 6,
        )
        # Cycle 2: sudden stop → v=0 (accel = -10/0.1 = -100 rad/s², limit 5.0)
        result = joint_acceleration_limit(
            obs=_obs(positions=[1.0] * 6),
            action=_action([1.0] * 6),
            dt=0.1,
            max_acceleration=[5.0] * 6,
        )
        assert result.decision == GuardDecision.CLAMP
        # Prev velocity was 10, decel limited to 5.0 → new v = 10 - 5*0.1 = 9.5
        # → new pos = 1.0 + 9.5 * 0.1 = 1.95
        np.testing.assert_allclose(
            result.clamped_action.target_joint_positions, [1.95] * 6, atol=1e-6
        )

    def test_metadata_includes_acceleration(self):
        """PASS result should include max_acceleration in metadata."""
        result = joint_acceleration_limit(
            obs=_obs(positions=[0.0] * 6),
            action=_action([0.01] * 6),
            dt=0.1,
            max_acceleration=[10.0] * 6,
        )
        assert result.decision == GuardDecision.PASS
        assert "max_acceleration" in result.metadata

    def test_staleness_resets_baseline(self, monkeypatch):
        """After a time gap > dt * staleness_factor, treat as first cycle (PASS)."""
        import time as _time

        from dam.boundary.callbacks import kinematics as kin_mod

        fake_time = [0.0]
        monkeypatch.setattr(_time, "monotonic", lambda: fake_time[0])

        # Cycle 1: establish baseline at t=0
        joint_acceleration_limit(
            obs=_obs(positions=[0.0] * 6),
            action=_action([0.0] * 6),
            dt=0.02,
            max_acceleration=[5.0] * 6,
        )
        # Cycle 2: huge jump but within staleness window → should clamp
        fake_time[0] = 0.02
        result = joint_acceleration_limit(
            obs=_obs(positions=[0.0] * 6),
            action=_action([1.0] * 6),
            dt=0.02,
            max_acceleration=[5.0] * 6,
        )
        assert result.decision == GuardDecision.CLAMP

        # Cycle 3: same jump but after staleness gap → should PASS (baseline reset)
        fake_time[0] = 10.0  # way beyond 0.02 * 5.0 = 0.1s
        result = joint_acceleration_limit(
            obs=_obs(positions=[0.0] * 6),
            action=_action([1.0] * 6),
            dt=0.02,
            max_acceleration=[5.0] * 6,
        )
        assert result.decision == GuardDecision.PASS

    def test_clamp_includes_qp_term(self):
        """CLAMP result should include QPTerm metadata for QP aggregator."""
        from dam.guard.aggregators.motion_qp import QPTerm

        # Cycle 1: establish baseline
        joint_acceleration_limit(
            obs=_obs(positions=[0.0] * 6),
            action=_action([0.0] * 6),
            dt=0.02,
            max_acceleration=[5.0] * 6,
        )
        # Cycle 2: big jump → clamp
        result = joint_acceleration_limit(
            obs=_obs(positions=[0.0] * 6),
            action=_action([1.0] * 6),
            dt=0.02,
            max_acceleration=[5.0] * 6,
        )
        assert result.decision == GuardDecision.CLAMP
        assert "motion_qp" in result.metadata
        qp = result.metadata["motion_qp"]
        assert isinstance(qp, QPTerm)
        assert qp.upper.shape == (6,)
        assert qp.lower.shape == (6,)
        # Bounds should be finite and lower <= upper
        assert np.all(np.isfinite(qp.upper))
        assert np.all(np.isfinite(qp.lower))
        assert np.all(qp.lower <= qp.upper)

    def test_boundary_name_scopes_state(self):
        """Different boundary_name values get independent velocity histories."""
        # Instance A: establish baseline at v=0
        joint_acceleration_limit(
            obs=_obs(positions=[0.0] * 6),
            action=_action([0.0] * 6),
            dt=0.02,
            max_acceleration=[5.0] * 6,
            boundary_name="arm_left",
        )
        # Instance B: also baseline at v=0
        joint_acceleration_limit(
            obs=_obs(positions=[0.0] * 6),
            action=_action([0.0] * 6),
            dt=0.02,
            max_acceleration=[5.0] * 6,
            boundary_name="arm_right",
        )
        # Instance A: big jump → should clamp (has baseline from A's first cycle)
        result_a = joint_acceleration_limit(
            obs=_obs(positions=[0.0] * 6),
            action=_action([1.0] * 6),
            dt=0.02,
            max_acceleration=[5.0] * 6,
            boundary_name="arm_left",
        )
        assert result_a.decision == GuardDecision.CLAMP

        # Instance B: same jump → should also clamp (has its own baseline)
        result_b = joint_acceleration_limit(
            obs=_obs(positions=[0.0] * 6),
            action=_action([1.0] * 6),
            dt=0.02,
            max_acceleration=[5.0] * 6,
            boundary_name="arm_right",
        )
        assert result_b.decision == GuardDecision.CLAMP


# ── EE velocity limit ──────────────────────────��───────────────────────────────


class TestEEVelocityLimit:
    """Tests for the ee_velocity_limit callback."""

    def test_no_jacobian_passes(self):
        """Without Jacobian, EE velocity cannot be checked → PASS."""
        result = ee_velocity_limit(
            obs=_obs(positions=[0.0] * 6),
            action=_action([0.1] * 6),
            dt=0.1,
            max_ee_velocity=0.5,
        )
        assert result.decision == GuardDecision.PASS
        assert "no Jacobian" in result.reason

    def test_slow_ee_passes(self):
        """EE moving within speed limit → PASS."""
        # Jacobian: identity-like (1:1 joint-to-EE mapping for first 3 joints)
        J = np.zeros((3, 6), dtype=np.float64)
        J[0, 0] = 0.1  # small arm length
        J[1, 1] = 0.1
        J[2, 2] = 0.1
        # v_joint = [0.5, 0.5, 0.5, 0, 0, 0] → v_ee = [0.05, 0.05, 0.05]
        # ||v_ee|| ≈ 0.087 m/s < 0.5 m/s
        result = ee_velocity_limit(
            obs=_obs(positions=[0.0] * 6),
            action=_action([0.05] * 3 + [0.0] * 3),
            dt=0.1,
            max_ee_velocity=0.5,
            J_linear=J,
        )
        assert result.decision == GuardDecision.PASS
        assert result.metadata["ee_speed"] < 0.5

    def test_fast_ee_clamped(self):
        """EE exceeding speed limit → CLAMP with uniform scaling."""
        J = np.zeros((3, 6), dtype=np.float64)
        J[0, 0] = 1.0
        J[1, 1] = 1.0
        J[2, 2] = 1.0
        # v_joint = [5.0, 5.0, 5.0, 0, 0, 0] → v_ee = [5.0, 5.0, 5.0]
        # ||v_ee|| ≈ 8.66 m/s >> 0.5 m/s
        result = ee_velocity_limit(
            obs=_obs(positions=[0.0] * 6),
            action=_action([0.5] * 3 + [0.0] * 3),
            dt=0.1,
            max_ee_velocity=0.5,
            J_linear=J,
        )
        assert result.decision == GuardDecision.CLAMP
        assert "scaled" in result.reason
        # Clamped EE speed should be ≈ 0.5 m/s
        clamped_v = (
            np.array(result.clamped_action.target_joint_positions) - np.array([0.0] * 6)
        ) / 0.1
        v_ee_after = J @ clamped_v
        assert float(np.linalg.norm(v_ee_after)) == pytest.approx(0.5, abs=0.01)

    def test_metadata_has_ee_speed(self):
        """Metadata includes EE velocity info."""
        J = np.eye(3, 6, dtype=np.float64) * 0.1
        result = ee_velocity_limit(
            obs=_obs(positions=[0.0] * 6),
            action=_action([0.01] * 6),
            dt=0.1,
            max_ee_velocity=1.0,
            J_linear=J,
        )
        assert "ee_speed" in result.metadata
        assert "max_ee_velocity" in result.metadata

    def test_non_finite_rejects(self):
        """Non-finite joint state → REJECT."""
        result = ee_velocity_limit(
            obs=_obs(positions=[float("nan")] * 6),
            action=_action([0.0] * 6),
            dt=0.1,
            max_ee_velocity=0.5,
        )
        assert result.decision == GuardDecision.REJECT

    def test_clamp_includes_qp_term(self):
        """CLAMP result should include QPTerm with A-matrix constraint."""
        from dam.guard.aggregators.motion_qp import QPTerm

        J = np.eye(3, 6, dtype=np.float64)
        result = ee_velocity_limit(
            obs=_obs(positions=[0.0] * 6),
            action=_action([0.5] * 3 + [0.0] * 3),
            dt=0.1,
            max_ee_velocity=0.5,
            J_linear=J,
        )
        assert result.decision == GuardDecision.CLAMP
        assert "motion_qp" in result.metadata
        qp = result.metadata["motion_qp"]
        assert isinstance(qp, QPTerm)
        assert qp.A is not None
        assert qp.b is not None
        assert qp.A.shape == (1, 6)
        assert qp.b.shape == (1,)
        assert np.all(np.isfinite(qp.A))
        assert np.all(np.isfinite(qp.b))

    def test_more_motors_than_jacobian_columns(self):
        """Robot reports 6 motors but URDF Jacobian has 5 columns (SO-ARM:
        5 arm joints + gripper). The QPTerm linearization must not crash
        on the width mismatch; the unmodelled joint gets a zero coefficient."""
        J = np.eye(3, 5, dtype=np.float64)
        result = ee_velocity_limit(
            obs=_obs(positions=[0.0] * 6),
            action=_action([0.5] * 3 + [0.0] * 3),
            dt=0.1,
            max_ee_velocity=0.5,
            J_linear=J,
        )
        assert result.decision == GuardDecision.CLAMP
        qp = result.metadata["motion_qp"]
        assert qp.A.shape == (1, 6)
        assert qp.A[0, 5] == 0.0
        assert np.all(np.isfinite(qp.A))
        assert np.all(np.isfinite(qp.b))

    def test_jacobian_joint_indices_gather(self):
        """With a layout-derived column map, joint state is gathered by
        index instead of positionally — here the modelled joints are the
        *last* five entries (gripper first), which positional truncation
        would get wrong."""
        J = np.zeros((3, 5), dtype=np.float64)
        J[0, 0] = 1.0  # EE x velocity = joint obs[1] velocity
        idx = np.array([1, 2, 3, 4, 5])
        # Only the gripper (obs[0]) moves fast: EE doesn't move at all.
        result = ee_velocity_limit(
            obs=_obs(positions=[0.0] * 6),
            action=_action([5.0] + [0.0] * 5),
            dt=0.1,
            max_ee_velocity=0.5,
            J_linear=J,
            jacobian_joint_indices=idx,
        )
        assert result.decision == GuardDecision.PASS
        # Now the mapped joint obs[1] moves fast → CLAMP.
        result = ee_velocity_limit(
            obs=_obs(positions=[0.0] * 6),
            action=_action([0.0, 0.5] + [0.0] * 4),
            dt=0.1,
            max_ee_velocity=0.5,
            J_linear=J,
            jacobian_joint_indices=idx,
        )
        assert result.decision == GuardDecision.CLAMP
        qp = result.metadata["motion_qp"]
        assert qp.A.shape == (1, 6)
        assert qp.A[0, 0] == 0.0  # gripper column untouched
        assert qp.A[0, 1] != 0.0


# ── dynamic joint count ────────────────────────────────────────────────────────


class TestDynamicJointCount:
    """L1 callbacks adapt to however many joints the action/obs contain."""

    def setup_method(self):
        from dam.boundary.callbacks.kinematics import _prev_vel

        _prev_vel.clear()

    def test_position_limits_4dof_defaults(self):
        result = joint_position_limits(action=_action([0.0] * 4))
        assert result.decision == GuardDecision.PASS

    def test_position_limits_4dof_clamps(self):
        result = joint_position_limits(action=_action([5.0, 0.0, 0.0, -5.0]))
        assert result.decision == GuardDecision.CLAMP
        clamped = result.clamped_action.target_joint_positions
        assert len(clamped) == 4
        assert clamped[0] == pytest.approx(np.pi)
        assert clamped[3] == pytest.approx(-np.pi)

    def test_position_limits_7dof(self):
        result = joint_position_limits(action=_action([0.5] * 7))
        assert result.decision == GuardDecision.PASS

    def test_velocity_limit_4dof_defaults(self):
        result = joint_velocity_limit(
            obs=_obs(positions=[0.0] * 4),
            action=_action([0.1] * 4),
            dt=0.1,
        )
        assert result.decision == GuardDecision.PASS

    def test_velocity_limit_4dof_clamps(self):
        result = joint_velocity_limit(
            obs=_obs(positions=[0.0] * 4),
            action=_action([10.0] * 4),
            dt=0.1,
        )
        assert result.decision == GuardDecision.CLAMP
        assert len(result.clamped_action.target_joint_positions) == 4

    def test_velocity_limit_7dof(self):
        result = joint_velocity_limit(
            obs=_obs(positions=[0.0] * 7),
            action=_action([0.01] * 7),
            dt=0.1,
        )
        assert result.decision == GuardDecision.PASS


# ── workspace ────────────────────────────────────────────────────────────────


class TestWorkspace:
    BOUNDS = [[-0.4, 0.4], [-0.4, 0.4], [0.02, 0.6]]

    def test_inside_bounds_pass(self):
        obs = _obs(ee_pose=[0.1, 0.1, 0.3, 0, 0, 0, 1])
        action = _action([0.0] * 6)
        r = workspace(obs=obs, action=action, bounds=self.BOUNDS)
        assert r.decision == GuardDecision.PASS
        assert "ee_pos" in r.metadata

    def test_outside_bounds_clamp(self):
        obs = _obs(ee_pose=[0.5, 0.1, 0.3, 0, 0, 0, 1])
        action = _action([0.1] * 6)
        r = workspace(obs=obs, action=action, bounds=self.BOUNDS)
        assert r.decision == GuardDecision.CLAMP
        assert r.clamped_action is not None
        np.testing.assert_array_equal(r.clamped_action.target_joint_positions, obs.joint_positions)

    def test_no_ee_pose_pass(self):
        obs = _obs()
        action = _action([0.0] * 6)
        r = workspace(obs=obs, action=action, bounds=self.BOUNDS)
        assert r.decision == GuardDecision.PASS
        assert "bounds" in r.metadata

    def test_default_bounds_used(self):
        obs = _obs(ee_pose=[0.1, 0.1, 0.3, 0, 0, 0, 1])
        action = _action([0.0] * 6)
        r = workspace(obs=obs, action=action)
        assert r.decision == GuardDecision.PASS

    def test_clamp_holds_current_position(self):
        positions = [0.2, -0.1, 0.5, 0.3, -0.2, 0.1]
        obs = _obs(positions=positions, ee_pose=[0.0, 0.0, 0.8, 0, 0, 0, 1])
        action = _action([0.3] * 6)
        r = workspace(obs=obs, action=action, bounds=self.BOUNDS)
        assert r.decision == GuardDecision.CLAMP
        np.testing.assert_array_almost_equal(r.clamped_action.target_joint_positions, positions)

    def test_cbf_inside_safe_action_pass(self):
        """With Jacobian, an action that stays inside should PASS."""
        obs = _obs(positions=[0.0] * 6, ee_pose=[0.1, 0.1, 0.3, 0, 0, 0, 1])
        action = _action([0.0] * 6)
        J = np.zeros((3, 6))
        J[0, 0] = 0.1  # small Jacobian
        r = workspace(obs=obs, action=action, bounds=self.BOUNDS, J_linear=J)
        assert r.decision == GuardDecision.PASS
        assert "cbf_margin_min" in r.metadata

    def test_cbf_outside_clamp_with_qp_term(self):
        """With Jacobian, outside EE should produce CLAMP with motion_qp."""
        obs = _obs(positions=[0.0] * 6, ee_pose=[0.5, 0.1, 0.3, 0, 0, 0, 1])
        action = _action([0.1] * 6)
        J = np.eye(3, 6) * 0.1
        r = workspace(obs=obs, action=action, bounds=self.BOUNDS, J_linear=J)
        assert r.decision == GuardDecision.CLAMP
        assert "motion_qp" in r.metadata
        qp = r.metadata["motion_qp"]
        assert qp.A is not None
        assert qp.b is not None
        assert qp.A.shape == (6, 6)

    def test_cbf_action_leaving_workspace_clamp(self):
        """Inside EE but action would push EE out → CLAMP with QPTerm."""
        obs = _obs(positions=[0.0] * 6, ee_pose=[0.39, 0.0, 0.3, 0, 0, 0, 1])
        # Large action in the direction J maps to +x EE
        J = np.zeros((3, 6))
        J[0, 0] = 1.0  # joint 0 → x
        action = _action([0.5, 0.0, 0.0, 0.0, 0.0, 0.0])
        r = workspace(obs=obs, action=action, bounds=self.BOUNDS, J_linear=J)
        assert r.decision == GuardDecision.CLAMP
        assert "motion_qp" in r.metadata
        assert r.metadata["cbf_margin_min"] < 0

    def test_cbf_no_jacobian_outside_halts(self):
        """Without Jacobian, outside EE should still halt (fallback)."""
        obs = _obs(positions=[0.1, 0.2, 0.3, 0.0, 0.0, 0.0], ee_pose=[0.5, 0.1, 0.3, 0, 0, 0, 1])
        action = _action([0.1] * 6)
        r = workspace(obs=obs, action=action, bounds=self.BOUNDS)
        assert r.decision == GuardDecision.CLAMP
        np.testing.assert_array_almost_equal(
            r.clamped_action.target_joint_positions, obs.joint_positions
        )

    def test_cbf_fewer_action_joints_than_jacobian(self):
        """Action with fewer joints than Jacobian DOF should not crash."""
        # 4-joint action but 6-DOF Jacobian — CBF margin must pad, not crash
        obs = _obs(positions=[0.0] * 4, ee_pose=[0.5, 0.1, 0.3, 0, 0, 0, 1])
        action = _action([0.1] * 4)
        J = np.eye(3, 6) * 0.1  # 6-DOF Jacobian
        r = workspace(obs=obs, action=action, bounds=self.BOUNDS, J_linear=J)
        assert r.decision == GuardDecision.CLAMP  # outside bounds → clamp

    def test_cbf_more_action_joints_than_jacobian(self):
        """Action with more joints than Jacobian DOF should work (extras ignored)."""
        obs = _obs(positions=[0.0] * 8, ee_pose=[0.1, 0.1, 0.3, 0, 0, 0, 1])
        action = _action([0.0] * 8)
        J = np.eye(3, 6) * 0.1  # 6-DOF Jacobian
        r = workspace(obs=obs, action=action, bounds=self.BOUNDS, J_linear=J)
        assert r.decision == GuardDecision.PASS


# ── keep_out_zone ─────────────────────────────────────────────────────────────


class TestKeepOutZone:
    SPHERES = [[0.2, 0.0, 0.3, 0.05]]  # center=(0.2,0,0.3), r=0.05

    def test_outside_sphere_pass(self):
        obs = _obs(positions=[0.0] * 6, ee_pose=[0.0, 0.0, 0.3, 0, 0, 0, 1])
        action = _action([0.0] * 6)
        J = np.eye(3, 6) * 0.1
        r = keep_out_zone(obs=obs, action=action, spheres=self.SPHERES, J_linear=J)
        assert r.decision == GuardDecision.PASS

    def test_inside_sphere_clamp_with_qp(self):
        obs = _obs(positions=[0.0] * 6, ee_pose=[0.2, 0.0, 0.3, 0, 0, 0, 1])
        action = _action([0.0] * 6)
        J = np.eye(3, 6) * 0.1
        r = keep_out_zone(obs=obs, action=action, spheres=self.SPHERES, J_linear=J)
        assert r.decision == GuardDecision.CLAMP
        assert "motion_qp" in r.metadata
        assert r.metadata["motion_qp"].A is not None

    def test_action_entering_sphere_clamp(self):
        """EE outside but action would push it in → CLAMP."""
        obs = _obs(positions=[0.0] * 6, ee_pose=[0.15, 0.0, 0.3, 0, 0, 0, 1])
        J = np.zeros((3, 6))
        J[0, 0] = 1.0  # joint 0 → x
        # Large positive action pushes EE toward sphere at x=0.2
        action = _action([0.3, 0.0, 0.0, 0.0, 0.0, 0.0])
        r = keep_out_zone(obs=obs, action=action, spheres=self.SPHERES, J_linear=J)
        assert r.decision == GuardDecision.CLAMP
        assert r.metadata["cbf_margin_min"] < 0

    def test_no_spheres_pass(self):
        obs = _obs(positions=[0.0] * 6, ee_pose=[0.2, 0.0, 0.3, 0, 0, 0, 1])
        action = _action([0.0] * 6)
        r = keep_out_zone(obs=obs, action=action, spheres=[])
        assert r.decision == GuardDecision.PASS

    def test_no_jacobian_inside_halts(self):
        obs = _obs(positions=[0.1, 0.2, 0.3, 0.0, 0.0, 0.0], ee_pose=[0.2, 0.0, 0.3, 0, 0, 0, 1])
        action = _action([0.0] * 6)
        r = keep_out_zone(obs=obs, action=action, spheres=self.SPHERES)
        assert r.decision == GuardDecision.CLAMP
        np.testing.assert_array_almost_equal(
            r.clamped_action.target_joint_positions, obs.joint_positions
        )

    def test_ee_at_sphere_center_still_clamps(self):
        """EE exactly at sphere center should still produce a non-degenerate CLAMP."""
        obs = _obs(positions=[0.0] * 6, ee_pose=[0.2, 0.0, 0.3, 0, 0, 0, 1])
        action = _action([0.0] * 6)
        J = np.eye(3, 6) * 0.1
        r = keep_out_zone(obs=obs, action=action, spheres=self.SPHERES, J_linear=J)
        assert r.decision == GuardDecision.CLAMP
        assert "motion_qp" in r.metadata
        # The QPTerm A matrix must have at least one non-zero row
        # (regression: zero-normal bug produced all-zero A when EE == center)
        assert np.any(np.abs(r.metadata["motion_qp"].A) > 1e-12)

    def test_multiple_spheres(self):
        spheres = [[0.2, 0.0, 0.3, 0.05], [-0.2, 0.0, 0.3, 0.05]]
        obs = _obs(positions=[0.0] * 6, ee_pose=[0.0, 0.0, 0.3, 0, 0, 0, 1])
        action = _action([0.0] * 6)
        J = np.eye(3, 6) * 0.1
        r = keep_out_zone(obs=obs, action=action, spheres=spheres, J_linear=J)
        assert r.decision == GuardDecision.PASS
        assert r.metadata["n_spheres"] == 2


# ── orientation_limit ─────────────────────────────────────────────────────────


def _rotation_x(deg: float) -> np.ndarray:
    """Rotation matrix about X axis."""
    r = np.radians(deg)
    return np.array([[1, 0, 0], [0, np.cos(r), -np.sin(r)], [0, np.sin(r), np.cos(r)]])


class TestOrientationLimit:
    def test_upright_pass(self):
        """Tool axis aligned with reference → PASS."""
        R = np.eye(3)
        obs = _obs(positions=[0.0] * 6)
        action = _action([0.0] * 6)
        J_ang = np.eye(3, 6) * 0.1
        r = orientation_limit(obs=obs, action=action, ee_rot=R, J_angular=J_ang, max_tilt_deg=30.0)
        assert r.decision == GuardDecision.PASS
        assert r.metadata["tilt_deg"] < 1.0

    def test_tilted_beyond_limit_clamp(self):
        """Tool axis tilted 45° with 30° limit → CLAMP with QPTerm."""
        R = _rotation_x(45.0)
        obs = _obs(positions=[0.0] * 6)
        action = _action([0.0] * 6)
        J_ang = np.eye(3, 6) * 0.1
        r = orientation_limit(obs=obs, action=action, ee_rot=R, J_angular=J_ang, max_tilt_deg=30.0)
        assert r.decision == GuardDecision.CLAMP
        assert "motion_qp" in r.metadata
        assert r.metadata["tilt_deg"] > 30.0

    def test_no_angular_jacobian_upright_pass(self):
        """Without angular Jacobian but within limit → PASS."""
        R = np.eye(3)
        obs = _obs(positions=[0.0] * 6)
        action = _action([0.0] * 6)
        r = orientation_limit(obs=obs, action=action, ee_rot=R, max_tilt_deg=30.0)
        assert r.decision == GuardDecision.PASS

    def test_no_angular_jacobian_violated_halts(self):
        """Without angular Jacobian and beyond limit → halt fallback."""
        R = _rotation_x(45.0)
        obs = _obs(positions=[0.1, 0.2, 0.3, 0.0, 0.0, 0.0])
        action = _action([0.0] * 6)
        r = orientation_limit(obs=obs, action=action, ee_rot=R, max_tilt_deg=30.0)
        assert r.decision == GuardDecision.CLAMP
        np.testing.assert_array_almost_equal(
            r.clamped_action.target_joint_positions, obs.joint_positions
        )

    def test_custom_axes(self):
        R = np.eye(3)
        obs = _obs(positions=[0.0] * 6)
        action = _action([0.0] * 6)
        J_ang = np.eye(3, 6) * 0.1
        r = orientation_limit(
            obs=obs,
            action=action,
            ee_rot=R,
            J_angular=J_ang,
            max_tilt_deg=30.0,
            reference_axis=[0.0, 0.0, 1.0],
            tool_axis=[0.0, 0.0, 1.0],
        )
        assert r.decision == GuardDecision.PASS


# ── force_torque_limit ───────────────────────────────────────────────────────


class TestForceTorqueLimit:
    def test_both_under_limits_pass(self):
        obs = _obs(force_torque=[1.0, 0, 0, 0.5, 0, 0])
        r = force_torque_limit(obs=obs, max_force_n=50.0, max_torque_nm=10.0)
        assert r.decision.name == "PASS"
        assert "force_n" in r.metadata
        assert "torque_nm" in r.metadata

    def test_force_over_limit_reject(self):
        obs = _obs(force_torque=[100.0, 0, 0, 0, 0, 0])
        r = force_torque_limit(obs=obs, max_force_n=50.0, max_torque_nm=10.0)
        assert r.decision.name == "REJECT"
        assert "force" in r.reason

    def test_torque_over_limit_reject(self):
        obs = _obs(force_torque=[0, 0, 0, 20.0, 0, 0])
        r = force_torque_limit(obs=obs, max_force_n=50.0, max_torque_nm=10.0)
        assert r.decision.name == "REJECT"
        assert "torque" in r.reason

    def test_no_data_pass(self):
        obs = _obs()
        r = force_torque_limit(obs=obs)
        assert r.decision.name == "PASS"

    def test_channel_based_reading(self):
        obs = Observation(
            timestamp=0.0,
            joint_positions=np.array([0.0] * 6),
            channels={"ft_sensor": np.array([100.0, 0, 0, 0, 0, 0])},
        )
        r = force_torque_limit(obs=obs, max_force_n=50.0, channel="ft_sensor")
        assert r.decision.name == "REJECT"
        assert "force" in r.reason

    def test_channel_fallback_to_obs_force_torque(self):
        """When channel is missing, falls back to obs.force_torque."""
        obs = _obs(force_torque=[100.0, 0, 0, 0, 0, 0])
        r = force_torque_limit(obs=obs, max_force_n=50.0, channel="nonexistent")
        assert r.decision.name == "REJECT"

    def test_short_data_no_torque_check(self):
        """When data has < 6 elements, torque is treated as 0."""
        obs = Observation(
            timestamp=0.0,
            joint_positions=np.array([0.0] * 6),
            channels={"force_torque": np.array([1.0, 0, 0])},
        )
        r = force_torque_limit(obs=obs, max_force_n=50.0, max_torque_nm=10.0)
        assert r.decision.name == "PASS"
        assert r.metadata["torque_nm"] == 0.0


# ── NaN/Inf protection (L3 telemetry callbacks) ──────────────────────────────


class TestNonFiniteSensorData:
    """NaN/Inf in sensor data must REJECT, not silently pass or produce empty reasons."""

    def test_temperature_nan_rejects(self):
        obs = _obs(metadata={"channels": None})
        obs = Observation(
            timestamp=0.0,
            joint_positions=np.array([0.0] * 6),
            channels={"temperature": np.array([np.nan, 42.0])},
        )
        r = temperature_limit(obs=obs, max_temperature_c=55.0)
        assert r.decision.name == "REJECT"
        assert "Non-finite" in r.reason

    def test_current_inf_rejects(self):
        obs = Observation(
            timestamp=0.0,
            joint_positions=np.array([0.0] * 6),
            channels={"current": np.array([0.5, np.inf])},
        )
        r = current_limit(obs=obs, max_current_a=1.5)
        assert r.decision.name == "REJECT"
        assert "Non-finite" in r.reason

    def test_voltage_nan_rejects_with_reason(self):
        obs = Observation(
            timestamp=0.0,
            joint_positions=np.array([0.0] * 6),
            channels={"voltage": np.array([np.nan])},
        )
        r = voltage_limit(obs=obs)
        assert r.decision.name == "REJECT"
        assert "Non-finite" in r.reason
        assert r.reason != ""

    def test_force_torque_nan_rejects(self):
        obs = _obs(force_torque=[np.nan, 0, 0, 0, 0, 0])
        r = force_torque_limit(obs=obs, max_force_n=50.0)
        assert r.decision.name == "REJECT"
        assert "Non-finite" in r.reason


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
            assert "force_torque_limit" in reg.list_all()
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
