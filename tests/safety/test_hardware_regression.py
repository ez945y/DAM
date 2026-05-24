"""Safety regression tests for HardwareGuard (L3)."""

from __future__ import annotations

import time

import numpy as np

from dam.guard.builtin.hardware import HardwareGuard
from dam.guard.layer import GuardLayer
from dam.injection.static import precompute_injection
from dam.types.action import ActionProposal
from dam.types.observation import Observation
from dam.types.result import GuardDecision

# ── Helpers ──────────────────────────────────────────────────────────────��─


def make_obs():
    return Observation(
        timestamp=time.monotonic(),
        joint_positions=np.zeros(6),
        joint_velocities=np.zeros(6),
    )


def make_action():
    return ActionProposal(target_joint_positions=np.zeros(6))


def _make_runtime_with_hardware_guard():
    """Build a GuardRuntime with HardwareGuard and a mock sink with bad status."""
    from dam.runtime.guard_runtime import GuardRuntime

    g = HardwareGuard()
    g.set_name("hw")
    precompute_injection(g, {})

    from dam.boundary.constraint import BoundaryConstraint
    from dam.boundary.node import BoundaryNode
    from dam.boundary.single import SingleNodeContainer

    rt = GuardRuntime(
        guards=[g],
        boundary_containers={
            "hw": SingleNodeContainer(BoundaryNode("hwnode", BoundaryConstraint()))
        },
        task_config={"default": ["hw"]},
        always_active=[],
        config_pool={},
    )
    return rt


# ── Tests ───────────────────────���─────────────────────────────��────────────


def test_hardware_fault_propagates_to_reject():
    """HardwareGuard FAULT → GuardRuntime rejects the action."""
    rt = _make_runtime_with_hardware_guard()
    rt.start_task("default")

    obs = Observation(
        timestamp=time.monotonic(),
        joint_positions=np.zeros(6),
        joint_velocities=np.zeros(6),
        metadata={"hardware_status": {"temperature_c": 100.0, "current_a": 1.0, "error_codes": []}},
    )
    action = make_action()

    validated, results = rt.validate(obs, action, "trace-hw")

    fault_results = [r for r in results if r.decision == GuardDecision.FAULT]
    assert len(fault_results) >= 1
    assert validated is None, "FAULT should cause rejection"


def test_l3_is_hardware_layer():
    """HardwareGuard is registered at L3 (hardware layer)."""
    g = HardwareGuard()
    assert g.get_layer() == GuardLayer.L3
    assert g.get_layer().value == 3
