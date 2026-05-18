"""L3 — Hardware-monitoring boundary callbacks.

Heartbeat / staleness, motor telemetry (temperature, current, voltage), and
contact-force limits.  These watch the physical device, independent of task
state.
"""

from __future__ import annotations

import time

import numpy as np

from dam.boundary.callbacks._helpers import _read_channel
from dam.boundary.callbacks._registry import boundary_callback
from dam.types.observation import Observation


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


# ── Observation-channel constraints ───────────────────────────────────────────
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


@boundary_callback(
    name="check_force_torque_safe",
    layer="L3",
    description="Rejects if force or torque magnitude exceeds thresholds.",
)
def check_force_torque_safe(
    *, obs: Observation, max_force_n: float = 50.0, max_torque_nm: float = 10.0
) -> bool:
    """Reject on contact-force / torque overload (physical hardware safety)."""
    if obs.force_torque is None:
        return True
    f_mag = float(np.linalg.norm(obs.force_torque[:3]))
    t_mag = float(np.linalg.norm(obs.force_torque[3:]))
    return f_mag <= max_force_n and t_mag <= max_torque_nm
