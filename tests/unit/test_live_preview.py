"""Tests for the live preview pipeline — binary protocol, WS coalescing, telemetry push with camera JPEGs.

Verifies:
  1. Binary protocol v1/v2 encoding and camera-name parsing
  2. TelemetryService pushes JPEG payloads to subscriber queues
  3. WS send loop coalescing: stale frames are dropped, only latest per camera survives
  4. _serialise_cycle includes active_cameras when camera_jpegs are provided
  5. Frame throughput: push() stays under 1 ms even with large JPEGs
"""

from __future__ import annotations

import asyncio
import time

import numpy as np
import pytest

from dam.services.routers.telemetry import _parse_camera_name
from dam.services.telemetry import BINARY_PROTOCOL_VERSION, TelemetryService, _serialise_cycle
from dam.types.action import ActionProposal
from dam.types.risk import CycleResult, RiskLevel

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_cycle(cycle_id: int = 0) -> CycleResult:
    return CycleResult(
        cycle_id=cycle_id,
        trace_id=f"trace-{cycle_id}",
        validated_action=None,
        original_proposal=ActionProposal(target_joint_positions=np.zeros(6)),
        was_clamped=False,
        was_rejected=False,
        guard_results=[],
        fallback_triggered=None,
        latency_ms={"total": 5.0},
        risk_level=RiskLevel.NORMAL,
    )


def _make_jpeg(size_kb: int = 30) -> bytes:
    """Return fake JPEG bytes of approximately `size_kb` KB."""
    return b"\xff\xd8\xff" + b"\x00" * (size_kb * 1024 - 3)


def _encode_binary_v2(cam: str, cycle_id: int, jpeg: bytes) -> bytes:
    name_bytes = cam.encode()
    return (
        BINARY_PROTOCOL_VERSION
        + cycle_id.to_bytes(4, "big")
        + len(name_bytes).to_bytes(1, "big")
        + name_bytes
        + jpeg
    )


def _encode_binary_v1(cam: str, jpeg: bytes) -> bytes:
    name_bytes = cam.encode()
    return b"\x01" + len(name_bytes).to_bytes(1, "big") + name_bytes + jpeg


# ── Binary Protocol Parsing ──────────────────────────────────────────────────


class TestParseCameraName:
    def test_v2_basic(self):
        payload = _encode_binary_v2("cam0", 42, b"\xff\xd8\xff")
        assert _parse_camera_name(payload) == "cam0"

    def test_v2_long_name(self):
        name = "front_left_camera_hd"
        payload = _encode_binary_v2(name, 0, _make_jpeg())
        assert _parse_camera_name(payload) == name

    def test_v1_basic(self):
        payload = _encode_binary_v1("cam1", b"\xff\xd8\xff")
        assert _parse_camera_name(payload) == "cam1"

    def test_empty_payload(self):
        assert _parse_camera_name(b"") is None
        assert _parse_camera_name(b"\x00") is None
        assert _parse_camera_name(b"\x02\x00") is None

    def test_unknown_magic(self):
        assert _parse_camera_name(b"\x99\x00\x00\x00\x00\x00\x00") is None

    def test_v2_truncated_header(self):
        assert _parse_camera_name(b"\x02\x00\x00\x00") is None

    def test_multiple_cameras_distinct(self):
        p1 = _encode_binary_v2("cam_left", 1, _make_jpeg())
        p2 = _encode_binary_v2("cam_right", 1, _make_jpeg())
        assert _parse_camera_name(p1) == "cam_left"
        assert _parse_camera_name(p2) == "cam_right"


# ── Serialise Cycle with Camera JPEGs ────────────────────────────────────────


class TestSerialiseCycleWithCameras:
    def test_active_cameras_included(self):
        result = _make_cycle(1)
        jpegs = {"cam0": _make_jpeg(), "cam1": _make_jpeg()}
        event = _serialise_cycle(result, camera_jpegs=jpegs)
        assert set(event["active_cameras"]) == {"cam0", "cam1"}

    def test_no_cameras_no_key(self):
        event = _serialise_cycle(_make_cycle(0))
        assert "active_cameras" not in event

    def test_empty_cameras_no_key(self):
        event = _serialise_cycle(_make_cycle(0), camera_jpegs=None)
        assert "active_cameras" not in event


# ── TelemetryService Push with Camera JPEGs ──────────────────────────────────


class TestTelemetryPushWithJpegs:
    def _make_svc_with_sub(self):
        svc = TelemetryService(history_size=100)
        loop = asyncio.new_event_loop()
        svc.attach_loop(loop)
        q = svc.subscribe()
        return svc, loop, q

    @staticmethod
    def _drain(loop, q):
        """Run pending loop callbacks then drain the queue."""
        loop.call_soon(loop.stop)
        loop.run_forever()
        items = []
        while not q.empty():
            items.append(q.get_nowait())
        return items

    def test_push_sends_json_and_binary_to_queue(self):
        svc, loop, q = self._make_svc_with_sub()
        try:
            jpeg = _make_jpeg(10)
            svc.push(_make_cycle(0), camera_jpegs={"cam0": jpeg})

            items = self._drain(loop, q)
            json_items = [i for i in items if isinstance(i, dict)]
            bin_items = [i for i in items if isinstance(i, bytes)]
            assert len(json_items) == 1
            assert json_items[0]["type"] == "cycle"
            assert len(bin_items) == 1
            assert _parse_camera_name(bin_items[0]) == "cam0"
        finally:
            svc.unsubscribe(q)
            loop.close()

    def test_push_multi_camera(self):
        svc, loop, q = self._make_svc_with_sub()
        try:
            jpegs = {"cam_left": _make_jpeg(5), "cam_right": _make_jpeg(5)}
            svc.push(_make_cycle(0), camera_jpegs=jpegs)

            items = self._drain(loop, q)
            bin_items = [i for i in items if isinstance(i, bytes)]
            cameras = {_parse_camera_name(b) for b in bin_items}
            assert cameras == {"cam_left", "cam_right"}
        finally:
            svc.unsubscribe(q)
            loop.close()

    def test_push_no_jpegs_sends_only_json(self):
        svc, loop, q = self._make_svc_with_sub()
        try:
            svc.push(_make_cycle(0), camera_jpegs=None)

            items = self._drain(loop, q)
            assert all(isinstance(i, dict) for i in items)
            assert len(items) == 1
        finally:
            svc.unsubscribe(q)
            loop.close()

    def test_binary_payload_contains_jpeg(self):
        svc, loop, q = self._make_svc_with_sub()
        try:
            jpeg = _make_jpeg(20)
            svc.push(_make_cycle(5), camera_jpegs={"cam0": jpeg})

            items = self._drain(loop, q)
            bin_items = [i for i in items if isinstance(i, bytes)]
            assert len(bin_items) == 1
            assert bin_items[0].endswith(jpeg[-100:])
        finally:
            svc.unsubscribe(q)
            loop.close()


# ── WS Coalescing Logic ─────────────────────────────────────────────────────


class TestWSCoalescing:
    """Test the coalescing pattern used in the WS send loop.

    When the consumer falls behind, the send loop drains the queue and
    keeps only the latest cycle JSON + latest binary per camera.
    This test replicates that logic outside of the actual WS handler.
    """

    @staticmethod
    def _coalesce(batch: list) -> tuple[dict | None, dict[str, bytes], list]:
        """Replicate the coalescing logic from _stream_telemetry."""
        latest_cycle = None
        latest_binary: dict[str, bytes] = {}
        misc: list = []
        for item in batch:
            if isinstance(item, bytes):
                cam = _parse_camera_name(item)
                if cam is not None:
                    latest_binary[cam] = item
                else:
                    misc.append(item)
            elif isinstance(item, dict) and item.get("type") == "cycle":
                latest_cycle = item
            else:
                misc.append(item)
        return latest_cycle, latest_binary, misc

    def test_coalesce_keeps_latest_cycle(self):
        batch = [
            {"type": "cycle", "cycle_id": 1},
            {"type": "cycle", "cycle_id": 2},
            {"type": "cycle", "cycle_id": 3},
        ]
        cycle, binary, _ = self._coalesce(batch)
        assert cycle["cycle_id"] == 3
        assert len(binary) == 0

    def test_coalesce_keeps_latest_per_camera(self):
        jpeg_old = _make_jpeg(10)
        jpeg_new = _make_jpeg(10)
        batch = [
            _encode_binary_v2("cam0", 1, jpeg_old),
            _encode_binary_v2("cam0", 2, jpeg_new),
            _encode_binary_v2("cam1", 1, jpeg_old),
        ]
        _, binary, _ = self._coalesce(batch)
        assert set(binary.keys()) == {"cam0", "cam1"}
        # cam0 should have the payload from cycle 2
        assert binary["cam0"] == _encode_binary_v2("cam0", 2, jpeg_new)

    def test_coalesce_mixed_batch(self):
        """50 cycles × 2 cameras → coalesced to 1 cycle + 2 binaries."""
        batch = []
        jpeg = _make_jpeg(5)
        for i in range(50):
            batch.append({"type": "cycle", "cycle_id": i})
            batch.append(_encode_binary_v2("cam0", i, jpeg))
            batch.append(_encode_binary_v2("cam1", i, jpeg))

        cycle, binary, misc = self._coalesce(batch)
        assert cycle["cycle_id"] == 49
        assert set(binary.keys()) == {"cam0", "cam1"}
        assert len(misc) == 0

    def test_coalesce_preserves_non_cycle_events(self):
        batch = [
            {"type": "system_status", "state": "RUNNING"},
            {"type": "cycle", "cycle_id": 1},
            {"type": "config_updated", "key": "hz"},
            _encode_binary_v2("cam0", 1, _make_jpeg()),
        ]
        cycle, binary, misc = self._coalesce(batch)
        assert cycle["cycle_id"] == 1
        assert len(binary) == 1
        assert len(misc) == 2
        assert misc[0]["type"] == "system_status"
        assert misc[1]["type"] == "config_updated"

    def test_coalesce_massive_backlog_reduction(self):
        """Simulate 10s backlog at 5Hz, 2 cameras → 100 cycles, 200 binaries.
        After coalescing: 1 cycle + 2 binaries."""
        jpeg = _make_jpeg(30)
        batch = []
        for i in range(100):
            batch.append({"type": "cycle", "cycle_id": i})
            batch.append(_encode_binary_v2("cam_left", i, jpeg))
            batch.append(_encode_binary_v2("cam_right", i, jpeg))

        assert len(batch) == 300
        cycle, binary, misc = self._coalesce(batch)
        total_after = (1 if cycle else 0) + len(binary) + len(misc)
        assert total_after == 3
        # 99% reduction
        assert total_after / len(batch) < 0.02

    def test_coalesce_empty_batch(self):
        cycle, binary, misc = self._coalesce([])
        assert cycle is None
        assert binary == {}
        assert misc == []


# ── Push Performance ─────────────────────────────────────────────────────────


class TestPushPerformance:
    """Verify push() stays fast even with large payloads."""

    def test_push_latency_under_1ms(self):
        svc = TelemetryService(history_size=100)
        jpeg = _make_jpeg(100)  # 100 KB JPEG
        times = []
        for i in range(50):
            t0 = time.perf_counter()
            svc.push(_make_cycle(i), camera_jpegs={"cam0": jpeg})
            times.append((time.perf_counter() - t0) * 1000)

        avg_ms = sum(times) / len(times)
        assert avg_ms < 1.0, f"push() avg={avg_ms:.3f}ms, expected < 1ms"

    def test_push_multi_camera_latency(self):
        svc = TelemetryService(history_size=100)
        jpegs = {f"cam{i}": _make_jpeg(50) for i in range(4)}
        times = []
        for i in range(30):
            t0 = time.perf_counter()
            svc.push(_make_cycle(i), camera_jpegs=jpegs)
            times.append((time.perf_counter() - t0) * 1000)

        avg_ms = sum(times) / len(times)
        assert avg_ms < 2.0, f"push() 4-cam avg={avg_ms:.3f}ms, expected < 2ms"


# ── Queue Drop Strategy ──────────────────────────────────────────────────────


class TestQueueDropStrategy:
    """Verify that the queue doesn't grow unbounded when consumer is slow."""

    def test_queue_bounded_under_burst(self):
        svc = TelemetryService(history_size=100)
        loop = asyncio.new_event_loop()
        svc.attach_loop(loop)
        q = svc.subscribe()
        try:
            jpeg = _make_jpeg(30)
            for i in range(1000):
                svc.push(_make_cycle(i), camera_jpegs={"cam0": jpeg})

            # Run the loop to process call_soon_threadsafe callbacks
            loop.call_soon(loop.stop)
            loop.run_forever()

            # Queue maxsize is 500; should not exceed that
            assert q.qsize() <= 500
        finally:
            svc.unsubscribe(q)
            loop.close()

    def test_latest_data_survives_overflow(self):
        """After overflow, queue should contain recent data, not only old."""
        svc = TelemetryService(history_size=100)
        loop = asyncio.new_event_loop()
        svc.attach_loop(loop)
        q = svc.subscribe()
        try:
            jpeg = _make_jpeg(5)
            for i in range(600):
                svc.push(_make_cycle(i), camera_jpegs={"cam0": jpeg})

            loop.call_soon(loop.stop)
            loop.run_forever()

            cycle_ids = []
            while not q.empty():
                item = q.get_nowait()
                if isinstance(item, dict) and item.get("type") == "cycle":
                    cycle_ids.append(item["cycle_id"])

            assert len(cycle_ids) > 0
            assert max(cycle_ids) >= 400
        finally:
            svc.unsubscribe(q)
            loop.close()
