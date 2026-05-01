"""Experiment 1 — Boundary Precision Validation (實驗一：攔截邊界精準度驗證).

Sweeps disturbance intensity across four scenarios and records the interception
rate at each level.  Outputs a CSV summary and a matplotlib figure.

Scenarios
---------
L1-A  Joint angle offset  σ ∈ {0.05, 0.10, …, 0.50} rad   (Gaussian noise added to
       ACT output joint positions)
L1-B  Velocity scale      k ∈ {1.2, 1.4, …, 3.0}           (commanded velocities × k)
L2-A  Collision distance  d ∈ {10, 9, …, 1} cm             (end-effector clearance)
L2-B  Timeout ratio       r ∈ {0.5×, 0.67×, …, 2.0×}       (node active time / T_timeout)

Usage
-----
    python scripts/run_boundary_scan.py [--trials N] [--outdir PATH]

    N       trials per (scenario, level) — default 20
    outdir  where to write results        — default ./data/exp1_boundary_scan/
"""

from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path

import numpy as np

from dam.guard.builtin.execution import ExecutionGuard

# ── DAM imports ──────────────────────────────────────────────────────────────
from dam.guard.builtin.motion import MotionGuard
from dam.types.action import ActionProposal
from dam.types.observation import Observation
from dam.types.result import GuardDecision

# ── Helpers ──────────────────────────────────────────────────────────────────

_N_JOINTS = 6
_JOINT_UPPER = np.array([1.8243, 1.7691, 1.6026, 1.8067, 3.0741, 1.7453])
_JOINT_LOWER = -_JOINT_UPPER.copy()
_JOINT_LOWER[-1] = 0.0
_MAX_VEL = np.full(_N_JOINTS, 1.5)
_WORKSPACE_BOUNDS = [[-0.40, 0.40], [-0.40, 0.40], [0.02, 0.60]]
_EE_SAFE = np.array([0.0, 0.0, 0.30])  # end-effector safely inside workspace
_T_TIMEOUT = 2.0  # reference task node timeout (seconds)


def _make_obs(
    joint_positions: np.ndarray | None = None,
    ee_pose: np.ndarray | None = None,
) -> Observation:
    pos = joint_positions if joint_positions is not None else np.zeros(_N_JOINTS)
    pose = ee_pose if ee_pose is not None else np.concatenate([_EE_SAFE, np.zeros(4)])
    return Observation(
        timestamp=time.monotonic(),
        joint_positions=pos,
        joint_velocities=np.zeros(_N_JOINTS),
        end_effector_pose=pose,
    )


def _nominal_action() -> np.ndarray:
    """Mid-range joint positions well within limits."""
    return np.zeros(_N_JOINTS)


def _is_intercepted(result) -> bool:
    # CLAMP counts as intercepted: the guard modified the action to enforce safety.
    return result.decision in (GuardDecision.REJECT, GuardDecision.FAULT, GuardDecision.CLAMP)


# ── L1 guards (MotionGuard) ──────────────────────────────────────────────────


def _build_motion_guard() -> MotionGuard:
    import dam

    # Apply @dam.guard decorator so get_layer() works without a full runtime.
    cls = dam.guard("L1")(MotionGuard)
    g = cls()
    g._guard_name = "motion_scan"
    return g


_MOTION_KWARGS = dict(
    upper=_JOINT_UPPER.tolist(),
    lower=_JOINT_LOWER.tolist(),
    max_velocity=_MAX_VEL.tolist(),
    bounds=_WORKSPACE_BOUNDS,
)


def scan_l1_joint_offset(trials: int) -> list[dict]:
    """L1-A: Gaussian noise σ on joint positions."""
    sigmas = np.linspace(0.05, 0.50, 10)
    guard = _build_motion_guard()
    rows = []
    rng = np.random.default_rng(42)

    for sigma in sigmas:
        intercepted = 0
        for _ in range(trials):
            base = _nominal_action()
            perturbed = base + rng.normal(0, sigma, _N_JOINTS)
            obs = _make_obs(joint_positions=perturbed)
            action = ActionProposal(target_joint_positions=perturbed)
            result = guard.check(obs=obs, action=action, **_MOTION_KWARGS)
            if _is_intercepted(result):
                intercepted += 1
        rows.append(
            {
                "scenario": "L1-A_joint_offset",
                "disturbance_label": "sigma_rad",
                "disturbance_value": round(float(sigma), 4),
                "intercepted": intercepted,
                "trials": trials,
                "interception_rate": intercepted / trials,
            }
        )
        print(f"  L1-A σ={sigma:.2f}  intercepted={intercepted}/{trials}")
    return rows


def scan_l1_velocity_scale(trials: int) -> list[dict]:
    """L1-B: velocity magnitude scaled by factor k."""
    ks = np.linspace(1.2, 3.0, 10)
    guard = _build_motion_guard()
    rows = []

    # Nominal velocity: ~80 % of limit so that k ≥ 1.25 reliably triggers clamping.
    nominal_vel = _MAX_VEL * 0.8

    for k in ks:
        intercepted = 0
        for _ in range(trials):
            scaled_vel = nominal_vel * k
            obs = Observation(
                timestamp=time.monotonic(),
                joint_positions=_nominal_action(),
                joint_velocities=scaled_vel,
                end_effector_pose=np.concatenate([_EE_SAFE, np.zeros(4)]),
            )
            action = ActionProposal(
                target_joint_positions=_nominal_action(),
                target_joint_velocities=scaled_vel,
            )
            result = guard.check(obs=obs, action=action, **_MOTION_KWARGS)
            if _is_intercepted(result):
                intercepted += 1
        rows.append(
            {
                "scenario": "L1-B_velocity_scale",
                "disturbance_label": "k",
                "disturbance_value": round(float(k), 4),
                "intercepted": intercepted,
                "trials": trials,
                "interception_rate": intercepted / trials,
            }
        )
        print(f"  L1-B k={k:.2f}  intercepted={intercepted}/{trials}")
    return rows


# ── L2 guards (ExecutionGuard) ───────────────────────────────────────────────


def scan_l2_collision_distance(trials: int) -> list[dict]:
    """L2-A: End-effector clearance relative to a prohibited-zone boundary.

    A tight prohibited zone is defined at x_max = 0.30 m.  The scan sweeps
    the ee clearance d from +5 cm (safely inside) down through 0 to −5 cm
    (outside, definitely intercepted), producing a boundary-crossing curve.

    Positive d  → ee is d metres inside the boundary  → PASS
    Negative d  → ee is |d| metres outside the boundary → REJECT/CLAMP
    """
    # d > 0: inside zone; d < 0: outside zone
    d_values_cm = np.linspace(5, -5, 10)  # +5 cm … -5 cm
    prohibited_x_max = 0.30  # m (prohibited zone boundary, tighter than workspace)
    bounds = [[-0.40, prohibited_x_max], [-0.40, 0.40], [0.02, 0.60]]
    rows = []

    from dam.boundary.builtin_callbacks import workspace as workspace_cb

    for d_cm in d_values_cm:
        intercepted = 0
        d_m = d_cm / 100.0
        # Place ee at (x_max - d, 0, 0.30):  d>0 → inside, d<0 → outside
        ee_x = prohibited_x_max - d_m
        for _ in range(trials):
            ee_pose = np.array([ee_x, 0.0, 0.30, 0, 0, 0, 1], dtype=np.float64)
            obs = _make_obs(joint_positions=_nominal_action(), ee_pose=ee_pose)
            passed = workspace_cb(obs=obs, bounds=bounds)
            if not passed:
                intercepted += 1
        rows.append(
            {
                "scenario": "L2-A_collision_distance",
                "disturbance_label": "d_cm",
                "disturbance_value": round(float(d_cm), 2),
                "intercepted": intercepted,
                "trials": trials,
                "interception_rate": intercepted / trials,
            }
        )
        print(f"  L2-A d={d_cm:+.1f}cm  intercepted={intercepted}/{trials}")
    return rows


def scan_l2_timeout(trials: int) -> list[dict]:
    """L2-B: Node execution time relative to T_timeout.

    Injects a fake node that has been active for ratio × T_timeout seconds and
    checks whether ExecutionGuard's timeout constraint fires.
    """
    ratios = np.linspace(0.5, 2.0, 10)
    rows = []

    import dam
    from dam.boundary.constraint import BoundaryConstraint
    from dam.boundary.node import BoundaryNode
    from dam.boundary.single import SingleNodeContainer

    guard_cls = dam.guard("L2")(ExecutionGuard)
    guard = guard_cls()
    guard._guard_name = "execution_scan"

    node_id = "timeout_test_node"
    constraint = BoundaryConstraint(params={})
    # timeout_sec lives on BoundaryNode, not in constraint.params.
    node = BoundaryNode(
        node_id=node_id,
        constraint=constraint,
        fallback="emergency_stop",
        timeout_sec=_T_TIMEOUT,
    )
    container = SingleNodeContainer(node=node)

    for ratio in ratios:
        intercepted = 0
        active_duration = ratio * _T_TIMEOUT
        for _ in range(trials):
            fake_start = time.monotonic() - active_duration
            node_start_times = {node_id: fake_start}
            obs = _make_obs()
            result = guard.check(
                obs=obs,
                active_containers=[container],
                node_start_times=node_start_times,
            )
            if _is_intercepted(result):
                intercepted += 1
        rows.append(
            {
                "scenario": "L2-B_timeout",
                "disturbance_label": "ratio",
                "disturbance_value": round(float(ratio), 4),
                "intercepted": intercepted,
                "trials": trials,
                "interception_rate": intercepted / trials,
            }
        )
        print(f"  L2-B ratio={ratio:.2f}×T  intercepted={intercepted}/{trials}")
    return rows


# ── Reporting ─────────────────────────────────────────────────────────────────


def write_csv(rows: list[dict], path: Path) -> None:
    if not rows:
        return
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"CSV saved: {path}")


def plot_results(rows: list[dict], outdir: Path) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed — skipping plot generation.")
        return

    scenarios = {}
    for r in rows:
        scenarios.setdefault(r["scenario"], []).append(r)

    fig, axes = plt.subplots(1, len(scenarios), figsize=(5 * len(scenarios), 4))
    if len(scenarios) == 1:
        axes = [axes]

    for ax, (name, data) in zip(axes, scenarios.items(), strict=False):
        xs = [d["disturbance_value"] for d in data]
        ys = [d["interception_rate"] * 100 for d in data]
        ax.plot(xs, ys, marker="o", linewidth=2)
        ax.axhline(50, color="orange", linestyle="--", linewidth=1, label="x50")
        ax.axhline(90, color="red", linestyle="--", linewidth=1, label="x90")
        ax.set_title(name, fontsize=10)
        ax.set_xlabel(data[0]["disturbance_label"])
        ax.set_ylabel("Interception rate (%)")
        ax.set_ylim(-5, 105)
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)

    fig.tight_layout()
    out = outdir / "boundary_scan.png"
    fig.savefig(out, dpi=150)
    print(f"Plot saved: {out}")
    plt.close(fig)


def _interpolate_x(xs: np.ndarray, rates: np.ndarray, target: float) -> float | None:
    """Linear interpolation to find the x-value where interception rate hits target."""
    for i in range(len(rates) - 1):
        r1, r2 = rates[i], rates[i + 1]
        if (r1 <= target <= r2) or (r1 >= target >= r2):
            if abs(r2 - r1) < 1e-9:
                return float(xs[i])
            t = (target - r1) / (r2 - r1)
            return float(xs[i] + t * (xs[i + 1] - xs[i]))
    return None


def _process_scenario(name: str, data: list[dict]) -> None:
    xs = np.array([d["disturbance_value"] for d in data])
    rates = np.array([d["interception_rate"] for d in data])

    x50 = _interpolate_x(xs, rates, 0.50)
    x90 = _interpolate_x(xs, rates, 0.90)
    steepness = (x90 - x50) if (x50 is not None and x90 is not None) else None

    x50_s = "N/A" if x50 is None else f"{x50:.4f}"
    x90_s = "N/A" if x90 is None else f"{x90:.4f}"
    steep_s = "N/A" if steepness is None else f"{steepness:.4f}"
    print(f"  {name:<35}  x50={x50_s:>8}  x90={x90_s:>8}  steepness={steep_s:>8}")


def compute_summary(rows: list[dict]) -> None:
    """Print x50/x90 and steepness per scenario."""
    scenarios: dict[str, list] = {}
    for r in rows:
        scenarios.setdefault(r["scenario"], []).append(r)

    print("\n── Summary ──────────────────────────────")
    for name, data in scenarios.items():
        _process_scenario(name, data)


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="DAM Experiment 1 — Boundary Precision Scan")
    parser.add_argument("--trials", type=int, default=20, help="Trials per (scenario, level)")
    parser.add_argument("--outdir", type=str, default="data/exp1_boundary_scan")
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    all_rows: list[dict] = []

    print("=== L1-A: Joint Angle Offset ===")
    all_rows += scan_l1_joint_offset(args.trials)

    print("=== L1-B: Velocity Scale ===")
    all_rows += scan_l1_velocity_scale(args.trials)

    print("=== L2-A: Collision Distance ===")
    all_rows += scan_l2_collision_distance(args.trials)

    print("=== L2-B: Timeout Trigger ===")
    all_rows += scan_l2_timeout(args.trials)

    write_csv(all_rows, outdir / "results.csv")
    plot_results(all_rows, outdir)
    compute_summary(all_rows)

    total = sum(r["trials"] for r in all_rows)
    print(f"\nDone. Total trials: {total}")


if __name__ == "__main__":
    main()
