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

    # ── Tests ──────────────────────────────────────────────────────────────────

    obs = Observation(
        timestamp=time.monotonic(), joint_positions=np.zeros(6), joint_velocities=np.zeros(6)
    )
    result = HG.check(obs=obs, hardware_status=None)
    assert result.decision == GuardDecision.PASS


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
    assert "temperature" in result.reason.lower() or "Temperature" in result.reason


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
    assert "current" in result.reason.lower() or "Current" in result.reason


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
