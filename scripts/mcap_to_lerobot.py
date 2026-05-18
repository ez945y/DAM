#!/usr/bin/env python3
"""Convert DAM loopback MCAP sessions into a native LeRobot dataset.

The script only maps DAM MCAP messages into LeRobot frames. Dataset creation,
validation, episode writing, image/video handling, metadata, and stats are all
delegated to LeRobot's own ``LeRobotDataset`` APIs.
"""

from __future__ import annotations

import argparse
import base64
import json
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
from mcap.reader import make_reader

try:
    import msgpack
except ImportError:  # pragma: no cover - mcap extra should install this in DAM envs
    msgpack = None

from lerobot.datasets.lerobot_dataset import LeRobotDataset
from lerobot.datasets.utils import build_dataset_frame
from lerobot.utils.constants import ACTION, OBS_STR
from PIL import Image

_IDX_CYCLE = 0
_IDX_OBS_TIMESTAMP = 1
_IDX_ACTIVE_TASK = 6
_IDX_ACTIVE_CAMERAS = 8
_IDX_OBS_JOINT_POSITIONS = 9
_IDX_OBS_CHANNELS = 10
_IDX_ACTION_POSITIONS = 11
_IDX_VALIDATED_POSITIONS = 13


@dataclass
class DamFrame:
    cycle_id: int
    timestamp: float
    obs_positions: list[float] = field(default_factory=list)
    obs_channels: dict[str, list[float]] = field(default_factory=dict)
    action_positions: list[float] = field(default_factory=list)
    validated_positions: list[float] | None = None
    task: str | None = None
    sequence: int | None = None
    images: dict[str, np.ndarray] = field(default_factory=dict)


def _decode_payload(encoding: str, data: bytes) -> Any:
    if "msgpack" in encoding:
        if msgpack is None:
            raise RuntimeError("msgpack is required to read DAM msgpack MCAP sessions")
        return msgpack.unpackb(data, raw=False)
    if "json" in encoding or not encoding:
        return json.loads(data.decode("utf-8"))
    raise ValueError(f"Unsupported MCAP message encoding: {encoding}")


def _as_float_list(value: Any) -> list[float]:
    if value is None:
        return []
    if isinstance(value, np.ndarray):
        return [float(v) for v in value.reshape(-1)]
    if isinstance(value, list | tuple):
        return [float(v) for v in value]
    return [float(value)]


def _parse_cycle_message(data: Any, sequence: int) -> DamFrame | None:
    if isinstance(data, list | tuple):
        if len(data) <= _IDX_ACTION_POSITIONS:
            return None
        frame = DamFrame(
            cycle_id=int(data[_IDX_CYCLE]),
            timestamp=float(data[_IDX_OBS_TIMESTAMP]),
            obs_positions=_as_float_list(data[_IDX_OBS_JOINT_POSITIONS]),
            obs_channels={
                str(k): _as_float_list(v) for k, v in (data[_IDX_OBS_CHANNELS] or {}).items()
            }
            if len(data) > _IDX_OBS_CHANNELS and isinstance(data[_IDX_OBS_CHANNELS], dict)
            else {},
            action_positions=_as_float_list(data[_IDX_ACTION_POSITIONS]),
            validated_positions=_as_float_list(data[_IDX_VALIDATED_POSITIONS])
            if len(data) > _IDX_VALIDATED_POSITIONS and data[_IDX_VALIDATED_POSITIONS] is not None
            else None,
            task=str(data[_IDX_ACTIVE_TASK])
            if len(data) > _IDX_ACTIVE_TASK and data[_IDX_ACTIVE_TASK]
            else None,
            sequence=sequence,
        )
        if len(data) > _IDX_ACTIVE_CAMERAS and data[_IDX_ACTIVE_CAMERAS]:
            frame.obs_channels.setdefault(
                "_active_camera_count", [float(len(data[_IDX_ACTIVE_CAMERAS]))]
            )
        return frame

    if isinstance(data, dict) and "cycle_id" in data and "obs_joint_positions" in data:
        return DamFrame(
            cycle_id=int(data["cycle_id"]),
            timestamp=float(data.get("obs_timestamp", data.get("timestamp", 0.0))),
            obs_positions=_as_float_list(data.get("obs_joint_positions")),
            obs_channels={
                str(k): _as_float_list(v) for k, v in (data.get("obs_channels") or {}).items()
            },
            action_positions=_as_float_list(data.get("action_positions")),
            validated_positions=_as_float_list(data.get("validated_positions"))
            if data.get("validated_positions") is not None
            else None,
            task=data.get("active_task"),
            sequence=sequence,
        )
    return None


def _parse_image_message(data: Any, fallback_name: str) -> tuple[str, np.ndarray] | None:
    if isinstance(data, dict):
        name = str(data.get("camera_name") or fallback_name)
        raw = data.get("data")
    elif isinstance(data, list | tuple) and len(data) >= 5:
        name = str(data[0] or fallback_name)
        raw = data[4]
    else:
        return None

    if isinstance(raw, str):
        raw = base64.b64decode(raw)
    if not isinstance(raw, bytes | bytearray):
        return None
    image = Image.open(BytesIO(raw)).convert("RGB")
    return name, np.asarray(image)


def _read_mcap(path: Path) -> list[DamFrame]:
    frames: dict[int, DamFrame] = {}
    sequence_to_cycle: dict[int, int] = {}
    images_by_sequence: dict[int, dict[str, np.ndarray]] = {}

    with path.open("rb") as f:
        reader = make_reader(f)
        for _, channel, msg in reader.iter_messages(log_time_order=True):
            topic = channel.topic
            data = _decode_payload(channel.message_encoding, msg.data)

            if topic == "/dam/cycle":
                frame = _parse_cycle_message(data, msg.sequence)
                if frame is not None:
                    frames[frame.cycle_id] = frame
                    sequence_to_cycle[msg.sequence] = frame.cycle_id
                continue

            if topic == "/dam/obs" and isinstance(data, dict):
                cycle_id = int(data["cycle_id"])
                frame = frames.setdefault(
                    cycle_id,
                    DamFrame(cycle_id=cycle_id, timestamp=float(data.get("timestamp", 0.0))),
                )
                frame.obs_positions = _as_float_list(data.get("joint_positions"))
                frame.obs_channels.update(
                    {
                        str(k): _as_float_list(v)
                        for k, v in data.items()
                        if k not in {"cycle_id", "timestamp", "joint_positions"}
                    }
                )
                continue

            if topic == "/dam/action" and isinstance(data, dict):
                cycle_id = int(data["cycle_id"])
                frame = frames.setdefault(
                    cycle_id,
                    DamFrame(cycle_id=cycle_id, timestamp=float(data.get("timestamp", 0.0))),
                )
                frame.action_positions = _as_float_list(data.get("target_positions"))
                frame.validated_positions = _as_float_list(data.get("validated_positions")) or None
                continue

            if topic.startswith("/dam/images/"):
                fallback_name = topic.rsplit("/", 1)[-1]
                parsed = _parse_image_message(data, fallback_name)
                if parsed is not None:
                    name, image = parsed
                    images_by_sequence.setdefault(msg.sequence, {})[name] = image

    for seq, images in images_by_sequence.items():
        cycle_id = sequence_to_cycle.get(seq)
        if cycle_id is not None and cycle_id in frames:
            frames[cycle_id].images.update(images)

    return [
        f
        for f in sorted(frames.values(), key=lambda item: item.cycle_id)
        if f.obs_positions and f.action_positions
    ]


def _names(prefix: str, count: int) -> list[str]:
    return [f"{prefix}_{idx}" for idx in range(count)]


def _feature_safe_name(name: str) -> str:
    # LeRobot validates feature names and disallows "/"; keep the source key
    # otherwise intact so MCAP observation channels stay recognizable.
    return name.replace("/", "_")


def _vector_names(base: str, length: int) -> list[str]:
    return [base] if length == 1 else [f"{base}_{idx}" for idx in range(length)]


def _obs_channel_layout(frames: list[DamFrame]) -> list[tuple[str, str, list[str]]]:
    channel_lengths: dict[str, int] = {}
    for frame in frames:
        for channel, values in frame.obs_channels.items():
            if channel.startswith("_"):
                continue
            channel_lengths[channel] = max(channel_lengths.get(channel, 0), len(values))
    channel_specs: list[tuple[str, str, list[str]]] = []
    for channel, length in sorted(channel_lengths.items()):
        safe_channel = _feature_safe_name(channel)
        channel_specs.append((channel, safe_channel, _vector_names(safe_channel, length)))
    return channel_specs


def _state_values(
    frame: DamFrame,
    joint_names: list[str],
    channel_specs: list[tuple[str, str, list[str]]],
) -> dict[str, float]:
    values = {
        name: float(frame.obs_positions[idx]) if idx < len(frame.obs_positions) else 0.0
        for idx, name in enumerate(joint_names)
    }
    for channel, _safe_channel, channel_names in channel_specs:
        channel_values = frame.obs_channels.get(channel, [])
        for idx, name in enumerate(channel_names):
            values[name] = float(channel_values[idx]) if idx < len(channel_values) else 0.0
    return values


def _camera_shapes(frames: list[DamFrame]) -> dict[str, tuple[int, int, int]]:
    shapes: dict[str, tuple[int, int, int]] = {}
    for frame in frames:
        for cam_name, image in frame.images.items():
            shapes.setdefault(cam_name, tuple(int(v) for v in image.shape))
    return shapes


def _make_features(
    frames: list[DamFrame],
    joint_names: list[str],
    action_names: list[str],
    channel_specs: list[tuple[str, str, list[str]]],
    *,
    use_video: bool,
) -> dict[str, dict[str, Any]]:
    features: dict[str, dict[str, Any]] = {
        f"{OBS_STR}.state": {
            "dtype": "float32",
            "shape": (len(joint_names),),
            "names": joint_names,
        },
        ACTION: {
            "dtype": "float32",
            "shape": (len(action_names),),
            "names": action_names,
        },
    }

    for _channel, safe_channel, names in channel_specs:
        features[f"{OBS_STR}.{safe_channel}"] = {
            "dtype": "float32",
            "shape": (len(names),),
            "names": names,
        }

    for cam_name, shape in _camera_shapes(frames).items():
        features[f"{OBS_STR}.images.{_feature_safe_name(cam_name)}"] = {
            "dtype": "video" if use_video else "image",
            "shape": shape,
            "names": ["height", "width", "channels"],
        }

    return features


def convert(args: argparse.Namespace) -> None:
    input_path = Path(args.input).expanduser().resolve()
    root = Path(args.output_root).expanduser().resolve()
    frames = _read_mcap(input_path)
    if not frames:
        raise SystemExit(f"No usable DAM frames found in {input_path}")

    first = frames[0]
    joint_names = (
        args.joint_names.split(",")
        if args.joint_names
        else _names("joint", len(first.obs_positions))
    )
    if len(joint_names) != len(first.obs_positions):
        raise SystemExit(
            f"--joint-names has {len(joint_names)} names but MCAP observations have {len(first.obs_positions)} joints"
        )

    action_sample = (
        first.validated_positions
        if args.action_source == "validated" and first.validated_positions
        else first.action_positions
    )
    action_names = (
        joint_names
        if len(action_sample) == len(joint_names)
        else _names("action", len(action_sample))
    )
    channel_specs = _obs_channel_layout(frames)

    features = _make_features(
        frames,
        joint_names,
        action_names,
        channel_specs,
        use_video=not args.images_as_files,
    )

    dataset = LeRobotDataset.create(
        repo_id=args.repo_id,
        fps=args.fps,
        features=features,
        root=root,
        robot_type=args.robot_type,
        use_videos=not args.images_as_files,
        image_writer_processes=args.image_writer_processes,
        image_writer_threads=args.image_writer_threads,
        batch_encoding_size=args.batch_encoding_size,
    )

    for frame in frames:
        action_positions = (
            frame.validated_positions
            if args.action_source == "validated" and frame.validated_positions
            else frame.action_positions
        )
        obs_values = _state_values(frame, joint_names, channel_specs)
        obs_values.update({_feature_safe_name(k): v for k, v in frame.images.items()})
        action_values = {
            name: float(action_positions[idx]) if idx < len(action_positions) else 0.0
            for idx, name in enumerate(action_names)
        }
        item = {
            **build_dataset_frame(dataset.features, obs_values, prefix=OBS_STR),
            **build_dataset_frame(dataset.features, action_values, prefix=ACTION),
            "task": args.task or frame.task or "dam_mcap_replay",
        }
        dataset.add_frame(item)

    dataset.save_episode(parallel_encoding=not args.no_parallel_encoding)
    print(f"Wrote {len(frames)} frames to LeRobot dataset at {dataset.root}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="DAM loopback .mcap file")
    parser.add_argument("--output-root", required=True, help="Output dataset root directory")
    parser.add_argument(
        "--repo-id", required=True, help="LeRobot dataset repo id, e.g. local/dam_session"
    )
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--robot-type", default="dam")
    parser.add_argument("--task", default=None, help="Natural-language task label for the episode")
    parser.add_argument(
        "--joint-names",
        default=None,
        help="Comma-separated joint names; inferred as joint_0... by default",
    )
    parser.add_argument(
        "--action-source",
        choices=("validated", "target"),
        default="validated",
        help="Use validated positions when present, or raw policy target positions",
    )
    parser.add_argument(
        "--images-as-files",
        action="store_true",
        help="Store image features as images instead of videos",
    )
    parser.add_argument("--image-writer-processes", type=int, default=0)
    parser.add_argument("--image-writer-threads", type=int, default=0)
    parser.add_argument("--batch-encoding-size", type=int, default=1)
    parser.add_argument("--no-parallel-encoding", action="store_true")
    convert(parser.parse_args())


if __name__ == "__main__":
    main()
