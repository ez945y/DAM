#!/usr/bin/env python3
"""Benchmark the live preview pipeline end-to-end.

Measures:
  1. WebSocket throughput — how fast the server can push frames
  2. JPEG payload sizes — actual bytes per camera per frame
  3. Round-trip latency — time from push() to WS client receipt
  4. Frame hub latency — time to call latest_jpegs()
  5. Downscale cost — time spent in _downscale_for_preview()

Usage:
    # Against a running dam_host (must be started separately):
    python scripts/bench_live_preview.py --live

    # Standalone (no running server needed — synthetic benchmark):
    python scripts/bench_live_preview.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── Standalone benchmark (no server needed) ──────────────────────────────────


def bench_frame_hub() -> None:
    """Measure frame hub put/get latency and JPEG sizes."""
    import cv2
    import numpy as np

    from dam.camera.frame_hub import CameraFrameHub

    hub = CameraFrameHub(window_sec=5.0)

    # Simulate cameras at different resolutions
    resolutions = {
        "cam_480p": (640, 480),
        "cam_720p": (1280, 720),
        "cam_1080p": (1920, 1080),
    }

    print("=" * 70)
    print("FRAME HUB BENCHMARK")
    print("=" * 70)

    for cam_name, (w, h) in resolutions.items():
        frame = np.random.randint(0, 255, (h, w, 3), dtype=np.uint8)
        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        jpeg = bytes(buf)

        # Measure put_jpeg
        put_times = []
        for _i in range(100):
            t0 = time.perf_counter()
            hub.put_jpeg(cam_name, time.monotonic(), jpeg, w, h)
            put_times.append((time.perf_counter() - t0) * 1000)

        # Measure latest_jpegs
        get_times = []
        sizes = []
        for _ in range(100):
            t0 = time.perf_counter()
            result = hub.latest_jpegs()
            get_times.append((time.perf_counter() - t0) * 1000)
            if cam_name in result:
                sizes.append(len(result[cam_name]))

        avg_size = statistics.mean(sizes) if sizes else 0
        print(f"\n  {cam_name} ({w}x{h}):")
        print(f"    JPEG size:       {avg_size / 1024:.1f} KB")
        print(
            f"    put_jpeg:        {statistics.mean(put_times):.3f} ms (p99: {sorted(put_times)[98]:.3f} ms)"
        )
        print(
            f"    latest_jpegs:    {statistics.mean(get_times):.3f} ms (p99: {sorted(get_times)[98]:.3f} ms)"
        )


def bench_downscale() -> None:
    """Measure downscale cost at various resolutions."""
    import cv2
    import numpy as np

    print("\n" + "=" * 70)
    print("DOWNSCALE BENCHMARK")
    print("=" * 70)

    resolutions = {
        "480p": (640, 480),
        "720p": (1280, 720),
        "1080p": (1920, 1080),
    }

    for label, (w, h) in resolutions.items():
        frame = np.random.randint(0, 255, (h, w, 3), dtype=np.uint8)
        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        original_jpeg = bytes(buf)

        times = []
        out_sizes = []
        for _ in range(50):
            t0 = time.perf_counter()
            img = cv2.imdecode(np.frombuffer(original_jpeg, np.uint8), cv2.IMREAD_COLOR)
            ih, iw = img.shape[:2]
            max_dim = 480
            if max(ih, iw) > max_dim:
                scale = max_dim / max(ih, iw)
                resized = cv2.resize(
                    img, (int(iw * scale), int(ih * scale)), interpolation=cv2.INTER_AREA
                )
                ok2, buf2 = cv2.imencode(".jpg", resized, [cv2.IMWRITE_JPEG_QUALITY, 60])
                out = bytes(buf2) if ok2 else original_jpeg
            else:
                out = original_jpeg
            elapsed = (time.perf_counter() - t0) * 1000
            times.append(elapsed)
            out_sizes.append(len(out))

        print(f"\n  {label} ({w}x{h}):")
        print(f"    Original:        {len(original_jpeg) / 1024:.1f} KB")
        print(
            f"    Downscaled:      {statistics.mean(out_sizes) / 1024:.1f} KB  ({statistics.mean(out_sizes) / len(original_jpeg) * 100:.0f}%)"
        )
        print(
            f"    Downscale time:  {statistics.mean(times):.2f} ms (p99: {sorted(times)[48]:.2f} ms)"
        )


def bench_telemetry_service() -> None:
    """Measure TelemetryService.push() → queue → consumer latency."""
    import cv2
    import numpy as np

    from dam.services.telemetry import TelemetryService
    from dam.types.risk import CycleResult, RiskLevel

    print("\n" + "=" * 70)
    print("TELEMETRY SERVICE PUSH BENCHMARK")
    print("=" * 70)

    svc = TelemetryService(history_size=100)
    loop = asyncio.new_event_loop()
    svc.attach_loop(loop)

    q = svc.subscribe()

    # Prepare test data
    frame = np.random.randint(0, 255, (1080, 1920, 3), dtype=np.uint8)
    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    jpeg_1080p = bytes(buf)

    frame_small = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
    _, buf = cv2.imencode(".jpg", frame_small, [cv2.IMWRITE_JPEG_QUALITY, 60])
    jpeg_480p = bytes(buf)

    def make_result(cycle_id: int) -> CycleResult:
        return CycleResult(
            cycle_id=cycle_id,
            trace_id=f"test-{cycle_id}",
            validated_action=None,
            original_proposal=None,
            guard_results=[],
            was_clamped=False,
            was_rejected=False,
            risk_level=RiskLevel.NORMAL,
            fallback_triggered=None,
            latency_ms={"total": 5.0},
        )

    for label, jpeg in [("1080p Q85", jpeg_1080p), ("480p Q60", jpeg_480p)]:
        # Drain queue
        while not q.empty():
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                break

        push_times = []
        for i in range(200):
            result = make_result(i)
            camera_jpegs = {"cam0": jpeg}
            t0 = time.perf_counter()
            svc.push(result, camera_jpegs=camera_jpegs)
            push_times.append((time.perf_counter() - t0) * 1000)

        # Check queue depth
        depth = q.qsize()

        print(f"\n  {label} ({len(jpeg) / 1024:.0f} KB):")
        print(
            f"    push() time:     {statistics.mean(push_times):.3f} ms (p99: {sorted(push_times)[197]:.3f} ms)"
        )
        print(f"    Queue depth:     {depth} items after 200 pushes")
        print(f"    Payload/frame:   {len(jpeg) / 1024:.1f} KB JPEG + ~500 B JSON")
        print(
            f"    Bandwidth @5Hz:  {len(jpeg) * 5 / 1024:.0f} KB/s = {len(jpeg) * 5 / 1024 / 1024:.1f} MB/s"
        )

    svc.unsubscribe(q)
    loop.close()


def bench_ws_send_simulation() -> None:
    """Simulate the WS send loop with coalescing vs without."""
    import cv2
    import numpy as np

    from dam.services.telemetry import BINARY_PROTOCOL_VERSION, TelemetryService
    from dam.types.risk import CycleResult, RiskLevel

    print("\n" + "=" * 70)
    print("WS SEND LOOP SIMULATION (coalescing effect)")
    print("=" * 70)

    frame = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 60])
    jpeg = bytes(buf)

    # Build binary payloads like TelemetryService would
    def make_binary(cam: str, cycle_id: int, jpeg_data: bytes) -> bytes:
        name_bytes = cam.encode()
        return (
            BINARY_PROTOCOL_VERSION
            + cycle_id.to_bytes(4, "big")
            + len(name_bytes).to_bytes(1, "big")
            + name_bytes
            + jpeg_data
        )

    # Simulate 50 cycles (10 seconds at 5 Hz) queued up
    items: list = []
    for i in range(50):
        items.append({"type": "cycle", "cycle_id": i, "timestamp": time.time()})
        items.append(make_binary("cam0", i, jpeg))
        items.append(make_binary("cam1", i, jpeg))

    print(f"\n  Queued: {len(items)} items ({50} cycles × 2 cameras)")
    print(f"  JPEG size: {len(jpeg) / 1024:.1f} KB each")

    # Without coalescing: send all 150 items
    total_bytes_no_coalesce = sum(
        len(item) if isinstance(item, bytes) else len(json.dumps(item).encode()) for item in items
    )

    # With coalescing: only latest cycle + latest per camera
    from dam.services.routers.telemetry import _parse_camera_name

    latest_cycle = None
    latest_binary: dict[str, bytes] = {}
    misc = []
    for item in items:
        if isinstance(item, bytes):
            cam = _parse_camera_name(item)
            if cam:
                latest_binary[cam] = item
            else:
                misc.append(item)
        elif isinstance(item, dict) and item.get("type") == "cycle":
            latest_cycle = item
        else:
            misc.append(item)

    coalesced = misc[:]
    if latest_cycle:
        coalesced.append(latest_cycle)
    coalesced.extend(latest_binary.values())

    total_bytes_coalesced = sum(
        len(item) if isinstance(item, bytes) else len(json.dumps(item).encode())
        for item in coalesced
    )

    print("\n  Without coalescing:")
    print(f"    Items to send:   {len(items)}")
    print(f"    Total bytes:     {total_bytes_no_coalesce / 1024:.0f} KB")

    print("\n  With coalescing:")
    print(f"    Items to send:   {len(coalesced)}")
    print(f"    Total bytes:     {total_bytes_coalesced / 1024:.0f} KB")
    print(
        f"    Reduction:       {(1 - total_bytes_coalesced / total_bytes_no_coalesce) * 100:.0f}%"
    )


# ── Live server benchmark ────────────────────────────────────────────────────


async def bench_live_ws(ws_url: str, duration_s: float = 10.0) -> None:
    """Connect to a running dam_host and measure actual WS throughput."""
    try:
        import websockets
    except ImportError:
        print("ERROR: pip install websockets  (needed for --live mode)")
        return

    print("=" * 70)
    print(f"LIVE WS BENCHMARK — {ws_url}/ws/telemetry")
    print(f"  Collecting for {duration_s}s...")
    print("=" * 70)

    json_count = 0
    binary_count = 0
    binary_bytes = 0
    binary_sizes: list[int] = []
    frame_times: list[float] = []
    cameras_seen: dict[str, int] = {}

    t_start = time.monotonic()

    try:
        async with websockets.connect(f"{ws_url}/ws/telemetry") as ws:
            while time.monotonic() - t_start < duration_s:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                except TimeoutError:
                    print("  (no message for 2s)")
                    continue

                now = time.monotonic()
                if isinstance(msg, bytes):
                    binary_count += 1
                    binary_bytes += len(msg)
                    binary_sizes.append(len(msg))
                    frame_times.append(now)
                    # Parse camera name
                    if len(msg) > 6 and msg[0] == 0x02:
                        name_len = msg[5]
                        cam = msg[6 : 6 + name_len].decode("utf-8", errors="replace")
                        cameras_seen[cam] = cameras_seen.get(cam, 0) + 1
                elif isinstance(msg, str):
                    json_count += 1
    except Exception as e:
        print(f"  Connection error: {e}")
        return

    elapsed = time.monotonic() - t_start
    print(f"\n  Duration:          {elapsed:.1f}s")
    print(f"  JSON messages:     {json_count} ({json_count / elapsed:.1f}/s)")
    print(f"  Binary frames:     {binary_count} ({binary_count / elapsed:.1f}/s)")

    if binary_sizes:
        print(
            f"  Frame sizes:       min={min(binary_sizes) / 1024:.1f}KB  avg={statistics.mean(binary_sizes) / 1024:.1f}KB  max={max(binary_sizes) / 1024:.1f}KB"
        )
        print(
            f"  Bandwidth:         {binary_bytes / elapsed / 1024:.0f} KB/s = {binary_bytes / elapsed / 1024 / 1024:.1f} MB/s"
        )

    if cameras_seen:
        print("  Cameras:")
        for cam, count in sorted(cameras_seen.items()):
            print(f"    {cam}: {count} frames ({count / elapsed:.1f} fps)")

    if len(frame_times) > 1:
        intervals = [frame_times[i + 1] - frame_times[i] for i in range(len(frame_times) - 1)]
        print(
            f"  Frame intervals:   min={min(intervals) * 1000:.0f}ms  avg={statistics.mean(intervals) * 1000:.0f}ms  max={max(intervals) * 1000:.0f}ms  stddev={statistics.stdev(intervals) * 1000:.0f}ms"
        )

        # Check for stalls (>500ms gap)
        stalls = [iv for iv in intervals if iv > 0.5]
        if stalls:
            print(
                f"  ⚠ STALLS (>500ms): {len(stalls)} occurrences, worst={max(stalls) * 1000:.0f}ms"
            )
        else:
            print("  ✓ No stalls detected (all intervals < 500ms)")


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark live preview pipeline")
    parser.add_argument("--live", action="store_true", help="Connect to running dam_host")
    parser.add_argument("--url", default="ws://localhost:8080", help="WebSocket URL")
    parser.add_argument("--duration", type=float, default=10.0, help="Live test duration (seconds)")
    args = parser.parse_args()

    if args.live:
        asyncio.run(bench_live_ws(args.url, args.duration))
    else:
        bench_frame_hub()
        bench_downscale()
        bench_telemetry_service()
        bench_ws_send_simulation()

        print("\n" + "=" * 70)
        print("VERDICT")
        print("=" * 70)
        print("  Run with --live against a running dam_host to measure real WS throughput.")
        print("  Look for: frame sizes > 100KB, bandwidth > 1MB/s, stalls > 500ms.")


if __name__ == "__main__":
    main()
