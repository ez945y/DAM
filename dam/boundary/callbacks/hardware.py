"""L3 — Hardware-monitoring boundary callbacks.

Heartbeat / staleness, motor telemetry (temperature, current, voltage), and
contact-force limits.  These watch the physical device, independent of task
state.
"""

from __future__ import annotations

import json
import logging
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


def _indexed_map(arr: np.ndarray) -> dict[str, float]:
    """Convert a 1-D array to {\"J1\": v, \"J2\": v, …} for inspector display."""
    return {f"J{i + 1}": round(float(v), 3) for i, v in enumerate(np.ravel(arr))}


logger = logging.getLogger(__name__)


def _try_psutil_host_health() -> dict[str, Any]:
    try:
        import psutil  # type: ignore[import-untyped]
    except Exception:
        logger.debug("psutil unavailable, host health metrics disabled")
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


_nvidia_smi_available: bool | None = None


def _try_nvidia_smi(timeout_sec: float = 0.25) -> dict[str, Any]:
    global _nvidia_smi_available
    if _nvidia_smi_available is False:
        return {}

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
    except OSError:
        _nvidia_smi_available = False
        return {}
    except subprocess.TimeoutExpired:
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
    if gpus:
        _nvidia_smi_available = True
        return {"gpus": gpus}
    return {}


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
    category="hardware",
    description="Safety check for observation staleness.",
    params={
        "max_staleness_ms": "Maximum age of the latest observation before hardware_watchdog treats it as stale."
    },
)
def hardware_watchdog(
    *,
    obs: Observation,
    now: float | None = None,
    max_staleness_ms: float = 1000.0,
) -> GuardResult:
    # Adapter-level read failure: reject immediately regardless of timing.
    if obs.metadata and obs.metadata.get("read_failure"):
        reason_detail = ""
        hw = obs.metadata.get("hardware_status")
        if isinstance(hw, dict):
            reason_detail = f": {hw.get('reason', '')}"
        return GuardResult(
            decision=GuardDecision.REJECT,
            guard_name="hardware_watchdog",
            layer=GuardLayer.L3,
            reason=f"Sensor read failure{reason_detail}",
            metadata={"read_failure": True, "limit_ms": max_staleness_ms},
        )
    current = time.monotonic() if now is None else now
    staleness_ms = (current - obs.timestamp) * 1000.0
    meta = {"staleness_ms": round(staleness_ms, 2), "limit_ms": max_staleness_ms}
    if staleness_ms <= max_staleness_ms:
        return GuardResult.success("hardware_watchdog", GuardLayer.L3, metadata=meta)
    return GuardResult(
        decision=GuardDecision.REJECT,
        guard_name="hardware_watchdog",
        layer=GuardLayer.L3,
        reason=f"Heartbeat lost: {staleness_ms:.0f}ms stale (limit {max_staleness_ms:.0f}ms)",
        metadata=meta,
    )


# ── Observation-channel constraints ───────────────────────────────────────────
# These callbacks read from obs.channels (the generic observation dict).
# Each sensor type has different safety characteristics — not all are CBF.


@boundary_callback(
    name="temperature_limit",
    layer="L3",
    category="hardware",
    description="Rejects if any motor temperature exceeds threshold (°C).",
    params={
        "max_temperature_c": "Maximum motor temperature in Celsius.",
        "channel": "Observation channel name to read from obs.channels.",
    },
)
def temperature_limit(
    *,
    obs: Observation,
    max_temperature_c: float = 55.0,
    channel: str = "temperature",
) -> GuardResult:
    data = _read_channel(obs, channel)
    if data is None:
        return GuardResult.success("temperature_limit", GuardLayer.L3)
    meta: dict[str, Any] = {
        "temperatures": _indexed_map(data),
        "limit_c": max_temperature_c,
    }
    if bool(np.all(data <= max_temperature_c)):
        return GuardResult.success("temperature_limit", GuardLayer.L3, metadata=meta)
    worst = float(np.max(data))
    return GuardResult(
        decision=GuardDecision.REJECT,
        guard_name="temperature_limit",
        layer=GuardLayer.L3,
        reason=f"Motor temperature {worst:.1f}°C exceeds {max_temperature_c:.1f}°C",
        metadata=meta,
    )


@boundary_callback(
    name="current_limit",
    layer="L3",
    category="hardware",
    description="Rejects if any motor current exceeds threshold (A).",
    params={
        "max_current_a": "Maximum absolute motor current in amps.",
        "channel": "Observation channel name to read from obs.channels.",
    },
)
def current_limit(
    *,
    obs: Observation,
    max_current_a: float = 1.5,
    channel: str = "current",
) -> GuardResult:
    data = _read_channel(obs, channel)
    if data is None:
        return GuardResult.success("current_limit", GuardLayer.L3)
    abs_data = np.abs(data)
    meta: dict[str, Any] = {
        "currents": _indexed_map(data),
        "limit_a": max_current_a,
    }
    if bool(np.all(abs_data <= max_current_a)):
        return GuardResult.success("current_limit", GuardLayer.L3, metadata=meta)
    worst_idx = int(np.argmax(abs_data))
    worst = float(abs_data[worst_idx])
    return GuardResult(
        decision=GuardDecision.REJECT,
        guard_name="current_limit",
        layer=GuardLayer.L3,
        reason=f"J{worst_idx + 1} current {worst:.2f}A exceeds {max_current_a:.2f}A",
        metadata=meta,
    )


@boundary_callback(
    name="voltage_limit",
    layer="L3",
    category="hardware",
    description="Rejects if supply voltage is outside safe band (V).",
    params={
        "min_voltage_v": "Minimum allowed supply voltage.",
        "max_voltage_v": "Maximum allowed supply voltage.",
        "channel": "Observation channel name to read from obs.channels.",
    },
)
def voltage_limit(
    *,
    obs: Observation,
    min_voltage_v: float = 10.0,
    max_voltage_v: float = 13.0,
    channel: str = "voltage",
) -> GuardResult:
    data = _read_channel(obs, channel)
    if data is None:
        return GuardResult.success("voltage_limit", GuardLayer.L3)
    meta: dict[str, Any] = {
        "voltages": _indexed_map(data),
        "range_v": [min_voltage_v, max_voltage_v],
    }
    if bool(np.all((data >= min_voltage_v) & (data <= max_voltage_v))):
        return GuardResult.success("voltage_limit", GuardLayer.L3, metadata=meta)
    lo_violations = data < min_voltage_v
    hi_violations = data > max_voltage_v
    parts: list[str] = []
    if np.any(lo_violations):
        worst = float(np.min(data))
        parts.append(f"under-voltage {worst:.2f}V < {min_voltage_v:.2f}V")
    if np.any(hi_violations):
        worst = float(np.max(data))
        parts.append(f"over-voltage {worst:.2f}V > {max_voltage_v:.2f}V")
    return GuardResult(
        decision=GuardDecision.REJECT,
        guard_name="voltage_limit",
        layer=GuardLayer.L3,
        reason="; ".join(parts),
        metadata=meta,
    )


@boundary_callback(
    name="force_limit",
    layer="L3",
    category="hardware",
    description="Rejects if force magnitude exceeds threshold (N).",
    params={
        "max_force_n": "Maximum allowed force magnitude in Newtons.",
        "channel": "Observation channel name to read from obs.channels.",
    },
)
def force_limit(
    *,
    obs: Observation,
    max_force_n: float = 50.0,
    channel: str = "force_torque",
) -> GuardResult:
    data = _read_channel(obs, channel)
    if data is None:
        if obs.force_torque is not None:
            data = np.asarray(obs.force_torque)
        else:
            return GuardResult.success("force_limit", GuardLayer.L3)
    force = data[:3] if len(data) >= 6 else data
    f_mag = float(np.linalg.norm(force))
    meta: dict[str, Any] = {"force_n": round(f_mag, 3), "limit_n": max_force_n}
    if f_mag <= max_force_n:
        return GuardResult.success("force_limit", GuardLayer.L3, metadata=meta)
    return GuardResult(
        decision=GuardDecision.REJECT,
        guard_name="force_limit",
        layer=GuardLayer.L3,
        reason=f"Force {f_mag:.1f}N exceeds {max_force_n:.1f}N",
        metadata=meta,
    )


@boundary_callback(
    name="check_force_torque_safe",
    layer="L3",
    category="hardware",
    description="Rejects if force or torque magnitude exceeds thresholds.",
    params={
        "max_force_n": "Maximum allowed force magnitude in Newtons.",
        "max_torque_nm": "Maximum allowed torque magnitude in Newton-metres.",
    },
)
def check_force_torque_safe(
    *, obs: Observation, max_force_n: float = 50.0, max_torque_nm: float = 10.0
) -> GuardResult:
    if obs.force_torque is None:
        return GuardResult.success("check_force_torque_safe", GuardLayer.L3)
    f_mag = float(np.linalg.norm(obs.force_torque[:3]))
    t_mag = float(np.linalg.norm(obs.force_torque[3:]))
    meta: dict[str, Any] = {
        "force_n": round(f_mag, 3),
        "torque_nm": round(t_mag, 3),
        "limit_force_n": max_force_n,
        "limit_torque_nm": max_torque_nm,
    }
    if f_mag <= max_force_n and t_mag <= max_torque_nm:
        return GuardResult.success("check_force_torque_safe", GuardLayer.L3, metadata=meta)
    parts: list[str] = []
    if f_mag > max_force_n:
        parts.append(f"force {f_mag:.1f}N > {max_force_n:.1f}N")
    if t_mag > max_torque_nm:
        parts.append(f"torque {t_mag:.1f}Nm > {max_torque_nm:.1f}Nm")
    return GuardResult(
        decision=GuardDecision.REJECT,
        guard_name="check_force_torque_safe",
        layer=GuardLayer.L3,
        reason="; ".join(parts),
        metadata=meta,
    )


@boundary_callback(
    name="host_health_limit",
    layer="L3",
    category="host",
    description="Faults if host CPU/GPU/memory/temperature crosses configured limits.",
    params={
        "max_cpu_percent": "Host CPU usage percentage above which host_health_limit faults.",
        "max_memory_percent": "Host memory usage percentage above which host_health_limit faults.",
        "max_temperature_c": "Host temperature threshold in Celsius.",
        "max_gpu_percent": "GPU usage percentage above which host_health_limit faults.",
        "max_gpu_temperature_c": "GPU temperature threshold in Celsius above which host_health_limit faults.",
    },
)
def host_health_limit(
    *,
    obs: Observation | None = None,
    max_cpu_percent: float | None = 95.0,
    max_memory_percent: float | None = 95.0,
    max_temperature_c: float | None = 90.0,
    max_gpu_percent: float | None = 98.0,
    max_gpu_temperature_c: float | None = 90.0,
) -> GuardResult:
    """Check computer health via the standard L3 boundary path.

    Reads from ``obs.metadata["host_health"]`` — the runtime injects host
    health at the observation phase, just like adapter-provided channels.
    Falls back to direct collection when obs is unavailable (e.g. tests).
    """
    health: dict = (
        obs.metadata.get("host_health") if obs is not None and obs.metadata else None
    ) or collect_host_health()
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
