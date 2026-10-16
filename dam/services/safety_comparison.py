"""Bounded, offline A/B replay and executable regression fixtures.

Both candidates see every recorded input in order, including warm-up cycles before
an asserted range. No runner, source, sink, or hardware adapter is constructed.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Annotated, Any, Literal, Self

import numpy as np
import yaml
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, StringConstraints, model_validator

from dam.config.schema import StackfileConfig

MAX_BYTES = 64 * 1024 * 1024
MAX_CYCLES = 20_000
MAX_SECONDS = 60
_DECISIONS = {"PASS", "CLAMP", "REJECT", "FAULT"}
_JOINT = {"joint_position_limits", "joint_velocity_limit", "joint_acceleration_limit"}
_SPATIAL = {"workspace", "keep_out_zone", "orientation_limit", "ee_velocity_limit"}
_CHANNELS = {
    "temperature_limit": "temperature",
    "current_limit": "current",
    "voltage_limit": "voltage",
    "force_torque_limit": "force_torque",
}
_SUPPORTED = (
    _JOINT | _SPATIAL | set(_CHANNELS) | {"task_workspace_bounds", "task_joint_speed_limit"}
)


class _RegressionConfigSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=255)
    yaml: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class _ExpectedOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    cycle: int = Field(ge=0)
    decision: Literal["PASS", "CLAMP", "REJECT", "FAULT"]
    output: list[FiniteFloat] | None = None
    output_velocities: list[FiniteFloat] | None = None

    @model_validator(mode="after")
    def complete_output_assertion(self) -> Self:
        if ("output" in self.model_fields_set) != ("output_velocities" in self.model_fields_set):
            raise ValueError("Output assertions must include both positions and velocities")
        return self


class _RegressionCase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    mcap: str = Field(min_length=1, max_length=255)
    mcap_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    config: _RegressionConfigSnapshot
    cycle_start: int = Field(ge=0)
    cycle_end: int = Field(ge=0)
    expected: list[_ExpectedOutcome] = Field(min_length=1, max_length=MAX_CYCLES)

    @model_validator(mode="after")
    def complete_range(self) -> Self:
        count = self.cycle_end - self.cycle_start + 1
        if not 0 < count <= MAX_CYCLES or len(self.expected) != count:
            raise ValueError("Regression assertions must cover the entire declared cycle range")
        if any(item.cycle != self.cycle_start + index for index, item in enumerate(self.expected)):
            raise ValueError("Regression cycle range must be contiguous, ordered and unique")
        return self


def validate_regression(case: Any) -> dict[str, Any]:
    """Validate persisted assertions before listing or running untrusted JSON."""
    return _RegressionCase.model_validate(case).model_dump(exclude_unset=True)


def fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def config_snapshot(text: str, name: str) -> dict[str, Any]:
    try:
        config = StackfileConfig.model_validate(yaml.safe_load(text))
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid Stackfile YAML: {exc}") from exc
    return {
        "name": name,
        "yaml": text,
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
        "config": config,
    }


def read_cycles(path: Path) -> list[dict[str, Any]]:
    """Normalize parser failures, including malformed MCAP headers, for the API."""
    try:
        return _read_cycles(path)
    except (ValueError, OSError):
        raise
    except Exception as exc:
        raise ValueError(f"Cannot read MCAP: {type(exc).__name__}: {exc}") from exc


def _read_cycles(path: Path) -> list[dict[str, Any]]:
    """Decode complete recorded inputs; retain incomplete cycles as findings."""
    from mcap.reader import make_reader

    if path.stat().st_size > MAX_BYTES:
        raise ValueError("Session exceeds the 64 MiB offline comparison limit")
    cycles: dict[int, dict[str, Any]] = {}
    seen_topics: set[tuple[str, int]] = set()
    with path.open("rb") as stream:
        for _, channel, message in make_reader(stream).iter_messages():
            if channel.topic not in {"/dam/obs", "/dam/action", "/dam/cycle"}:
                continue
            try:
                if channel.message_encoding == "json":
                    record = json.loads(message.data)
                else:
                    import msgpack

                    record = msgpack.unpackb(message.data, raw=False)
                if isinstance(record, list) and channel.topic == "/dam/cycle":
                    # Historical positional CycleRecordData; no invented observations.
                    keys = {
                        0: "cycle_id",
                        1: "obs_timestamp",
                        6: "active_task",
                        9: "obs_joint_positions",
                        10: "obs_channels",
                        11: "action_positions",
                        12: "action_velocities",
                    }
                    record = {key: record[i] for i, key in keys.items() if i < len(record)}
                if (
                    not isinstance(record, dict)
                    or type(record.get("cycle_id")) is not int
                    or record["cycle_id"] < 0
                ):
                    raise ValueError("missing integer cycle_id")
                cid = record["cycle_id"]
                identity = (channel.topic, cid)
                if identity in seen_topics:
                    raise ValueError(f"Duplicate {channel.topic} record for cycle {cid}")
                seen_topics.add(identity)
                cycle = cycles.setdefault(cid, {"cycle": cid})
                if len(cycles) > MAX_CYCLES:
                    raise ValueError("Session exceeds the 20,000 cycle comparison limit")
                if channel.topic == "/dam/obs":
                    cycle["obs"] = record
                elif channel.topic == "/dam/action":
                    cycle["action"] = record
                else:
                    cycle["task"] = record.get("active_task")
                    if "obs_joint_positions" in record:
                        cycle.setdefault(
                            "obs",
                            {
                                "joint_positions": record.get("obs_joint_positions"),
                                "timestamp": record.get("obs_timestamp"),
                                "obs_channels": record.get("obs_channels"),
                            },
                        )
                    if "action_positions" in record:
                        cycle.setdefault(
                            "action",
                            {
                                "target_positions": record.get("action_positions"),
                                "target_velocities": record.get("action_velocities"),
                            },
                        )
            except Exception as exc:
                raise ValueError(f"Cannot decode {channel.topic}: {exc}") from exc
    if not cycles:
        raise ValueError("Session has no replayable cycles")
    return [cycles[cid] for cid in sorted(cycles)]


def _vector(value: Any, name: str, *, required: bool = False) -> Any:
    if value is None and not required:
        return None
    if not isinstance(value, list):
        raise ValueError(f"Missing or unsupported {name}")
    arr = np.asarray(value, dtype=float)
    if arr.ndim != 1 or not np.all(np.isfinite(arr)):
        raise ValueError(f"Invalid {name}: expected a finite vector")
    return arr


def _inputs(cycle: dict[str, Any]) -> tuple[Any, Any]:
    from dam.types.action import ActionProposal
    from dam.types.observation import Observation

    o = cycle.get("obs") or {}
    a = cycle.get("action") or {}
    timestamp = o.get("timestamp")
    if not isinstance(timestamp, (float, int)) or not math.isfinite(timestamp):
        raise ValueError("Missing or invalid observation timestamp")
    channels = dict(o.get("obs_channels") or {})
    reserved = {"timestamp", "cycle_id", "joint_positions", "obs_channels"}
    channels.update({k: v for k, v in o.items() if k not in reserved and isinstance(v, list)})
    parsed = {key: _vector(value, key, required=True) for key, value in channels.items()}
    obs = Observation(
        timestamp=float(timestamp),
        joint_positions=_vector(o.get("joint_positions"), "joint_positions", required=True),
        joint_velocities=parsed.get("joint_velocities"),
        end_effector_pose=parsed.get("end_effector_pose"),
        force_torque=parsed.get("force_torque"),
        channels=parsed or None,
    )
    action = ActionProposal(
        target_joint_positions=_vector(a.get("target_positions"), "proposed action", required=True),
        target_joint_velocities=_vector(a.get("target_velocities"), "proposed velocities"),
    )
    return obs, action


def _unsupported(config: StackfileConfig) -> list[str]:
    issues = []
    if not config.tasks:
        issues.append("Stackfile defines no tasks")
    # Entire configuration is checked before runtime construction, including inactive
    # nodes: runtime preflight may instantiate their guards or load their models.
    for name, boundary in config.boundaries.items():
        if boundary.type != "single" or len(boundary.nodes) != 1:
            issues.append(
                f"{name}: container transitions are not recorded; only single nodes supported"
            )
        for node in boundary.nodes:
            if node.callback not in _SUPPORTED:
                issues.append(
                    f"{name}: {node.callback or 'implicit guard'} has no faithful offline input contract"
                )
            if node.timeout_sec is not None:
                issues.append(f"{name}: node timeout requires runtime scheduling history")
    return issues


def _evaluate(cycles: list[dict[str, Any]], snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    from dam.api import _register_builtins
    from dam.guard.aggregator import aggregate_decisions
    from dam.runtime.guard_runtime import GuardRuntime

    config = snapshot["config"]
    unsupported = _unsupported(config)
    if unsupported:
        return [{"status": "unsupported", "decision": None, "issues": unsupported} for _ in cycles]
    runtime = None
    acceleration_scopes: list[str] = []
    results: list[dict[str, Any]] = []
    blocked: str | None = None
    deadline = time.monotonic() + MAX_SECONDS
    try:
        _register_builtins()
        offline_config = config.model_copy(deep=True)
        offline_config.safety.slow_lane = None
        # The standalone acceleration callback keeps its initial velocity in a
        # process-wide dictionary keyed by boundary_name. Scope only that internal
        # callback parameter to this candidate, including on every regression run.
        # run_callbacks restores the container's public boundary name on results.
        for boundary in offline_config.boundaries.values():
            for node in boundary.nodes:
                if node.callback == "joint_acceleration_limit":
                    scope = f"offline-acceleration-{uuid.uuid4().hex}"
                    node.params["boundary_name"] = scope
                    acceleration_scopes.append(scope)
        runtime = GuardRuntime._from_config(offline_config)
        previous_timestamp: float | None = None
        previous_cycle: int | None = None
        for cycle in cycles:
            if time.monotonic() > deadline:
                blocked = "Comparison exceeded the 60 second evaluation limit"
            if blocked:
                results.append({"status": "error", "decision": None, "issues": [blocked]})
                continue
            try:
                if previous_cycle is not None and cycle["cycle"] != previous_cycle + 1:
                    raise ValueError(f"Missing recorded cycles after {previous_cycle}")
                previous_cycle = cycle["cycle"]
                obs, action = _inputs(cycle)
                if previous_timestamp is not None and obs.timestamp <= previous_timestamp:
                    raise ValueError("Observation timestamps must strictly increase")
                previous_timestamp = obs.timestamp
                task = cycle.get("task")
                # A missing task is unambiguous only for a single-task candidate.
                if not task and len(config.tasks) == 1:
                    task = next(iter(config.tasks))
                if not isinstance(task, str) or task not in config.tasks:
                    raise ValueError(f"Recorded task {task!r} is not defined by this candidate")
                if runtime._active_task != task:
                    runtime.start_task(task)
                    runtime._node_start_times = {
                        name: obs.timestamp for name in runtime._node_start_times
                    }
                names = list(config.safety.always_active_list()) + config.tasks[task].boundaries
                for name in names:
                    boundary = config.boundaries[name]
                    node = boundary.nodes[0]
                    callback = node.callback
                    if callback in _JOINT and (
                        len(obs.joint_positions) == 0
                        or len(obs.joint_positions) != len(action.target_joint_positions)
                    ):
                        raise ValueError(
                            f"{name}: joint observation/action dimensions are missing or differ"
                        )
                    if callback == "task_joint_speed_limit" and obs.joint_velocities is None:
                        raise ValueError(f"{name}: joint_velocities is missing")
                    if (
                        callback == "task_workspace_bounds"
                        and obs.end_effector_pose is None
                        and runtime._select_dynamics() is None
                    ):
                        raise ValueError(f"{name}: end_effector_pose/FK is missing")
                    if callback in _CHANNELS:
                        channel = node.params.get("channel", _CHANNELS[callback])
                        if channel not in (obs.channels or {}):
                            raise ValueError(f"{name}: required channel {channel!r} is missing")
                    if callback in _SPATIAL and runtime._select_dynamics() is None:
                        raise ValueError(f"{name}: offline FK/dynamics solver is unavailable")
                output, guards = runtime.validate(
                    obs,
                    action,
                    trace_id=f"compare-{cycle['cycle']}",
                    now=obs.timestamp,
                    emit_side_effects=False,
                )
                decision = aggregate_decisions(guards).decision.name if guards else "PASS"
                out = None if output is None else output.target_joint_positions.tolist()
                out_vel = (
                    None
                    if output is None or output.target_joint_velocities is None
                    else output.target_joint_velocities.tolist()
                )
                if out is not None and not np.all(np.isfinite(out)):
                    raise ValueError("Runtime produced a non-finite output")
                delta = (
                    None
                    if out is None
                    else float(np.linalg.norm(np.asarray(out) - action.target_joint_positions))
                )
                results.append(
                    {
                        "status": "evaluated",
                        "decision": decision,
                        "output": out,
                        "output_velocities": out_vel,
                        "correction_l2": delta,
                        "guards": [
                            {"name": g.guard_name, "decision": g.decision.name, "reason": g.reason}
                            for g in guards
                        ],
                        "issues": [],
                    }
                )
            except Exception as exc:
                # Continuing after a missing cycle would invent state/history for
                # acceleration and warning counters. Mark the remaining suffix.
                blocked = f"Cannot reconstruct history after cycle {cycle['cycle']}: {exc}"
                results.append({"status": "error", "decision": None, "issues": [str(exc)]})
    except Exception as exc:
        return [{"status": "error", "decision": None, "issues": [str(exc)]} for _ in cycles]
    finally:
        try:
            if runtime is not None:
                # shutdown joins the evaluation workers before releasing caches.
                runtime.shutdown()
        finally:
            from dam.boundary.callbacks.kinematics import _prev_vel

            for scope in acceleration_scopes:
                _prev_vel.pop(scope, None)
    return results


def compare(path: Path, left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("Session exceeds the 64 MiB offline comparison limit")
    before = fingerprint(path)
    cycles = read_cycles(path)
    left_results = _evaluate(cycles, left)
    right_results = _evaluate(cycles, right)
    if before != fingerprint(path):
        raise ValueError("Recording changed during comparison; retry a completed session")
    rows = []
    for cycle, a, b in zip(cycles, left_results, right_results, strict=True):
        comparable = a["status"] == b["status"] == "evaluated"
        output_delta = None
        if (
            comparable
            and a["output"] is not None
            and b["output"] is not None
            and len(a["output"]) == len(b["output"])
        ):
            output_delta = float(np.linalg.norm(np.asarray(a["output"]) - np.asarray(b["output"])))
        changed = comparable and (
            a["decision"] != b["decision"]
            or a["output"] != b["output"]
            or a.get("output_velocities") != b.get("output_velocities")
        )
        rows.append(
            {
                "cycle": cycle["cycle"],
                "timestamp": (cycle.get("obs") or {}).get("timestamp"),
                "left": a,
                "right": b,
                "comparable": comparable,
                "changed": changed,
                "output_delta_l2": output_delta,
            }
        )
    evaluated = sum(row["comparable"] for row in rows)
    differences = [row for row in rows if row["changed"]]
    return {
        "status": "complete" if evaluated == len(rows) else ("partial" if evaluated else "error"),
        "mcap": path.name,
        "mcap_sha256": before,
        "total": len(rows),
        "compared": evaluated,
        "error_cycles": len(rows) - evaluated,
        "difference_count": len(differences),
        "configs": {
            side: {k: v for k, v in snap.items() if k != "config"}
            for side, snap in (("left", left), ("right", right))
        },
        "distributions": {
            side: dict(Counter(r.get("decision") or r["status"].upper() for r in result))
            for side, result in (("left", left_results), ("right", right_results))
        },
        "cycles": rows,
        "differences": differences,
        "top_differences": sorted(
            differences,
            key=lambda row: (
                row["left"]["decision"] != row["right"]["decision"],
                row["output_delta_l2"] or 0,
            ),
            reverse=True,
        )[:20],
        "evaluation_mode": "synchronous_offline",
        "limitations": [
            "All supported guards evaluate synchronously every cycle; slow-lane scheduling, stale verdicts and startup waits are not reproduced.",
            "Offline verdict comparison on recorded observations and proposed actions; not a new robot trajectory.",
            "Fallback execution, asynchronous timing, images, host health and sensor age are not reconstructed.",
            "Missing inputs invalidate subsequent history; no zero-filled sensor values are used.",
            "Correction norms use the recorded action units and compare positions separately from velocities.",
        ],
    }


def make_regression(
    comparison: dict[str, Any],
    *,
    name: str,
    side: str,
    cycle_start: int,
    cycle_end: int,
    expected_decision: str | None = None,
) -> dict[str, Any]:
    if (
        side not in {"left", "right"}
        or type(cycle_start) is not int
        or type(cycle_end) is not int
        or cycle_start < 0
        or not 0 < cycle_end - cycle_start + 1 <= MAX_CYCLES
    ):
        raise ValueError("Invalid side or cycle range")
    if expected_decision is not None and expected_decision not in _DECISIONS:
        raise ValueError("Invalid expected decision")
    rows = [row for row in comparison["cycles"] if cycle_start <= row["cycle"] <= cycle_end]
    if len(rows) != cycle_end - cycle_start + 1 or any(
        row["cycle"] != cycle_start + index for index, row in enumerate(rows)
    ):
        raise ValueError("Regression range must be fully covered by recorded cycles")
    if any(row[side]["status"] != "evaluated" for row in rows):
        raise ValueError("Regression range must contain evaluated cycles without missing data")
    return validate_regression(
        {
            "name": name,
            "mcap": comparison["mcap"],
            "mcap_sha256": comparison["mcap_sha256"],
            "config": comparison["configs"][side],
            "cycle_start": cycle_start,
            "cycle_end": cycle_end,
            "expected": [
                {
                    "cycle": row["cycle"],
                    "decision": expected_decision or row[side]["decision"],
                    **(
                        {
                            "output": row[side]["output"],
                            "output_velocities": row[side]["output_velocities"],
                        }
                        if expected_decision is None
                        else {}
                    ),
                }
                for row in rows
            ],
        }
    )


def run_regression(path: Path, case: dict[str, Any]) -> dict[str, Any]:
    try:
        case = validate_regression(case)
    except ValueError as exc:
        return {"status": "error", "issues": [f"Invalid regression case: {exc}"], "mismatches": []}
    if path.stat().st_size > MAX_BYTES or fingerprint(path) != case["mcap_sha256"]:
        return {
            "status": "error",
            "issues": ["MCAP fingerprint changed or size limit exceeded"],
            "mismatches": [],
        }
    snapshot = config_snapshot(case["config"]["yaml"], case["config"]["name"])
    if snapshot["sha256"] != case["config"]["sha256"]:
        return {
            "status": "error",
            "issues": ["Configuration fingerprint mismatch"],
            "mismatches": [],
        }
    cycles = read_cycles(path)
    # Keep the complete prefix to reconstruct velocity/history correctly.
    cycles = [row for row in cycles if row["cycle"] <= case["cycle_end"]]
    actual = {
        row["cycle"]: result
        for row, result in zip(cycles, _evaluate(cycles, snapshot), strict=True)
    }
    mismatches = []
    errors: list[Any] = []
    if not case["expected"]:
        errors.append("Regression has no assertions")
    for expected in case["expected"]:
        result = actual.get(expected["cycle"])
        if result is None or result["status"] != "evaluated":
            errors.append({"cycle": expected["cycle"], "actual": result})
            continue
        matches = result["decision"] == expected["decision"]
        for key in ("output", "output_velocities"):
            if key not in expected:
                continue
            wanted, got = expected[key], result.get(key)
            if wanted is None or got is None:
                matches = matches and wanted == got
            else:
                matches = (
                    matches
                    and len(wanted) == len(got)
                    and bool(np.allclose(wanted, got, rtol=0, atol=1e-8))
                )
        if not matches:
            mismatches.append({"cycle": expected["cycle"], "expected": expected, "actual": result})
    if fingerprint(path) != case["mcap_sha256"]:
        errors.append("MCAP changed during regression execution")
    return {
        "status": "error" if errors else ("fail" if mismatches else "pass"),
        "checked": len(case["expected"]),
        "mismatches": mismatches,
        "issues": errors,
    }
