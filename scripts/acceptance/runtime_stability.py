"""Public-SDK stability measurements. Run with python -m scripts.acceptance.runtime_stability.

50 Hz and acceptance limits are fixed. --probe produces explicitly incomplete
reports; it never substitutes for the 10,000-cycle/1,000-lifecycle/8-hour gates.
Raw cycle records are little-endian float64 triples: elapsed_s, call_ms,
completion_from_scheduled_start_ms. Memory samples precede loading raw timings.
"""

from __future__ import annotations

import argparse
import copy
import gc
import hashlib
import importlib.metadata
import json
import platform
import struct
import tempfile
import threading
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import psutil  # type: ignore[import-untyped]

import dam

PERIOD = 0.02


def configuration(hybrid: bool) -> dict[str, Any]:
    config: dict[str, Any] = {
        "hardware": {"joint_names": [f"j{i}" for i in range(6)], "degrees_mode": False},
        "safety": {"control_hz": 50},
        "boundaries": {
            "bounds": {
                "layer": "L1",
                "type": "single",
                "nodes": [
                    {
                        "callback": "joint_position_limits",
                        "params": {"lower": [-1] * 6, "upper": [1] * 6},
                    }
                ],
            }
        },
        "tasks": {"default": {"boundaries": ["bounds"]}},
    }
    if hybrid:
        config["safety"]["slow_lane"] = {"task_hz": 10, "max_staleness_ms": 300}
        config["boundaries"]["task"] = {
            "layer": "L2",
            "type": "single",
            "nodes": [{"callback": "task_joint_speed_limit", "params": {"max_speed": 2.0}}],
        }
        config["tasks"]["default"]["boundaries"].append("task")
    return config


def resources() -> dict[str, Any]:
    proc = psutil.Process()
    return {
        "rss_bytes": proc.memory_info().rss,
        "file_descriptors": proc.num_fds(),
        "process_threads": proc.num_threads(),
        "dam_python_workers": [t.name for t in threading.enumerate() if t.name.startswith("dam-")],
    }


def source_fingerprint() -> str:
    import dam_rs

    root = Path(dam.__file__).parent
    digest = hashlib.sha256()
    files = sorted(root.rglob("*.py"))
    files += sorted(Path(dam_rs.__file__).parent.glob("*.so"))
    files += [Path(__file__)]
    for path in files:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def metadata(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_sha256": source_fingerprint(),
        "started_at_utc": datetime.now(UTC).isoformat(),
        "pid": psutil.Process().pid,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu_count": psutil.cpu_count(),
        "versions": {
            name: importlib.metadata.version(name)
            for name in ("robot-dam", "dam-rs", "numpy", "torch", "psutil")
        },
        "configuration": config,
        "configuration_sha256": hashlib.sha256(
            json.dumps(config, sort_keys=True).encode()
        ).hexdigest(),
        "frequency_hz": 50,
        "p99_limit_ms": 10,
        "deadline_ms": 20,
        "deadline_violation_rate_limit": 0.001,
    }


def pause_until(deadline: float) -> None:
    remaining = deadline - time.perf_counter()
    if remaining > 0:
        time.sleep(remaining)


def measure(output: Path, hybrid: bool, cycles: int, soak: bool, probe: bool) -> dict[str, Any]:
    config = configuration(hybrid)
    info = metadata(config)
    info.update(status="running", planned_cycles=cycles)
    output.with_suffix(".json").write_text(json.dumps(info, indent=2))
    obs = np.zeros(6)
    normal, clamped = np.full(6, 0.25), np.full(6, 2.0)
    initialized = time.perf_counter()
    rail = dam.Guardrail(config, quiet=True)
    info["initialization_ms"] = (time.perf_counter() - initialized) * 1000
    samples: list[dict[str, Any]] = []
    decisions: Counter[str] = Counter()
    try:
        first = time.perf_counter()
        rail.evaluate({"joints": obs, "action": normal})
        info["first_call_ms"] = (time.perf_counter() - first) * 1000
        warm = time.perf_counter()
        for i in range(100):
            pause_until(warm + i * PERIOD)
            rail.evaluate({"joints": obs, "action": clamped if i % 10 == 0 else normal})
        baseline = resources()
        with (
            output.with_suffix(".bin").open("wb") as raw,
            output.with_suffix(".samples.jsonl").open("w") as sample_file,
        ):
            started = time.perf_counter()
            for cycle in range(cycles):
                scheduled = started + cycle * PERIOD
                pause_until(scheduled)
                if cycle % 3000 == 0:
                    sample = {"elapsed_s": time.perf_counter() - started, **resources()}
                    samples.append(sample)
                    sample_file.write(json.dumps(sample) + "\n")
                    sample_file.flush()
                call_start = time.perf_counter()
                result = rail.evaluate(
                    {"joints": obs, "action": clamped if cycle % 10 == 0 else normal}
                )
                call_end = time.perf_counter()
                decisions[result.decision.name] += 1
                # Include scheduling delay and sampling overhead in the cycle deadline.
                raw.write(
                    struct.pack(
                        "<ddd",
                        call_end - started,
                        (call_end - call_start) * 1000,
                        (call_end - scheduled) * 1000,
                    )
                )
            pause_until(started + cycles * PERIOD)
            info["elapsed_s"] = time.perf_counter() - started
            info["resources_before_close"] = resources()
            info["diagnostics"] = rail.diagnostics()
        rail.close()
        info["resources_after_close"] = resources()
    finally:
        rail.close()
    # Allocate analysis arrays only after the resource observation is complete.
    data = np.fromfile(output.with_suffix(".bin"), dtype="<f8").reshape(-1, 3)
    assert len(data) == cycles
    info.update(cycles=cycles, decisions=dict(decisions), baseline_resources=baseline)
    info["source_unchanged"] = info["source_sha256"] == source_fingerprint()
    info["latency_ms"] = dict(
        zip(("p50", "p95", "p99"), np.percentile(data[:, 1], [50, 95, 99]).tolist(), strict=True)
    )
    info["latency_ms"]["max"] = float(data[:, 1].max())
    info["deadline_violation_rate"] = float(np.mean(data[:, 2] > 20))
    info["max_completion_ms"] = float(data[:, 2].max())
    passed = (
        info["latency_ms"]["p99"] <= 10
        and info["deadline_violation_rate"] <= 0.001
        and not decisions["FAULT"]
        and not decisions["REJECT"]
        and info["source_unchanged"]
    )
    if soak:
        hours = [
            [s["rss_bytes"] for s in samples if hour * 3600 <= s["elapsed_s"] < (hour + 1) * 3600]
            for hour in range(8)
        ]
        medians = [float(np.median(group)) if group else None for group in hours]
        info["hourly_rss_medians"] = medians
        complete = all(value is not None for value in medians) and info["elapsed_s"] >= 28800
        if complete:
            values = [float(value) for value in medians if value is not None]
            memory_ok = values[-1] <= values[0] * 1.10 and not all(
                b > a for a, b in zip(values, values[1:], strict=False)
            )
        else:
            memory_ok = False
        info["memory_gate_passed"] = memory_ok
        passed = passed and complete and memory_ok
    else:
        complete = cycles >= 10000
    end = info["resources_after_close"]
    resources_ok = (
        end["file_descriptors"] <= baseline["file_descriptors"] and not end["dam_python_workers"]
    )
    info["resources_gate_passed"] = resources_ok
    info["status"] = (
        "incomplete"
        if probe or not complete
        else ("passed" if passed and resources_ok else "failed")
    )
    output.with_suffix(".json").write_text(json.dumps(info, indent=2))
    return info


def lifecycle(output: Path, iterations: int, probe: bool) -> dict[str, Any]:
    config = configuration(True)
    inputs = {"joints": np.zeros(6), "action": np.full(6, 0.25)}

    def once() -> None:
        with tempfile.TemporaryDirectory(prefix="dam-lifecycle-") as directory:
            session_config = copy.deepcopy(config)
            session_config["loopback"] = {"output_dir": directory}
            with dam.Guardrail(session_config, quiet=True) as rail:
                assert rail.evaluate(inputs).decision == dam.GuardDecision.PASS
                rail.reset()
                assert rail.evaluate(inputs).decision == dam.GuardDecision.PASS
            rail.close()
            assert len(list(Path(directory).glob("*.mcap"))) == 2

    for _ in range(5):
        once()
    gc.collect()
    baseline = resources()
    samples = []
    for count in range(iterations):
        once()
        if count % 100 == 99:
            gc.collect()
            samples.append({"iterations": count + 1, **resources()})
            assert not samples[-1]["dam_python_workers"]
    gc.collect()
    end = resources()
    passed = (
        end["file_descriptors"] <= baseline["file_descriptors"]
        and end["process_threads"] <= baseline["process_threads"]
        and not end["dam_python_workers"]
    )
    info = {
        **metadata(config),
        "iterations": iterations,
        "recording": "two completed MCAP files per iteration in a disposable directory",
        "baseline": baseline,
        "final": end,
        "samples": samples,
        "status": "incomplete"
        if probe or iterations < 1000
        else ("passed" if passed else "failed"),
    }
    output.with_suffix(".json").write_text(json.dumps(info, indent=2))
    return info


def create_evidence_directory() -> Path:
    """Create an exclusive run folder beneath the fixed repository evidence root."""
    root = Path(__file__).resolve().parents[2] / "logs" / "acceptance-l5"
    if root.resolve() != root:
        raise ValueError("Acceptance evidence root must not traverse symlinks")
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="run-", dir=root))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("benchmark", "lifecycle", "soak"))
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()
    output = create_evidence_directory()
    print(json.dumps({"mode": args.mode, "output": str(output)}), flush=True)
    if args.mode == "lifecycle":
        reports = [lifecycle(output / "lifecycle", 10 if args.probe else 1000, args.probe)]
    elif args.mode == "soak":
        reports = [measure(output / "soak", True, 200 if args.probe else 1440000, True, args.probe)]
    else:
        reports = [
            measure(output / name, hybrid, 100 if args.probe else 10000, False, args.probe)
            for name, hybrid in (("fast", False), ("hybrid", True))
        ]
    print(json.dumps([{k: r[k] for k in ("status",)} for r in reports]), flush=True)
    return 0 if args.probe or all(r["status"] == "passed" for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
