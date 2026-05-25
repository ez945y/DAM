from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import msgpack
from mcap.writer import Writer

from scripts import joint_diagnostics, mcap_triage


def _cycle(
    cycle_id: int,
    obs: list[float],
    validated: list[float],
    *,
    clamped: bool = True,
) -> dict:
    return {
        "cycle_id": cycle_id,
        "obs_timestamp": float(cycle_id) / 30.0,
        "obs_joint_positions": obs,
        "action_positions": [2.0, 0.0],
        "validated_positions": validated,
        "has_clamp": clamped,
        "has_violation": False,
        "active_task": "pick",
        "active_boundaries": ["joint_velocity_limit"],
        "active_context": "normal",
        "failure_guard_names": ["joint_velocity_limit"] if clamped else [],
        "failure_decisions": ["CLAMP"] if clamped else [],
        "failure_reasons": ["limited velocity"] if clamped else [],
    }


def test_latest_session_ignores_empty_files_and_selects_newest(tmp_path: Path) -> None:
    old = tmp_path / "session_old.mcap"
    latest = tmp_path / "session_latest.mcap"
    empty = tmp_path / "session_empty.mcap"
    old.write_bytes(b"x" * 100)
    latest.write_bytes(b"x" * 101)
    empty.write_bytes(b"")
    old.touch()
    latest.touch()

    assert mcap_triage.latest_session(tmp_path) == latest


def test_summary_flags_validated_command_without_observed_response() -> None:
    cycles = [_cycle(i, [0.0, i * 0.02], [0.05, (i * 0.02) + 0.05]) for i in range(5)]
    report = mcap_triage.summarize_cycles(cycles, runtime_status={"state": "stopped"})

    assert report["read_only"] is True
    assert report["session"]["clamp_pct"] == 100.0
    assert report["joints"][0]["command_without_response"] is True
    assert report["joints"][1]["command_without_response"] is False
    assert {finding["code"] for finding in report["findings"]} == {
        "runtime_not_running",
        "all_cycles_clamped",
        "command_without_response",
    }
    json.dumps(report)


def test_comparison_reports_large_initial_pose_change_only() -> None:
    current = mcap_triage.summarize_cycles(
        [_cycle(0, [0.0, -1.7], [0.05, -1.65]), _cycle(1, [0.0, -1.7], [0.05, -1.65])],
        path=Path("/tmp/current.mcap"),
    )
    baseline = mcap_triage.summarize_cycles(
        [_cycle(0, [0.1, 0.25], [0.15, 0.30]), _cycle(1, [0.1, 0.25], [0.15, 0.30])],
        path=Path("/tmp/baseline.mcap"),
    )

    comparison = mcap_triage.compare_reports(current, baseline)

    assert comparison["available"] is True
    assert comparison["large_start_pose_changes"] == ["J2"]
    assert "only when robot, task, and calibration match" in comparison["caveat"]


def test_empty_session_is_json_safe_warning() -> None:
    report = mcap_triage.summarize_cycles([], path=Path("/tmp/empty.mcap"))
    assert report["schema"] == "dam.mcap_triage.v1"
    assert report["session"]["cycles"] == 0
    assert report["findings"][0]["code"] == "empty_session"


def test_partial_empty_session_preserves_incomplete_log_evidence() -> None:
    report = mcap_triage.summarize_cycles([], read_info={"read_status": "partial"})
    assert report["read_only"] is True
    assert report["session"]["read_status"] == "partial"
    assert report["findings"][0]["code"] == "partial_session"


def _write_mcap(path: Path, records: list[tuple[str, str, dict]]) -> None:
    with path.open("wb") as fh:
        writer = Writer(fh)
        writer.start()
        schema_id = writer.register_schema(name="test", encoding="jsonschema", data=b"{}")
        channels: dict[tuple[str, str], int] = {}
        for sequence, (topic, encoding, record) in enumerate(records):
            key = (topic, encoding)
            if key not in channels:
                channels[key] = writer.register_channel(
                    topic=topic, message_encoding=encoding, schema_id=schema_id
                )
            channel_id = channels[key]
            payload = (
                msgpack.packb(record, use_bin_type=True)
                if "msgpack" in encoding
                else json.dumps(record).encode()
            )
            writer.add_message(
                channel_id=channel_id,
                log_time=sequence + 1,
                publish_time=sequence + 1,
                data=payload,
            )
        writer.finish()


def test_reader_joins_split_json_and_combined_msgpack_layouts(tmp_path: Path) -> None:
    compact_path = tmp_path / "session_compact.mcap"
    split_path = tmp_path / "session_split.mcap"
    cycle = _cycle(0, [0.0, 0.0], [0.05, 0.05])
    _write_mcap(compact_path, [("/dam/cycle", "application/x-msgpack", cycle)])
    _write_mcap(
        split_path,
        [
            (
                "/dam/cycle",
                "json",
                {
                    k: cycle[k]
                    for k in (
                        "cycle_id",
                        "has_clamp",
                        "has_violation",
                        "active_task",
                        "active_boundaries",
                        "failure_guard_names",
                        "failure_decisions",
                        "failure_reasons",
                    )
                },
            ),
            (
                "/dam/obs",
                "json",
                {"cycle_id": 0, "timestamp": 0.0, "joint_positions": [0.0, 0.0]},
            ),
            (
                "/dam/action",
                "json",
                {
                    "cycle_id": 0,
                    "target_positions": [2.0, 0.0],
                    "validated_positions": [0.05, 0.05],
                    "was_clamped": True,
                },
            ),
        ],
    )

    compact, compact_info = mcap_triage.read_session(compact_path)
    split, split_info = mcap_triage.read_session(split_path)
    compact_report = mcap_triage.summarize_cycles(compact, read_info=compact_info)
    split_report = mcap_triage.summarize_cycles(split, read_info=split_info)

    assert compact_report["session"]["clamped_cycles"] == split_report["session"]["clamped_cycles"]
    assert compact_report["joints"] == split_report["joints"]
    assert compact_report["session"]["encodings"] == ["application/x-msgpack"]
    assert split_report["session"]["encodings"] == ["json"]


def test_rejected_cycle_is_not_reported_as_a_sent_command() -> None:
    cycle = _cycle(0, [0.0, 0.0], [0.05, 0.05])
    cycle["has_violation"] = True
    cycle["validated_positions"] = None
    cycle["failure_guard_names"] = ["workspace"]
    cycle["failure_decisions"] = ["REJECT"]

    report = mcap_triage.summarize_cycles([cycle])

    assert report["joints"] == []
    assert "rejected_without_validated_command" in {
        finding["code"] for finding in report["findings"]
    }


def test_legacy_joint_diagnostics_default_never_starts_a_session(
    tmp_path: Path, monkeypatch
) -> None:
    sessions = tmp_path / "data" / "robot" / "sessions"
    sessions.mkdir(parents=True)
    existing = sessions / "session_existing.mcap"
    existing.write_bytes(b"x" * 100)
    run_session = MagicMock(side_effect=AssertionError("must not actuate hardware"))
    monkeypatch.setattr(joint_diagnostics, "ROOT", tmp_path)
    monkeypatch.setattr(joint_diagnostics, "run_session", run_session)
    monkeypatch.setattr(joint_diagnostics, "read_cycles", lambda _path: [])
    monkeypatch.setattr(joint_diagnostics, "analyse", lambda _cycles, control_freq: {})
    monkeypatch.setattr(joint_diagnostics, "print_report", lambda _report: None)
    monkeypatch.setattr(sys, "argv", ["joint_diagnostics.py"])

    joint_diagnostics.main()

    run_session.assert_not_called()
