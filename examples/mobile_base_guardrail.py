# Copyright (c) 2024, Robot Control Library
# SPDX-License-Identifier: BSD-3-Clause

"""Guard a mobile base's [v, omega] command with DAM — the whole integration.

A differential-drive base observes a planar pose ``[x, y, yaw]`` and commands a
twist ``[v, omega]``. Obs and action are *different spaces*, so there is no 5-D
vector to assemble: you register a solver + a callback, build a Guardrail from a
stackfile, then feed it one dict per cycle and get a validated command back.

Run:  python examples/mobile_base_guardrail.py
"""

from __future__ import annotations

import numpy as np

import dam

DT = 0.1


# 1. Register the embodiment-specific pieces the stackfile references by name.
@dam.solver_factory("ackermann", capabilities=["rollout"])
def make_ackermann(wheel_base: float = 0.12):
    return _Ackermann(wheel_base)


@dam.callback("rollout_inside_band", layer="L1")
def rollout_inside_band(*, base_pose, action, solvers, x_min, x_max, dt: float = DT):
    """Reject a command whose predicted next pose leaves the safe x-band."""
    x_next, _y, _yaw = solvers["ackermann"].rollout(base_pose, action, dt)
    return bool(x_min <= x_next <= x_max)


class _Ackermann:
    """Minimal planar rollout: integrate [x, y, yaw] under a [v, omega] twist."""

    def __init__(self, wheel_base: float) -> None:
        self.wheel_base = wheel_base

    def rollout(self, pose, command, dt: float):
        x, y, yaw = (float(v) for v in pose)
        v, omega = (float(a) for a in command)
        yaw_next = yaw + omega * dt
        return (x + v * np.cos(yaw) * dt, y + v * np.sin(yaw) * dt, yaw_next)


def main() -> None:
    # 2. Build the guard from a stackfile. On reject, stop the base.
    rail = dam.Guardrail("examples/stackfiles/jetbot_lane_safety.yaml", safe_action=[0.0, 0.0])

    # 3. Each control cycle: hand it a dict, get a validated [v, omega] back.
    pose = [0.0, 0.0, 0.0]
    for step, command in enumerate([[0.6, 0.0], [0.6, 0.2], [-5.0, 0.0], [0.4, -0.1]]):
        safe = rail({"base_pose": pose, "action": np.asarray(command)})
        decision = rail.last_results[0].decision.name if rail.last_results else "PASS"
        print(f"step {step}: cmd={command} -> safe={np.round(safe, 3).tolist()} [{decision}]")
        # advance the (fake) base with the validated command
        pose = list(_Ackermann(0.12).rollout(pose, safe, DT))


if __name__ == "__main__":
    main()
