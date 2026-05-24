#!/usr/bin/env python3
"""End-to-end WebSocket benchmark — spins up a real FastAPI server, pushes
frames from a background thread, and measures what a real WS client receives.

This is the ground-truth test: if the client sees smooth 5 Hz frames with
small payloads, the pipeline is healthy. If not, the bottleneck is here.

Usage:
    .venv/bin/python scripts/bench_ws_e2e.py
"""

from __future__ import annotations

import asyncio
import statistics
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np
import uvicorn
from fastapi import FastAPI

from dam.services.routers.telemetry import create_telemetry_router
from dam.services.telemetry import TelemetryService
from dam.types.risk import CycleResult, RiskLevel

PORT = 18765


def make_result(cycle_id: int) -> CycleResult:
    return CycleResult(
        cycle_id=cycle_id,
        trace_id=f"bench-{cycle_id}",
        validated_action=None,
        original_proposal=None,
        guard_results=[],
        was_clamped=False,
        was_rejected=False,
        risk_level=RiskLevel.NORMAL,
        latency_ms={"total": 5.0},
    )


def make_jpeg(w: int, h: int, quality: int) -> bytes:
    frame = np.random.randint(0, 255, (h, w, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return bytes(buf)


def producer_thread(svc: TelemetryService, jpeg: bytes, hz: float, duration: float) -> None:
    """Simulate the control loop pushing frames at `hz` Hz."""
    interval = 1.0 / hz
    cycle_id = 0
    t_start = time.monotonic()
    while time.monotonic() - t_start < duration:
        result = make_result(cycle_id)
        svc.push(result, camera_jpegs={"cam0": jpeg})
        cycle_id += 1
        time.sleep(interval)
    # Signal done
    svc.push(make_result(cycle_id), camera_jpegs=None)


async def ws_client(port: int, duration: float) -> dict:
    """Connect via WS and measure what we actually receive."""
    import websockets

    stats = {
        "json_count": 0,
        "binary_count": 0,
        "binary_sizes": [],
        "frame_times": [],
        "first_frame_t": None,
        "last_frame_t": None,
    }

    url = f"ws://localhost:{port}/ws/telemetry"
    t_start = time.monotonic()

    try:
        async with websockets.connect(url, max_size=10 * 1024 * 1024) as ws:
            while time.monotonic() - t_start < duration + 2:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=3.0)
                except TimeoutError:
                    break

                now = time.monotonic()
                if isinstance(msg, bytes):
                    stats["binary_count"] += 1
                    stats["binary_sizes"].append(len(msg))
                    stats["frame_times"].append(now)
                    if stats["first_frame_t"] is None:
                        stats["first_frame_t"] = now
                    stats["last_frame_t"] = now
                else:
                    stats["json_count"] += 1
    except Exception as e:
        print(f"  WS client error: {e}")

    return stats


def run_scenario(label: str, w: int, h: int, quality: int, hz: float, duration: float) -> None:
    jpeg = make_jpeg(w, h, quality)
    print(f"\n{'─' * 60}")
    print(f"  {label}")
    print(f"  Resolution: {w}x{h}  Quality: {quality}  JPEG: {len(jpeg) / 1024:.0f} KB")
    print(f"  Push rate: {hz} Hz  Duration: {duration}s")
    print(f"{'─' * 60}")

    svc = TelemetryService(history_size=100)

    app = FastAPI()
    app.include_router(create_telemetry_router(svc))

    @app.on_event("startup")
    async def startup():
        svc.attach_loop(asyncio.get_event_loop())
        # Start producer after a small delay
        t = threading.Thread(
            target=producer_thread,
            args=(svc, jpeg, hz, duration),
            daemon=True,
        )
        t.start()

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=PORT,
            log_level="error",
        )
    )

    async def run_test():
        # Start server in background
        server_task = asyncio.create_task(server.serve())
        await asyncio.sleep(0.5)  # Let server start

        stats = await ws_client(PORT, duration)

        server.should_exit = True
        await server_task
        return stats

    stats = asyncio.run(run_test())

    # Report
    print("\n  Results:")
    print(f"    JSON messages:   {stats['json_count']}")
    print(f"    Binary frames:   {stats['binary_count']}")

    expected = int(hz * duration)
    recv_rate = stats["binary_count"] / duration if duration > 0 else 0
    print(f"    Expected frames: {expected}")
    print(f"    Receive rate:    {recv_rate:.1f} fps")

    if stats["binary_sizes"]:
        sizes = stats["binary_sizes"]
        print(f"    Frame size:      {statistics.mean(sizes) / 1024:.0f} KB")
        bw = sum(sizes) / duration / 1024
        print(f"    Bandwidth:       {bw:.0f} KB/s ({bw / 1024:.1f} MB/s)")

    if len(stats["frame_times"]) > 1:
        intervals = [
            stats["frame_times"][i + 1] - stats["frame_times"][i]
            for i in range(len(stats["frame_times"]) - 1)
        ]
        print(
            f"    Frame intervals: avg={statistics.mean(intervals) * 1000:.0f}ms  "
            f"max={max(intervals) * 1000:.0f}ms  "
            f"stddev={statistics.stdev(intervals) * 1000:.0f}ms"
        )

        stalls = [iv for iv in intervals if iv > 0.5]
        if stalls:
            print(f"    ⚠ STALLS >500ms: {len(stalls)}x  worst={max(stalls) * 1000:.0f}ms")
        else:
            print("    ✓ No stalls (all < 500ms)")

        jitter = [abs(iv - 1.0 / hz) for iv in intervals]
        print(
            f"    Jitter:          avg={statistics.mean(jitter) * 1000:.0f}ms  "
            f"max={max(jitter) * 1000:.0f}ms"
        )

    drop_rate = max(0, expected - stats["binary_count"]) / max(1, expected) * 100
    if drop_rate > 10:
        print(f"    ⚠ DROP RATE:     {drop_rate:.0f}% — pipeline can't keep up!")
    elif drop_rate > 0:
        print(f"    Drop rate:       {drop_rate:.0f}%")
    else:
        print("    ✓ No drops")


def main():
    print("=" * 60)
    print("END-TO-END WEBSOCKET BENCHMARK")
    print("=" * 60)

    # Scenario 1: Small images (should work perfectly)
    run_scenario("480p Q60 (downscaled preview)", 640, 480, 60, hz=5, duration=5)

    # Scenario 2: HD without downscale
    run_scenario("720p Q85 (full res)", 1280, 720, 85, hz=5, duration=5)

    # Scenario 3: Full HD without downscale
    run_scenario("1080p Q85 (full res)", 1920, 1080, 85, hz=5, duration=5)

    # Scenario 4: Small images but high rate
    run_scenario("480p Q60 @ 30Hz (stress test)", 640, 480, 60, hz=30, duration=5)

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print("  If 480p Q60 is smooth but 1080p Q85 stalls → image size is the issue")
    print("  If all scenarios stall → something else is wrong (asyncio, WS send)")
    print("  If none stall → issue is in the frontend, not backend")


if __name__ == "__main__":
    main()
