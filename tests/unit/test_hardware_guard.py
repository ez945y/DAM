"""Unit tests for HardwareGuard (L3)."""

from __future__ import annotations

import time

import numpy as np
import pytest

from dam.guard.builtin.hardware import HardwareGuard
from dam.injection.static import precompute_injection
from dam.types.observation import Observation
from dam.types.result import GuardDecision


@pytest.fixture
def HG():
    """Return a fresh HardwareGuard instance (already decorated at import time)."""
    g = HardwareGuard()
    precompute_injection(g, {})
    return g


def test_pass_within_limits(HG):
    """Normal temperature and current within defaults → PASS."""
    status = {
        "temperature_c": 45.0,
        "current_a": 2.5,
        "error_codes": [],
    }
    obs = Observation(
        timestamp=time.monotonic(), joint_positions=np.zeros(6), joint_velocities=np.zeros(6)
    )
    result = HG.check(obs=obs, hardware_status=status)
    assert result.decision == GuardDecision.PASS


def test_fault_on_overtemperature(HG):
    """Temperature exceeding default limit (70°C) → FAULT."""
    status = {
        "temperature_c": 80.0,
        "current_a": 1.0,
        "error_codes": [],
    }
    obs = Observation(
        timestamp=time.monotonic(), joint_positions=np.zeros(6), joint_velocities=np.zeros(6)
    )
    result = HG.check(obs=obs, hardware_status=status, max_temperature_c=70.0)
    assert result.decision == GuardDecision.FAULT
    assert result.fault_source == "hardware"
    assert "temp" in result.reason.lower()


def test_fault_on_overcurrent(HG):
    """Current exceeding default limit (5.0A) → FAULT."""
    status = {
        "temperature_c": 30.0,
        "current_a": 6.0,
        "error_codes": [],
    }
    obs = Observation(
        timestamp=time.monotonic(), joint_positions=np.zeros(6), joint_velocities=np.zeros(6)
    )
    result = HG.check(obs=obs, hardware_status=status, max_current_a=5.0)
    assert result.decision == GuardDecision.FAULT
    assert result.fault_source == "hardware"
    assert "current" in result.reason.lower()


def test_motor_peak_requires_consecutive_frames_before_fault(HG):
    status = {
        "temperatures": {"m1": 90.0},
        "currents": {"m1": 0.2},
        "voltages": {"m1": 7.4},
        "error_codes": [],
    }
    obs = Observation(
        timestamp=time.monotonic(), joint_positions=np.zeros(6), joint_velocities=np.zeros(6)
    )

    first = HG.check(
        obs=obs,
        hardware_status=status,
        max_temperature_c=80.0,
        consecutive_fault_frames=2,
    )
    second = HG.check(
        obs=obs,
        hardware_status=status,
        max_temperature_c=80.0,
        consecutive_fault_frames=2,
    )

    assert first.decision == GuardDecision.PASS
    assert first.metadata["hardware_peak"]["streak"] == 1
    assert second.decision == GuardDecision.FAULT
    assert "streak 2/2" in second.reason


def test_voltage_monitor_can_be_enabled(HG):
    status = {"voltages": {"m1": 5.5}, "error_codes": []}
    obs = Observation(
        timestamp=time.monotonic(), joint_positions=np.zeros(6), joint_velocities=np.zeros(6)
    )

    off = HG.check(obs=obs, hardware_status=status, monitor_voltage=False)
    on = HG.check(obs=obs, hardware_status=status, monitor_voltage=True, min_voltage_v=6.0)

    assert off.decision == GuardDecision.PASS
    assert on.decision == GuardDecision.FAULT
    assert "Voltage" in on.reason


def test_fault_on_error_code(HG):
    """Non-zero error code in error_codes list → FAULT."""
    status = {
        "temperature_c": 30.0,
        "current_a": 1.0,
        "error_codes": [1],
    }
    obs = Observation(
        timestamp=time.monotonic(), joint_positions=np.zeros(6), joint_velocities=np.zeros(6)
    )
    result = HG.check(obs=obs, hardware_status=status)
    assert result.decision == GuardDecision.FAULT
    assert result.fault_source == "hardware"


def _hardware_watchdog_container(max_staleness_ms: float):
    from dam.boundary.constraint import BoundaryConstraint
    from dam.boundary.node import BoundaryNode
    from dam.boundary.single import SingleNodeContainer

    constraint = BoundaryConstraint(
        callback="hardware_watchdog",
        params={"max_staleness_ms": max_staleness_ms},
    )
    return SingleNodeContainer(BoundaryNode("hardware_watchdog", constraint))


def _host_health_container(max_cpu_percent: float):
    from dam.boundary.constraint import BoundaryConstraint
    from dam.boundary.node import BoundaryNode
    from dam.boundary.single import SingleNodeContainer

    constraint = BoundaryConstraint(
        callback="host_health_limit",
        params={"max_cpu_percent": max_cpu_percent},
    )
    return SingleNodeContainer(BoundaryNode("host_health", constraint))


def test_watchdog_callback_uses_boundary_params(HG):
    """L3 callback params should be read from the active boundary node."""
    from dam.boundary.builtin_callbacks import register_all

    register_all()
    now = time.monotonic()
    obs = Observation(
        timestamp=now - 0.837,
        joint_positions=np.zeros(6),
        joint_velocities=np.zeros(6),
    )

    result = HG.check(
        obs=obs,
        now=now,
        cycle_id=226,
        active_containers=[_hardware_watchdog_container(1000.0)],
    )

    assert result.decision == GuardDecision.PASS


def test_watchdog_callback_fault_reports_boundary_param_limit(HG):
    """Fault reason should report the configured watchdog limit, not a fixed default."""
    from dam.boundary.builtin_callbacks import register_all

    register_all()
    now = time.monotonic()
    obs = Observation(
        timestamp=now - 0.837,
        joint_positions=np.zeros(6),
        joint_velocities=np.zeros(6),
    )

    result = HG.check(
        obs=obs,
        now=now,
        cycle_id=226,
        active_containers=[_hardware_watchdog_container(500.0)],
    )

    assert result.decision == GuardDecision.FAULT
    assert "limit 500ms" in result.reason


def test_host_health_callback_faults_through_l3_boundary(HG, monkeypatch):
    """Computer health must use the L3 boundary callback path, not side-channel logging."""
    import importlib

    from dam.boundary.builtin_callbacks import register_all

    register_all()
    hw_module = importlib.import_module("dam.guard.builtin.hardware")
    monkeypatch.setattr(
        hw_module,
        "collect_host_health",
        lambda: {"cpu_percent": 99.5, "memory_percent": 30.0, "timestamp": 1.0},
    )

    obs = Observation(
        timestamp=time.monotonic(),
        joint_positions=np.zeros(6),
        joint_velocities=np.zeros(6),
    )

    result = HG.check(obs=obs, active_containers=[_host_health_container(90.0)])

    assert result.decision == GuardDecision.FAULT
    assert result.fault_source == "hardware"
    assert "CPU" in result.reason
    assert result.metadata["host_health"]["cpu_percent"] == 99.5


def test_host_health_boundary_metadata_is_recorded_on_pass(HG, monkeypatch):
    import importlib

    from dam.boundary.builtin_callbacks import register_all

    register_all()
    hw_module = importlib.import_module("dam.guard.builtin.hardware")
    monkeypatch.setattr(
        hw_module,
        "collect_host_health",
        lambda: {"cpu_percent": 12.0, "memory_percent": 30.0, "timestamp": 1.0},
    )

    obs = Observation(
        timestamp=time.monotonic(),
        joint_positions=np.zeros(6),
        joint_velocities=np.zeros(6),
    )

    result = HG.check(obs=obs, active_containers=[_host_health_container(90.0)])

    assert result.decision == GuardDecision.PASS
    assert result.metadata["host_health"]["cpu_percent"] == 12.0
