"""Contract tests for Guard.expected_decisions.

Each concrete guard declares the GuardDecision verbs it may emit. FAULT is
implicitly always allowed (internal exception path). The set is checked here
so a guard accidentally drifting (e.g. starting to CLAMP when previously
only REJECTing) trips a test instead of silently changing layer semantics.
"""

from __future__ import annotations

from dam.guard.base import Guard
from dam.guard.builtin import ExecutionGuard, HardwareGuard, MotionGuard, OODGuard
from dam.guard.layer import GuardLayer
from dam.types.result import GuardDecision

PASS = GuardDecision.PASS
CLAMP = GuardDecision.CLAMP
REJECT = GuardDecision.REJECT
FAULT = GuardDecision.FAULT


def test_guard_base_declares_expected_decisions_field() -> None:
    # The annotation must exist on the abstract class so concrete guards know
    # to override it.
    assert "expected_decisions" in Guard.__annotations__


def test_ood_guard_decisions() -> None:
    assert OODGuard.expected_decisions == frozenset({PASS, REJECT, FAULT})


def test_motion_guard_decisions() -> None:
    assert MotionGuard.expected_decisions == frozenset({PASS, CLAMP, REJECT, FAULT})


def test_execution_guard_decisions() -> None:
    assert ExecutionGuard.expected_decisions == frozenset({PASS, CLAMP, REJECT, FAULT})


def test_hardware_guard_decisions() -> None:
    assert HardwareGuard.expected_decisions == frozenset({PASS, CLAMP, FAULT})


def test_fault_is_always_implicitly_allowed() -> None:
    for cls in (OODGuard, MotionGuard, ExecutionGuard, HardwareGuard):
        assert FAULT in cls.expected_decisions, cls.__name__


def test_each_guard_overrides_expected_decisions() -> None:
    # Concrete guards must not rely on the abstract default (there isn't one).
    for cls in (OODGuard, MotionGuard, ExecutionGuard, HardwareGuard):
        assert "expected_decisions" in cls.__dict__, (
            f"{cls.__name__} must declare its own expected_decisions"
        )


def test_builtin_guards_declare_canonical_layer_on_class() -> None:
    # Phase 5: each builtin guard carries its canonical layer via the
    # @dam.guard(layer=...) class decorator — importing the class is enough,
    # no manual decoration needed in tests or call sites.
    expected = {
        OODGuard: GuardLayer.L0,
        MotionGuard: GuardLayer.L1,
        ExecutionGuard: GuardLayer.L2,
        HardwareGuard: GuardLayer.L3,
    }
    for cls, layer in expected.items():
        assert cls._guard_layer == layer, cls.__name__
        # The decorator also primes the injection slots used at startup.
        assert hasattr(cls, "_cached_param_names"), cls.__name__
