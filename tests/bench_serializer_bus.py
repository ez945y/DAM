#!/usr/bin/env python3
"""Benchmark SerializerBus (Rust JSON) vs Python json.dumps().

Compares:
  1. Python json.dumps() baseline
  2. Rust SerializerBus.serialize_cycle()
  3. Measures serialization time per cycle
  4. Calculates performance gain

Usage:
  python tests/bench_serializer_bus.py [--cycles N]
"""

import argparse
import json
import logging
import sys
import time
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dam_rs import SerializerBus

from dam.guard.layer import GuardLayer
from dam.logging.console import setup_colored_logging
from dam.logging.cycle_record import CycleRecord
from dam.logging.loopback_writer import _json
from dam.types.result import GuardDecision, GuardResult

setup_colored_logging(
    level=logging.INFO,
    fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)


def create_test_cycle(
    cycle_id: int,
    has_violation: bool = False,
    num_guards: int = 5,
) -> CycleRecord:
    """Create a realistic test CycleRecord."""
    guard_results = []
    for i in range(num_guards):
        decision = GuardDecision.REJECT if has_violation else GuardDecision.PASS
        result = GuardResult(
            decision=decision,
            guard_name=f"guard_{i}",
            layer=GuardLayer(i % 5),
            reason="test" if has_violation else "pass",
            fault_source=None,
        )
        guard_results.append(result)

    return CycleRecord(
        cycle_id=cycle_id,
        trace_id=f"trace_{cycle_id}",
        triggered_at=time.time(),
        active_task="test_task",
        active_boundaries=("boundary_0", "boundary_1"),
        obs_timestamp=time.time(),
        obs_joint_positions=[0.1] * 7,
        obs_joint_velocities=[0.05] * 7,
        obs_end_effector_pose=[1.0, 2.0, 3.0, 0.0, 0.0, 0.0, 1.0],
        obs_force_torque=[0.1] * 6,
        obs_metadata={},
        action_positions=[0.1] * 7,
        action_velocities=[0.05] * 7,
        validated_positions=[0.1] * 7,
        validated_velocities=[0.05] * 7,
        was_clamped=False,
        fallback_triggered=None,
        guard_results=tuple(guard_results),
        latency_stages={
            "source": 1.0,
            "policy": 2.0,
            "guards": 3.0,
            "sink": 0.5,
            "total": 6.5,
        },
        latency_layers={
            "L0": 0.5,
            "L1": 0.6,
            "L2": 0.7,
            "L3": 0.8,
            "L4": 0.9,
        },
        latency_guards={f"guard_{i}": 1.0 + i * 0.1 for i in range(num_guards)},
        has_violation=has_violation,
        has_clamp=False,
        violated_layer_mask=0x1F if has_violation else 0,
        clamped_layer_mask=0,
    )


def benchmark_python_serialization(num_cycles: int = 1000) -> dict:
    """Benchmark Python json.dumps() serialization."""
    logger.info("=== Benchmarking Python json.dumps() ===")
    logger.info(f"Cycles: {num_cycles}")

    times = []

    for cycle_idx in range(num_cycles):
        rec = create_test_cycle(cycle_idx, has_violation=(cycle_idx % 20 == 0))

        # Time the total serialization (all 6 message types)
        t0 = time.perf_counter_ns()

        # 1. /dam/cycle
        cycle_msg = {
            "cycle_id": rec.cycle_id,
            "timestamp": rec.obs_timestamp,
            "active_task": rec.active_task,
            "active_boundaries": list(rec.active_boundaries),
            "has_violation": rec.has_violation,
            "has_clamp": rec.has_clamp,
            "violated_layer_mask": rec.violated_layer_mask,
            "clamped_layer_mask": rec.clamped_layer_mask,
            "source_ms": rec.latency_stages.get("source", 0.0),
            "policy_ms": rec.latency_stages.get("policy", 0.0),
            "guards_ms": rec.latency_stages.get("guards", 0.0),
            "sink_ms": rec.latency_stages.get("sink", 0.0),
            "total_ms": rec.latency_stages.get("total", 0.0),
        }
        _json(cycle_msg)

        # 2. /dam/obs
        obs_msg = {
            "cycle_id": rec.cycle_id,
            "timestamp": rec.obs_timestamp,
            "joint_positions": rec.obs_joint_positions,
        }
        if rec.obs_joint_velocities:
            obs_msg["joint_velocities"] = rec.obs_joint_velocities
        if rec.obs_end_effector_pose:
            obs_msg["end_effector_pose"] = rec.obs_end_effector_pose
        if rec.obs_force_torque:
            obs_msg["force_torque"] = rec.obs_force_torque
        _json(obs_msg)

        # 3. /dam/action
        action_msg = {
            "cycle_id": rec.cycle_id,
            "timestamp": rec.obs_timestamp,
            "target_positions": rec.action_positions,
            "was_clamped": rec.was_clamped,
            "fallback_triggered": rec.fallback_triggered,
        }
        if rec.action_velocities:
            action_msg["target_velocities"] = rec.action_velocities
        if rec.validated_positions:
            action_msg["validated_positions"] = rec.validated_positions
        if rec.validated_velocities:
            action_msg["validated_velocities"] = rec.validated_velocities
        _json(action_msg)

        # 4. /dam/L0-L4
        for result in rec.guard_results:
            layer_int = int(result.layer)
            is_violation = result.decision in (GuardDecision.REJECT, GuardDecision.FAULT)
            is_clamp = result.decision == GuardDecision.CLAMP
            guard_msg = {
                "cycle_id": rec.cycle_id,
                "timestamp": rec.obs_timestamp,
                "guard_name": result.guard_name,
                "layer": layer_int,
                "decision": int(result.decision),
                "decision_name": result.decision.name,
                "reason": result.reason,
                "latency_ms": rec.latency_guards.get(result.guard_name),
                "is_violation": is_violation,
                "is_clamp": is_clamp,
                "fault_source": result.fault_source,
            }
            _json(guard_msg)

        # 5. /dam/latency
        latency_msg = {
            "cycle_id": rec.cycle_id,
            "timestamp": rec.obs_timestamp,
        }
        for key in ("source", "policy", "guards", "sink", "total"):
            latency_msg[f"{key}_ms"] = rec.latency_stages.get(key, 0.0)
        for key in ("L0", "L1", "L2", "L3", "L4"):
            latency_msg[f"{key}_ms"] = rec.latency_layers.get(key, 0.0)
        _json(latency_msg)

        elapsed_ns = time.perf_counter_ns() - t0
        times.append(elapsed_ns / 1000)  # Convert to microseconds

    return {
        "num_cycles": num_cycles,
        "times_us": times,
    }


def benchmark_rust_serializer(num_cycles: int = 1000) -> dict:
    """Benchmark Rust SerializerBus.serialize_cycle()."""
    logger.info("=== Benchmarking Rust SerializerBus ===")
    logger.info(f"Cycles: {num_cycles}")

    serializer = SerializerBus()
    times = []

    for cycle_idx in range(num_cycles):
        rec = create_test_cycle(cycle_idx, has_violation=(cycle_idx % 20 == 0))

        # Convert CycleRecord to dict (this overhead is counted)
        record_dict = asdict(rec)

        # Convert datetime/objects to serializable types
        record_dict["active_boundaries"] = list(record_dict["active_boundaries"])

        # guard_results are already dicts from asdict()
        # Just add the derived fields
        for _i, g_dict in enumerate(record_dict["guard_results"]):
            decision_int = g_dict["decision"]
            g_dict["decision"] = decision_int  # Keep as int
            g_dict["decision_name"] = GuardDecision(decision_int).name
            g_dict["is_violation"] = decision_int in (
                int(GuardDecision.REJECT),
                int(GuardDecision.FAULT),
            )
            g_dict["is_clamp"] = decision_int == int(GuardDecision.CLAMP)

        t0 = time.perf_counter_ns()
        _ = serializer.serialize_cycle(record_dict)
        elapsed_ns = time.perf_counter_ns() - t0

        times.append(elapsed_ns / 1000)  # Convert to microseconds

    return {
        "num_cycles": num_cycles,
        "times_us": times,
    }


def print_results(python_results: dict, rust_results: dict) -> None:
    """Print comparison results."""
    py_times = sorted(python_results["times_us"])
    rs_times = sorted(rust_results["times_us"])

    py_mean = sum(py_times) / len(py_times)
    py_p95 = py_times[int(0.95 * len(py_times))]
    py_p99 = py_times[int(0.99 * len(py_times))]

    rs_mean = sum(rs_times) / len(rs_times)
    rs_p95 = rs_times[int(0.95 * len(rs_times))]
    rs_p99 = rs_times[int(0.99 * len(rs_times))]

    py_total = sum(py_times) / 1000  # Convert to ms
    rs_total = sum(rs_times) / 1000  # Convert to ms

    gain_mean = ((py_mean - rs_mean) / py_mean) * 100
    gain_p95 = ((py_p95 - rs_p95) / py_p95) * 100
    gain_p99 = ((py_p99 - rs_p99) / py_p99) * 100
    gain_total = ((py_total - rs_total) / py_total) * 100

    print("\n" + "=" * 80)
    print("SERIALIZATION BENCHMARK RESULTS")
    print("=" * 80)

    print("\nPython json.dumps():")
    print(f"  Cycles:    {python_results['num_cycles']}")
    print(f"  Mean:      {py_mean:.2f} µs")
    print(f"  P95:       {py_p95:.2f} µs")
    print(f"  P99:       {py_p99:.2f} µs")
    print(f"  Total:     {py_total:.2f} ms")

    print("\nRust SerializerBus:")
    print(f"  Cycles:    {rust_results['num_cycles']}")
    print(f"  Mean:      {rs_mean:.2f} µs")
    print(f"  P95:       {rs_p95:.2f} µs")
    print(f"  P99:       {rs_p99:.2f} µs")
    print(f"  Total:     {rs_total:.2f} ms")

    print("\nPerformance Gain:")
    print(f"  Mean:      {gain_mean:+.1f}%")
    print(f"  P95:       {gain_p95:+.1f}%")
    print(f"  P99:       {gain_p99:+.1f}%")
    print(f"  Total:     {gain_total:+.1f}%")

    print("\n" + "=" * 80)
    if gain_mean >= 20:
        print(f"✅ PASS: {gain_mean:.1f}% gain exceeds 20% threshold")
    else:
        print(f"❌ FAIL: {gain_mean:.1f}% gain below 20% threshold")
    print("=" * 80)

    return gain_mean >= 20


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=1000, help="Number of cycles to benchmark")
    args = parser.parse_args()

    python_results = benchmark_python_serialization(num_cycles=args.cycles)
    rust_results = benchmark_rust_serializer(num_cycles=args.cycles)

    passed = print_results(python_results, rust_results)
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
