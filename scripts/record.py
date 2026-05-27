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
import shutil
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
            str_val = os.path.expandvars(str(value))
            args.append(f"--{full_key}={str_val}")
    return args


def _build_hardware_args(data: dict) -> list[str]:
    """Extract robot, cameras, and teleop CLI args from the hardware: section."""
    import json

    hardware = data.get("hardware")
    if not hardware or not isinstance(hardware, dict):
        return []

    args: list[str] = []
    sources = hardware.get("sources", {})

    # Find the motor source → robot config
    _calib_base = Path.home() / ".cache" / "huggingface" / "lerobot" / "calibration"
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
            # Auto-resolve calibration_dir from robot_type
            calib_dir = src.get("calibration_dir")
            if not calib_dir and robot_type:
                auto_dir = _calib_base / "robots" / robot_type
                if auto_dir.exists():
                    calib_dir = str(auto_dir)
            if calib_dir:
                args.append(f"--robot.calibration_dir={calib_dir}")
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
        teleop_type = teleop.get("type", "")
        if teleop_type:
            args.append(f"--teleop.type={teleop_type}")
        if teleop.get("port"):
            args.append(f"--teleop.port={teleop['port']}")
        if teleop.get("id"):
            args.append(f"--teleop.id={teleop['id']}")
        # Auto-resolve calibration_dir from teleop type
        calib_dir = teleop.get("calibration_dir")
        if not calib_dir and teleop_type:
            auto_dir = _calib_base / "teleoperators" / teleop_type
            if auto_dir.exists():
                calib_dir = str(auto_dir)
        if calib_dir:
            args.append(f"--teleop.calibration_dir={calib_dir}")

    return args


def _resolve_hf_user(data: dict) -> None:
    """Set HF_USER env var from recording.hf_user if not already set."""
    recording = data.get("recording")
    if not recording or not isinstance(recording, dict):
        return
    hf_user = recording.get("hf_user")
    if hf_user and not os.environ.get("HF_USER"):
        os.environ["HF_USER"] = str(hf_user)
        print(f"[DAM] HF_USER={hf_user} (from stackfile)")


def _dataset_cache_dir(repo_id: str) -> Path:
    return Path.home() / ".cache" / "huggingface" / "lerobot" / repo_id


_REQUIRED_META = ["meta/info.json", "meta/tasks.parquet", "meta/episodes.parquet"]


def _local_dataset_valid(repo_id: str) -> bool:
    """True if local cache has all required metadata files."""
    cache_dir = _dataset_cache_dir(repo_id)
    return cache_dir.exists() and all((cache_dir / f).exists() for f in _REQUIRED_META)


def _cleanup_stale_cache(repo_id: str) -> None:
    """Remove incomplete local dataset cache from a crashed run."""
    cache_dir = _dataset_cache_dir(repo_id)
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
        print(f"[DAM] Removed incomplete dataset cache: {cache_dir}")


def _load_recording_args(stackfile: str) -> list[str]:
    """Read hardware: + recording: from the stackfile and flatten to CLI args.

    Auto-detects issues:
    - hf_user in YAML → sets HF_USER env var if not already set
    - resume=true but dataset doesn't exist → downgrades to resume=false
    - Incomplete local cache from crashed runs → cleaned up automatically
    """
    path = Path(stackfile)
    if not path.exists():
        print(f"[DAM] Warning: stackfile not found: {stackfile}")
        return []

    with path.open() as f:
        data = yaml.safe_load(f)

    # Set HF_USER from YAML before expanding env vars
    _resolve_hf_user(data)

    args: list[str] = []

    # Hardware → robot / cameras / teleop args
    args.extend(_build_hardware_args(data))

    # Recording → dataset / display / resume args
    recording = data.get("recording")
    if recording and isinstance(recording, dict):
        # Don't forward hf_user to lerobot (it's a DAM-only key)
        recording = {k: v for k, v in recording.items() if k != "hf_user"}

        dataset_cfg = recording.get("dataset", {})
        repo_id = os.path.expandvars(str(dataset_cfg.get("repo_id", "")))
        is_resume = recording.get("resume", False)

        if repo_id and not _local_dataset_valid(repo_id):
            _cleanup_stale_cache(repo_id)

        if is_resume and repo_id and not _local_dataset_valid(repo_id):
            # Check HuggingFace — but only trust it if the repo has real data
            hf_valid = False
            try:
                from huggingface_hub import HfApi

                api = HfApi()
                info = api.dataset_info(repo_id, files_metadata=False)
                # A valid lerobot dataset must have all required meta files
                sibling_names = {s.rfilename for s in (info.siblings or [])}
                hf_valid = all(f in sibling_names for f in _REQUIRED_META)
            except Exception:
                pass

            if hf_valid:
                print(f"[DAM] Resuming from HuggingFace: {repo_id}")
            else:
                print(
                    f"[DAM] Dataset '{repo_id}' not found or incomplete — "
                    "starting fresh (resume=false)"
                )
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
        # lerobot's record() also logs via the root logger
        logging.getLogger().setLevel(logging.WARNING)

    # Fix VideoToolbox bitrate error: pyav calculates a default bit_rate that
    # VideoToolbox rejects with "Error setting bitrate property: -12900".
    # Workaround: patch _get_codec_options to skip quality params for VT codecs
    # and add allow_sw=1 as a fallback. Also patch _CameraEncoderThread to set
    # bit_rate=0 on the stream after creation (before first encode).
    import lerobot.datasets.video_utils as _vutils

    _VT_CODECS = {"h264_videotoolbox", "hevc_videotoolbox"}
    _orig_get_codec_options = _vutils._get_codec_options

    def _patched_get_codec_options(*args, **kwargs):  # type: ignore[no-untyped-def]
        vcodec = args[0] if args else kwargs.get("vcodec", "")
        if vcodec in _VT_CODECS:
            # Skip lerobot's quality options — they cause the bitrate error.
            # Let VideoToolbox use its own defaults + allow software fallback.
            opts = {}
            g = args[2] if len(args) > 2 else kwargs.get("g", 2)
            if g is not None:
                opts["g"] = str(g)
            opts["allow_sw"] = "1"
            opts["realtime"] = "1"
            return opts
        return _orig_get_codec_options(*args, **kwargs)

    _vutils._get_codec_options = _patched_get_codec_options  # type: ignore[assignment]

    with unittest.mock.patch.object(
        factory_mod, "make_default_processors", _patched_make_default_processors
    ):
        sys.argv = ["lerobot-record"] + lerobot_argv
        from lerobot.scripts.lerobot_record import record

        record()


if __name__ == "__main__":
    main()
