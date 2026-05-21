"""Unit tests for dam/guard/pipeline.py — the shared callback-driven pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pytest

from dam.boundary.callbacks._registry import boundary_callback
from dam.guard.layer import GuardLayer
from dam.guard.pipeline import (
    CallbackResult,
    aggregate,
    run_and_aggregate,
    run_callbacks,
    sequential_clamp_aggregator,
)
from dam.registry.callback import get_global_registry
from dam.types.action import ActionProposal, ValidatedAction
from dam.types.result import GuardDecision

# ── Stub container / node / constraint ────────────────────────────────────────


@dataclass
class _Constraint:
    callback: str | None
    params: dict[str, Any]


@dataclass
class _Node:
    node_id: str
    constraint: _Constraint | None


class _Container:
    def __init__(self, name: str, node: _Node) -> None:
        self.name = name
        self._node = node

    def get_active_node(self) -> _Node:
        return self._node


def _make_container(
    name: str, callback: str | None, params: dict[str, Any] | None = None
) -> _Container:
    constraint = _Constraint(callback=callback, params=params or {})
    return _Container(name=name, node=_Node(node_id=f"{name}/0", constraint=constraint))


# ── Test fixtures: register a handful of callbacks via the real registry ──────


def _register_once(reg: Any, name: str, fn: Any) -> None:
    """Idempotent registration helper; the global registry raises on duplicates."""
    if name not in reg._registry:  # noqa: SLF001 — test inspecting registry state
        reg.register(name, fn)


# Test callbacks defined at module level so the @boundary_callback decorator
# only fires once (it raises on duplicate names in the catalog).


@boundary_callback(name="_test_bool_pass", layer="L1", description="")
def _cb_bool_pass() -> bool:
    return True


@boundary_callback(name="_test_bool_fail", layer="L1", description="")
def _cb_bool_fail() -> bool:
    return False


@boundary_callback(name="_test_tuple_fail", layer="L1", description="")
def _cb_tuple_fail() -> tuple[bool, str]:
    return False, "explicit reason"


@boundary_callback(name="_test_callback_result_clamp", layer="L1", description="")
def _cb_clamp(action: ActionProposal) -> CallbackResult:
    clamped = ValidatedAction(
        target_joint_positions=np.zeros_like(action.target_joint_positions),
        was_clamped=True,
        original_proposal=action,
    )
    return CallbackResult.clamp("_test_callback_result_clamp", clamped, reason="zeroed")


@boundary_callback(name="_test_raises", layer="L1", description="")
def _cb_raises() -> bool:
    raise RuntimeError("boom")


@boundary_callback(name="_test_param_echo", layer="L1", description="")
def _cb_param_echo(value: int = 0) -> CallbackResult:
    return CallbackResult.ok(boundary_name="_test_param_echo", reason=str(value))


@pytest.fixture(autouse=True)
def _register_test_callbacks() -> None:
    reg = get_global_registry()
    for name, fn in (
        ("_test_bool_pass", _cb_bool_pass),
        ("_test_bool_fail", _cb_bool_fail),
        ("_test_tuple_fail", _cb_tuple_fail),
        ("_test_callback_result_clamp", _cb_clamp),
        ("_test_raises", _cb_raises),
        ("_test_param_echo", _cb_param_echo),
    ):
        _register_once(reg, name, fn)


# ── run_callbacks ─────────────────────────────────────────────────────────────


def test_run_callbacks_empty_containers_returns_empty_list() -> None:
    assert run_callbacks(containers=None, runtime_pool={}) == []
    assert run_callbacks(containers=[], runtime_pool={}) == []


def test_run_callbacks_skips_containers_without_callback() -> None:

    c = _make_container("b1", callback=None)
    assert run_callbacks(containers=[c], runtime_pool={}) == []


def test_run_callbacks_coerces_bool_pass() -> None:

    c = _make_container("b1", callback="_test_bool_pass")
    results = run_callbacks(containers=[c], runtime_pool={})
    assert len(results) == 1
    assert results[0].decision == GuardDecision.PASS
    assert results[0].boundary_name == "b1"


def test_run_callbacks_coerces_bool_fail_with_default_reason() -> None:

    c = _make_container("b1", callback="_test_bool_fail", params={"k": 1})
    results = run_callbacks(containers=[c], runtime_pool={})
    assert results[0].decision == GuardDecision.REJECT
    assert "_test_bool_fail" in results[0].reason
    assert "k=1" in results[0].reason


def test_run_callbacks_coerces_tuple_with_explicit_reason() -> None:

    c = _make_container("b1", callback="_test_tuple_fail")
    results = run_callbacks(containers=[c], runtime_pool={})
    assert results[0].decision == GuardDecision.REJECT
    assert results[0].reason == "explicit reason"


def test_run_callbacks_accepts_callback_result_with_clamp() -> None:

    c = _make_container("b1", callback="_test_callback_result_clamp")
    action = ActionProposal(target_joint_positions=np.array([1.0, 1.0, 1.0]))
    results = run_callbacks(containers=[c], runtime_pool={"action": action})
    assert len(results) == 1
    r = results[0]
    assert r.decision == GuardDecision.CLAMP
    assert r.clamped_action is not None
    assert np.allclose(r.clamped_action.target_joint_positions, 0.0)


def test_run_callbacks_captures_exception_as_fault() -> None:

    c = _make_container("b1", callback="_test_raises")
    results = run_callbacks(containers=[c], runtime_pool={})
    assert results[0].decision == GuardDecision.FAULT
    assert "boom" in results[0].reason
    assert results[0].fault_source == "guard_code"


def test_run_callbacks_filters_by_expected_layer() -> None:

    c = _make_container("b1", callback="_test_bool_pass")  # registered as L1
    assert run_callbacks(containers=[c], runtime_pool={}, expected_layer="L2") == []
    assert run_callbacks(containers=[c], runtime_pool={}, expected_layer="L1") != []


def test_run_callbacks_params_override_runtime_pool() -> None:
    """Static node params win over runtime pool values for the same key."""
    c = _make_container("b1", callback="_test_param_echo", params={"value": 42})
    results = run_callbacks(containers=[c], runtime_pool={"value": 7})
    assert results[0].reason == "42"


# ── aggregate ─────────────────────────────────────────────────────────────────


def test_aggregate_empty_results_returns_pass() -> None:
    out = aggregate([], guard_name="g", guard_layer=GuardLayer.L1)
    assert out.decision == GuardDecision.PASS


def test_aggregate_fault_beats_everything() -> None:
    results = [
        CallbackResult.ok("a"),
        CallbackResult.violate("b", "bad"),
        CallbackResult.fault("c", "oops", fault_source="environment"),
    ]
    out = aggregate(results, guard_name="g", guard_layer=GuardLayer.L1)
    assert out.decision == GuardDecision.FAULT
    assert "c: oops" in out.reason
    assert out.fault_source == "environment"


def test_aggregate_reject_beats_clamp_and_pass() -> None:
    action = ActionProposal(target_joint_positions=np.array([1.0, 1.0]))
    clamped = ValidatedAction(
        target_joint_positions=np.zeros(2), was_clamped=True, original_proposal=action
    )
    results = [
        CallbackResult.clamp("a", clamped),
        CallbackResult.violate("b", "nope"),
        CallbackResult.ok("c"),
    ]
    out = aggregate(results, guard_name="g", guard_layer=GuardLayer.L1)
    assert out.decision == GuardDecision.REJECT
    assert "b: nope" in out.reason


def test_aggregate_clamp_returns_fused_action() -> None:
    action = ActionProposal(target_joint_positions=np.array([2.0, 2.0]))
    clamp_a = ValidatedAction(
        target_joint_positions=np.array([1.0, 2.0]),
        was_clamped=True,
        original_proposal=action,
    )
    clamp_b = ValidatedAction(
        target_joint_positions=np.array([2.0, 1.0]),
        was_clamped=True,
        original_proposal=action,
    )
    results = [
        CallbackResult.clamp("a", clamp_a),
        CallbackResult.clamp("b", clamp_b),
    ]
    out = aggregate(results, guard_name="g", guard_layer=GuardLayer.L1, action_in=clamp_a)
    assert out.decision == GuardDecision.CLAMP
    assert out.clamped_action is not None
    # merge_restrictive picks values further from the original proposal per joint.
    # original = [2,2]; clamp_a = [1,2], clamp_b = [2,1].
    # Joint 0: a moved (1.0), b didn't. → take a's value = 1.0
    # Joint 1: b moved (1.0), a didn't. → take b's value = 1.0
    assert np.allclose(out.clamped_action.target_joint_positions, [1.0, 1.0])


def test_aggregate_all_pass_returns_pass() -> None:
    results = [CallbackResult.ok("a"), CallbackResult.ok("b")]
    out = aggregate(results, guard_name="g", guard_layer=GuardLayer.L1)
    assert out.decision == GuardDecision.PASS


def test_aggregate_clamp_aggregator_returning_none_degrades_to_pass() -> None:
    action = ActionProposal(target_joint_positions=np.array([1.0]))
    clamped = ValidatedAction(
        target_joint_positions=np.array([0.5]), was_clamped=True, original_proposal=action
    )
    results = [CallbackResult.clamp("a", clamped)]
    out = aggregate(
        results,
        guard_name="g",
        guard_layer=GuardLayer.L1,
        clamp_aggregator=lambda _clamps, _action_in: None,
    )
    assert out.decision == GuardDecision.PASS


# ── sequential_clamp_aggregator ───────────────────────────────────────────────


def test_sequential_aggregator_no_clamps_passes_action_in_through() -> None:
    action = ActionProposal(target_joint_positions=np.array([1.0]))
    a_in = ValidatedAction(target_joint_positions=np.array([0.5]), original_proposal=action)
    out = sequential_clamp_aggregator([], a_in)
    assert out is a_in


def test_sequential_aggregator_single_clamp_returns_it() -> None:
    action = ActionProposal(target_joint_positions=np.array([1.0]))
    c = ValidatedAction(
        target_joint_positions=np.array([0.5]), was_clamped=True, original_proposal=action
    )
    out = sequential_clamp_aggregator([CallbackResult.clamp("a", c)], None)
    assert out is c


# ── run_and_aggregate convenience ─────────────────────────────────────────────


def test_run_and_aggregate_end_to_end() -> None:

    c = _make_container("b1", callback="_test_bool_fail")
    results, final = run_and_aggregate(
        containers=[c],
        runtime_pool={},
        expected_layer="L1",
        guard_name="g",
        guard_layer=GuardLayer.L1,
    )
    assert len(results) == 1
    assert final.decision == GuardDecision.REJECT
