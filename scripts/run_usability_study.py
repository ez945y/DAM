"""Experiment — Normal-Use False Trigger Study (實驗：正常使用誤觸發研究 / RQ3).

Drives the real L0–L2 guard stack with *benign* observations that stay inside
every configured limit, across legal deployment variations (object shift,
natural lighting jitter, an operator passing by, a small payload). A safe
system should let all of these through; any REJECT / FAULT / CLAMP on a benign
frame is counted as a false trigger.

Numbers come from real ``guard.check()`` decisions — nothing is hardcoded.

Usage
-----
    python scripts/run_usability_study.py [--trials-per-scenario N] [--seed S]
                                          [--outdir PATH]
"""

from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path

import numpy as np

from dam.types.action import ActionProposal
from dam.types.observation import Observation
from dam.types.result import GuardDecision

_N_JOINTS = 6
_JOINT_UPPER = np.array([1.8243, 1.7691, 1.6026, 1.8067, 3.0741, 1.7453])
_JOINT_LOWER = -_JOINT_UPPER.copy()
_JOINT_LOWER[-1] = 0.0
_MAX_VEL = np.full(_N_JOINTS, 1.5)
_WORKSPACE_BOUNDS = [[-0.40, 0.40], [-0.40, 0.40], [0.02, 0.60]]

_MOTION_KWARGS = dict(
    upper=_JOINT_UPPER.tolist(),
    lower=_JOINT_LOWER.tolist(),
    max_velocity=_MAX_VEL.tolist(),
    bounds=_WORKSPACE_BOUNDS,
)

# Each scenario perturbs a benign nominal frame within safe limits.
SCENARIOS = (
    "standard",
    "object_shift",
    "natural_light",
    "operator_passby",
    "small_payload",
)


def _make_guards() -> dict[str, object]:
    import dam
    from dam.guard.builtin.execution import ExecutionGuard
    from dam.guard.builtin.motion import MotionGuard
    from dam.guard.builtin.ood import OODGuard

    ood = dam.guard("L0")(OODGuard)(backend="welford")
    ood._guard_name = "ood"
    motion = dam.guard("L1")(MotionGuard)()
    motion._guard_name = "motion"
    execution = dam.guard("L2")(ExecutionGuard)()
    execution._guard_name = "execution"
    return {"ood": ood, "motion": motion, "execution": execution}


def _benign_obs(scenario: str, rng: np.random.Generator) -> Observation:
    """A nominal in-limits observation, perturbed for the given legal variation."""
    pos = rng.uniform(_JOINT_LOWER * 0.4, _JOINT_UPPER * 0.4)
    vel = rng.uniform(-_MAX_VEL * 0.25, _MAX_VEL * 0.25)
    ee = np.array([0.0, 0.0, 0.30, 0.0, 0.0, 0.0, 1.0], dtype=np.float64)
    ft = np.zeros(6)

    if scenario == "object_shift":
        ee[0] += rng.uniform(-0.12, 0.12)  # still well inside ±0.40 workspace
        ee[1] += rng.uniform(-0.12, 0.12)
    elif scenario == "natural_light":
        pos = pos + rng.normal(0.0, 0.01, _N_JOINTS)  # tiny perception jitter
    elif scenario == "operator_passby":
        vel = vel + rng.normal(0.0, 0.15, _N_JOINTS)  # transient, still ≤ max_vel
        vel = np.clip(vel, -_MAX_VEL * 0.6, _MAX_VEL * 0.6)
    elif scenario == "small_payload":
        ft = np.array([rng.uniform(-4.0, 4.0) for _ in range(3)] + [0.0, 0.0, 0.0])

    return Observation(
        timestamp=time.monotonic(),
        joint_positions=pos,
        joint_velocities=vel,
        end_effector_pose=ee,
        force_torque=ft,
    )


def _intercepted(result: object) -> bool:
    return getattr(result, "decision", None) in (
        GuardDecision.REJECT,
        GuardDecision.FAULT,
        GuardDecision.CLAMP,
    )


def run_scenario(name: str, trials: int, rng: np.random.Generator) -> dict:
    guards = _make_guards()
    # Warm up the online OOD estimator on nominal frames so it has a baseline
    # distribution before we measure benign-frame false triggers.
    for _ in range(max(60, trials)):
        guards["ood"].check(obs=_benign_obs("standard", rng))  # type: ignore[union-attr]

    false_triggers = 0
    successes = 0
    for _ in range(trials):
        obs = _benign_obs(name, rng)
        action = ActionProposal(
            target_joint_positions=obs.joint_positions,
            target_joint_velocities=obs.joint_velocities,
        )
        tripped = (
            _intercepted(guards["ood"].check(obs=obs))  # type: ignore[union-attr]
            or _intercepted(
                guards["motion"].check(obs=obs, action=action, **_MOTION_KWARGS)  # type: ignore[union-attr]
            )
            or _intercepted(
                guards["execution"].check(  # type: ignore[union-attr]
                    obs=obs, active_containers=[], node_start_times={}
                )
            )
        )
        if tripped:
            false_triggers += 1
        else:
            successes += 1

    return {
        "scenario": name,
        "trials": trials,
        "false_triggers": false_triggers,
        "false_trigger_rate": false_triggers / trials if trials else 0.0,
        "success_rate": successes / trials if trials else 0.0,
    }


def run_study(trials_per_scenario: int, seed: int) -> list[dict]:
    rng = np.random.default_rng(seed)
    return [run_scenario(name, trials_per_scenario, rng) for name in SCENARIOS]


def write_csv(rows: list[dict], path: Path) -> None:
    if not rows:
        path.write_text("")
        return
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"CSV saved: {path}")


def plot_results(rows: list[dict], outdir: Path) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed — skipping plot generation.")
        return
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(
        [r["scenario"] for r in rows],
        [r["false_trigger_rate"] * 100 for r in rows],
        color="#3b82f6",
    )
    ax.set_ylabel("False trigger rate (%)")
    ax.set_title("Normal-Use False Trigger Study (RQ3)")
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    out = outdir / "usability_false_triggers.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print(f"Plot saved: {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description="DAM Normal-Use False Trigger Study (RQ3)")
    parser.add_argument("--trials-per-scenario", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--outdir", type=str, default="data/experiments/usability")
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    rows = run_study(args.trials_per_scenario, args.seed)
    for r in rows:
        print(
            f"  {r['scenario']:<16} false={r['false_triggers']}/{r['trials']}  "
            f"success={r['success_rate']:.3f}"
        )
    write_csv(rows, outdir / "results.csv")
    plot_results(rows, outdir)


if __name__ == "__main__":
    main()
