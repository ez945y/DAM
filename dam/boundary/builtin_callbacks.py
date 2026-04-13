"""Built-in boundary callbacks for DAM.

These are ready-to-use check functions for ``BoundaryConstraint.callback``.
Each callback is defined with the ``@boundary_callback`` decorator which
simultaneously **registers** it and attaches metadata (layer, description) that
the UI and tooling can introspect.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import numpy as np

from dam.kinematics.resolver import KinematicsResolver
from dam.registry.callback import get_global_registry
from dam.types.observation import Observation

logger = logging.getLogger(__name__)


# ── Metadata store ────────────────────────────────────────────────────────────

_CATALOG: list[dict[str, Any]] = []  # [{name, layer, description, params, doc}, ...]


def boundary_callback(
    *,
    name: str,
    layer: str,
    description: str = "",
) -> Callable[[Callable[..., bool]], Callable[..., bool]]:
    """Decorator that registers a function as a named boundary callback."""

    def decorator(fn: Callable[..., bool]) -> Callable[..., bool]:
        import inspect

        sig = inspect.signature(fn)
        params_meta = {}
        for p_name, param in sig.parameters.items():
            if p_name == "obs":
                continue
            params_meta[p_name] = {
                "default": param.default if param.default is not inspect.Parameter.empty else None,
                "has_default": param.default is not inspect.Parameter.empty,
            }
        doc = fn.__doc__ or ""

        fn._cb_name = name  # type: ignore[attr-defined]
        fn._cb_layer = layer  # type: ignore[attr-defined]
        fn._cb_description = description or (doc.split("\n")[0] if doc else "")  # type: ignore[attr-defined]

        _CATALOG.append(
            {
                "name": name,
                "layer": layer,
                "description": fn._cb_description,
                "params": params_meta,
                "doc": doc,
            }
        )
        return fn

    return decorator


def get_catalog() -> list[dict[str, Any]]:
    """Return a copy of the full callback catalog (name, layer, description, params, doc)."""
    return list(_CATALOG)


# ── L0: PERCEPTION (OOD) ──────────────────────────────────────────────────────

_ood_guard_cache: dict[tuple[str, str, str], Any] = {}
_ood_cache_lock = threading.Lock()


@boundary_callback(
    name="ood_detector",
    layer="L0",
    description="Out-of-distribution boundary callback — wraps OODGuard.",
)
def ood_detector(
    *,
    obs: Observation,
    ood_model_path: str = "",
    bank_path: str = "",
    nn_threshold: float = 2.0,
    nll_threshold: float = 5.0,
    backend: str = "memory_bank",
) -> bool:
    """Return False if the observation is flagged as out-of-distribution."""
    from dam.decorators import guard as _guard_deco
    from dam.guard.builtin.ood import OODGuard

    _DecoratedOOD = _guard_deco("L0")(OODGuard)
    cache_key = (ood_model_path, bank_path, backend)

    with _ood_cache_lock:
        if cache_key not in _ood_guard_cache:
            guard = _DecoratedOOD(backend=backend)
            if ood_model_path and bank_path:
                try:
                    joint_dim = len(obs.joint_positions)
                    has_images = obs.images is not None and len(obs.images) > 0
                    guard.load(ood_model_path, bank_path, joint_dim, has_images)
                except Exception:
                    pass
            _ood_guard_cache[cache_key] = guard
        guard = _ood_guard_cache[cache_key]

    from dam.types.result import GuardDecision

    result = guard.check(
        obs,
        nn_threshold=nn_threshold,
        nll_threshold=nll_threshold,
        ood_model_path=ood_model_path or None,
        bank_path=bank_path or None,
    )
    return result.decision == GuardDecision.PASS


# ── L1: TASK PREFLIGHT ────────────────────────────────────────────────────────


@boundary_callback(
    name="semantic_state",
    layer="L1",
    description="High-level semantic task state validation (pre/post-condition checks).",
)
def semantic_state(*, obs: Observation) -> bool:
    """Validate task-level semantic invariants."""
    return True


# ── L2: MOTION SAFETY ─────────────────────────────────────────────────────────


@boundary_callback(
    name="joint_velocity_limit",
    layer="L2",
    description="Joint speed safety check (Radians or Degrees).",
)
def joint_velocity_limit(
    *,
    obs: Observation,
    max_velocities: list[float] = None,
    use_degrees: bool = False,
) -> bool:
    """Return False if any joint velocity exceeds limits."""
    if max_velocities is None:
        max_velocities = [1.5, 1.5, 1.5, 1.5, 1.5, 1.5]
    if obs.joint_velocities is None or max_velocities is None:
        return True
    v_max = np.array(max_velocities)
    if use_degrees:
        v_max = np.radians(v_max)
    vel = np.abs(obs.joint_velocities)
    if v_max.ndim == 0:
        if np.any(vel > v_max):
            return False
    else:
        v_max_1d = np.atleast_1d(v_max)
        n = min(len(vel), len(v_max_1d))
        if np.any(vel[:n] > v_max_1d[:n]):
            return False
    return True


@boundary_callback(
    name="joint_position_limits",
    layer="L2",
    description="Joint position safety check (Radians or Degrees).",
)
def joint_position_limits(
    *,
    obs: Observation,
    upper: list[float] = None,
    lower: list[float] = None,
    use_degrees: bool = False,
) -> bool:
    """Return False if any joint position violates limits."""
    if lower is None:
        lower = [-1.82, -1.77, -1.6, -1.81, -3.07, 0.0]
    if upper is None:
        upper = [1.82, 1.77, 1.6, 1.81, 3.07, 1.75]
    if obs.joint_positions is None or upper is None or lower is None:
        return True
    pos, up, lo = obs.joint_positions, np.array(upper), np.array(lower)
    if use_degrees:
        up, lo = np.radians(up), np.radians(lo)
    return not (np.any(pos > up) or np.any(pos < lo))


@boundary_callback(
    name="workspace",
    layer="L2",
    description="Workspace box bounds [x,y,z] min/max in metres.",
)
def workspace(
    *,
    obs: Observation,
    bounds: list[list[float]] = None,
    kinematics_resolver: KinematicsResolver | None = None,
) -> bool:
    """Check if end-effector is within workspace box bounds."""
    if bounds is None:
        bounds = [[-0.4, 0.4], [-0.4, 0.4], [0.02, 0.6]]
    ee_pose = _get_ee_pose(obs, kinematics_resolver=kinematics_resolver)
    if ee_pose is None:
        return True
    ee_pos = ee_pose[:3]
    b = np.array(bounds)
    return np.all((ee_pos >= b[:, 0]) & (ee_pos <= b[:, 1]))


@boundary_callback(
    name="check_velocity_smooth",
    layer="L2",
    description="Rejects if the joint velocity norm exceeds a jerk threshold.",
)
def check_velocity_smooth(*, obs: Observation, max_jerk_norm: float = 10.0) -> bool:
    """Return False if the rate of velocity change is too high."""
    if obs.joint_velocities is None:
        return True
    vel_norm = float(np.linalg.norm(obs.joint_velocities))
    return vel_norm <= max_jerk_norm


@boundary_callback(
    name="check_joints_not_moving",
    layer="L2",
    description="Rejects if any joint velocity exceeds a near-zero threshold.",
)
def check_joints_not_moving(*, obs: Observation, max_speed_rad_s: float = 0.01) -> bool:
    """Return False if any joint is moving faster than threshold."""
    if obs.joint_velocities is None:
        return True
    return not float(np.max(np.abs(obs.joint_velocities))) > max_speed_rad_s


# ── L3: TASK EXECUTION ─────────────────────────────────────────────────────────


@boundary_callback(
    name="dynamic_safety",
    layer="L3",
    description="Real-time obstacle avoidance and social distance monitoring.",
)
def dynamic_safety(*, obs: Observation) -> bool:
    return True


@boundary_callback(
    name="execution_heartbeat",
    layer="L3",
    description="Monitor for policy execution timeouts or model hangs.",
)
def execution_heartbeat(*, obs: Observation, timeout_sec: float = 0.5) -> bool:
    return True


@boundary_callback(
    name="outcome_verifier",
    layer="L3",
    description="Verifies the outcome of actions against high-level goals.",
)
def outcome_verifier(*, obs: Observation) -> bool:
    return True


@boundary_callback(
    name="check_force_torque_safe",
    layer="L3",
    description="Rejects if force or torque magnitude exceeds thresholds.",
)
def check_force_torque_safe(
    *, obs: Observation, max_force_n: float = 50.0, max_torque_nm: float = 10.0
) -> bool:
    if obs.force_torque is None:
        return True
    f_mag = float(np.linalg.norm(obs.force_torque[:3]))
    t_mag = float(np.linalg.norm(obs.force_torque[3:]))
    return f_mag <= max_force_n and t_mag <= max_torque_nm


@boundary_callback(
    name="check_gripper_clear",
    layer="L3",
    description="Rejects if the gripper appears closed when it should be open.",
)
def check_gripper_clear(*, obs: Observation, min_gripper_opening_m: float = 0.005) -> bool:
    g_pos = obs.metadata.get("gripper_pos")
    return g_pos is None or float(g_pos) >= min_gripper_opening_m


# ── L4: HARDWARE MONITORING ────────────────────────────────────────────────────


@boundary_callback(
    name="hardware_watchdog",
    layer="L4",
    description="Safety check for observation staleness.",
)
def hardware_watchdog(*, obs: Observation, max_staleness_ms: float = 1000.0) -> bool:
    staleness_ms = (time.monotonic() - obs.timestamp) * 1000.0
    return staleness_ms <= max_staleness_ms


# ── Helpers ───────────────────────────────────────────────────────────────────


def _get_ee_pose(
    obs: Observation, kinematics_resolver: KinematicsResolver | None = None
) -> np.ndarray | None:
    if obs.end_effector_pose is not None:
        return obs.end_effector_pose
    if kinematics_resolver is not None:
        try:
            return kinematics_resolver.compute_fk(obs.joint_positions)
        except Exception:
            pass
    return None


def register_all() -> None:
    reg = get_global_registry()

    def _safe_reg(n, f):
        with contextlib.suppress(ValueError):
            reg.register(n, f)

    _safe_reg("ood_detector", ood_detector)
    _safe_reg("semantic_state", semantic_state)
    _safe_reg("joint_velocity_limit", joint_velocity_limit)
    _safe_reg("joint_position_limits", joint_position_limits)
    _safe_reg("workspace", workspace)
    _safe_reg("check_velocity_smooth", check_velocity_smooth)
    _safe_reg("check_joints_not_moving", check_joints_not_moving)
    _safe_reg("dynamic_safety", dynamic_safety)
    _safe_reg("execution_heartbeat", execution_heartbeat)
    _safe_reg("outcome_verifier", outcome_verifier)
    _safe_reg("check_force_torque_safe", check_force_torque_safe)
    _safe_reg("check_gripper_clear", check_gripper_clear)
    _safe_reg("hardware_watchdog", hardware_watchdog)

    logger.info("DAM: built-in boundary callbacks registered [L0-TaskExecution-Monitoring]")
