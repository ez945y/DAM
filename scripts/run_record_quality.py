"""Experiment — Failure Record Quality (實驗：失效紀錄品質 / RQ5).

Drives the real guard stack through violating scenarios spanning all three
event categories (感知異常 / 動作風險 / 硬體風險), builds a failure record from
each *real* interception using the shared production classifier
(:mod:`dam.runtime.failure_classify`), then scores the harvested records on:

  * required_fields     — every mandatory field present and non-empty
  * event_classification — failure_type is a valid taxonomy value
  * layer_labels        — every layer label is well-formed (``L<digit>``)
  * readable_reason     — every reason is human-readable (non-trivial text)
  * obs_window          — observation channels were captured with the record
  * semantic_diversity  — distinct (event type, guard set) combinations

Every number is computed from real ``guard.check()`` output — nothing is
hardcoded.

Usage
-----
    python scripts/run_record_quality.py [--trials N] [--seed S] [--outdir PATH]
"""

from __future__ import annotations

import argparse
import csv
import re
import time
from pathlib import Path
from typing import Any

import numpy as np

from dam.runtime.failure_classify import classify_failure, select_failure_results
from dam.types.action import ActionProposal
from dam.types.observation import Observation

_N_JOINTS = 6
_JOINT_UPPER = np.array([1.8243, 1.7691, 1.6026, 1.8067, 3.0741, 1.7453])
_JOINT_LOWER = -_JOINT_UPPER.copy()
_JOINT_LOWER[-1] = 0.0
_MAX_VEL = np.full(_N_JOINTS, 1.5)
_WORKSPACE_BOUNDS = [[-0.40, 0.40], [-0.40, 0.40], [0.02, 0.60]]
_T_TIMEOUT = 2.0
_MOTION_KWARGS = dict(
    upper=_JOINT_UPPER.tolist(),
    lower=_JOINT_LOWER.tolist(),
    max_velocity=_MAX_VEL.tolist(),
    bounds=_WORKSPACE_BOUNDS,
)
_LAYER_RE = re.compile(r"^L\d$")
_VALID_TYPES = {"ood_only", "guard_triggered", "hardware_triggered"}


def _obs(channels: dict[str, list[float]], **kw: Any) -> Observation:
    return Observation(
        timestamp=time.monotonic(),
        joint_positions=kw.get("joint_positions", np.zeros(_N_JOINTS)),
        joint_velocities=kw.get("joint_velocities", np.zeros(_N_JOINTS)),
        end_effector_pose=kw.get(
            "ee_pose", np.array([0.0, 0.0, 0.30, 0, 0, 0, 1], dtype=np.float64)
        ),
        metadata={"channels": sorted(channels)},
    )


def _record(cycle_id: int, obs: Observation, guard_results: list[Any]) -> dict[str, Any] | None:
    failures = select_failure_results(guard_results)
    failure_type = classify_failure(failures)
    if failure_type is None:
        return None
    return {
        "cycle_id": cycle_id,
        "trace_id": f"rq5-{cycle_id}",
        "timestamp": obs.timestamp,
        "failure_type": failure_type,
        "guard_names": [r.guard_name for r in failures],
        "layers": [f"L{int(r.layer)}" for r in failures],
        "decisions": [r.decision.name for r in failures],
        "reasons": [r.reason for r in failures],
        "fault_sources": [r.fault_source for r in failures],
        "observation_channels": list(obs.metadata.get("channels", [])),
    }


def _violating_records(rng: np.random.Generator, trials: int) -> list[dict[str, Any]]:
    import dam
    from dam.boundary.callbacks.hardware import host_health_limit
    from dam.boundary.constraint import BoundaryConstraint
    from dam.boundary.node import BoundaryNode
    from dam.boundary.single import SingleNodeContainer
    from dam.guard.builtin.execution import ExecutionGuard
    from dam.guard.builtin.motion import MotionGuard
    from dam.guard.builtin.ood import OODGuard

    motion = dam.guard("L1")(MotionGuard)()
    motion._guard_name = "motion"
    execution = dam.guard("L2")(ExecutionGuard)()
    execution._guard_name = "execution"
    ood = dam.guard("L0")(OODGuard)(backend="welford")
    ood._guard_name = "ood"
    for _ in range(80):  # warm up the online OOD baseline on nominal frames
        ood.check(obs=_obs({"joint": [0.0]}, joint_positions=rng.uniform(-0.2, 0.2, _N_JOINTS)))

    node = BoundaryNode(
        node_id="rq5_timeout",
        constraint=BoundaryConstraint(params={}),
        fallback="emergency_stop",
        timeout_sec=_T_TIMEOUT,
    )
    container = SingleNodeContainer(node=node)

    records: list[dict[str, Any]] = []
    cid = 0
    for _ in range(trials):
        # 動作風險: joint position far beyond the upper limit (L1).
        o = _obs({"joint_positions": [0.0]}, joint_positions=_JOINT_UPPER * 1.8)
        a = ActionProposal(target_joint_positions=_JOINT_UPPER * 1.8)
        r = _record(cid, o, [motion.check(obs=o, action=a, **_MOTION_KWARGS)])
        if r:
            records.append(r)
        cid += 1

        # 動作風險: commanded velocity far over the limit (L1).
        o = _obs({"joint_velocities": [0.0]}, joint_velocities=_MAX_VEL * 4.0)
        a = ActionProposal(
            target_joint_positions=np.zeros(_N_JOINTS),
            target_joint_velocities=_MAX_VEL * 4.0,
        )
        r = _record(cid, o, [motion.check(obs=o, action=a, **_MOTION_KWARGS)])
        if r:
            records.append(r)
        cid += 1

        # 動作風險: node far past its timeout (L2).
        o = _obs({"timing": [0.0]})
        res = execution.check(
            obs=o,
            active_containers=[container],
            node_start_times={"rq5_timeout": time.monotonic() - 3.0 * _T_TIMEOUT},
        )
        r = _record(cid, o, [res])
        if r:
            records.append(r)
        cid += 1

        # 硬體風險: host health breach via the real L3 callback.
        o = _obs({"host_health": [0.0]})
        res = host_health_limit(
            host_health={"cpu_percent": 99.7, "memory_percent": 50.0, "temperature_c": 40.0},
        )
        r = _record(cid, o, [res])
        if r:
            records.append(r)
        cid += 1

        # 感知異常: extreme out-of-distribution observation (L0).
        o = _obs({"joint_positions": [0.0]}, joint_positions=np.full(_N_JOINTS, 50.0))
        res = ood.check(obs=o)
        r = _record(cid, o, [res])
        if r:
            records.append(r)
        cid += 1

    return records


def _score(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    n = len(records)
    if n == 0:
        return []

    def rate(pred: Any) -> float:
        return sum(1 for rec in records if pred(rec)) / n

    required = ("cycle_id", "trace_id", "timestamp", "failure_type", "guard_names", "layers")
    combos = {(r["failure_type"], tuple(sorted(r["guard_names"]))) for r in records}
    types_present = {r["failure_type"] for r in records}

    return [
        {
            "metric": "required_fields",
            "rate": round(rate(lambda r: all(r.get(k) not in (None, "", []) for k in required)), 4),
            "count": n,
        },
        {
            "metric": "event_classification",
            "rate": round(rate(lambda r: r["failure_type"] in _VALID_TYPES), 4),
            "count": n,
        },
        {
            "metric": "layer_labels",
            "rate": round(
                rate(lambda r: bool(r["layers"]) and all(_LAYER_RE.match(x) for x in r["layers"])),
                4,
            ),
            "count": n,
        },
        {
            "metric": "readable_reason",
            "rate": round(
                rate(
                    lambda r: (
                        bool(r["reasons"])
                        and all(isinstance(x, str) and len(x.strip()) >= 8 for x in r["reasons"])
                    )
                ),
                4,
            ),
            "count": n,
        },
        {
            "metric": "obs_window",
            "rate": round(rate(lambda r: bool(r["observation_channels"])), 4),
            "count": n,
        },
        {
            # Coverage of the 3-category event taxonomy across harvested
            # records (1.0 = 感知異常 + 動作風險 + 硬體風險 all represented).
            "metric": "semantic_diversity",
            "rate": round(len(types_present) / len(_VALID_TYPES), 4),
            "count": len(combos),
        },
    ]


def run_quality(trials: int, seed: int) -> list[dict[str, Any]]:
    rng = np.random.default_rng(seed)
    return _score(_violating_records(rng, trials))


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    if not rows:
        path.write_text("")
        return
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"CSV saved: {path}")


def plot_results(rows: list[dict[str, Any]], outdir: Path) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed — skipping plot generation.")
        return
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar([r["metric"] for r in rows], [r["rate"] for r in rows], color="#10b981")
    ax.set_ylabel("Quality rate")
    ax.set_ylim(0, 1.05)
    ax.set_title("Failure Record Quality (RQ5)")
    ax.tick_params(axis="x", rotation=30)
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    out = outdir / "failure_record_quality.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print(f"Plot saved: {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description="DAM Failure Record Quality (RQ5)")
    parser.add_argument("--trials", type=int, default=40)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--outdir", type=str, default="data/experiments/failure_record_quality")
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    rows = run_quality(args.trials, args.seed)
    for r in rows:
        print(f"  {r['metric']:<22} rate={r['rate']:.4f}  count={r['count']}")
    write_csv(rows, outdir / "results.csv")
    plot_results(rows, outdir)


if __name__ == "__main__":
    main()
