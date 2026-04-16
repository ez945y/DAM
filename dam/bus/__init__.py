"""Bus module — Rust data plane (mandatory).

``dam_rs`` is a compiled Rust extension (PyO3/maturin).  It MUST be built
before importing this module.  There is no Python fallback.

Build instructions:
    cd dam-rust/dam-py
    maturin develop --release        # editable dev install
    # or via Docker:
    docker build -t dam:latest -f docker/Dockerfile .

If the extension is missing, an ImportError is raised immediately so the
problem is caught at startup rather than silently degrading to slower,
non-real-time Python code.
"""

from __future__ import annotations

from typing import Any

try:
    from dam_rs import (  # noqa: F401
        ActionBus,
        MetricBus,
        ObservationBus,
        RiskController,
        WatchdogTimer,
    )
except ImportError as _err:
    raise RuntimeError(
        "\n\n"
        "  ╔══════════════════════════════════════════════════════════════╗\n"
        "  ║  DAM: Rust extension 'dam_rs' is not installed.             ║\n"
        "  ║                                                              ║\n"
        "  ║  Build it with:                                              ║\n"
        "  ║    cd dam-rust/dam-py && maturin develop --release           ║\n"
        "  ║                                                              ║\n"
        "  ║  Or build the production image:                              ║\n"
        "  ║    docker build --target runner -t dam_engine:latest         ║\n"
        "  ║      -f docker/Dockerfile .                                  ║\n"
        "  ╚══════════════════════════════════════════════════════════════╝\n"
    ) from _err


class PipelineMetricBus:
    """Structured adapter around the Rust ``MetricBus``.

    The Rust bus exposes a flat ``push(name, value)`` interface optimised for
    throughput.  This adapter adds the semantic layer needed by the control loop:

    * ``push_guard(name, layer, latency_ms)`` — per-guard latency with layer tag
    * ``push_stage(name, latency_ms)``        — pipeline-stage latency
    * ``commit_cycle()``                      — flush per-layer sums into snapshot
    * ``snapshot()``                          — structured dict consumed by telemetry

    Guard latencies are stored under the guard name; stage latencies under
    ``"stage:<name>"`` to avoid collisions with guard names.

    Per-layer latency sums are computed by ``commit_cycle()``:  guards accumulate
    into ``_cycle_layer_sums`` during a cycle, then ``commit_cycle()`` publishes
    those sums to ``_layers`` and resets the accumulators.  This means
    ``snapshot()["layers"]`` is always the *previous* committed cycle's view (empty
    before the first commit), matching the expected semantics.
    """

    _STAGE_PREFIX = "stage:"

    def __init__(self) -> None:
        self._bus: MetricBus = MetricBus()
        # Latest stage values kept in Python to avoid a full scan of all_latest()
        self._stages: dict[str, float] = {}
        # Per-layer accumulator for the current in-flight cycle
        self._cycle_layer_sums: dict[str, float] = {}
        # Published layer sums from the most recently committed cycle.
        # All previously-seen layer keys are kept (with 0.0 when inactive) so
        # that downstream consumers can detect when a layer goes silent.
        self._layers: dict[str, float] = {}
        # Set of layer keys ever seen, used to zero-fill on empty cycles
        self._known_layers: set[str] = set()

    # ── write ──────────────────────────────────────────────────────────────

    def push_guard(self, name: str, layer: int, latency_ms: float) -> None:
        """Record per-guard latency and accumulate into the per-layer sum."""
        self._bus.push(name, latency_ms)
        layer_key = f"L{layer}"
        self._known_layers.add(layer_key)
        self._cycle_layer_sums[layer_key] = self._cycle_layer_sums.get(layer_key, 0.0) + latency_ms

    def push_stage(self, name: str, latency_ms: float) -> None:
        """Record pipeline-stage latency (source, policy, guards, sink, total)."""
        self._stages[name] = latency_ms
        self._bus.push(f"{self._STAGE_PREFIX}{name}", latency_ms)

    def commit_cycle(self) -> None:
        """Publish accumulated layer sums and reset accumulators for the next cycle.

        All previously-seen layer keys are preserved with 0.0 for cycles where
        they have no activity, so downstream consumers can detect silent layers.
        """
        self._layers = {k: self._cycle_layer_sums.get(k, 0.0) for k in self._known_layers}
        self._cycle_layer_sums = {}

    # ── read ───────────────────────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        """Return a structured snapshot consumed by TelemetryService._build_perf.

        Returns::

            {
              "guards": {name: latest_ms, …},
              "stages": {name: latest_ms, …},
              "layers": {layer_name: sum_ms, …},   # from last commit_cycle()
            }
        """
        all_vals = self._bus.all_latest()
        guards = {k: v for k, v in all_vals.items() if not k.startswith(self._STAGE_PREFIX)}
        return {"guards": guards, "stages": dict(self._stages), "layers": dict(self._layers)}

    def guard_names(self) -> list[str]:
        return [n for n in self._bus.guard_names() if not n.startswith(self._STAGE_PREFIX)]

    def latest(self, name: str) -> float | None:
        return self._bus.latest(name)

    def mean(self, name: str) -> float | None:
        return self._bus.mean(name)

    def max(self, name: str) -> float | None:
        return self._bus.max(name)

    def clear(self) -> None:
        self._bus.clear()
        self._stages.clear()
        self._cycle_layer_sums.clear()
        self._layers.clear()
        self._known_layers.clear()


__all__ = [
    "ObservationBus",
    "WatchdogTimer",
    "RiskController",
    "MetricBus",
    "ActionBus",
    "PipelineMetricBus",
]
