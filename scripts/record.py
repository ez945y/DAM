#!/usr/bin/env python3
"""Safe recording — runs lerobot-record with DAM safety guards.

All configuration lives in ONE stackfile YAML.  The ``recording:`` section
is flattened into lerobot-record CLI arguments; the rest configures DAM's
guard pipeline.

Usage:
    .venv/bin/python scripts/record.py                             # uses default stackfile
    .venv/bin/python scripts/record.py --stackfile=my_safety.yaml  # custom stackfile
    make record                                                    # same via Makefile
    make record ARGS="--stackfile=my_safety.yaml"

Any extra CLI args override the YAML values:
    .venv/bin/python scripts/record.py --dataset.num_episodes=20

DAM-only arguments (not forwarded to lerobot):
    --stackfile PATH   Safety stackfile (default: examples/stackfiles/safety.yaml)
    --dam-task NAME    Task name in stackfile (default: first task)
"""

from __future__ import annotations

import argparse
import os
import sys
import unittest.mock
from pathlib import Path

import yaml


def _flatten_dict(d: dict, prefix: str = "") -> list[str]:
    """Flatten nested dict into lerobot-style --dotted.key=value args."""
    args: list[str] = []
    for key, value in d.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            args.extend(_flatten_dict(value, full_key))
        elif isinstance(value, bool):
            args.append(f"--{full_key}={'true' if value else 'false'}")
        elif value is None:
            continue
        else:
            # Expand environment variables (e.g. ${HF_USER})
            str_val = str(value)
            str_val = os.path.expandvars(str_val)
            args.append(f"--{full_key}={str_val}")
    return args


def _build_hardware_args(data: dict) -> list[str]:
    """Extract robot, cameras, and teleop CLI args from the hardware: section.

    Mapping:
      hardware.sources (type: motor) → --robot.type, --robot.port, --robot.id
      hardware.sources (type: opencv) → --robot.cameras='{"name": {...}, ...}'
      hardware.teleop → --teleop.type, --teleop.port, --teleop.id
    """
    import json

    hardware = data.get("hardware")
    if not hardware or not isinstance(hardware, dict):
        return []

    args: list[str] = []
    sources = hardware.get("sources", {})

    # Find the motor source → robot config
    for _name, src in sources.items():
        if not isinstance(src, dict):
            continue
        if src.get("type") == "motor":
            robot_type = src.get("robot_type", hardware.get("preset", ""))
            if robot_type:
                args.append(f"--robot.type={robot_type}")
            if src.get("port"):
                args.append(f"--robot.port={src['port']}")
            if src.get("id"):
                args.append(f"--robot.id={src['id']}")
            break

    # Collect opencv sources → cameras JSON
    cameras: dict[str, dict] = {}
    for name, src in sources.items():
        if not isinstance(src, dict):
            continue
        if src.get("type") == "opencv":
            cam_cfg: dict = {"type": "opencv"}
            for k in ("index_or_path", "width", "height", "fps"):
                if k in src:
                    cam_cfg[k] = src[k]
            cameras[name] = cam_cfg

    if cameras:
        cameras_json = json.dumps(cameras)
        args.append(f"--robot.cameras={cameras_json}")

    # Teleop config
    teleop = hardware.get("teleop", {})
    if isinstance(teleop, dict) and teleop:
        if teleop.get("type"):
            args.append(f"--teleop.type={teleop['type']}")
        if teleop.get("port"):
            args.append(f"--teleop.port={teleop['port']}")
        if teleop.get("id"):
            args.append(f"--teleop.id={teleop['id']}")

    return args


def _dataset_exists_locally_or_remote(repo_id: str) -> bool:
    """Check if dataset exists locally (cached) or on HuggingFace."""
    import shutil
    from pathlib import Path as _Path

    cache_dir = _Path.home() / ".cache" / "huggingface" / "lerobot" / repo_id

    # Local cache exists and has valid metadata → real dataset
    if (cache_dir / "meta" / "info.json").exists():
        return True

    # Directory exists but no metadata → leftover from a failed run.
    # Remove it so lerobot can create a fresh dataset (it uses exist_ok=False).
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
        print(f"[DAM] Removed stale cache directory: {cache_dir}")

    # Check HuggingFace
    try:
        from huggingface_hub import HfApi

        api = HfApi()
        api.dataset_info(repo_id)
        return True
    except Exception:
        return False


def _load_recording_args(stackfile: str) -> list[str]:
    """Read hardware: + recording: from the stackfile and flatten to CLI args.

    - hardware.sources (motor) → --robot.*
    - hardware.sources (opencv) → --robot.cameras='{...}'
    - hardware.teleop → --teleop.*
    - recording.* → --dataset.*, --display_data, --resume, etc.

    Auto-detects issues:
    - resume=true but dataset doesn't exist → downgrades to resume=false
    """
    path = Path(stackfile)
    if not path.exists():
        print(f"[DAM] Warning: stackfile not found: {stackfile}")
        return []

    with path.open() as f:
        data = yaml.safe_load(f)

    args: list[str] = []

    # Hardware → robot / cameras / teleop args
    args.extend(_build_hardware_args(data))

    # Recording → dataset / display / resume args
    recording = data.get("recording")
    if recording and isinstance(recording, dict):
        # Safeguard: if resume=true but dataset doesn't exist, auto-downgrade
        dataset_cfg = recording.get("dataset", {})
        repo_id = dataset_cfg.get("repo_id", "")
        repo_id = os.path.expandvars(repo_id)
        is_resume = recording.get("resume", False)

        if is_resume and repo_id and not _dataset_exists_locally_or_remote(repo_id):
            print(f"[DAM] Dataset '{repo_id}' not found — starting fresh (resume=false)")
            recording = {**recording, "resume": False}

        args.extend(_flatten_dict(recording))

    return args


def main() -> None:
    # Split DAM-specific args from lerobot args.
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--stackfile",
        default="examples/stackfiles/safety.yaml",
        help="Path to safety stackfile (contains both DAM config and recording args)",
    )
    parser.add_argument(
        "--dam-task",
        default=None,
        dest="task",
        help="Task name in the stackfile (default: first task)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Show full lerobot-record args and config",
    )
    our_args, cli_overrides = parser.parse_known_args()

    # Build lerobot args: YAML defaults + CLI overrides (CLI wins)
    yaml_args = _load_recording_args(our_args.stackfile)

    # CLI overrides replace YAML args for the same key
    override_keys = {arg.split("=")[0] for arg in cli_overrides if arg.startswith("--")}
    yaml_filtered = [arg for arg in yaml_args if arg.split("=")[0] not in override_keys]
    lerobot_argv = yaml_filtered + cli_overrides

    print(f"[DAM] Stackfile: {our_args.stackfile}")
    print(f"[DAM] Task: {our_args.task or '(auto)'}")
    print(f"[DAM] Forwarding {len(lerobot_argv)} args to lerobot-record")
    if cli_overrides:
        print(f"[DAM] CLI overrides: {' '.join(cli_overrides)}")
    if our_args.verbose:
        for arg in lerobot_argv:
            print(f"       {arg}")

    # Monkey-patch make_default_processors to inject safety step.
    from dam.processor import SafetyProcessorStep

    _original = None

    def _patched_make_default_processors():  # type: ignore[no-untyped-def]
        teleop, robot_action, obs_proc = _original()
        step = SafetyProcessorStep(our_args.stackfile, task=our_args.task)
        robot_action.steps.insert(0, step)
        print("[DAM] SafetyProcessorStep injected into robot_action_processor ✓")
        return teleop, robot_action, obs_proc

    import lerobot.processor.factory as factory_mod

    _original = factory_mod.make_default_processors

    # Ensure .venv/bin is on PATH so rerun viewer binary can be found
    venv_bin = str(Path(sys.executable).parent)
    if venv_bin not in os.environ.get("PATH", ""):
        os.environ["PATH"] = venv_bin + os.pathsep + os.environ.get("PATH", "")

    # Suppress lerobot's verbose config dump unless --verbose
    import logging

    if not our_args.verbose:
        logging.getLogger("lerobot").setLevel(logging.WARNING)

    with unittest.mock.patch.object(
        factory_mod, "make_default_processors", _patched_make_default_processors
    ):
        sys.argv = ["lerobot-record"] + lerobot_argv
        from lerobot.scripts.lerobot_record import record

        record()


if __name__ == "__main__":
    main()
