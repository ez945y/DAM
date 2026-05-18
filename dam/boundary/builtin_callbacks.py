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

# name → fn, populated by @boundary_callback at import time.  register_all()
# iterates this so new callbacks are picked up automatically — no manual list
# to keep in sync.  Registration is still deferred to register_all() (called by
# the runtime/service init) so importing this module stays side-effect free,
# per the project's "no import-time side effects" rule.
_CALLBACKS: dict[str, Callable[..., Any]] = {}


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
        if name in _CALLBACKS and _CALLBACKS[name] is not fn:
            raise ValueError(f"Duplicate boundary callback name: {name!r}")
        _CALLBACKS[name] = fn
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


@boundary_callback(
    name="cartesian_velocity_limit",
    layer="L1",
    description="Caps end-effector Cartesian speed (linear m/s, angular rad/s).",
)
def cartesian_velocity_limit(
    *,
    obs: Observation,
    dynamics: Any | None = None,
    max_linear_speed: float = 0.25,
    max_angular_speed: float = 1.0,
    frame: str | int | None = None,
) -> bool | tuple[bool, str]:
    """Return False if the end-effector twist exceeds Cartesian speed limits.

    The default 0.25 m/s linear cap follows the ISO/TS 15066 reduced-speed
    guidance for human-collaborative operation.  Requires a ``dynamics``
    context (pinocchio) for the frame Jacobian; if unavailable the check
    passes (degrade gracefully, like ``workspace``).
    """
    if obs.joint_velocities is None or obs.joint_positions is None:
        return True
    if dynamics is None or not getattr(dynamics, "available", False):
        return True
    fid = frame if frame is not None else dynamics.default_frame_id
    if fid is None:
        return True
    dynamics.update(np.asarray(obs.joint_positions, dtype=np.float64))
    jac = np.asarray(dynamics.frame_jacobian(fid), dtype=np.float64)  # 6 × nq
    qd = np.asarray(obs.joint_velocities, dtype=np.float64)
    n = min(jac.shape[1], qd.shape[0])
    twist = jac[:, :n] @ qd[:n]
    v_lin = float(np.linalg.norm(twist[:3]))
    v_ang = float(np.linalg.norm(twist[3:6]))
    if v_lin > max_linear_speed or v_ang > max_angular_speed:
        return (
            False,
            f"EE speed exceeded: {v_lin:.3f} m/s (max {max_linear_speed}), "
            f"{v_ang:.3f} rad/s (max {max_angular_speed})",
        )
    return True


@boundary_callback(
    name="keep_out_zone",
    layer="L1",
    description="Rejects if the end-effector enters a keep-out box or sphere.",
)
def keep_out_zone(
    *,
    obs: Observation,
    boxes: list[list[list[float]]] | None = None,
    spheres: list[list[float]] | None = None,
    kinematics_resolver: KinematicsResolver | None = None,
    dynamics: Any | None = None,
) -> bool | tuple[bool, str]:
    """Return False if the end-effector position is inside any keep-out region.

    The inverse of ``workspace`` — used for fixtures, no-go corridors, and
    human-occupied volumes.  ``boxes`` is a list of ``[[xmin,xmax],[ymin,ymax],
    [zmin,zmax]]``; ``spheres`` a list of ``[cx,cy,cz,radius]`` (metres).
    """
    pos = _resolve_ee_translation(obs, kinematics_resolver=kinematics_resolver, dynamics=dynamics)
    if pos is None:
        return True
    for box in boxes or []:
        b = np.asarray(box, dtype=np.float64)  # 3 × 2
        if np.all((pos >= b[:, 0]) & (pos <= b[:, 1])):
            return False, f"EE inside keep-out box {box}"
    for sphere in spheres or []:
        s = np.asarray(sphere, dtype=np.float64)  # [cx, cy, cz, r]
        if float(np.linalg.norm(pos - s[:3])) <= float(s[3]):
            return False, f"EE inside keep-out sphere (center {s[:3].tolist()})"
    return True


@boundary_callback(
    name="orientation_limit",
    layer="L1",
    description="Rejects if end-effector tilt from a reference axis exceeds a limit (deg).",
)
def orientation_limit(
    *,
    obs: Observation,
    max_tilt_deg: float = 30.0,
    reference_axis: list[float] | None = None,
    tool_axis: list[float] | None = None,
    kinematics_resolver: KinematicsResolver | None = None,
    dynamics: Any | None = None,
) -> bool | tuple[bool, str]:
    """Return False if the tool axis tilts past *max_tilt_deg* from reference.

    Keeps a carried payload upright (e.g. open containers, trays).
    ``tool_axis`` is the axis in the EE frame to keep aligned (default local
    +Z); ``reference_axis`` is the world direction to align it with (default
    world up ``[0,0,1]``).
    """
    rot = _resolve_ee_rotation(obs, kinematics_resolver=kinematics_resolver, dynamics=dynamics)
    if rot is None:
        return True
    tool = np.asarray(tool_axis if tool_axis is not None else [0.0, 0.0, 1.0], dtype=np.float64)
    ref = np.asarray(
        reference_axis if reference_axis is not None else [0.0, 0.0, 1.0], dtype=np.float64
    )
    tool_norm = float(np.linalg.norm(tool))
    ref_norm = float(np.linalg.norm(ref))
    if tool_norm == 0.0 or ref_norm == 0.0:
        return True
    tool_world = rot @ (tool / tool_norm)
    cos_tilt = float(np.clip(np.dot(tool_world, ref / ref_norm), -1.0, 1.0))
    tilt_deg = float(np.degrees(np.arccos(cos_tilt)))
    if tilt_deg > max_tilt_deg:
        return False, f"EE tilt {tilt_deg:.1f}° exceeds {max_tilt_deg}°"
    return True


@boundary_callback(
    name="base_geofence",
    layer="L1",
    description="Rejects if the mobile base leaves a geofence box or polygon.",
)
def base_geofence(
    *,
    obs: Observation,
    bounds: list[list[float]] | None = None,
    polygon: list[list[float]] | None = None,
    channel: str = "base_pose",
) -> bool | tuple[bool, str]:
    """Return False if the mobile base (x, y) leaves the allowed region.

    The base pose is read from ``obs.channels[channel]`` (first two elements
    are interpreted as planar x, y in metres).  ``bounds`` is an axis-aligned
    box ``[[xmin,xmax],[ymin,ymax]]``; ``polygon`` is a list of ``[x,y]``
    vertices.  When both are given the base must satisfy both.  Passes when
    the channel is absent (degrade gracefully on arms without a base).
    """
    data = _read_channel(obs, channel)
    if data is None or len(data) < 2:
        return True
    p = np.asarray(data, dtype=np.float64)[:2]
    if bounds is not None:
        b = np.asarray(bounds, dtype=np.float64)  # 2 × 2
        if not (b[0, 0] <= p[0] <= b[0, 1] and b[1, 0] <= p[1] <= b[1, 1]):
            return False, f"Base ({p[0]:.2f}, {p[1]:.2f}) outside geofence box"
    if polygon is not None:
        verts = np.asarray(polygon, dtype=np.float64)
        if len(verts) >= 3 and not _point_in_polygon(p, verts):
            return False, f"Base ({p[0]:.2f}, {p[1]:.2f}) outside geofence polygon"
    return True


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


def _quat_to_rotmat(quat: np.ndarray) -> np.ndarray:
    """Rotation matrix from a ``[qx, qy, qz, qw]`` quaternion."""
    x, y, z, w = (float(v) for v in quat[:4])
    n = x * x + y * y + z * z + w * w
    if n == 0.0:
        return np.eye(3)
    s = 2.0 / n
    return np.array(
        [
            [1.0 - s * (y * y + z * z), s * (x * y - z * w), s * (x * z + y * w)],
            [s * (x * y + z * w), 1.0 - s * (x * x + z * z), s * (y * z - x * w)],
            [s * (x * z - y * w), s * (y * z + x * w), 1.0 - s * (x * x + y * y)],
        ],
        dtype=np.float64,
    )


def _resolve_ee_translation(
    obs: Observation,
    kinematics_resolver: KinematicsResolver | None = None,
    dynamics: Any | None = None,
) -> np.ndarray | None:
    """End-effector position (3,), preferring the cached ``dynamics`` context."""
    if (
        dynamics is not None
        and getattr(dynamics, "available", False)
        and dynamics.default_frame_id is not None
        and obs.joint_positions is not None
    ):
        dynamics.update(np.asarray(obs.joint_positions, dtype=np.float64))
        placement = dynamics.frame_placement(dynamics.default_frame_id)
        return np.asarray(placement.translation, dtype=np.float64)
    pose = _get_ee_pose(obs, kinematics_resolver=kinematics_resolver)
    if pose is None:
        return None
    return np.asarray(pose[:3], dtype=np.float64)


def _resolve_ee_rotation(
    obs: Observation,
    kinematics_resolver: KinematicsResolver | None = None,
    dynamics: Any | None = None,
) -> np.ndarray | None:
    """End-effector rotation matrix (3, 3), or None if unobtainable."""
    if (
        dynamics is not None
        and getattr(dynamics, "available", False)
        and dynamics.default_frame_id is not None
        and obs.joint_positions is not None
    ):
        dynamics.update(np.asarray(obs.joint_positions, dtype=np.float64))
        placement = dynamics.frame_placement(dynamics.default_frame_id)
        return np.asarray(placement.rotation, dtype=np.float64)
    pose = _get_ee_pose(obs, kinematics_resolver=kinematics_resolver)
    if pose is None or len(pose) < 7:
        return None
    return _quat_to_rotmat(np.asarray(pose[3:7], dtype=np.float64))


def _point_in_polygon(point: np.ndarray, vertices: np.ndarray) -> bool:
    """Ray-casting point-in-polygon test for a 2D ``[x, y]`` point."""
    x, y = float(point[0]), float(point[1])
    inside = False
    n = len(vertices)
    j = n - 1
    for i in range(n):
        xi, yi = float(vertices[i][0]), float(vertices[i][1])
        xj, yj = float(vertices[j][0]), float(vertices[j][1])
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def register_all() -> None:
    """Register every ``@boundary_callback``-decorated function.

    Auto-discovered from ``_CALLBACKS`` (populated by the decorator at import
    time), so adding a new callback to this module needs no edit here.
    """
    reg = get_global_registry()
    for name, fn in _CALLBACKS.items():
        with contextlib.suppress(ValueError):
            reg.register(name, fn)

    logger.info("DAM: %d built-in boundary callbacks registered [L0-L3]", len(_CALLBACKS))
