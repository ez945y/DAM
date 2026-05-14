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
    del writer
    time.sleep(0.15)
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
                image_payloads[cam] = bytes(decoded[4])

    assert set(f"/dam/images/{cam}" for cam in cameras).issubset(topics)
    assert set(image_payloads) == set(cameras)
    assert {cam: image_payloads[cam] for cam in cameras} == {
        cam: f"jpeg-{cam}".encode() for cam in cameras
    }
    assert cycle is not None
    assert cycle[8] == list(cameras)

    service = McapSessionService(str(tmp_path))
    detail = service.get_cycle_detail(path.name, 42)
    assert detail is not None
    assert detail["active_cameras"] == list(cameras)
    assert set(detail["cameras"]) == set(cameras)
