"""Telemetry Service — WebSocket broadcaster for real-time cycle data.

Usage::

    svc = TelemetryService()
    svc.push(cycle_result)       # call from GuardRuntime loop
    # FastAPI integration: mount via create_app()
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from collections import deque
from typing import Any

from dam.types.risk import CycleResult

logger = logging.getLogger(__name__)


def _serialise_cycle(result: CycleResult) -> dict[str, Any]:
    """Convert CycleResult to a JSON-serialisable dict."""
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
    return {
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


class TelemetryService:
    """Thread-safe WebSocket broadcaster.

    Push CycleResult objects from the control loop thread.
    WebSocket consumers are registered/deregistered asynchronously.
    A ring buffer (``history_size``) stores recent events for new subscribers.
    """

    def __init__(self, history_size: int = 200) -> None:
        self._history: deque[dict[str, Any]] = deque(maxlen=history_size)
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: set[Any] = set()  # asyncio.Queue per subscriber
        self._total_pushed: int = 0

    # ── Producer API (called from control loop thread) ───────────────────────

    def push(self, result: CycleResult) -> None:
        """Serialise and broadcast a CycleResult to all WebSocket consumers."""
        event = _serialise_cycle(result)
        with self._lock:
            self._history.append(event)
            self._total_pushed += 1
            subs = list(self._subscribers)

        if self._loop is not None and subs:
            for q in subs:
                with contextlib.suppress(Exception):
                    self._loop.call_soon_threadsafe(q.put_nowait, event)

    def push_raw(self, event: dict[str, Any]) -> None:
        """Push an arbitrary JSON-serialisable event dict."""
        with self._lock:
            self._history.append(event)
            subs = list(self._subscribers)
        if self._loop is not None and subs:
            for q in subs:
                with contextlib.suppress(Exception):
                    self._loop.call_soon_threadsafe(q.put_nowait, event)

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
        with self._lock:
            self._subscribers.discard(q)

    def get_history(self, n: int | None = None) -> list[dict[str, Any]]:
        """Return the last ``n`` events (or all if n is None)."""
        with self._lock:
            items = list(self._history)
        return items[-n:] if n else items

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    @property
    def total_pushed(self) -> int:
        return self._total_pushed
