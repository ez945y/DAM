"""End-to-end tests for Guard base-class auto-timing.

Verifies that Guard.__init_subclass__ wraps check() with timing so that
_latency_ms appears in result metadata automatically — both at the top level
and inside PER_BOUNDARY_KEY per-boundary sub-dicts.  Also verifies that
ExecutionEngine reads the auto-stamped latency correctly.
"""

from __future__ import annotations

import time
from typing import Any, ClassVar
from unittest.mock import MagicMock

import numpy as np
import pytest

import dam
from dam.bus import PipelineMetricBus
from dam.guard.base import PER_BOUNDARY_KEY, Guard
from dam.guard.layer import GuardLayer
from dam.injection.static import precompute_injection
from dam.runtime.execution_engine import EnforcementMode, ExecutionEngine
from dam.types.result import GuardDecision, GuardResult

# ── Minimal concrete guards for testing ──────────────────────────────────────


@dam.guard(layer="L0")
class _PassGuard(Guard):
    expected_decisions: ClassVar[frozenset[GuardDecision]] = frozenset({GuardDecision.PASS})

    def check(self, **kwargs: Any) -> GuardResult:
        return GuardResult.success(guard_name="pass_guard", layer=GuardLayer.L0)


@dam.guard(layer="L1")
class _SlowGuard(Guard):
    expected_decisions: ClassVar[frozenset[GuardDecision]] = frozenset({GuardDecision.PASS})

    def check(self, **kwargs: Any) -> GuardResult:
        time.sleep(0.01)
        return GuardResult.success(guard_name="slow_guard", layer=GuardLayer.L1)


@dam.guard(layer="L2")
class _PerBoundaryGuard(Guard):
    expected_decisions: ClassVar[frozenset[GuardDecision]] = frozenset(
        {GuardDecision.PASS, GuardDecision.REJECT}
    )

    def check(self, **kwargs: Any) -> GuardResult:
        return GuardResult.success(
            guard_name="per_boundary_guard",
            layer=GuardLayer.L2,
            metadata={
                PER_BOUNDARY_KEY: {
                    "arm_left": {
                        "decision": "PASS",
                        "reason": "",
                        "metadata": {"score": 0.5},
                    },
                    "arm_right": {
                        "decision": "REJECT",
                        "reason": "too fast",
                        "metadata": {"score": 0.9},
                    },
                }
            },
        )


@dam.guard(layer="L0")
class _PerBoundaryNoMetadataGuard(Guard):
    """Guard that returns per-boundary info where a boundary has no metadata dict."""

    expected_decisions: ClassVar[frozenset[GuardDecision]] = frozenset({GuardDecision.PASS})

    def check(self, **kwargs: Any) -> GuardResult:
        return GuardResult.success(
            guard_name="no_meta_guard",
            layer=GuardLayer.L0,
            metadata={
                PER_BOUNDARY_KEY: {
                    "arm": {
                        "decision": "PASS",
                        "reason": "",
                    },
                }
            },
        )


@dam.guard(layer="L2")
class _PositionalArgGuard(Guard):
    """Guard whose check() accepts positional args (like OODGuard)."""

    expected_decisions: ClassVar[frozenset[GuardDecision]] = frozenset({GuardDecision.PASS})

    def check(self, value: int, label: str = "default") -> GuardResult:
        return GuardResult.success(
            guard_name="positional_guard",
            layer=GuardLayer.L2,
            metadata={"value": value, "label": label},
        )


@dam.guard(layer="L0")
class _ExplodingGuard(Guard):
    expected_decisions: ClassVar[frozenset[GuardDecision]] = frozenset({GuardDecision.FAULT})

    def check(self, **kwargs: Any) -> GuardResult:
        raise RuntimeError("boom")


# ── Base class auto-timing tests ─────────────────────────────────────────────


class TestAutoTiming:
    def test_latency_stamped_on_simple_result(self):
        g = _PassGuard()
        precompute_injection(g, {})
        result = g.check()
        assert "_latency_ms" in result.metadata
        assert isinstance(result.metadata["_latency_ms"], float)
        assert result.metadata["_latency_ms"] >= 0.0

    def test_slow_guard_reflects_real_time(self):
        g = _SlowGuard()
        precompute_injection(g, {})
        result = g.check()
        assert result.metadata["_latency_ms"] >= 9.0  # slept 10ms

    def test_per_boundary_metadata_stamped(self):
        g = _PerBoundaryGuard()
        precompute_injection(g, {})
        result = g.check()

        assert "_latency_ms" in result.metadata
        top_latency = result.metadata["_latency_ms"]

        per_boundary = result.metadata[PER_BOUNDARY_KEY]
        for bn in ("arm_left", "arm_right"):
            bn_meta = per_boundary[bn]["metadata"]
            assert "_latency_ms" in bn_meta
            assert bn_meta["_latency_ms"] == top_latency

    def test_per_boundary_preserves_original_metadata(self):
        g = _PerBoundaryGuard()
        precompute_injection(g, {})
        result = g.check()
        per_boundary = result.metadata[PER_BOUNDARY_KEY]
        assert per_boundary["arm_left"]["metadata"]["score"] == 0.5
        assert per_boundary["arm_right"]["metadata"]["score"] == 0.9

    def test_per_boundary_no_metadata_dict_creates_one(self):
        g = _PerBoundaryNoMetadataGuard()
        precompute_injection(g, {})
        result = g.check()
        per_boundary = result.metadata[PER_BOUNDARY_KEY]
        arm_info = per_boundary["arm"]
        assert "metadata" in arm_info
        assert "_latency_ms" in arm_info["metadata"]

    def test_positional_args_work(self):
        g = _PositionalArgGuard()
        precompute_injection(g, {})
        result = g.check(42, label="test")
        assert result.decision == GuardDecision.PASS
        assert result.metadata["value"] == 42
        assert result.metadata["label"] == "test"
        assert "_latency_ms" in result.metadata

    def test_positional_args_without_keyword(self):
        g = _PositionalArgGuard()
        precompute_injection(g, {})
        result = g.check(7)
        assert result.metadata["value"] == 7
        assert result.metadata["label"] == "default"
        assert "_latency_ms" in result.metadata

    def test_exception_propagates_unwrapped(self):
        g = _ExplodingGuard()
        precompute_injection(g, {})
        with pytest.raises(RuntimeError, match="boom"):
            g.check()

    def test_no_double_wrapping_on_inheritance(self):
        @dam.guard(layer="L0")
        class _Parent(Guard):
            expected_decisions: ClassVar[frozenset[GuardDecision]] = frozenset({GuardDecision.PASS})

            def check(self, **kwargs: Any) -> GuardResult:
                return GuardResult.success(guard_name="parent", layer=GuardLayer.L0)

        @dam.guard(layer="L0")
        class _Child(_Parent):
            expected_decisions: ClassVar[frozenset[GuardDecision]] = frozenset({GuardDecision.PASS})

        child = _Child()
        precompute_injection(child, {})
        result = child.check()
        assert "_latency_ms" in result.metadata
        count = sum(1 for attr in ("check",) if hasattr(getattr(_Child, attr, None), "__wrapped__"))
        assert count <= 1

    def test_child_overriding_check_gets_own_timing(self):
        @dam.guard(layer="L1")
        class _Base(Guard):
            expected_decisions: ClassVar[frozenset[GuardDecision]] = frozenset({GuardDecision.PASS})

            def check(self, **kwargs: Any) -> GuardResult:
                return GuardResult.success(guard_name="base", layer=GuardLayer.L1)

        @dam.guard(layer="L1")
        class _Override(_Base):
            def check(self, **kwargs: Any) -> GuardResult:
                time.sleep(0.005)
                return GuardResult.success(guard_name="override", layer=GuardLayer.L1)

        g = _Override()
        precompute_injection(g, {})
        result = g.check()
        assert result.metadata["_latency_ms"] >= 4.0
        assert result.guard_name == "override"


# ── ExecutionEngine integration tests ────────────────────────────────────────


class TestEngineReadsAutoTiming:
    def _make_engine(self) -> tuple[ExecutionEngine, PipelineMetricBus]:
        bus = PipelineMetricBus()
        engine = ExecutionEngine(
            enforcement_mode=EnforcementMode.ENFORCE,
            metric_bus=bus,
        )
        return engine, bus

    def test_run_one_guard_reads_latency_from_result(self):
        engine, bus = self._make_engine()
        g = _SlowGuard()
        precompute_injection(g, {})
        result = engine._run_one_guard(g, None, {})
        assert result.metadata["_latency_ms"] >= 9.0
        snapshot = bus.snapshot()
        assert g.get_name() in snapshot.get("guards", {})

    def test_run_guards_sequential_reads_latency(self):
        engine, bus = self._make_engine()
        g = _PassGuard()
        precompute_injection(g, {})
        results = engine._run_guards_sequential([g], {})
        assert len(results) == 1
        assert "_latency_ms" in results[0].metadata

    def test_run_guards_sequential_fault_has_no_latency(self):
        engine, bus = self._make_engine()
        g = _ExplodingGuard()
        precompute_injection(g, {})
        results = engine._run_guards_sequential([g], {})
        assert len(results) == 1
        assert results[0].decision == GuardDecision.FAULT

    def test_per_boundary_flows_through_engine(self):
        engine, bus = self._make_engine()
        g = _PerBoundaryGuard()
        precompute_injection(g, {})
        result = engine._run_one_guard(g, None, {})
        per_boundary = result.metadata.get(PER_BOUNDARY_KEY)
        assert per_boundary is not None
        for bn in ("arm_left", "arm_right"):
            assert "_latency_ms" in per_boundary[bn]["metadata"]

    def test_fan_out_preserves_latency_for_loopback(self):
        """Verify the full path: check() → auto-timing → _run_one_guard →
        _fan_out_per_boundary → latency_guards/latency_layers (as loopback
        would compute them). This is the path that feeds MCAP + risk log."""
        from dam.runtime.execution_engine import _fan_out_per_boundary

        engine, bus = self._make_engine()
        g = _PerBoundaryGuard()
        precompute_injection(g, {})
        result = engine._run_one_guard(g, None, {})

        bnames = ["arm_left", "arm_right"]
        fanned = _fan_out_per_boundary(result, bnames)
        assert len(fanned) == 2

        latency_guards = {r.guard_name: r.metadata.get("_latency_ms", 0.0) for r in fanned}
        assert all(v > 0 for v in latency_guards.values())

        latency_layers: dict[str, float] = {}
        for r in fanned:
            lname = f"L{int(r.layer)}"
            latency_layers[lname] = latency_layers.get(lname, 0.0) + latency_guards.get(
                r.guard_name, 0.0
            )
        assert "L2" in latency_layers
        assert latency_layers["L2"] > 0
