"""Risk Log Service — historical risk event store with query and export."""

from __future__ import annotations

import csv
import io
import json
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class RiskEvent:
    """A single recorded risk event."""

    event_id: int
    timestamp: float
    cycle_id: int
    trace_id: str
    risk_level: str  # RiskLevel.name
    was_clamped: bool
    was_rejected: bool
    fallback_triggered: str | None
    guard_results: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RiskLogService:
    """In-memory risk event store.

    Stores all cycles; provides:
        record(cycle_result)  — add a CycleResult to the log
        query(...)            — filter by time range, risk level, rejected-only
        export_json(...)      — JSON string
        export_csv(...)       — CSV string
        stats()               — summary statistics
    """

    def __init__(self, max_events: int = 10_000) -> None:
        self._events: list[RiskEvent] = []
        self._lock = threading.Lock()
        self._max_events = max_events
        self._next_id = 0

    # ── Recording ─────────────────────────────────────────────────────────────

    def record(self, result: Any) -> None:
        """Record a CycleResult object."""
        guard_summaries = [
            {
                "name": gr.guard_name,
                "layer": gr.layer.name if hasattr(gr.layer, "name") else str(gr.layer),
                "decision": gr.decision.name,
                "reason": gr.reason,
            }
            for gr in result.guard_results
        ]
        event = RiskEvent(
            event_id=self._next_id,
            timestamp=time.time(),
            cycle_id=result.cycle_id,
            trace_id=result.trace_id,
            risk_level=result.risk_level.name
            if hasattr(result.risk_level, "name")
            else str(result.risk_level),
            was_clamped=result.was_clamped,
            was_rejected=result.was_rejected,
            fallback_triggered=result.fallback_triggered,
            guard_results=guard_summaries,
            latency_ms=dict(result.latency_ms),
        )
        with self._lock:
            self._next_id += 1
            self._events.append(event)
            # Evict oldest if over capacity
            if len(self._events) > self._max_events:
                self._events = self._events[-self._max_events :]

    # ── Query ─────────────────────────────────────────────────────────────────

    def query(
        self,
        since: float | None = None,
        until: float | None = None,
        min_risk_level: str | None = None,
        rejected_only: bool = False,
        clamped_only: bool = False,
        limit: int = 500,
    ) -> list[RiskEvent]:
        """Filter events.

        Args:
            since:          Unix timestamp lower bound (inclusive).
            until:          Unix timestamp upper bound (inclusive).
            min_risk_level: Minimum risk level name ("NORMAL","ELEVATED","CRITICAL","EMERGENCY").
            rejected_only:  Return only rejected cycles.
            clamped_only:   Return only clamped cycles.
            limit:          Maximum number of events to return (newest first).
        """
        _level_map = {
            "NORMAL": 0,
            "ELEVATED": 1,
            "CRITICAL": 2,
            "EMERGENCY": 3,
        }
        min_level_int = _level_map.get(min_risk_level or "NORMAL", 0)

        with self._lock:
            events = list(self._events)

        results = []
        for ev in reversed(events):  # newest first
            if since is not None and ev.timestamp < since:
                continue
            if until is not None and ev.timestamp > until:
                continue
            if _level_map.get(ev.risk_level, 0) < min_level_int:
                continue
            if rejected_only and not ev.was_rejected:
                continue
            if clamped_only and not ev.was_clamped:
                continue
            results.append(ev)
            if len(results) >= limit:
                break
        return results

    def get_by_id(self, event_id: int) -> RiskEvent | None:
        with self._lock:
            for ev in self._events:
                if ev.event_id == event_id:
                    return ev
        return None

    # ── Export ────────────────────────────────────────────────────────────────

    def export_json(self, events: list[RiskEvent] | None = None) -> str:
        """Return JSON string of events (all if None)."""
        if events is None:
            with self._lock:
                events = list(self._events)
        return json.dumps([e.to_dict() for e in events], indent=2)

    def export_csv(self, events: list[RiskEvent] | None = None) -> str:
        """Return CSV string (no guard_results column; flat fields only)."""
        if events is None:
            with self._lock:
                events = list(self._events)
        output = io.StringIO()
        fieldnames = [
            "event_id",
            "timestamp",
            "cycle_id",
            "trace_id",
            "risk_level",
            "was_clamped",
            "was_rejected",
            "fallback_triggered",
            "total_latency_ms",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for ev in events:
            writer.writerow(
                {
                    "event_id": ev.event_id,
                    "timestamp": ev.timestamp,
                    "cycle_id": ev.cycle_id,
                    "trace_id": ev.trace_id,
                    "risk_level": ev.risk_level,
                    "was_clamped": ev.was_clamped,
                    "was_rejected": ev.was_rejected,
                    "fallback_triggered": ev.fallback_triggered or "",
                    "total_latency_ms": ev.latency_ms.get("total", 0.0),
                }
            )
        return output.getvalue()

    # ── Statistics ────────────────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        with self._lock:
            events = list(self._events)
        if not events:
            return {
                "total": 0,
                "rejected": 0,
                "clamped": 0,
                "by_risk_level": {},
                "avg_latency_ms": None,
            }
        rejected = sum(1 for e in events if e.was_rejected)
        clamped = sum(1 for e in events if e.was_clamped)
        by_level: dict[str, int] = {}
        latencies = []
        for e in events:
            by_level[e.risk_level] = by_level.get(e.risk_level, 0) + 1
            if "total" in e.latency_ms:
                latencies.append(e.latency_ms["total"])
        return {
            "total": len(events),
            "rejected": rejected,
            "clamped": clamped,
            "by_risk_level": by_level,
            "avg_latency_ms": sum(latencies) / len(latencies) if latencies else None,
        }

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
            self._next_id = 0
