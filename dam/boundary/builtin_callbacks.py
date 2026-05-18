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
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator that registers a function as a named boundary callback."""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
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
    temporal_smoothing_frames: int = 3,
) -> bool:
    """Return False if the observation is flagged as out-of-distribution."""
    from dam.decorators import guard as _guard_deco
    from dam.guard.builtin.ood import OODGuard

    decorated_ood = _guard_deco("L0")(OODGuard)
    smoothing_frames = max(1, int(temporal_smoothing_frames))
    cache_key = (ood_model_path, bank_path, backend)

    with _ood_cache_lock:
        if cache_key not in _ood_guard_cache:
            guard = decorated_ood(backend=backend)
            if ood_model_path and bank_path:
                try:
                    joint_dim = len(obs.joint_positions)
                    has_images = obs.images is not None and len(obs.images) > 0
                    guard.load(ood_model_path, bank_path, joint_dim, has_images)
                except Exception:  # noqa: BLE001 — guard runs untrained if model files are missing/invalid
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
        temporal_smoothing_frames=smoothing_frames,
    )
    return result.decision == GuardDecision.PASS


# ── L2: TASK EXECUTION (semantic) ────────────────────────────────────────────


@boundary_callback(
    name="semantic_state",
    layer="L2",
    description="High-level semantic task state validation (pre/post-condition checks).",
)
def semantic_state(*, obs: Observation) -> bool:
    """Validate task-level semantic invariants."""
    return True


# ── L1: PHYSICAL KINEMATICS ───────────────────────────────────────────────────


@boundary_callback(
    name="joint_velocity_limit",
    layer="L1",
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
    layer="L1",
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
    layer="L1",
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
    layer="L1",
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
    layer="L1",
    description="Rejects if any joint velocity exceeds a near-zero threshold.",
)
def check_joints_not_moving(*, obs: Observation, max_speed_rad_s: float = 0.01) -> bool:
    """Return False if any joint is moving faster than threshold."""
    if obs.joint_velocities is None:
        return True
    return float(np.max(np.abs(obs.joint_velocities))) <= max_speed_rad_s


# ── L2: TASK EXECUTION ────────────────────────────────────────────────────────


@boundary_callback(
    name="check_force_torque_safe",
    layer="L2",
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
    layer="L2",
    description="Rejects if the gripper appears closed when it should be open.",
)
def check_gripper_clear(*, obs: Observation, min_gripper_opening_m: float = 0.005) -> bool:
    g_pos = obs.metadata.get("gripper_pos")
    return g_pos is None or float(g_pos) >= min_gripper_opening_m


# ── L3: HARDWARE MONITORING ───────────────────────────────────────────────────


@boundary_callback(
    name="hardware_watchdog",
    layer="L3",
    description="Safety check for observation staleness.",
)
def hardware_watchdog(
    *,
    obs: Observation,
    now: float | None = None,
    max_staleness_ms: float = 1000.0,
) -> bool | tuple[bool, str]:
    current = time.monotonic() if now is None else now
    staleness_ms = (current - obs.timestamp) * 1000.0
    if staleness_ms <= max_staleness_ms:
        return True
    return False, f"Heartbeat lost: {staleness_ms:.0f}ms stale (limit {max_staleness_ms:.0f}ms)"


# ── L3: OBSERVATION-CHANNEL CONSTRAINTS ───────────────────────────────────
# These callbacks read from obs.channels (the generic observation dict).
# Each sensor type has different safety characteristics — not all are CBF.


@boundary_callback(
    name="temperature_limit",
    layer="L3",
    description="Rejects if any motor temperature exceeds threshold (°C).",
)
def temperature_limit(
    *,
    obs: Observation,
    max_temperature_c: float = 55.0,
    channel: str = "temperature",
) -> bool:
    """Return False if any motor temperature exceeds *max_temperature_c*.

    Temperature is a slow-moving scalar — hard-threshold is appropriate
    (no need for CBF dynamics).
    """
    data = _read_channel(obs, channel)
    if data is None:
        return True
    return bool(np.all(data <= max_temperature_c))


@boundary_callback(
    name="current_limit",
    layer="L3",
    description="Rejects if any motor current exceeds threshold (A).",
)
def current_limit(
    *,
    obs: Observation,
    max_current_a: float = 1.5,
    channel: str = "current",
) -> bool:
    """Return False if any motor current exceeds *max_current_a*.

    Overcurrent indicates stall or collision.  Hard threshold — the
    servo's own current limiter is the ground truth; this is a
    software-level second opinion.
    """
    data = _read_channel(obs, channel)
    if data is None:
        return True
    return bool(np.all(np.abs(data) <= max_current_a))


@boundary_callback(
    name="voltage_limit",
    layer="L3",
    description="Rejects if supply voltage is outside safe band (V).",
)
def voltage_limit(
    *,
    obs: Observation,
    min_voltage_v: float = 6.0,
    max_voltage_v: float = 8.5,
    channel: str = "voltage",
) -> bool:
    """Return False if any voltage reading is outside [min, max].

    Under-voltage → servo brown-out / erratic motion.
    Over-voltage → component damage.  Both warrant immediate stop.
    """
    data = _read_channel(obs, channel)
    if data is None:
        return True
    return bool(np.all((data >= min_voltage_v) & (data <= max_voltage_v)))


@boundary_callback(
    name="force_limit",
    layer="L3",
    description="Rejects if force magnitude exceeds threshold (N).",
)
def force_limit(
    *,
    obs: Observation,
    max_force_n: float = 50.0,
    channel: str = "force_torque",
) -> bool:
    """Return False if force magnitude from a force/torque channel exceeds limit.

    Reads from the generic observation channel dict (``obs.channels``).
    For 6-axis F/T sensors the first 3 elements are force; for load-cell
    channels the entire vector is force.
    """
    data = _read_channel(obs, channel)
    if data is None:
        # Fall back to typed field for backward compat
        if obs.force_torque is not None:
            return bool(float(np.linalg.norm(obs.force_torque[:3])) <= max_force_n)
        return True
    # If 6-element F/T, use first 3 (force).  Otherwise use all.
    force = data[:3] if len(data) >= 6 else data
    return bool(float(np.linalg.norm(force)) <= max_force_n)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _read_channel(obs: Observation, channel: str) -> np.ndarray | None:
    """Read a named channel from obs.channels (or iter_channels fallback)."""
    if obs.channels and channel in obs.channels:
        return np.asarray(obs.channels[channel])
    # Try iter_channels for typed fields like force_torque
    for name, value in obs.iter_channels():
        if name == channel:
            return np.asarray(value)
    return None


def _get_ee_pose(
    obs: Observation, kinematics_resolver: KinematicsResolver | None = None
) -> np.ndarray | None:
    if obs.end_effector_pose is not None:
        return obs.end_effector_pose
    if kinematics_resolver is not None:
        try:
            return kinematics_resolver.compute_fk(obs.joint_positions)
        except Exception:  # noqa: BLE001 — FK failure is non-fatal; caller falls back to None
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
    _safe_reg("check_force_torque_safe", check_force_torque_safe)
    _safe_reg("check_gripper_clear", check_gripper_clear)
    _safe_reg("hardware_watchdog", hardware_watchdog)
    _safe_reg("temperature_limit", temperature_limit)
    _safe_reg("current_limit", current_limit)
    _safe_reg("voltage_limit", voltage_limit)
    _safe_reg("force_limit", force_limit)

    logger.info("DAM: built-in boundary callbacks registered [L0-L3]")
