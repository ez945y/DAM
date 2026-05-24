#!/usr/bin/env python3
"""Joint velocity diagnostics — run 200 cycles and analyse per-joint behavior.

Usage:
    # Run fresh session + analyse:
    .venv/bin/python scripts/joint_diagnostics.py

    # Analyse an existing MCAP file:
    .venv/bin/python scripts/joint_diagnostics.py --mcap data/robot/sessions/session_xxx.mcap

    # Custom cycles / stackfile:
    .venv/bin/python scripts/joint_diagnostics.py --cycles 200 --stack examples/stackfiles/demo.yaml
"""

from __future__ import annotations

import argparse
import glob
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent

try:
    import msgpack
except ImportError:
    print("msgpack required: pip install msgpack", file=sys.stderr)
    sys.exit(1)

try:
    from mcap.reader import make_reader
except ImportError:
    print("mcap required: pip install mcap", file=sys.stderr)
    sys.exit(1)


def _latest_mcap(session_dir: Path) -> Path | None:
    files = sorted(session_dir.glob("*.mcap"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def run_session(stack: str, task: str, cycles: int) -> Path | None:
    """Run dam CLI and return the MCAP file produced."""
    session_dir = ROOT / "data" / "robot" / "sessions"
    before = set(session_dir.glob("*.mcap")) if session_dir.exists() else set()

    cmd = [
        str(ROOT / ".venv" / "bin" / "dam"),
        "run",
        stack,
        "--task",
        task,
        "--cycles",
        str(cycles),
    ]
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    if result.returncode != 0:
        print(f"dam run failed (exit {result.returncode}):", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        return None
    print(result.stdout)

    after = set(session_dir.glob("*.mcap")) if session_dir.exists() else set()
    new_files = after - before
    if not new_files:
        latest = _latest_mcap(session_dir)
        if latest and latest.stat().st_mtime > time.time() - 30:
            return latest
        print("No new MCAP file found after run.", file=sys.stderr)
        return None
    return max(new_files, key=lambda p: p.stat().st_mtime)


def read_cycles(mcap_path: Path) -> list[dict]:
    """Read /dam/cycle messages from an MCAP file."""
    cycles = []
    with open(mcap_path, "rb") as f:
        reader = make_reader(f)
        for _schema, _channel, msg in reader.iter_messages(topics=["/dam/cycle"]):
            data = msgpack.unpackb(msg.data, raw=False)
            cycles.append(data)
    return cycles


def analyse(cycles: list[dict], control_freq: float = 30.0) -> dict:
    """Compute per-joint velocity / acceleration / clamp metrics."""
    dt = 1.0 / control_freq
    n_cycles = len(cycles)
    if n_cycles < 2:
        print("Not enough cycles to analyse.", file=sys.stderr)
        return {}

    n_joints = len(cycles[0].get("obs_joint_positions", []))
    if n_joints == 0:
        print("No joint position data in cycles.", file=sys.stderr)
        return {}

    valid_cycles = [
        c
        for c in cycles
        if len(c.get("obs_joint_positions", [])) == n_joints
        and len(c.get("action_positions", [])) == n_joints
    ]
    if len(valid_cycles) < 2:
        print(f"Only {len(valid_cycles)} valid cycles (need ≥2).", file=sys.stderr)
        return {}
    n_cycles = len(valid_cycles)
    cycles = valid_cycles

    obs = np.array([c["obs_joint_positions"] for c in cycles])
    action = np.array([c["action_positions"] for c in cycles])
    validated = np.array([c.get("validated_positions") or c["action_positions"] for c in cycles])
    was_clamped = [c.get("was_clamped", False) for c in cycles]
    was_rejected = [c.get("was_rejected", False) for c in cycles]

    # Observed velocity: how fast joints actually moved between cycles
    obs_vel = np.diff(obs, axis=0) / dt  # (N-1, J) rad/s
    obs_vel_deg = np.degrees(obs_vel)

    # Commanded velocity: what the policy asked for
    cmd_vel = (action[:-1] - obs[:-1]) / dt  # (N-1, J) rad/s
    cmd_vel_deg = np.degrees(cmd_vel)

    # Validated velocity: what the guard pipeline actually sent
    val_vel = (validated[:-1] - obs[:-1]) / dt
    val_vel_deg = np.degrees(val_vel)

    # Acceleration (jerk proxy): change in observed velocity
    obs_acc = np.diff(obs_vel, axis=0) / dt  # (N-2, J) rad/s²

    # Action delta: how much policy wanted to move each cycle
    action_delta = action - obs  # (N, J) rad
    action_delta_deg = np.degrees(action_delta)

    # Validated delta: how much was actually sent
    validated_delta = validated - obs
    validated_delta_deg = np.degrees(validated_delta)

    # Clamp ratio: how much the guard cut
    clamp_ratio = np.where(
        np.abs(action_delta) > 1e-9,
        np.abs(validated_delta) / (np.abs(action_delta) + 1e-12),
        1.0,
    )

    # Guard result extraction
    guard_vel_results = []
    for c in cycles:
        for gr in c.get("guard_results", []):
            if isinstance(gr, dict) and gr.get("name") == "joint_velocity_limit":
                guard_vel_results.append(gr)
                break
        else:
            guard_vel_results.append(None)

    results = {
        "n_cycles": n_cycles,
        "n_joints": n_joints,
        "dt": dt,
        "obs": obs,
        "action": action,
        "validated": validated,
        "obs_vel": obs_vel,
        "obs_vel_deg": obs_vel_deg,
        "cmd_vel": cmd_vel,
        "cmd_vel_deg": cmd_vel_deg,
        "val_vel": val_vel,
        "val_vel_deg": val_vel_deg,
        "obs_acc": obs_acc,
        "action_delta_deg": action_delta_deg,
        "validated_delta_deg": validated_delta_deg,
        "clamp_ratio": clamp_ratio,
        "was_clamped": was_clamped,
        "was_rejected": was_rejected,
        "guard_vel_results": guard_vel_results,
    }
    return results


def print_report(r: dict) -> None:
    """Print a human-readable diagnostic report."""
    if not r:
        return

    n = r["n_cycles"]
    nj = r["n_joints"]
    dt = r["dt"]
    max_vel_limit = 1.5  # rad/s from stackfile

    print("=" * 72)
    print(f"  JOINT DIAGNOSTICS — {n} cycles, dt={dt * 1000:.1f}ms, {nj} joints")
    print("=" * 72)

    # ── Per-joint summary ─────────────────────────────────────────────────
    print(
        f"\n{'Joint':<8} {'Obs Max°/s':>12} {'Cmd Max°/s':>12} {'Val Max°/s':>12} "
        f"{'Obs Mean°/s':>12} {'Clamp%':>8} {'Overshoot':>10}"
    )
    print("-" * 80)

    for j in range(nj):
        obs_max = np.max(np.abs(r["obs_vel_deg"][:, j]))
        cmd_max = np.max(np.abs(r["cmd_vel_deg"][:, j]))
        val_max = np.max(np.abs(r["val_vel_deg"][:, j]))
        obs_mean = np.mean(np.abs(r["obs_vel_deg"][:, j]))
        # How many cycles was this joint's command > limit?
        over_limit = np.sum(np.abs(r["cmd_vel"][:, j]) > max_vel_limit)
        over_pct = 100.0 * over_limit / (n - 1)

        # Overshoot: observed velocity exceeds validated velocity
        obs_exceeds_val = np.sum(np.abs(r["obs_vel"][:, j]) > np.abs(r["val_vel"][:, j]) + 0.01)

        print(
            f"  J{j + 1:<5} {obs_max:>12.1f} {cmd_max:>12.1f} {val_max:>12.1f} "
            f"{obs_mean:>12.1f} {over_pct:>7.1f}% {obs_exceeds_val:>10d}"
        )

    # ── Clamp statistics ──────────────────────────────────────────────────
    n_clamped = sum(r["was_clamped"])
    n_rejected = sum(r["was_rejected"])
    print(f"\nClamp events: {n_clamped}/{n} ({100 * n_clamped / n:.1f}%)")
    print(f"Reject events: {n_rejected}/{n} ({100 * n_rejected / n:.1f}%)")

    # ── Velocity limit headroom ───────────────────────────────────────────
    print(f"\nVelocity limit = {max_vel_limit:.2f} rad/s = {np.degrees(max_vel_limit):.1f}°/s")
    print(
        f"Max allowed Δpos/cycle = {max_vel_limit * dt:.4f} rad = {np.degrees(max_vel_limit * dt):.2f}°"
    )

    # ── Top 10 worst cycles for J1, J2 ────────────────────────────────────
    for j in [0, 1]:
        print(f"\n── Top 10 fastest cycles for J{j + 1} (by commanded velocity) ──")
        cmd_abs = np.abs(r["cmd_vel_deg"][:, j])
        top_idx = np.argsort(cmd_abs)[-10:][::-1]
        print(
            f"  {'Cycle':>6} {'Cmd°/s':>10} {'Val°/s':>10} {'Obs°/s':>10} "
            f"{'Δcmd°':>8} {'Δval°':>8} {'Clamped':>8}"
        )
        for i in top_idx:
            cyc = i + 1  # cycle number (1-based, since velocity is diff-based)
            print(
                f"  {cyc:>6} {r['cmd_vel_deg'][i, j]:>10.2f} "
                f"{r['val_vel_deg'][i, j]:>10.2f} "
                f"{r['obs_vel_deg'][i, j]:>10.2f} "
                f"{r['action_delta_deg'][i, j]:>8.3f} "
                f"{r['validated_delta_deg'][i, j]:>8.3f} "
                f"{'YES' if r['was_clamped'][i] else 'no':>8}"
            )

    # ── Acceleration spikes (J1, J2) ──────────────────────────────────────
    for j in [0, 1]:
        acc_deg = np.degrees(r["obs_acc"][:, j])
        print(f"\n── J{j + 1} acceleration (observed, °/s²) ──")
        print(
            f"  max: {np.max(np.abs(acc_deg)):.1f}  mean: {np.mean(np.abs(acc_deg)):.1f}  "
            f"std: {np.std(acc_deg):.1f}"
        )

    # ── Position range ────────────────────────────────────────────────────
    print("\n── Position range (observed, degrees) ──")
    for j in range(nj):
        pos_deg = np.degrees(r["obs"][:, j])
        print(
            f"  J{j + 1}: [{pos_deg.min():.1f}°, {pos_deg.max():.1f}°]  "
            f"Δ={pos_deg.max() - pos_deg.min():.1f}°"
        )

    # ── Enforcement check ─────────────────────────────────────────────────
    print("\n── Enforcement check ──")
    bypassed = 0
    for i in range(n - 1):
        if r["was_clamped"][i]:
            act_d = r["action_delta_deg"][i]
            val_d = r["validated_delta_deg"][i]
            if np.allclose(act_d, val_d, atol=0.01):
                bypassed += 1
    if bypassed > 0:
        print(
            f"  ⚠ {bypassed} cycles marked clamped but action == validated "
            f"(enforcement_mode may be monitor)"
        )
    else:
        print("  ✓ All clamped cycles have action ≠ validated (enforce mode working)")

    print("\n" + "=" * 72)


def main() -> None:
    parser = argparse.ArgumentParser(description="Joint velocity diagnostics")
    parser.add_argument("--mcap", type=str, help="Analyse an existing MCAP file")
    parser.add_argument(
        "--stack",
        default=".dam_stackfile.yaml",
        help="Stackfile path (default: .dam_stackfile.yaml)",
    )
    parser.add_argument("--task", default="demo", help="Task name")
    parser.add_argument("--cycles", type=int, default=200, help="Cycles to run")
    parser.add_argument(
        "--freq", type=float, default=30.0, help="Control frequency Hz (for dt calculation)"
    )
    args = parser.parse_args()

    mcap_path = Path(args.mcap) if args.mcap else run_session(args.stack, args.task, args.cycles)

    if mcap_path is None or not mcap_path.exists():
        print("No MCAP file to analyse.", file=sys.stderr)
        sys.exit(1)

    print(f"\nReading: {mcap_path}")
    cycles = read_cycles(mcap_path)
    print(f"Loaded {len(cycles)} cycles")

    r = analyse(cycles, control_freq=args.freq)
    print_report(r)


if __name__ == "__main__":
    main()
