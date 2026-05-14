"""Unit tests for HardwareGuard (L4)."""

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
