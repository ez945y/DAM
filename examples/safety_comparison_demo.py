"""Offline A/B comparison with a real MCAP and an executable regression case.

Run ``.venv/bin/python -m examples.safety_comparison_demo``. Add
``--install-for-console`` to save the two Stackfiles in the console library and
its recording in data/robot/sessions. No runtime adapters or hardware are opened.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

import yaml

from dam.services.safety_comparison import compare, config_snapshot, make_regression, run_regression


def create_demo(directory: Path) -> tuple[Path, Path, Path]:
    from mcap.writer import Writer

    directory.mkdir(parents=True, exist_ok=True)
    configs = []
    for name, limit in (("comparison-roomy", 2.0), ("comparison-precise", 1.0)):
        path = directory / f"{name}.yaml"
        config = {
            "version": "1",
            "hardware": {"joint_names": ["demo_joint"], "degrees_mode": False},
            "guards": [{"L1": "motion"}],
            "boundaries": {
                "joint_range": {
                    "layer": "L1",
                    "type": "single",
                    "nodes": [
                        {
                            "callback": "joint_position_limits",
                            "params": {"lower": [-limit], "upper": [limit]},
                        }
                    ],
                }
            },
            "tasks": {"comparison_demo": {"boundaries": ["joint_range"]}},
            "safety": {"control_hz": 30, "enforcement_mode": "enforce"},
        }
        path.write_text(yaml.safe_dump(config, sort_keys=False))
        configs.append(path)
    recording = directory / "session_safety_comparison_demo.mcap"
    with recording.open("wb") as stream:
        writer = Writer(stream)
        writer.start()
        schema = writer.register_schema("json", "jsonschema", b"{}")
        channel = writer.register_channel("/dam/cycle", "json", schema)
        for cid, target in enumerate([0.0, 0.5, 1.5, -1.5, 0.75]):
            payload = {
                "cycle_id": cid,
                "obs_timestamp": 10 + cid / 30,
                "active_task": "comparison_demo",
                "obs_joint_positions": [0.0],
                "obs_channels": {},
                "action_positions": [target],
                "action_velocities": None,
                "has_violation": False,
                "has_clamp": False,
            }
            timestamp = int(payload["obs_timestamp"] * 1_000_000_000)
            writer.add_message(channel, timestamp, json.dumps(payload).encode(), timestamp)
        writer.finish()
    return recording, configs[0], configs[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-for-console", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="dam-comparison-") as folder:
        recording, left, right = create_demo(Path(folder))
        result = compare(
            recording,
            config_snapshot(left.read_text(), left.stem),
            config_snapshot(right.read_text(), right.stem),
        )
        case = make_regression(
            result,
            name="Range violation must clamp",
            side="right",
            cycle_start=2,
            cycle_end=3,
            expected_decision="CLAMP",
        )
        regression = run_regression(recording, case)
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "compared": result["compared"],
                    "distributions": result["distributions"],
                    "differences": result["difference_count"],
                    "regression": regression,
                },
                indent=2,
            )
        )
        if result["status"] != "complete" or regression["status"] != "pass":
            raise SystemExit("Offline demonstration failed")
        if args.install_for_console:
            root = Path(__file__).resolve().parents[1]
            destinations = [
                (recording, root / "data/robot/sessions" / recording.name),
                (left, root / "data/stackfiles" / left.name),
                (right, root / "data/stackfiles" / right.name),
            ]
            existing = [str(dst) for _, dst in destinations if dst.exists()]
            if existing:
                raise SystemExit(f"Refusing to replace existing demo artifacts: {existing}")
            for src, dst in destinations:
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(src.read_bytes())
                print(f"Installed {dst}")
            print(
                "Open /replay and click Refresh choices, then select session_safety_comparison_demo.mcap."
            )


if __name__ == "__main__":
    main()
