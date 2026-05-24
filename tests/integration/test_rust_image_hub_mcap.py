from __future__ import annotations

import json
import time
from pathlib import Path

import msgpack
import pytest
from mcap.reader import make_reader

from dam.services.mcap_sessions import McapSessionService


def _write_violation_mcap(tmp_path: Path, cameras: tuple[str, ...]) -> Path:
    from dam_rs import ImageHub, McapWriter

    now = time.time()
    path = tmp_path / "session_multi_camera.mcap"
    hub = ImageHub(2.0)
    for idx, camera in enumerate(cameras):
        hub.submit_jpeg(
            camera,
            now - 0.05 + idx * 0.001,
            2 + idx,
            3 + idx,
            f"jpeg-{camera}".encode(),
        )

    writer = McapWriter()
    writer.attach_image_hub(hub, 2.0, False)
    writer.start(str(path))
    writer.write_cycle(
        json.dumps(
            {
                "cycle_id": 42,
                "obs_timestamp": now,
                "has_violation": True,
                "has_clamp": False,
                "violated_layer_mask": 1,
                "clamped_layer_mask": 0,
                "active_task": "task",
                "active_boundaries": ["boundary"],
                "active_cameras": list(cameras),
                "obs_joint_positions": [0.0],
                "obs_channels": {},
                "action_positions": [0.0],
                "action_velocities": None,
                "validated_positions": None,
                "validated_velocities": None,
                "was_clamped": False,
                "fallback_triggered": None,
                "guard_results": [],
                "latency_stages": {"source": 1.0, "total": 2.0},
                "latency_layers": {},
                "latency_guards": {},
                "image_data": [],
                "config_version": 0,
            }
        )
    )
    time.sleep(0.15)
    writer.stop()
    del writer
    time.sleep(0.05)
    return path


@pytest.mark.parametrize(
    "cameras",
    [
        ("top",),
        ("top", "wrist"),
        ("top", "wrist", "side"),
    ],
)
def test_rust_image_hub_writes_and_reader_returns_all_cameras(
    tmp_path: Path,
    cameras: tuple[str, ...],
) -> None:
    path = _write_violation_mcap(tmp_path, cameras)

    topics: list[str] = []
    cycle = None
    image_payloads: dict[str, bytes] = {}
    with path.open("rb") as f:
        for _schema, channel, message in make_reader(f).iter_messages():
            topics.append(channel.topic)
            decoded = msgpack.unpackb(message.data, raw=False)
            if channel.topic == "/dam/cycle":
                cycle = decoded
            elif channel.topic.startswith("/dam/images/"):
                cam = channel.topic.rsplit("/", 1)[1]
                img_data = decoded.get("data") if isinstance(decoded, dict) else decoded[4]
                image_payloads[cam] = bytes(img_data)

    assert set(f"/dam/images/{cam}" for cam in cameras).issubset(topics)
    assert set(image_payloads) == set(cameras)
    assert {cam: image_payloads[cam] for cam in cameras} == {
        cam: f"jpeg-{cam}".encode() for cam in cameras
    }
    assert cycle is not None
    active_cams = cycle.get("active_cameras") if isinstance(cycle, dict) else cycle[8]
    assert active_cams == list(cameras)

    service = McapSessionService(str(tmp_path))
    detail = service.get_cycle_detail(path.name, 42)
    assert detail is not None
    assert detail["active_cameras"] == list(cameras)
    assert set(detail["cameras"]) == set(cameras)


def test_rust_image_hub_captures_clamp_window_once_per_incident(tmp_path: Path) -> None:
    from dam_rs import ImageHub, McapWriter

    now = time.time()
    path = tmp_path / "session_clamp_incident.mcap"
    hub = ImageHub(2.0)
    cameras = ("top", "wrist")
    for idx, camera in enumerate(cameras):
        hub.submit_jpeg(camera, now - 0.05 + idx * 0.001, 640, 480, f"jpeg-{camera}".encode())

    writer = McapWriter()
    writer.attach_image_hub(hub, 2.0, True)
    writer.start(str(path))
    for i in range(5):
        writer.write_cycle(
            json.dumps(
                {
                    "cycle_id": i,
                    "obs_timestamp": now + i * 0.033,
                    "has_violation": False,
                    "has_clamp": True,
                    "violated_layer_mask": 0,
                    "clamped_layer_mask": 1,
                    "active_task": "task",
                    "active_boundaries": ["boundary"],
                    "active_cameras": list(cameras),
                    "obs_joint_positions": [0.0],
                    "obs_channels": {},
                    "action_positions": [0.0],
                    "action_velocities": None,
                    "validated_positions": None,
                    "validated_velocities": None,
                    "was_clamped": True,
                    "fallback_triggered": None,
                    "guard_results": [],
                    "latency_stages": {"source": 1.0, "total": 2.0},
                    "latency_layers": {},
                    "latency_guards": {},
                    "image_data": [],
                    "config_version": 0,
                }
            )
        )
    time.sleep(0.2)
    writer.stop()
    del writer
    time.sleep(0.05)

    image_topics: list[str] = []
    cycle_count = 0
    with path.open("rb") as f:
        for _schema, channel, _message in make_reader(f).iter_messages():
            if channel.topic == "/dam/cycle":
                cycle_count += 1
            elif channel.topic.startswith("/dam/images/"):
                image_topics.append(channel.topic)

    assert cycle_count == 5
    assert sorted(image_topics) == sorted(f"/dam/images/{cam}" for cam in cameras)


def test_rust_image_hub_does_not_write_frames_before_recording_cursor(tmp_path: Path) -> None:
    from dam_rs import ImageHub, McapWriter

    now = time.time()
    path = tmp_path / "session_start_gate.mcap"
    hub = ImageHub(2.0)
    hub.submit_jpeg("top", now - 0.5, 640, 480, b"before-start")
    cursor = hub.current_sequence()
    hub.submit_jpeg("top", now + 0.01, 640, 480, b"after-start")

    writer = McapWriter()
    writer.attach_image_hub(hub, 2.0, False, cursor)
    writer.start(str(path))
    writer.write_cycle(
        json.dumps(
            {
                "cycle_id": 1,
                "obs_timestamp": now + 0.02,
                "has_violation": True,
                "has_clamp": False,
                "violated_layer_mask": 1,
                "clamped_layer_mask": 0,
                "active_task": "task",
                "active_boundaries": ["boundary"],
                "active_cameras": ["top"],
                "obs_joint_positions": [0.0],
                "obs_channels": {},
                "action_positions": [0.0],
                "action_velocities": None,
                "validated_positions": None,
                "validated_velocities": None,
                "was_clamped": False,
                "fallback_triggered": None,
                "guard_results": [],
                "latency_stages": {"source": 1.0, "total": 2.0},
                "latency_layers": {},
                "latency_guards": {},
                "image_data": [],
                "config_version": 0,
            }
        )
    )
    time.sleep(0.15)
    writer.stop()
    del writer
    time.sleep(0.05)

    payloads: list[bytes] = []
    with path.open("rb") as f:
        for _schema, channel, message in make_reader(f).iter_messages():
            if channel.topic.startswith("/dam/images/"):
                decoded = msgpack.unpackb(message.data, raw=False)
                payloads.append(
                    bytes(decoded.get("data") if isinstance(decoded, dict) else decoded[4])
                )

    assert payloads == [b"after-start"]


def test_rust_image_hub_streams_new_frames_across_cycles(tmp_path: Path) -> None:
    from dam_rs import ImageHub, McapWriter

    now = time.time()
    path = tmp_path / "session_stream.mcap"
    hub = ImageHub(2.0)
    hub.submit_jpeg("top", now - 0.1, 640, 480, b"before-start")
    cursor = hub.current_sequence()
    hub.submit_jpeg("top", now + 0.01, 640, 480, b"top-1")
    hub.submit_jpeg("wrist", now + 0.015, 640, 480, b"wrist-1")
    hub.submit_jpeg("top", now + 0.04, 640, 480, b"top-2")

    writer = McapWriter()
    writer.attach_image_hub(hub, 2.0, False, cursor)
    writer.start(str(path))
    for cycle_id, obs_timestamp in ((1, now + 0.02), (2, now + 0.05)):
        writer.write_cycle(
            json.dumps(
                {
                    "cycle_id": cycle_id,
                    "obs_timestamp": obs_timestamp,
                    "has_violation": False,
                    "has_clamp": False,
                    "violated_layer_mask": 0,
                    "clamped_layer_mask": 0,
                    "active_task": "task",
                    "active_boundaries": ["boundary"],
                    "active_cameras": ["top", "wrist"],
                    "obs_joint_positions": [0.0],
                    "obs_channels": {},
                    "action_positions": [0.0],
                    "action_velocities": None,
                    "validated_positions": None,
                    "validated_velocities": None,
                    "was_clamped": False,
                    "fallback_triggered": None,
                    "guard_results": [],
                    "latency_stages": {"source": 1.0, "total": 2.0},
                    "latency_layers": {},
                    "latency_guards": {},
                    "image_data": [],
                    "config_version": 0,
                }
            )
        )
    time.sleep(0.15)
    writer.stop()
    del writer
    time.sleep(0.05)

    payloads_by_topic: dict[str, list[bytes]] = {}
    with path.open("rb") as f:
        for _schema, channel, message in make_reader(f).iter_messages():
            if channel.topic.startswith("/dam/images/"):
                decoded = msgpack.unpackb(message.data, raw=False)
                payloads_by_topic.setdefault(channel.topic, []).append(
                    bytes(decoded.get("data") if isinstance(decoded, dict) else decoded[4])
                )

    assert payloads_by_topic == {
        "/dam/images/top": [b"top-1", b"top-2"],
        "/dam/images/wrist": [b"wrist-1"],
    }


def test_rust_image_hub_flushes_tail_frames_on_stop(tmp_path: Path) -> None:
    from dam_rs import ImageHub, McapWriter

    now = time.time()
    path = tmp_path / "session_stop_tail.mcap"
    hub = ImageHub(2.0)
    cursor = hub.current_sequence()

    writer = McapWriter()
    writer.attach_image_hub(hub, 2.0, False, cursor)
    writer.start(str(path))
    hub.submit_jpeg("top", now + 0.01, 640, 480, b"top-cycle")
    writer.write_cycle(
        json.dumps(
            {
                "cycle_id": 1,
                "obs_timestamp": now + 0.02,
                "has_violation": False,
                "has_clamp": False,
                "violated_layer_mask": 0,
                "clamped_layer_mask": 0,
                "active_task": "task",
                "active_boundaries": ["boundary"],
                "active_cameras": ["top", "wrist"],
                "obs_joint_positions": [0.0],
                "obs_channels": {},
                "action_positions": [0.0],
                "action_velocities": None,
                "validated_positions": None,
                "validated_velocities": None,
                "was_clamped": False,
                "fallback_triggered": None,
                "guard_results": [],
                "latency_stages": {"source": 1.0, "total": 2.0},
                "latency_layers": {},
                "latency_guards": {},
                "image_data": [],
                "config_version": 0,
            }
        )
    )
    hub.submit_jpeg("top", now + 0.04, 640, 480, b"top-tail")
    hub.submit_jpeg("wrist", now + 0.045, 640, 480, b"wrist-tail")
    hub.submit_jpeg("top", now + 0.07, 640, 480, b"after-stop")
    time.sleep(0.15)
    writer.stop(now + 0.05)
    del writer
    time.sleep(0.05)

    payloads_by_topic: dict[str, list[bytes]] = {}
    with path.open("rb") as f:
        for _schema, channel, message in make_reader(f).iter_messages():
            if channel.topic.startswith("/dam/images/"):
                decoded = msgpack.unpackb(message.data, raw=False)
                payloads_by_topic.setdefault(channel.topic, []).append(
                    bytes(decoded.get("data") if isinstance(decoded, dict) else decoded[4])
                )

    assert payloads_by_topic == {
        "/dam/images/top": [b"top-cycle", b"top-tail"],
        "/dam/images/wrist": [b"wrist-tail"],
    }
