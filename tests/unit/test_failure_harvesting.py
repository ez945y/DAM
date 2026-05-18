from __future__ import annotations

import numpy as np

from dam.guard.layer import GuardLayer
from dam.runtime.guard_runtime import GuardRuntime
from dam.types.action import ActionProposal
from dam.types.observation import Observation
from dam.types.result import GuardResult


def _runtime_stub() -> GuardRuntime:
    rt = GuardRuntime.__new__(GuardRuntime)
    rt._cycle_id = 8
    rt._active_task = "paper_task"
    rt._active_container_names = ["ood_detector", "bounds"]
    return rt


def _obs() -> Observation:
    return Observation(timestamp=1.5, joint_positions=np.zeros(2), metadata={})


def _action() -> ActionProposal:
    return ActionProposal(target_joint_positions=np.array([0.1, 0.2]))


def test_failure_harvest_classifies_ood_only() -> None:
    rt = _runtime_stub()
    result = GuardResult.reject("OOD nll high", "ood", GuardLayer.L0)

    failure = rt._build_failure_harvest(
        obs=_obs(),
        action=_action(),
        validated=None,
        guard_results=[result],
        fallback_triggered=None,
        trace_id="trace",
        has_violation=True,
        has_clamp=False,
        violated_layer_mask=1,
        clamped_layer_mask=0,
        obs_channels={"current": [0.0]},
    )

    assert failure["failure_type"] == "ood_only"
    assert failure["tuple"]["failure_type"] == "ood_only"
    assert failure["tuple"]["observation_channels"] == ["current"]


def test_failure_harvest_promotes_ood_plus_action_to_guard_triggered() -> None:
    rt = _runtime_stub()
    results = [
        GuardResult.reject("OOD nll high", "ood", GuardLayer.L0),
        GuardResult.reject("joint limit", "motion", GuardLayer.L1),
    ]

    failure = rt._build_failure_harvest(
        obs=_obs(),
        action=_action(),
        validated=None,
        guard_results=results,
        fallback_triggered="emergency_stop",
        trace_id="trace",
        has_violation=True,
        has_clamp=False,
        violated_layer_mask=3,
        clamped_layer_mask=0,
        obs_channels={},
    )

    assert failure["failure_type"] == "guard_triggered"


def test_failure_harvest_hardware_has_highest_priority() -> None:
    rt = _runtime_stub()
    results = [
        GuardResult.reject("joint limit", "motion", GuardLayer.L1),
        GuardResult.fault(RuntimeError("motor stale"), "hardware", "hardware", GuardLayer.L3),
    ]

    failure = rt._build_failure_harvest(
        obs=_obs(),
        action=_action(),
        validated=None,
        guard_results=results,
        fallback_triggered="emergency_stop",
        trace_id="trace",
        has_violation=True,
        has_clamp=False,
        violated_layer_mask=10,
        clamped_layer_mask=0,
        obs_channels={},
    )

    assert failure["failure_type"] == "hardware_triggered"
