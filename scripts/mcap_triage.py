#!/usr/bin/env python3
"""Read-only incident triage for DAM MCAP sessions.

This is the first tool an agent should run when a physical robot appears
stalled, disconnected, or repeatedly clamped. It never starts a task, sends
an action, or changes runtime configuration. By default it reads the newest
recorded MCAP session and performs an optional read-only backend status GET.

Examples:
    .venv/bin/python scripts/mcap_triage.py
    .venv/bin/python scripts/mcap_triage.py --json
    .venv/bin/python scripts/mcap_triage.py --compare data/robot/sessions/session_known_good.mcap
    .venv/bin/python scripts/mcap_triage.py data/robot/sessions/session_problem.mcap --no-api
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

from dam.services.mcap_sessions import _decode_msg, _mcap_open

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SESSION_DIR = ROOT / "data" / "robot" / "sessions"
DEFAULT_STATUS_URL = "http://127.0.0.1:8080/api/control/status"
_CYCLE_TOPIC = "/dam/cycle"
_DEG = float(180.0 / np.pi)


def latest_session(session_dir: Path) -> Path | None:
    """Return the newest non-empty MCAP recording, if any."""
    candidates = [p for p in session_dir.glob("session_*.mcap") if p.stat().st_size >= 100]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def read_session(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Join both compact-Rust and split-topic Python MCAP layouts in memory."""
    try:
        from mcap.exceptions import EndOfFile, McapError
    except ImportError as exc:
        raise RuntimeError("mcap is required; install project dependencies first") from exc

    by_id: dict[int, dict[str, Any]] = {}
    topics: Counter[str] = Counter()
    encodings: set[str] = set()
    read_status = "complete"
    relevant = {
        _CYCLE_TOPIC,
        "/dam/obs",
        "/dam/action",
        "/dam/latency",
        "/dam/L0",
        "/dam/L1",
        "/dam/L2",
        "/dam/L3",
    }
    try:
        with _mcap_open(path) as reader:
            for _schema, channel, message in reader.iter_messages():
                topic = channel.topic
                topics[topic] += 1
                if topic not in relevant:
                    continue
                encodings.add(channel.message_encoding)
                data = _decode_msg(message.data, channel.message_encoding)
                if data is None or not isinstance(data.get("cycle_id"), int):
                    continue
                cycle_id = int(data["cycle_id"])
                cycle = by_id.setdefault(cycle_id, {"cycle_id": cycle_id, "guard_results": []})
                if topic == _CYCLE_TOPIC:
                    existing_results = cycle.get("guard_results", [])
                    cycle.update(data)
                    compact_results = data.get("guard_results")
                    cycle["guard_results"] = (
                        compact_results if isinstance(compact_results, list) else existing_results
                    )
                elif topic == "/dam/obs":
                    cycle["obs_joint_positions"] = data.get("joint_positions")
                    cycle["obs_timestamp"] = data.get("timestamp")
                elif topic == "/dam/action":
                    cycle["action_positions"] = data.get("target_positions")
                    cycle["validated_positions"] = data.get("validated_positions")
                    cycle["was_clamped"] = bool(data.get("was_clamped"))
                elif topic == "/dam/latency":
                    cycle["total_ms"] = data.get("total_ms")
                elif topic.startswith("/dam/L"):
                    cycle.setdefault("guard_results", []).append(data)
    except (EndOfFile, McapError):
        read_status = "partial"

    cycles = [by_id[key] for key in sorted(by_id)]
    return cycles, {
        "read_status": read_status,
        "topics": dict(topics),
        "encodings": sorted(encodings),
    }


def read_cycles(path: Path) -> list[dict[str, Any]]:
    """Compatibility helper for callers needing only joined cycle records."""
    return read_session(path)[0]


def read_runtime_status(url: str, timeout_s: float = 0.35) -> dict[str, Any] | None:
    """Perform one short, read-only status request; unavailable is not fatal."""
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as response:
            result = json.loads(response.read())
    except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError):
        return None
    return result if isinstance(result, dict) else None


def _vectors(cycles: list[dict[str, Any]], key: str, n_joints: int) -> np.ndarray | None:
    rows = [c.get(key) for c in cycles]
    if not rows or any(not isinstance(row, list) or len(row) != n_joints for row in rows):
        return None
    return np.asarray(rows, dtype=np.float64)


def _guard_counts(cycles: list[dict[str, Any]]) -> tuple[Counter[str], Counter[str]]:
    outcomes: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    for cycle in cycles:
        guard_results = cycle.get("guard_results")
        if isinstance(guard_results, list):
            for guard in guard_results:
                if not isinstance(guard, dict):
                    continue
                decision = str(guard.get("decision_name", "PASS"))
                name = str(guard.get("guard_name", "unknown"))
                if decision in {"CLAMP", "REJECT", "FAULT"}:
                    outcomes[f"{name}:{decision}"] += 1
                    reason = guard.get("reason")
                    if isinstance(reason, str) and reason:
                        reasons[reason] += 1
            continue
        for name, decision in zip(
            cycle.get("failure_guard_names", []),
            cycle.get("failure_decisions", []),
            strict=False,
        ):
            outcomes[f"{name}:{decision}"] += 1
        for reason in cycle.get("failure_reasons", []):
            if isinstance(reason, str) and reason:
                reasons[reason] += 1
    return outcomes, reasons


def summarize_cycles(
    cycles: list[dict[str, Any]],
    *,
    path: Path | None = None,
    runtime_status: dict[str, Any] | None = None,
    read_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a compact, JSON-safe triage report from recorded cycles."""
    if not cycles:
        partial = (read_info or {}).get("read_status") == "partial"
        return {
            "schema": "dam.mcap_triage.v1",
            "read_only": True,
            "session": {
                "path": str(path) if path else None,
                "cycles": 0,
                "read_status": (read_info or {}).get("read_status", "unknown"),
                "encodings": (read_info or {}).get("encodings", []),
                "topics": (read_info or {}).get("topics", {}),
            },
            "runtime_status": runtime_status,
            "findings": [
                {
                    "code": "partial_session" if partial else "empty_session",
                    "severity": "warning",
                }
            ],
        }

    n_joints = len(cycles[0].get("obs_joint_positions", []))
    obs = _vectors(cycles, "obs_joint_positions", n_joints)
    sent_cycles = [
        cycle
        for cycle in cycles
        if isinstance(cycle.get("validated_positions"), list)
        and len(cycle["validated_positions"]) == n_joints
    ]
    sent_obs = _vectors(sent_cycles, "obs_joint_positions", n_joints)
    action = _vectors(sent_cycles, "action_positions", n_joints)
    validated = _vectors(sent_cycles, "validated_positions", n_joints)
    valid_motion = (
        obs is not None
        and sent_obs is not None
        and action is not None
        and validated is not None
        and n_joints > 0
    )
    clamped = sum(bool(c.get("has_clamp") or c.get("was_clamped")) for c in cycles)
    rejected = sum(bool(c.get("has_violation")) for c in cycles)
    no_validated_command = sum(
        bool(c.get("has_violation")) and not isinstance(c.get("validated_positions"), list)
        for c in cycles
    )
    first_ts = cycles[0].get("obs_timestamp")
    last_ts = cycles[-1].get("obs_timestamp")
    duration_s = (
        round(float(last_ts) - float(first_ts), 2)
        if isinstance(first_ts, int | float) and isinstance(last_ts, int | float)
        else None
    )
    active_boundaries = cycles[-1].get("active_boundaries", [])
    outcomes, reasons = _guard_counts(cycles)
    findings: list[dict[str, Any]] = []
    read_status = (read_info or {}).get("read_status", "unknown")
    if read_status == "partial":
        findings.append(
            {
                "code": "partial_session",
                "severity": "warning",
                "detail": "session may still be recording or was not cleanly closed",
            }
        )

    state = runtime_status.get("state") if runtime_status else None
    if state and state != "running":
        findings.append(
            {
                "code": "runtime_not_running",
                "severity": "info",
                "detail": f"backend control state is {state}",
            }
        )
    if clamped == len(cycles):
        findings.append(
            {
                "code": "all_cycles_clamped",
                "severity": "warning",
                "detail": "every recorded command was modified by at least one guard",
            }
        )
    hardware_events = sum(
        count for key, count in outcomes.items() if "hardware" in key or "watchdog" in key
    )
    if hardware_events:
        findings.append(
            {
                "code": "hardware_guard_event",
                "severity": "critical",
                "cycles": hardware_events,
            }
        )
    if no_validated_command:
        findings.append(
            {
                "code": "rejected_without_validated_command",
                "severity": "critical",
                "cycles": no_validated_command,
                "detail": "rejected proposals were not sent to hardware",
            }
        )

    joints: list[dict[str, Any]] = []
    nonresponsive: list[str] = []
    if valid_motion:
        assert (
            obs is not None
            and sent_obs is not None
            and action is not None
            and validated is not None
        )
        observed_range_deg = np.ptp(obs, axis=0) * _DEG
        observed_net_deg = np.abs(obs[-1] - obs[0]) * _DEG
        requested_offset_deg = np.abs(action - sent_obs) * _DEG
        sent_offset_deg = np.abs(validated - sent_obs) * _DEG
        for idx in range(n_joints):
            attempted = sent_offset_deg[:, idx] >= 1.0
            attempted_pct = float(np.mean(attempted) * 100.0)
            sent_median = float(np.median(sent_offset_deg[:, idx]))
            stalled = bool(
                attempted_pct >= 20.0 and sent_median >= 1.0 and observed_range_deg[idx] < 2.0
            )
            name = f"J{idx + 1}"
            if stalled:
                nonresponsive.append(name)
            joints.append(
                {
                    "joint": name,
                    "start_deg": round(float(obs[0, idx] * _DEG), 2),
                    "end_deg": round(float(obs[-1, idx] * _DEG), 2),
                    "observed_range_deg": round(float(observed_range_deg[idx]), 2),
                    "observed_net_deg": round(float(observed_net_deg[idx]), 2),
                    "requested_offset_p95_deg": round(
                        float(np.percentile(requested_offset_deg[:, idx], 95)), 2
                    ),
                    "sent_offset_median_deg": round(sent_median, 2),
                    "sent_offset_p95_deg": round(
                        float(np.percentile(sent_offset_deg[:, idx], 95)), 2
                    ),
                    "attempted_cycles_pct": round(attempted_pct, 1),
                    "command_without_response": stalled,
                }
            )
    else:
        findings.append(
            {
                "code": "motion_vectors_unavailable",
                "severity": "warning",
                "detail": "session lacks complete observation/action/validated vectors",
            }
        )
    if nonresponsive:
        findings.append(
            {
                "code": "command_without_response",
                "severity": "critical",
                "joints": nonresponsive,
                "detail": "validated position changes were repeatedly sent but observed range stayed below 2 deg",
            }
        )

    cameras: set[str] = {
        topic.removeprefix("/dam/images/")
        for topic in (read_info or {}).get("topics", {})
        if topic.startswith("/dam/images/")
    }
    for cycle in cycles:
        cameras.update(cycle.get("active_cameras", []) or [])
    cycle_ids = [c["cycle_id"] for c in cycles if isinstance(c.get("cycle_id"), int)]
    expected_span = cycle_ids[-1] - cycle_ids[0] + 1 if cycle_ids else 0
    total_ms = [float(c["total_ms"]) for c in cycles if isinstance(c.get("total_ms"), int | float)]

    return {
        "schema": "dam.mcap_triage.v1",
        "read_only": True,
        "session": {
            "path": str(path.resolve()) if path else None,
            "cycles": len(cycles),
            "cycle_id_first": cycle_ids[0] if cycle_ids else None,
            "cycle_id_last": cycle_ids[-1] if cycle_ids else None,
            "cycle_gaps": max(0, expected_span - len(cycle_ids)),
            "duration_s": duration_s,
            "read_status": read_status,
            "encodings": (read_info or {}).get("encodings", []),
            "topics": (read_info or {}).get("topics", {}),
            "active_task": cycles[-1].get("active_task"),
            "active_boundaries": active_boundaries,
            "active_context": cycles[-1].get("active_context", "normal"),
            "cameras": sorted(cameras),
            "clamped_cycles": clamped,
            "clamp_pct": round(100.0 * clamped / len(cycles), 1),
            "violation_cycles": rejected,
            "total_latency_ms_p95": (
                round(float(np.percentile(total_ms, 95)), 2) if total_ms else None
            ),
            "total_latency_ms_max": round(max(total_ms), 2) if total_ms else None,
        },
        "runtime_status": runtime_status,
        "guard_outcomes": [
            {"outcome": name, "cycles": count} for name, count in outcomes.most_common()
        ],
        "top_failure_reasons": [
            {"reason": reason, "cycles": count} for reason, count in reasons.most_common(5)
        ],
        "joints": joints,
        "findings": findings,
    }


def compare_reports(report: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    """Compare starting joint poses; do not infer equivalence of tasks/models."""
    current_joints = report.get("joints", [])
    baseline_joints = baseline.get("joints", [])
    if len(current_joints) != len(baseline_joints) or not current_joints:
        return {"available": False, "reason": "joint summaries are incompatible"}
    deltas = [
        round(abs(float(cur["start_deg"]) - float(base["start_deg"])), 2)
        for cur, base in zip(current_joints, baseline_joints, strict=True)
    ]
    changed = [f"J{i + 1}" for i, value in enumerate(deltas) if value >= 20.0]
    return {
        "available": True,
        "baseline_path": baseline["session"].get("path"),
        "start_pose_delta_deg": deltas,
        "large_start_pose_changes": changed,
        "caveat": "baseline comparison is meaningful only when robot, task, and calibration match",
    }


def _format_report(report: dict[str, Any]) -> str:
    session = report["session"]
    lines = [
        "MCAP TRIAGE (READ-ONLY)",
        f"Session: {session.get('path')}  read={session.get('read_status')}",
        (
            f"Cycles: {session.get('cycles')}  duration={session.get('duration_s')}s  "
            f"task={session.get('active_task')}  context={session.get('active_context')}"
        ),
    ]
    status = report.get("runtime_status")
    lines.append(
        "Runtime: unavailable"
        if not status
        else f"Runtime: control={status.get('state')} backend={status.get('backend_state')}"
    )
    lines.append(
        f"Safety: clamped={session.get('clamped_cycles')}/{session.get('cycles')} "
        f"({session.get('clamp_pct')}%) violations={session.get('violation_cycles')}"
    )
    if report.get("guard_outcomes"):
        joined = ", ".join(
            f"{item['outcome']}={item['cycles']}" for item in report["guard_outcomes"][:4]
        )
        lines.append(f"Guard outcomes: {joined}")
    if report.get("joints"):
        lines.append("")
        lines.append("Joint  Start    End      Range    Sent p95  No response")
        for joint in report["joints"]:
            lines.append(
                f"{joint['joint']:<6} {joint['start_deg']:>7.1f}  {joint['end_deg']:>7.1f}  "
                f"{joint['observed_range_deg']:>7.1f}  {joint['sent_offset_p95_deg']:>8.1f}  "
                f"{'YES' if joint['command_without_response'] else 'no'}"
            )
    comparison = report.get("comparison")
    if comparison and comparison.get("available"):
        lines.append("")
        lines.append(f"Baseline: {comparison['baseline_path']}")
        changed = comparison["large_start_pose_changes"]
        lines.append(
            f"Large start-pose changes (>=20 deg): {', '.join(changed) if changed else 'none'}"
        )
    if report.get("findings"):
        lines.append("")
        lines.append("Findings:")
        for finding in report["findings"]:
            extra = f" ({', '.join(finding['joints'])})" if finding.get("joints") else ""
            lines.append(f"- [{finding['severity']}] {finding['code']}{extra}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mcap", nargs="?", help="MCAP file; defaults to newest session")
    parser.add_argument("--session-dir", type=Path, default=DEFAULT_SESSION_DIR)
    parser.add_argument("--compare", type=Path, help="Known-good MCAP baseline for pose comparison")
    parser.add_argument("--json", action="store_true", help="Print stable machine-readable JSON")
    parser.add_argument("--no-api", action="store_true", help="Skip the read-only local status GET")
    parser.add_argument("--status-url", default=DEFAULT_STATUS_URL)
    args = parser.parse_args(argv)

    path = Path(args.mcap) if args.mcap else latest_session(args.session_dir)
    if path is None or not path.is_file():
        print("No MCAP session found.", file=sys.stderr)
        return 1
    status = None if args.no_api else read_runtime_status(args.status_url)
    try:
        cycles, read_info = read_session(path)
        report = summarize_cycles(cycles, path=path, runtime_status=status, read_info=read_info)
        if args.compare:
            baseline_cycles, baseline_info = read_session(args.compare)
            report["comparison"] = compare_reports(
                report,
                summarize_cycles(baseline_cycles, path=args.compare, read_info=baseline_info),
            )
    except Exception as exc:  # noqa: BLE001 - diagnostic must surface corrupt/incomplete logs
        print(f"Unable to read MCAP session: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2) if args.json else _format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
