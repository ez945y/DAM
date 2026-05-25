"""Unit tests for HardwareGuard (L3).

HardwareGuard is a thin shell that dispatches to L3 boundary callbacks.
These tests verify the guard's behavior through boundary containers.
"""

from __future__ import annotations

import time

import numpy as np
import pytest

from dam.boundary.constraint import BoundaryConstraint
from dam.boundary.node import BoundaryNode
from dam.boundary.single import SingleNodeContainer
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


def _make_obs(**kwargs):
    defaults = dict(
        timestamp=time.monotonic(),
        joint_positions=np.zeros(6),
        joint_velocities=np.zeros(6),
    )
    defaults.update(kwargs)
    return Observation(**defaults)


def _container(callback, warn_frames=1, **params):
    constraint = BoundaryConstraint(callback=callback, params=params)
    node = BoundaryNode(callback, constraint, warn_frames=warn_frames)
    return SingleNodeContainer(node)


def _watchdog_container(max_staleness_ms: float):
    return _container("hardware_watchdog", max_staleness_ms=max_staleness_ms)


def _host_health_container(max_cpu_percent: float):
    return _container("host_health_limit", max_cpu_percent=max_cpu_percent)


def _temp_container(max_temperature_c: float = 55.0, warn_frames: int = 1):
    return _container(
        "temperature_limit", warn_frames=warn_frames, max_temperature_c=max_temperature_c
    )


def _current_container(max_current_a: float = 1.5, warn_frames: int = 1):
    return _container("current_limit", warn_frames=warn_frames, max_current_a=max_current_a)


@pytest.fixture(autouse=True)
def _register_callbacks():
    from dam.boundary.builtin_callbacks import register_all

    register_all()


# ── Basic pass / fault ───────────────────────────────────────────────────────


def test_no_containers_passes(HG):
    result = HG.check(obs=_make_obs())
    assert result.decision == GuardDecision.PASS


def test_pass_within_limits(HG):
    obs = _make_obs(channels={"temperature": np.array([40.0, 42.0])})
    c = _temp_container(55.0)
    result = HG.check(obs=obs, active_containers=[c])
    assert result.decision == GuardDecision.PASS


def test_fault_on_overtemperature(HG):
    obs = _make_obs(channels={"temperature": np.array([60.0, 55.0])})
    c = _temp_container(55.0)
    result = HG.check(obs=obs, active_containers=[c])
    assert result.decision == GuardDecision.FAULT


def test_fault_on_overcurrent(HG):
    obs = _make_obs(channels={"current": np.array([2.0, 0.5])})
    c = _current_container(1.5)
    result = HG.check(obs=obs, active_containers=[c])
    assert result.decision == GuardDecision.FAULT


# ── warn_frames (consecutive frames before fault) ──────────────────────────


def test_warn_frames_clamp_then_fault(HG):
    """With warn_frames=3, first two violations are CLAMP, third is FAULT."""
    obs = _make_obs(channels={"temperature": np.array([70.0])})
    c = _temp_container(55.0, warn_frames=3)
    c._runtime_boundary_name = "temp_check"

    for i in range(2):
        result = HG.check(obs=obs, active_containers=[c])
        assert result.decision == GuardDecision.CLAMP, f"cycle {i}: expected CLAMP"
        assert f"warn {i + 1}/3" in result.reason

    result = HG.check(obs=obs, active_containers=[c])
    assert result.decision == GuardDecision.FAULT
    assert "warn 3/3" in result.reason


def test_warn_streak_resets_on_pass(HG):
    """Streak resets when the violation clears."""
    obs_hot = _make_obs(channels={"temperature": np.array([70.0])})
    obs_cool = _make_obs(channels={"temperature": np.array([40.0])})
    c = _temp_container(55.0, warn_frames=3)
    c._runtime_boundary_name = "temp_reset"

    # Build up 2 CLAMPs
    HG.check(obs=obs_hot, active_containers=[c])
    HG.check(obs=obs_hot, active_containers=[c])

    # Temperature drops → PASS → streak resets
    result = HG.check(obs=obs_cool, active_containers=[c])
    assert result.decision == GuardDecision.PASS

    # Next violation starts from warn 1
    result = HG.check(obs=obs_hot, active_containers=[c])
    assert result.decision == GuardDecision.CLAMP
    assert "warn 1/3" in result.reason


# ── Watchdog and host health (boundary callback path) ────────────────────────


def test_watchdog_pass(HG):
    now = time.monotonic()
    obs = _make_obs(timestamp=now - 0.1)
    result = HG.check(obs=obs, now=now, active_containers=[_watchdog_container(1000.0)])
    assert result.decision == GuardDecision.PASS


def test_watchdog_fault(HG):
    now = time.monotonic()
    obs = _make_obs(timestamp=now - 0.837)
    result = HG.check(obs=obs, now=now, active_containers=[_watchdog_container(500.0)])
    assert result.decision == GuardDecision.FAULT
    assert "limit 500ms" in result.reason


def test_host_health_fault(HG, monkeypatch):
    import importlib

    hw_module = importlib.import_module("dam.guard.builtin.hardware")
    monkeypatch.setattr(
        hw_module,
        "collect_host_health",
        lambda: {"cpu_percent": 99.5, "memory_percent": 30.0, "timestamp": 1.0},
    )
    result = HG.check(obs=_make_obs(), active_containers=[_host_health_container(90.0)])
    assert result.decision == GuardDecision.FAULT
    assert "CPU" in result.reason


def test_host_health_pass_records_metadata(HG, monkeypatch):
    import importlib

    hw_module = importlib.import_module("dam.guard.builtin.hardware")
    monkeypatch.setattr(
        hw_module,
        "collect_host_health",
        lambda: {"cpu_percent": 12.0, "memory_percent": 30.0, "timestamp": 1.0},
    )
    result = HG.check(obs=_make_obs(), active_containers=[_host_health_container(90.0)])
    assert result.decision == GuardDecision.PASS
    assert result.metadata["host_health"]["cpu_percent"] == 12.0


# ── Telemetry summary ─────────────────────────────────────────────────────────


def test_telemetry_summary_in_metadata(HG):
    status = {
        "temperatures": {"m1": 45.0, "m2": 50.0},
        "currents": {"m1": 0.3, "m2": 0.5},
    }
    result = HG.check(obs=_make_obs(), hardware_status=status)
    assert result.decision == GuardDecision.PASS
    assert "temperatures" in result.metadata
