# Copyright (c) 2024, Robot Control Library
# SPDX-License-Identifier: BSD-3-Clause

"""Production-style Guardrail wrapper for a Jetbot diff-drive base.

Obs and action are *different spaces* — the base observes a planar pose
``[x, y, yaw]`` and commands a twist ``[v, omega]`` — so there is no 5-D vector
to assemble. The boundary callback asks for ``base_pose``, ``action`` and
``solvers`` by name; the runtime injects them, exactly like ``step()``.

    guard.filter(command, state) -> safe command   # torch in, torch out

Unsafe commands are QP-corrected in place (minimum change that keeps the
predicted pose inside the box); without a QP solver the command is rejected and
the base stops (``safe_action=[0, 0]``).

Run:  python examples/jetbot_guardrail.py
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import torch

import dam

try:  # QP is optional; without it, an unsafe command is rejected (stop).
    import osqp
    from scipy import sparse
except ImportError:  # pragma: no cover - exercised only when osqp is absent
    osqp = None


# ── Embodiment: a planar Ackermann/diff-drive solver ────────────────────────


@dataclass(frozen=True)
class AckermannSolver:
    """Integrate a planar pose under a ``[v, omega]`` twist and map it to wheels.

    ``rollout`` runs on torch tensors so the guard can autodiff it for the QP.
    """

    track_width: float = 0.12
    wheel_radius: float = 1.0
    max_v: float = 1.4
    max_omega: float = 6.0
    default_dt: float = 1.0 / 60.0

    def rollout(self, pose, command, dt: float | None = None) -> torch.Tensor:
        dt = self.default_dt if dt is None else float(dt)
        x, y, yaw = _as_tensor(pose).reshape(-1)[:3]
        v, omega = self.clamp(command)
        next_yaw = _wrap(yaw + omega * dt)
        if torch.abs(omega) < 1e-6:
            nx, ny = x + v * torch.cos(yaw) * dt, y + v * torch.sin(yaw) * dt
        else:
            r = v / omega
            nx = x + r * (torch.sin(next_yaw) - torch.sin(yaw))
            ny = y - r * (torch.cos(next_yaw) - torch.cos(yaw))
        return torch.stack([nx, ny, next_yaw])

    def clamp(self, command) -> torch.Tensor:
        v, omega = _as_tensor(command).reshape(-1)[:2]
        return torch.stack(
            [v.clamp(-self.max_v, self.max_v), omega.clamp(-self.max_omega, self.max_omega)]
        )

    def command_to_wheels(self, command) -> torch.Tensor:
        v, omega = self.clamp(command)
        left = v - omega * self.track_width / 2.0
        right = v + omega * self.track_width / 2.0
        return torch.stack([left / self.wheel_radius, right / self.wheel_radius])


def _as_tensor(value) -> torch.Tensor:
    if isinstance(value, torch.Tensor):
        return value.to(torch.float32)
    return torch.as_tensor(np.asarray(value, dtype=np.float32))


def _wrap(angle: torch.Tensor) -> torch.Tensor:
    return torch.atan2(torch.sin(angle), torch.cos(angle))


# ── Boundary callback (registered once at import) ───────────────────────────


@dam.callback("jetbot_clamp_inside_band", layer="L1")
def jetbot_clamp_inside_band(
    *, base_pose, action, solvers, x_min, x_max, y_abs_max=0.24, dt=1.0 / 15.0
):
    """Keep the predicted pose in [x_min, x_max] × [-y_abs_max, y_abs_max]."""
    solver = solvers["ackermann"]
    command = [float(a) for a in action.target_joint_positions[:2]]
    nxt = solver.rollout(base_pose, command, dt)
    if x_min <= float(nxt[0]) <= x_max and abs(float(nxt[1])) <= y_abs_max:
        return True  # already safe

    safe = _qp_correct(solver, base_pose, command, x_min, x_max, y_abs_max, dt)
    if safe is None:
        return False  # no QP → reject; the guard returns safe_action (stop)
    action.target_joint_positions[0], action.target_joint_positions[1] = safe
    return True


def _qp_correct(solver, pose, command, x_min, x_max, y_abs_max, dt):
    """Smallest Δ[v, omega] that pulls the predicted pose back into the box."""
    if osqp is None:
        return None

    v0, w0 = solver.clamp(command).tolist()
    u = torch.tensor([v0, w0], dtype=torch.float32, requires_grad=True)
    nxt = solver.rollout(pose, u, dt)
    x_next, y_next = nxt[0].item(), nxt[1].item()
    nxt[0].backward(retain_graph=True)
    gx = u.grad.clone().numpy()
    u.grad.zero_()
    nxt[1].backward()
    gy = u.grad.clone().numpy()

    # min ‖Δu‖²  s.t.  box on u0+Δu  and linearised box on the predicted pose.
    A = sparse.csc_matrix(np.array([[1.0, 0.0], [0.0, 1.0], gx, gy]))
    lo = np.array([-solver.max_v - v0, -solver.max_omega - w0, x_min - x_next, -y_abs_max - y_next])
    hi = np.array([solver.max_v - v0, solver.max_omega - w0, x_max - x_next, y_abs_max - y_next])
    P = sparse.csc_matrix(np.diag([1.0, 0.05]))  # prefer keeping the turn rate

    prob = osqp.OSQP()
    prob.setup(P, np.zeros(2), A, lo, hi, verbose=False, eps_abs=1e-5, eps_rel=1e-5)
    res = prob.solve()
    if res.info.status != "solved":
        return None  # solver failed → reject (stop)
    return float(v0 + res.x[0]), float(w0 + res.x[1])


# ── The wrapper: torch in, validated torch out ──────────────────────────────


class JetbotGuardrail:
    """Filter a Jetbot ``[v, omega]`` command through DAM (no URDF needed)."""

    def __init__(
        self,
        stackfile: str,
        *,
        solver: AckermannSolver | None = None,
        task: str = "default",
        quiet: bool = True,
    ) -> None:
        self.solver = solver or AckermannSolver()
        # Share the configured solver with the guard's callback via `solvers`.
        self._guard = dam.Guardrail(
            stackfile,
            task=task,
            solvers={"ackermann": self.solver},
            safe_action=[0.0, 0.0],
            quiet=quiet,
        )

    def filter(self, command: torch.Tensor, state: torch.Tensor) -> torch.Tensor:
        """``command`` is ``[v, omega]``, ``state`` is ``[x, y, yaw, ...]`` (1-D or batched-by-1)."""
        squeeze = command.dim() == 1
        if squeeze:
            command, state = command.unsqueeze(0), state.unsqueeze(0)
        safe = self._guard(
            {"base_pose": state[0, :3].detach().cpu().numpy(), "action": command[0]}
        ).unsqueeze(0)
        return safe.squeeze(0) if squeeze else safe

    def command_to_wheels(self, command: torch.Tensor) -> torch.Tensor:
        return torch.stack(
            [self.solver.command_to_wheels(row).to(command) for row in command.reshape(-1, 2)]
        )

    @property
    def last_results(self):
        """Raw per-callback guard results from the most recent ``filter`` call."""
        return self._guard.last_results

    def close(self) -> None:
        self._guard.close()


def main() -> None:
    rail = JetbotGuardrail(
        "examples/stackfiles/jetbot_clamp_safety.yaml",
        solver=AckermannSolver(track_width=0.12, wheel_radius=0.03),
        quiet=False,  # print the obs/action contract on startup
    )
    # Start near the x_min edge so a reverse command would leave the safe box.
    # With osqp installed the unsafe command is QP-corrected (PASS); without it
    # the command is rejected and the base stops (REJECT -> [0, 0]).
    state = torch.tensor([-0.20, 0.0, 0.0])  # [x, y, yaw], facing +x
    for cmd in ([0.4, 0.0], [-1.0, 0.0], [1.4, 0.2], [0.2, 0.5]):
        safe = rail.filter(torch.tensor(cmd), state)
        wheels = rail.command_to_wheels(safe.unsqueeze(0))[0]
        decision = rail.last_results[0].decision.name if rail.last_results else "PASS"
        print(
            f"cmd={cmd} -> safe={[round(s, 3) for s in safe.tolist()]} "
            f"wheels={[round(w, 2) for w in wheels.tolist()]} [{decision}]"
        )
    rail.close()


if __name__ == "__main__":
    main()
