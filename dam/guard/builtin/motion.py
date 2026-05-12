import logging
from typing import Any

import numpy as np

from dam.guard.base import Guard
from dam.types.action import ActionProposal, ValidatedAction
from dam.types.observation import Observation
from dam.types.result import GuardResult

logger = logging.getLogger(__name__)


class MotionGuard(Guard):
    """L1 motion safety guard: joint limits, velocity limits, and workspace bounds.

    Multiple L1 boundaries (joint_position_limits, joint_velocity_limit,
    bounds) can map to the same MotionGuard instance.  The stage builder
    collapses them into a single invocation per cycle — the merged config
    pool already carries all constraints, so one ``check()`` call covers
    everything.  Results are fanned out to each boundary name for the UI.

    When ``qp_solver`` is present in the config pool (opt-in per boundary),
    all constraints are fused into a single ProxSuite QP solve instead of
    independent box-clamps.
    """

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
        # QP path — when a boundary opts in via `qp_solver: "proxsuite"`
        # (data-driven, no special guard class), MotionGuard dispatches to
        # the optional QP filter instead of independent box-clamps.  Params
        # are simply more entries in the same config_pool that already
        # carries `upper` / `lower` / `max_velocity` — every L1 boundary's
        # constraints fuse into one solve.  ``dt`` + ``dynamics`` are
        # auto-injected by GuardRuntime; ``cbf_alpha`` opts the workspace
        # `bounds` constraint into a discrete-CBF formulation when those
        # are available.
        qp_solver: str | None = None,
        slack_weight: float = 1e6,
        cbf_alpha: float = 1.0,
        dt: float = 0.02,
        dynamics: Any = None,
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

        # ── QP solver dispatch ────────────────────────────────────────────────
        # When any L1 boundary contributes `qp_solver: "proxsuite"` to the
        # pool, fuse ALL constraints (position, velocity, workspace) into one
        # QP solve.  Falls back to box-clamp path if proxsuite is unavailable.
        if qp_solver == "proxsuite":
            qp_result = self._qp_clamp(
                obs=obs,
                action=action,
                upper=upper,
                lower=lower,
                max_velocity=max_velocity,
                bounds=bounds,
                slack_weight=slack_weight,
                cbf_alpha=cbf_alpha,
                dt=dt,
                dynamics=dynamics,
            )
            if qp_result is not None:
                return qp_result
            # qp_solver requested but proxsuite unavailable or solve failed —
            # fall through to the box-clamp path so the runtime stays safe.

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

    # ── QP-based clamp (optional, depends on proxsuite) ───────────────────────

    def _qp_clamp(
        self,
        *,
        obs: Observation,
        action: ActionProposal,
        upper: np.ndarray | None,
        lower: np.ndarray | None,
        max_velocity: np.ndarray | None,
        bounds: np.ndarray | None,
        slack_weight: float,
        cbf_alpha: float,
        dt: float,
        dynamics: Any,
    ) -> GuardResult | None:
        """Run ProxSuite QP fusing position + velocity + workspace constraints.

        All active L1 constraints are solved jointly.  Returns
        ``GuardResult.clamp(...)`` if the QP altered the action,
        ``GuardResult.success(...)`` if the nominal action is feasible,
        or ``None`` to signal the caller to fall back to the box-clamp
        path (proxsuite missing, solve failed, etc.).
        """
        from dam.runtime.qp_solver import available as qp_available
        from dam.runtime.qp_solver import solve_box_with_slack, workspace_cbf_constraints

        if not qp_available():
            return None

        u_nom = action.target_joint_positions
        q = np.asarray(obs.joint_positions, dtype=np.float64)

        # ── Velocity bounds as position constraints ──────────────────────────
        # |v| ≤ max_vel  ⟺  q − max_vel·dt ≤ u ≤ q + max_vel·dt
        vel_upper: np.ndarray | None = None
        vel_lower: np.ndarray | None = None
        if max_velocity is not None:
            mv = np.asarray(max_velocity, dtype=np.float64)
            n_u = u_nom.shape[0]
            # Broadcast scalar or per-joint
            if mv.ndim == 0:
                mv = np.full(n_u, float(mv))
            mv = mv[:n_u]
            q_n = q[:n_u]
            vel_upper = q_n + mv * dt
            vel_lower = q_n - mv * dt

        # ── Optional workspace CBF (consumes DynamicsContext when present) ──
        extra_A: np.ndarray | None = None
        extra_ub: np.ndarray | None = None
        cbf_active = False
        if (
            bounds is not None
            and dynamics is not None
            and getattr(dynamics, "available", False)
            and getattr(dynamics, "default_frame_id", None) is not None
        ):
            try:
                fid = dynamics.default_frame_id
                # Refresh once per cycle — DynamicsContext dedups same-q calls
                dynamics.update(obs.joint_positions)
                ee_pose = dynamics.frame_placement(fid)
                ee_pos = np.asarray(ee_pose.translation, dtype=np.float64)
                J = dynamics.frame_jacobian(fid)[:3]
                # Slice Jacobian + q to the joints this guard controls
                n_u = u_nom.shape[0]
                J = J[:, :n_u]
                q_slice = np.asarray(obs.joint_positions[:n_u], dtype=np.float64)
                extra_A, extra_ub = workspace_cbf_constraints(
                    q=q_slice,
                    ee_pos=ee_pos,
                    J_linear=J,
                    bounds=bounds,
                    cbf_alpha=float(cbf_alpha),
                    dt=float(dt),
                )
                cbf_active = True
            except Exception:  # noqa: BLE001
                logger.debug("workspace CBF skipped (Jacobian/frame failed)", exc_info=True)

        u_qp = solve_box_with_slack(
            u_nom,
            upper=upper,
            lower=lower,
            vel_upper=vel_upper,
            vel_lower=vel_lower,
            slack_weight=float(slack_weight),
            extra_A=extra_A,
            extra_ub=extra_ub,
        )
        if u_qp is None:
            return None

        layer = self.get_layer()
        name = self.get_name()

        if np.allclose(u_qp, u_nom, atol=1e-9):
            return GuardResult.success(guard_name=name, layer=layer)

        clamped_action = ValidatedAction(
            target_joint_positions=u_qp,
            target_joint_velocities=action.target_joint_velocities,
            was_clamped=True,
            original_proposal=action,
        )
        # Per-joint detail: which joints were clamped and why
        delta = u_qp - u_nom
        joint_details = []
        for j in range(len(u_nom)):
            if abs(delta[j]) < 1e-6:
                continue
            violations = []
            if upper is not None and u_nom[j] > upper[j]:
                violations.append(f"pos>{np.degrees(upper[j]):.1f}°")
            if lower is not None and u_nom[j] < lower[j]:
                violations.append(f"pos<{np.degrees(lower[j]):.1f}°")
            if vel_upper is not None and u_nom[j] > vel_upper[j]:
                violations.append("vel↑")
            if vel_lower is not None and u_nom[j] < vel_lower[j]:
                violations.append("vel↓")
            cause = f"[{','.join(violations)}]" if violations else ""
            joint_details.append(
                f"J{j + 1}:{np.degrees(u_nom[j]):.1f}→{np.degrees(u_qp[j]):.1f}°{cause}"
            )
        joints_str = (
            "; ".join(joint_details)
            if joint_details
            else f"Δ={float(np.linalg.norm(delta)):.4f}rad"
        )
        constraint_tags = []
        if upper is not None or lower is not None:
            constraint_tags.append("pos")
        if vel_upper is not None:
            constraint_tags.append("vel")
        if cbf_active:
            constraint_tags.append("CBF")
        reason = f"QP clamp [{'+'.join(constraint_tags)}]: {joints_str}"

        return GuardResult.clamp(
            clamped_action=clamped_action,
            guard_name=name,
            layer=layer,
            reason=reason,
        )
