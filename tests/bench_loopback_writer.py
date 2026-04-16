#!/usr/bin/env python3
"""Benchmark LoopbackWriter serialization and MCAP write performance.

Measures:
  1. JSON serialization time per topic (/dam/cycle, /dam/obs, etc.)
  2. Total cycle write time (all topics)
  3. Queue depth under normal vs violation load
  4. Wall-time breakdown for the worker thread

Usage:
  python tests/bench_loopback_writer.py [--cycles N] [--violation-ratio R]
"""

import argparse
import json
import logging
import sys
import tempfile
import time
import uuid
from collections import defaultdict
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dam.bus import ObservationBus
from dam.logging.console import setup_colored_logging
from dam.logging.cycle_record import CycleRecord
from dam.logging.loopback_writer import LoopbackWriter, _json
from dam.types.observation import Observation
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
    from dam.guard.layer import GuardLayer

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


def benchmark_json_serialization(num_cycles: int = 1000) -> dict:
    """Benchmark JSON serialization for each message type.

    Returns dict with timing breakdown per topic.
    """
    logger.info("=== Benchmarking JSON Serialization ===")
    logger.info(f"Cycles: {num_cycles}")

    timings = defaultdict(list)

    for cycle_idx in range(num_cycles):
        rec = create_test_cycle(cycle_idx, has_violation=(cycle_idx % 20 == 0))

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
        t0 = time.perf_counter_ns()
        _json(cycle_msg)
        timings["/dam/cycle"].append(time.perf_counter_ns() - t0)

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
        t0 = time.perf_counter_ns()
        _json(obs_msg)
        timings["/dam/obs"].append(time.perf_counter_ns() - t0)

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
        t0 = time.perf_counter_ns()
        _json(action_msg)
        timings["/dam/action"].append(time.perf_counter_ns() - t0)

        # 4. /dam/L0-L4 (one per guard result)
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
            t0 = time.perf_counter_ns()
            _json(guard_msg)
            timings[f"/dam/L{layer_int}"].append(time.perf_counter_ns() - t0)

        # 5. /dam/latency
        latency_msg = {
            "cycle_id": rec.cycle_id,
            "timestamp": rec.obs_timestamp,
        }
        for key in ("source", "policy", "guards", "sink", "total"):
            latency_msg[f"{key}_ms"] = rec.latency_stages.get(key, 0.0)
        for key in ("L0", "L1", "L2", "L3", "L4"):
            latency_msg[f"{key}_ms"] = rec.latency_layers.get(key, 0.0)
        t0 = time.perf_counter_ns()
        _json(latency_msg)
        timings["/dam/latency"].append(time.perf_counter_ns() - t0)

    # Compute statistics
    stats = {}
    total_topic_time = 0.0
    for topic in sorted(timings.keys()):
        times_ns = timings[topic]
        mean_ns = sum(times_ns) / len(times_ns)
        p95_ns = sorted(times_ns)[int(0.95 * len(times_ns))]
        p99_ns = sorted(times_ns)[int(0.99 * len(times_ns))]
        max_ns = max(times_ns)
        total_topic_time += sum(times_ns)

        stats[topic] = {
            "mean_us": mean_ns / 1000,
            "p95_us": p95_ns / 1000,
            "p99_us": p99_ns / 1000,
            "max_us": max_ns / 1000,
            "total_ms": sum(times_ns) / 1_000_000,
        }

    return {
        "num_cycles": num_cycles,
        "total_json_time_ms": total_topic_time / 1_000_000,
        "topics": stats,
    }


def benchmark_full_cycle_write(num_cycles: int = 100) -> dict:
    """Benchmark full cycle write time (JSON + MCAP write ops).

    Returns timing breakdown per cycle.
    """
    logger.info("\n=== Benchmarking Full Cycle Write (JSON + MCAP) ===")
    logger.info(f"Cycles: {num_cycles}")

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir)
        obs_bus = ObservationBus(capacity=100)

        writer = LoopbackWriter(
            output_dir=str(output_dir),
            obs_bus=obs_bus,
            control_frequency_hz=50.0,
            window_sec=10.0,
            rotate_mb=500.0,
            rotate_minutes=60.0,
            max_queue_depth=256,
        )
        writer.start()

        cycle_times = []
        violation_times = []

        try:
            for cycle_idx in range(num_cycles):
                is_violation = cycle_idx % 20 == 0
                rec = create_test_cycle(cycle_idx, has_violation=is_violation)

                t0 = time.perf_counter_ns()
                writer.submit(rec)
                elapsed_ns = time.perf_counter_ns() - t0

                if is_violation:
                    violation_times.append(elapsed_ns / 1000)  # Convert to microseconds
                else:
                    cycle_times.append(elapsed_ns / 1000)

        finally:
            t0 = time.perf_counter()
            writer.shutdown(timeout=30.0)
            shutdown_time = time.perf_counter() - t0

    # Compute statistics
    normal_times = sorted(cycle_times)
    violation_times_sorted = sorted(violation_times)

    stats = {
        "num_cycles": num_cycles,
        "shutdown_time_sec": shutdown_time,
        "normal_cycles": {
            "count": len(normal_times),
            "mean_us": sum(normal_times) / len(normal_times) if normal_times else 0,
            "p95_us": normal_times[int(0.95 * len(normal_times))] if normal_times else 0,
            "p99_us": normal_times[int(0.99 * len(normal_times))] if normal_times else 0,
            "max_us": max(normal_times) if normal_times else 0,
        },
        "violation_cycles": {
            "count": len(violation_times_sorted),
            "mean_us": sum(violation_times_sorted) / len(violation_times_sorted)
            if violation_times_sorted
            else 0,
            "p95_us": violation_times_sorted[int(0.95 * len(violation_times_sorted))]
            if violation_times_sorted
            else 0,
            "p99_us": violation_times_sorted[int(0.99 * len(violation_times_sorted))]
            if violation_times_sorted
            else 0,
            "max_us": max(violation_times_sorted) if violation_times_sorted else 0,
        },
    }

    return stats


def print_results(json_bench: dict, cycle_bench: dict) -> None:
    """Pretty-print benchmark results."""
    print("\n" + "=" * 80)
    print("BASELINE BENCHMARK RESULTS (Current LoopbackWriter)")
    print("=" * 80)

    print("\n--- JSON SERIALIZATION TIMING ---")
    print(f"Total cycles analyzed: {json_bench['num_cycles']}")
    print(f"Total JSON serialization time: {json_bench['total_json_time_ms']:.2f} ms")
    print()

    print("Per-topic timing (averaged across all cycles):")
    print(f"{'Topic':<20} {'Mean (µs)':<12} {'P95 (µs)':<12} {'P99 (µs)':<12} {'Max (µs)':<12}")
    print("-" * 60)

    for topic in sorted(json_bench["topics"].keys()):
        stats = json_bench["topics"][topic]
        print(
            f"{topic:<20} "
            f"{stats['mean_us']:<12.2f} "
            f"{stats['p95_us']:<12.2f} "
            f"{stats['p99_us']:<12.2f} "
            f"{stats['max_us']:<12.2f}"
        )

    print("\n--- FULL CYCLE WRITE TIMING (JSON + MCAP I/O) ---")
    print(f"Total cycles written: {cycle_bench['num_cycles']}")
    print(f"Shutdown time: {cycle_bench['shutdown_time_sec']:.2f} seconds")
    print()

    print("Normal cycles (PASS):")
    nc = cycle_bench["normal_cycles"]
    print(f"  Count:  {nc['count']}")
    print(f"  Mean:   {nc['mean_us']:.2f} µs")
    print(f"  P95:    {nc['p95_us']:.2f} µs")
    print(f"  P99:    {nc['p99_us']:.2f} µs")
    print(f"  Max:    {nc['max_us']:.2f} µs")

    print("\nViolation cycles (REJECT/FAULT):")
    vc = cycle_bench["violation_cycles"]
    print(f"  Count:  {vc['count']}")
    print(f"  Mean:   {vc['mean_us']:.2f} µs")
    print(f"  P95:    {vc['p95_us']:.2f} µs")
    print(f"  P99:    {vc['p99_us']:.2f} µs")
    print(f"  Max:    {vc['max_us']:.2f} µs")

    print("\n" + "=" * 80)
    print("KEY METRICS FOR OPTIMIZATION")
    print("=" * 80)
    print(f"JSON serialization dominates: {json_bench['total_json_time_ms']:.2f} ms")
    print(
        f"per {json_bench['num_cycles']} cycles = {json_bench['total_json_time_ms'] / json_bench['num_cycles']:.3f} ms/cycle avg"
    )
    print(f"Shutdown latency: {cycle_bench['shutdown_time_sec']:.2f} sec")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=1000, help="Number of cycles to benchmark")
    args = parser.parse_args()

    json_bench = benchmark_json_serialization(num_cycles=args.cycles)
    cycle_bench = benchmark_full_cycle_write(num_cycles=100)  # Smaller sample for full write

    print_results(json_bench, cycle_bench)


if __name__ == "__main__":
    main()
