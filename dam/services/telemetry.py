"""Telemetry Service — WebSocket broadcaster for real-time cycle data.

Usage::

    # Wire up once at runtime construction:
    svc = TelemetryService(metric_bus=runtime.metric_bus, cycle_budget_ms=20.0)
    svc.push(cycle_result)       # call from GuardRuntime loop

    # FastAPI integration: mount via create_app()

The ``perf`` key is attached to every ``cycle`` event when a ``MetricBus``
reference is provided.  Its shape::

    {
      "stages": {"source": ms, "policy": ms, "guards": ms, "sink": ms, "total": ms},
      "layers": {"L0": ms, "L2": ms, ...},
      "guards": {"guard_name": ms, ...},
      "deadline_ms": 20.0,
      "slack_ms": 9.2,
    }

Consumers that do not understand ``perf`` are unaffected — the field is simply
absent when no MetricBus is wired in.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from collections import deque
from typing import TYPE_CHECKING, Any

from dam.types.risk import CycleResult

if TYPE_CHECKING:
    from dam.bus import PipelineMetricBus

logger = logging.getLogger(__name__)

BINARY_PROTOCOL_VERSION = b"\x02"


def _serialise_cycle(
    result: CycleResult,
    live_images: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    """Convert CycleResult to a JSON-serialisable dict (pure safety data).

    Args:
        result: The cycle result to serialise.
        live_images: Optional dict of camera_name -> JPEG bytes for live camera
                     preview.  Encoded as binary payloads alongside JSON frames.
    """
    guard_statuses = []
    for gr in result.guard_results:
        layer_val = gr.layer.value if hasattr(gr.layer, "value") else int(gr.layer)
        guard_statuses.append(
            {
                "name": gr.guard_name,
                "layer": f"L{layer_val}",
                "decision": gr.decision.name,
                "reason": gr.reason,
            }
        )
    event: dict[str, Any] = {
        "type": "cycle",
        "cycle_id": result.cycle_id,
        "trace_id": result.trace_id,
        "was_clamped": result.was_clamped,
        "was_rejected": result.was_rejected,
        "risk_level": result.risk_level.name
        if hasattr(result.risk_level, "name")
        else str(result.risk_level),
        "fallback_triggered": result.fallback_triggered,
        "latency_ms": result.latency_ms,
        "guard_statuses": guard_statuses,
        "active_task": result.active_task,
        "active_boundaries": result.active_boundaries,
        "timestamp": time.time(),
    }
    if live_images:
        # We send camera names so the frontend knows how many binary payloads to expect.
        event["active_cameras"] = list(live_images.keys())
    return event


def _build_perf(metric_bus: PipelineMetricBus, cycle_budget_ms: float) -> dict[str, Any]:
    """Read a MetricBus snapshot and compute derived fields for the telemetry event."""
    snap: dict[str, Any] = metric_bus.snapshot()
    total_ms: float = snap.get("stages", {}).get("total", 0.0)
    return {
        **snap,
        "deadline_ms": cycle_budget_ms,
        "slack_ms": cycle_budget_ms - total_ms,
    }


class TelemetryService:
    """Thread-safe WebSocket broadcaster.

    Push CycleResult objects from the control loop thread.
    WebSocket consumers are registered/deregistered asynchronously.
    A ring buffer (``history_size``) stores recent events for new subscribers.

    Args:
        history_size:     Number of recent events retained for late subscribers.
        metric_bus:       Optional reference to the runtime's MetricBus.  When
                          provided, each cycle event is enriched with a ``perf``
                          sub-dict containing pipeline-stage, per-layer, and
                          per-guard latency breakdowns plus deadline/slack values.
        cycle_budget_ms:  The control-loop cycle budget in milliseconds, used
                          to compute ``slack_ms``.  Ignored when metric_bus is None.
    """

    def __init__(
        self,
        history_size: int = 200,
        metric_bus: PipelineMetricBus | None = None,
        cycle_budget_ms: float = 20.0,
    ) -> None:
        self._history: deque[dict[str, Any]] = deque(maxlen=history_size)
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: set[asyncio.Queue] = set()
        self._total_pushed: int = 0
        self._metric_bus = metric_bus
        self._cycle_budget_ms = cycle_budget_ms

    # ── Configuration API ────────────────────────────────────────────────────

    def set_metric_bus(self, metric_bus: PipelineMetricBus | None) -> None:
        """Dynamically wire a PipelineMetricBus to enrich telemetry with perf data."""
        self._metric_bus = metric_bus

    def set_cycle_budget(self, budget_ms: float) -> None:
        """Dynamically update the control loop budget for slack computation."""
        self._cycle_budget_ms = budget_ms

    # ── Producer API (called from control loop thread) ───────────────────────

    def push(
        self,
        result: CycleResult,
        live_images: dict[str, bytes] | None = None,
    ) -> None:
        """Serialise and broadcast a CycleResult to all WebSocket consumers.

        When a MetricBus was provided at construction, the event is enriched
        with a ``perf`` sub-dict before broadcasting.

        Args:
            result: The cycle result to broadcast.
            live_images: Optional dict of camera_name -> JPEG bytes.
        """
        event = _serialise_cycle(result, live_images=live_images)
        if self._metric_bus is not None:
            event["perf"] = _build_perf(self._metric_bus, self._cycle_budget_ms)

        with self._lock:
            self._history.append(event)
            self._total_pushed += 1
            subs = list(self._subscribers)

        if self._loop is not None and subs:
            for q in subs:
                self._loop.call_soon_threadsafe(_safe_queue_push, q, event)
                if live_images:
                    for cam, jpeg in live_images.items():
                        if not jpeg:
                            continue
                        name_bytes = cam.encode()
                        # Binary protocol v2:
                        # [magic: 0x02][cycle_id: 4 bytes][name_len: 1 byte][name][jpeg]
                        payload = (
                            BINARY_PROTOCOL_VERSION
                            + result.cycle_id.to_bytes(4, "big")
                            + len(name_bytes).to_bytes(1, "big")
                            + name_bytes
                            + jpeg
                        )
                        self._loop.call_soon_threadsafe(_safe_queue_push, q, payload)

    def push_raw(self, event: dict[str, Any]) -> None:
        """Push an arbitrary JSON-serialisable event dict."""
        with self._lock:
            self._history.append(event)
            subs = list(self._subscribers)
        if self._loop is not None and subs:
            for q in subs:
                self._loop.call_soon_threadsafe(_safe_queue_push, q, event)

    # ── Consumer API (called from async context) ─────────────────────────────

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Register the asyncio event loop (called once at app startup)."""
        self._loop = loop

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        """Create and register a new subscriber queue."""
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=500)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        """Unregister a subscriber queue."""
        with self._lock:
            self._subscribers.discard(q)

    def get_history(self, n: int | None = None) -> list[dict[str, Any]]:
        """Return the last ``n`` events (or all if n is None)."""
        with self._lock:
            items = list(self._history)
        return items[-n:] if n else items

    @property
    def subscriber_count(self) -> int:
        """Count of active WebSocket subscribers."""
        with self._lock:
            return len(self._subscribers)

    @property
    def total_pushed(self) -> int:
        """Cumulative total of telemetry events pushed since creation."""
        return self._total_pushed


def _safe_queue_push(q: asyncio.Queue, item: Any) -> None:
    """Helper to push to an asyncio.Queue, dropping the oldest item if full.

    This prevents QueueFull exceptions and ensures real-time telemetry always
    shows the latest state rather than lagging behind a slow consumer.
    """
    try:
        if q.full():
            # Real-time drop strategy
            with contextlib.suppress(asyncio.QueueEmpty):
                q.get_nowait()
        q.put_nowait(item)
    except asyncio.QueueFull:
        pass
    except Exception as e:
        # Prevent any unhandled errors from reaching the loop exception handler
        # which would print the binary payload to the console.
        logging.getLogger(__name__).debug("Telemetry push failed: %s", e)
