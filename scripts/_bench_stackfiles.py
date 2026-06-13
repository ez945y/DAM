"""Shared helper: build temporary stackfile YAMLs for bench / scan / quality scripts.

These scripts previously hand-rolled `BoundaryNode` / `SingleNodeContainer`
instances and dispatched directly into individual guards.  That bypassed the
runtime pipeline — boundary params, callback registration, stage assembly,
config-pool merging — and meant fixes to the production path didn't affect
the benchmarks.

The right interface is `GuardRuntime.from_stackfile(path)` + `runtime.validate(...)`,
i.e. the same path production runs.  This module writes a temporary stackfile
that mirrors `examples/stackfiles/*.yaml` and returns the parsed runtime.
Callers pick which guards / boundaries to include via flags.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from dam.boundary.callbacks import register_all as _register_callbacks
from dam.guard.builtin import register_all as _register_guards
from dam.runtime.guard_runtime import GuardRuntime

# Default kinematic limits used across the bench / scan / quality scripts.
# Tracks the SO101-style 6-DoF arm the project ships with by default.
N_JOINTS = 6
JOINT_UPPER = np.array([1.8243, 1.7691, 1.6026, 1.8067, 3.0741, 1.7453])
JOINT_LOWER = -JOINT_UPPER.copy()
JOINT_LOWER[-1] = 0.0
MAX_VEL = np.full(N_JOINTS, 1.5)
WORKSPACE_BOUNDS = [[-0.40, 0.40], [-0.40, 0.40], [0.02, 0.60]]


_registered = False


def _ensure_registries() -> None:
    """Idempotent registration of builtin guards + callbacks."""
    global _registered
    if _registered:
        return
    _register_callbacks()
    _register_guards()
    _registered = True


def _l1_boundaries() -> dict[str, dict[str, Any]]:
    return {
        "joint_position_limits": {
            "layer": "L1",
            "type": "single",
            "nodes": [
                {
                    "node_id": "n0",
                    "callback": "joint_position_limits",
                    "fallback": "emergency_stop",
                    "params": {
                        "upper": JOINT_UPPER.tolist(),
                        "lower": JOINT_LOWER.tolist(),
                    },
                }
            ],
        },
        "joint_velocity_limit": {
            "layer": "L1",
            "type": "single",
            "nodes": [
                {
                    "node_id": "n0",
                    "callback": "joint_velocity_limit",
                    "fallback": "emergency_stop",
                    "params": {"max_velocities": MAX_VEL.tolist()},
                }
            ],
        },
        "workspace": {
            "layer": "L1",
            "type": "single",
            "nodes": [
                {
                    "node_id": "n0",
                    "callback": "workspace",
                    "fallback": "emergency_stop",
                    "params": {"bounds": WORKSPACE_BOUNDS},
                }
            ],
        },
    }


def _l3_boundaries() -> dict[str, dict[str, Any]]:
    return {
        "hardware_watchdog": {
            "layer": "L3",
            "type": "single",
            "nodes": [
                {
                    "node_id": "n0",
                    "callback": "hardware_watchdog",
                    "fallback": "emergency_stop",
                    "params": {"max_staleness_ms": 1000},
                }
            ],
        }
    }


def _l0_boundaries() -> dict[str, dict[str, Any]]:
    return {
        "ood_detector": {
            "layer": "L0",
            "type": "single",
            "nodes": [
                {
                    "node_id": "n0",
                    "callback": "ood_detector",
                    "fallback": "emergency_stop",
                    "params": {"backend": "welford", "temporal_smoothing_frames": 1},
                }
            ],
        }
    }


def build_runtime(
    *,
    ood: bool = False,
    motion: bool = False,
    execution: bool = False,
    hardware: bool = False,
    control_frequency_hz: float = 30.0,
    enforcement_mode: str = "monitor",
    extra_boundaries: dict[str, dict[str, Any]] | None = None,
    motion_param_overrides: dict[str, dict[str, Any]] | None = None,
) -> tuple[GuardRuntime, Path]:
    """Materialise an in-memory stackfile selecting the requested guards, load
    it via :py:meth:`GuardRuntime.from_stackfile`, start the default task.

    Returns (runtime, stackfile_path).  Caller is responsible for letting the
    tempfile clean itself up (it's written to the system temp dir).

    ``enforcement_mode='monitor'`` keeps fallbacks from firing per cycle so
    the bench loop stays clean; switch to ``'enforce'`` to also exercise the
    fallback path.
    """
    _ensure_registries()

    guards: list[dict[str, str]] = []
    boundaries: dict[str, dict[str, Any]] = {}
    bnames: list[str] = []

    if ood:
        guards.append({"L0": "ood"})
        boundaries.update(_l0_boundaries())
        bnames.extend(_l0_boundaries().keys())
    if motion:
        guards.append({"L1": "motion"})
        b = _l1_boundaries()
        if motion_param_overrides:
            for bname, override in motion_param_overrides.items():
                if bname in b:
                    b[bname]["nodes"][0]["params"].update(override)
        boundaries.update(b)
        bnames.extend(b.keys())
    if execution:
        # ExecutionGuard runs even without an explicit boundary callback —
        # the empty L2 list gives the bench a no-op pass cost.
        guards.append({"L2": "execution"})
    if hardware:
        guards.append({"L3": "hardware"})
        boundaries.update(_l3_boundaries())
        bnames.extend(_l3_boundaries().keys())

    if extra_boundaries:
        for bn, body in extra_boundaries.items():
            boundaries[bn] = body
            bnames.append(bn)
            # Make sure the guard kind owning that layer is in the active list.
            layer = body.get("layer", "L2")
            kind = {"L0": "ood", "L1": "motion", "L2": "execution", "L3": "hardware"}.get(layer)
            if kind and not any(kind in d.values() for d in guards):
                guards.append({layer: kind})

    stack: dict[str, Any] = {
        "version": "1",
        "guards": guards,
        "boundaries": boundaries,
        "tasks": {"default": {"description": "bench", "boundaries": bnames}},
        "safety": {
            "control_hz": control_frequency_hz,
            "enforcement_mode": enforcement_mode,
            "no_task_behavior": "emergency_stop",
        },
        "fallbacks": {"emergency_stop": {"type": "emergency_stop"}},
    }

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, prefix="dam_bench_"
    ) as f:
        yaml.safe_dump(stack, f)
        path = Path(f.name)

    rt = GuardRuntime.from_stackfile(str(path))
    rt.start_task("default")
    return rt, path
