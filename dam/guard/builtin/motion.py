import logging

import numpy as np

from dam.guard.base import Guard
from dam.types.action import ActionProposal, ValidatedAction
from dam.types.observation import Observation
from dam.types.result import GuardResult

logger = logging.getLogger(__name__)


class MotionGuard(Guard):
    """L2 motion safety guard: joint limits, velocity limits, and workspace bounds."""

    _guard_kind = "motion"

    def __init__(self) -> None:
        self._prev_velocities: np.ndarray | None = None
        self._prev_timestamp: float | None = None
        self._last_dt: float | None = None
        # Cache for unit conversion and array casting
        self._cache_key: tuple | None = None
        self._cached_params: dict[str, np.ndarray | None] = {}

    def check(
        self,
        obs: Observation,
        action: ActionProposal,
        upper: np.ndarray | None = None,
        lower: np.ndarray | None = None,
        max_velocity: np.ndarray | None = None,
        max_velocities: np.ndarray | None = None,  # Alias for consistency
        max_acceleration: np.ndarray | None = None,
        max_accelerations: np.ndarray | None = None,  # Alias for consistency
        bounds: np.ndarray | None = None,
        use_degrees: bool = False,  # New: Support intuitive degree input
    ) -> GuardResult:
        # Resolve aliases
        if max_velocity is None:
            max_velocity = max_velocities
        if max_acceleration is None:
            max_acceleration = max_accelerations

        # 0. Param Processing with Caching (Initialization once)
        current_key = (
            id(upper),
            id(lower),
            id(max_velocity),
            id(max_acceleration),
            id(bounds),
            use_degrees,
        )
        if current_key != self._cache_key:
            if use_degrees:
                upper = np.radians(upper) if upper is not None else None
                lower = np.radians(lower) if lower is not None else None
                max_velocity = np.radians(max_velocity) if max_velocity is not None else None
                max_acceleration = (
                    np.radians(max_acceleration) if max_acceleration is not None else None
                )

            # Ensure they are numpy arrays
            upper = np.asarray(upper) if upper is not None else None
            lower = np.asarray(lower) if lower is not None else None
            max_velocity = np.asarray(max_velocity) if max_velocity is not None else None
            max_acceleration = (
                np.asarray(max_acceleration) if max_acceleration is not None else None
            )
            bounds = np.asarray(bounds) if bounds is not None else None

            self._cached_params = {
                "upper": upper,
                "lower": lower,
                "max_velocity": max_velocity,
                "max_acceleration": max_acceleration,
                "bounds": bounds,
            }
            self._cache_key = current_key
        else:
            upper = self._cached_params["upper"]
            lower = self._cached_params["lower"]
            max_velocity = self._cached_params["max_velocity"]
            max_acceleration = self._cached_params["max_acceleration"]
            bounds = self._cached_params["bounds"]

        layer = self.get_layer()
        name = self.get_name()
        max_ratio = 1.0  # Initialize to avoid UnboundLocalError during logging

        # 0. Timing and Velocity estimation
        # We need a stable dt. If this guard is called multiple times per cycle,
        # (obs.timestamp - self._prev_timestamp) might be 0.
        raw_dt = (
            (obs.timestamp - self._prev_timestamp)
            if self._prev_timestamp is not None
            else (1.0 / 20.0)
        )  # default 20Hz

        # If dt is too small, it means we are in the same cycle. Don't update state yet.
        # We use a threshold of 1ms.
        is_same_cycle = self._prev_timestamp is not None and abs(raw_dt) < 0.001

        # Use a realistic dt for velocity estimation (at least 5ms)
        effective_dt = max(raw_dt, 0.005) if not is_same_cycle else (self._last_dt or 0.02)

        positions = action.target_joint_positions.copy()

        # If the action doesn't provide velocities, derive them from position change
        if action.target_joint_velocities is not None:
            velocities = action.target_joint_velocities.copy()
            provided_velocities = True
        else:
            # Implied velocity: (target - current) / dt
            velocities = (positions - obs.joint_positions) / effective_dt
            provided_velocities = False

        # 1. Workspace bounds check — REJECT immediately (most severe)
        if bounds is not None and obs.end_effector_pose is not None:
            ee_pos = obs.end_effector_pose[:3]
            if not np.all((ee_pos >= bounds[:, 0]) & (ee_pos <= bounds[:, 1])):
                if not is_same_cycle:
                    self._prev_velocities = obs.joint_velocities.copy()
                    self._prev_timestamp = obs.timestamp
                    self._last_dt = effective_dt
                return GuardResult.reject(
                    reason=f"end-effector {ee_pos} outside workspace bounds {bounds}",
                    guard_name=name,
                    layer=layer,
                )

        # 2. Joint position clamp (Absolute limits)
        clamped_positions = positions.copy()
        was_clamped = False
        if upper is not None and lower is not None:
            clamped_positions = np.clip(positions, lower, upper)
            if not np.allclose(clamped_positions, positions):
                was_clamped = True
                # If we clamped positions, update velocities to match the new delta
                if not provided_velocities:
                    velocities = (clamped_positions - obs.joint_positions) / effective_dt

        # 3. Velocity clamp (Dynamic limits)
        clamped_velocities = velocities.copy()
        if max_velocity is not None:
            ratio = np.abs(velocities) / (np.abs(max_velocity) + 1e-12)
            max_ratio = float(np.max(ratio))
            if max_ratio > 1.0:
                clamped_velocities = velocities / max_ratio
                was_clamped = True

        # 4. Acceleration check (estimate from previous cycle's measured velocity)
        if max_acceleration is not None and self._prev_velocities is not None:
            accel = (clamped_velocities - self._prev_velocities) / effective_dt
            accel_ratio = np.abs(accel) / (np.abs(max_acceleration) + 1e-12)
            max_accel_ratio = float(np.max(accel_ratio))
            if max_accel_ratio > 1.0:
                # Limit acceleration by adjusting velocities
                allowable_delta = max_acceleration * effective_dt
                clamped_velocities = self._prev_velocities + np.clip(
                    clamped_velocities - self._prev_velocities,
                    -allowable_delta,
                    allowable_delta,
                )
                was_clamped = True

        # 5. Reconstruct positions if they were derived from limited velocities
        if was_clamped and not provided_velocities:
            clamped_positions = obs.joint_positions + clamped_velocities * effective_dt
            if upper is not None and lower is not None:
                clamped_positions = np.clip(clamped_positions, lower, upper)

        # Update state ONLY if we haven't seen this timestamp yet
        if not is_same_cycle:
            self._prev_velocities = obs.joint_velocities.copy()
            self._prev_timestamp = obs.timestamp
            self._last_dt = effective_dt

        if was_clamped:
            # Construct a detailed reason string for the UI/Logs
            reason_parts = []
            if max_ratio > 1.0:
                # Find the worst offender joint
                diffs = np.abs(velocities)
                worst_joint = int(np.argmax(diffs))
                orig_v = diffs[worst_joint]

                # Handle both array and scalar max_velocity for logging
                if max_velocity.ndim > 0:
                    lim_v = max_velocity[worst_joint % len(max_velocity)]
                else:
                    lim_v = float(max_velocity)

                reason_parts.append(
                    f"velocity clamp (J{worst_joint + 1}: {orig_v:.3f}->{lim_v:.3f})"
                )

            if not np.allclose(clamped_positions, positions):
                # Check for position out of bounds
                diff_mask = ~np.isclose(clamped_positions, positions)
                indices = np.nonzero(diff_mask)[0]
                if len(indices) > 0:
                    idx = int(indices[0])
                    reason_parts.append(
                        f"position clamp (J{idx + 1}: "
                        f"{positions[idx]:.3f}->{clamped_positions[idx]:.3f})"
                    )

            final_reason = "; ".join(reason_parts) if reason_parts else "motion limits applied"

            if not is_same_cycle:
                # Log details about clamping (only once per cycle)
                if max_ratio > 1.0:
                    logger.debug(
                        "MotionGuard: Velocity CLAMP (max_ratio=%.2f): %s", max_ratio, final_reason
                    )
                if not np.allclose(clamped_positions, positions):
                    logger.debug("MotionGuard: Joint position CLAMP: %s", final_reason)

            clamped_action = ValidatedAction(
                target_joint_positions=clamped_positions,
                target_joint_velocities=clamped_velocities
                if provided_velocities or action.target_joint_velocities is not None
                else None,
                was_clamped=True,
                original_proposal=action,
            )
            return GuardResult.clamp(
                clamped_action=clamped_action,
                guard_name=name,
                layer=layer,
                reason=final_reason,
            )

        return GuardResult.success(guard_name=name, layer=layer)
