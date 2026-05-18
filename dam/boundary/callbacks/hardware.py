"""L3 — Hardware-monitoring boundary callbacks.

Heartbeat / staleness, motor telemetry (temperature, current, voltage), and
contact-force limits.  These watch the physical device, independent of task
state.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from contextlib import suppress
from typing import Any

import numpy as np

from dam.boundary.callbacks._helpers import _read_channel
from dam.boundary.callbacks._registry import boundary_callback
from dam.guard.layer import GuardLayer
from dam.types.observation import Observation
from dam.types.result import GuardDecision, GuardResult

_HOST_HEALTH_CACHE: dict[str, Any] = {"t": 0.0, "value": {}}


def _try_psutil_host_health() -> dict[str, Any]:
    try:
        import psutil  # type: ignore[import-untyped]
    except Exception:
        return {}

    out: dict[str, Any] = {}
    with suppress(Exception):
        out["cpu_percent"] = float(psutil.cpu_percent(interval=None))
    with suppress(Exception):
        mem = psutil.virtual_memory()
        out["memory_percent"] = float(mem.percent)
        out["memory_available_mb"] = float(mem.available) / (1024.0 * 1024.0)
    with suppress(Exception):
        temps = psutil.sensors_temperatures()
        values = [
            float(entry.current)
            for entries in temps.values()
            for entry in entries
            if getattr(entry, "current", None) is not None
        ]
        if values:
            out["temperature_c"] = max(values)
    return out


def _load_average_percent() -> float | None:
    try:
        load1, _, _ = os.getloadavg()
        cpus = os.cpu_count() or 1
        return min(100.0, max(0.0, 100.0 * load1 / cpus))
    except OSError:
        return None


def _try_nvidia_smi(timeout_sec: float = 0.25) -> dict[str, Any]:
    query = "utilization.gpu,temperature.gpu,memory.used,memory.total"
    cmd = [
        "nvidia-smi",
        f"--query-gpu={query}",
        "--format=csv,noheader,nounits",
    ]
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if proc.returncode != 0 or not proc.stdout.strip():
        return {}

    gpus = []
    for line in proc.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 4:
            continue
        try:
            # nvidia-smi can emit "[N/A]" for unsupported metrics — skip those.
            util, temp, mem_used, mem_total = (float(p) for p in parts)
        except ValueError:
            continue
        gpus.append(
            {
                "util_percent": util,
                "temperature_c": temp,
                "memory_used_mb": mem_used,
                "memory_total_mb": mem_total,
            }
        )
    return {"gpus": gpus} if gpus else {}


def collect_host_health(*, ttl_sec: float = 1.0) -> dict[str, Any]:
    """Return a cached host health snapshot for L3 boundary callbacks."""
    now = time.monotonic()
    if now - float(_HOST_HEALTH_CACHE["t"]) < ttl_sec:
        cached = _HOST_HEALTH_CACHE["value"]
        if isinstance(cached, dict):
            return dict(cached)

    data = _try_psutil_host_health()
    data.setdefault("cpu_percent", _load_average_percent())
    data.update(_try_nvidia_smi())
    data["timestamp"] = time.time()
    clean = json.loads(json.dumps(data))
    _HOST_HEALTH_CACHE["t"] = now
    _HOST_HEALTH_CACHE["value"] = clean
    return dict(clean)


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


@boundary_callback(
    name="host_health_limit",
    layer="L3",
    description="Faults if host CPU/GPU/memory/temperature crosses configured limits.",
)
def host_health_limit(
    *,
    host_health: dict | None = None,
    max_cpu_percent: float | None = 95.0,
    max_memory_percent: float | None = 95.0,
    max_temperature_c: float | None = 90.0,
    max_gpu_percent: float | None = 98.0,
    max_gpu_temperature_c: float | None = 90.0,
) -> GuardResult:
    """Check computer health via the standard L3 boundary path.

    This callback intentionally returns a ``GuardResult`` so the sampled host
    metrics are preserved as MCAP guard metadata whenever the boundary fires.
    """
    health = host_health or collect_host_health()
    reasons: list[str] = []

    cpu = health.get("cpu_percent")
    if max_cpu_percent is not None and cpu is not None and float(cpu) > max_cpu_percent:
        reasons.append(f"CPU {float(cpu):.1f}% > {max_cpu_percent:.1f}%")

    memory = health.get("memory_percent")
    if max_memory_percent is not None and memory is not None and float(memory) > max_memory_percent:
        reasons.append(f"memory {float(memory):.1f}% > {max_memory_percent:.1f}%")

    temp = health.get("temperature_c")
    if max_temperature_c is not None and temp is not None and float(temp) > max_temperature_c:
        reasons.append(f"host temp {float(temp):.1f}C > {max_temperature_c:.1f}C")

    for i, gpu in enumerate(health.get("gpus") or []):
        util = gpu.get("util_percent")
        if max_gpu_percent is not None and util is not None and float(util) > max_gpu_percent:
            reasons.append(f"GPU{i} {float(util):.1f}% > {max_gpu_percent:.1f}%")
        gpu_temp = gpu.get("temperature_c")
        if (
            max_gpu_temperature_c is not None
            and gpu_temp is not None
            and float(gpu_temp) > max_gpu_temperature_c
        ):
            reasons.append(f"GPU{i} temp {float(gpu_temp):.1f}C > {max_gpu_temperature_c:.1f}C")

    if reasons:
        return GuardResult(
            decision=GuardDecision.FAULT,
            guard_name="host_health",
            layer=GuardLayer.L3,
            reason="; ".join(reasons),
            fault_source="hardware",
            metadata={"host_health": health},
        )
    return GuardResult.success(
        guard_name="host_health",
        layer=GuardLayer.L3,
        reason="host health within limits",
        metadata={"host_health": health},
    )
