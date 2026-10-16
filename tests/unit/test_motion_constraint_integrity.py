"""Regression cases for constraints lost during motion fusion."""

import uuid
from types import SimpleNamespace

import numpy as np
import pytest

import dam
from dam.guard.aggregators.motion_qp import QPTerm
from dam.guard.builtin.motion import MotionGuard
from dam.guard.pipeline import CallbackResult
from dam.types.action import ActionProposal, ValidatedAction
from dam.types.observation import Observation
from dam.types.result import GuardDecision


def _container(callback, **params):
    return SimpleNamespace(
        name=callback,
        get_active_node=lambda: SimpleNamespace(
            node_id="test", constraint=SimpleNamespace(callback=callback, params=params)
        ),
    )


def _check(values, containers):
    from dam.boundary.callbacks import register_all

    register_all()
    action = ActionProposal(target_joint_positions=np.array(values))
    obs = Observation(timestamp=0, joint_positions=np.zeros(len(values)))
    return MotionGuard().check(obs, action, active_containers=containers, dt=1)


def test_passing_velocity_limit_cannot_be_broken_by_position_clamp():
    result = _check(
        [0.1],
        [
            _container("joint_velocity_limit", max_velocities=[0.2]),
            _container("joint_position_limits", lower=[1.0], upper=[2.0]),
        ],
    )
    assert result.decision == GuardDecision.FAULT


def test_feasible_coupled_pass_constraint_participates_in_solve():
    name = "coupled_" + uuid.uuid4().hex
    calls = []

    def coupled():
        calls.append(True)
        return CallbackResult.ok(
            name, metadata={"motion_qp": QPTerm(A=np.array([[1.0, 1.0]]), b=np.array([1.0]))}
        )

    dam.register_callback(name, coupled, layer="L1")
    result = _check(
        [0.5, 0.5],
        [
            _container(name),
            _container("joint_position_limits", lower=[0.8, -2.0], upper=[2.0, 2.0]),
        ],
    )
    assert result.decision == GuardDecision.CLAMP
    u = result.clamped_action.target_joint_positions
    assert u[0] >= 0.8 - 1e-7
    assert u.sum() <= 1.0 + 1e-7
    assert calls == [True]


def test_incompatible_clamps_are_not_softened_into_a_command():
    result = _check(
        [0.0],
        [
            _container("joint_position_limits", lower=[1.0], upper=[2.0]),
            _container("joint_position_limits", lower=[-2.0], upper=[-1.0]),
        ],
    )
    assert result.decision == GuardDecision.FAULT


@pytest.mark.parametrize("delta,accepted", [(5e-8, True), (2e-7, False)])
def test_candidate_residual_tolerance_is_explicit(delta, accepted):
    from dam.guard.aggregators.motion_qp import validate_term

    term = QPTerm(upper=np.array([1.0]))
    if accepted:
        validate_term(term, np.array([1.0 + delta]))
    else:
        with pytest.raises(ValueError, match="upper bound"):
            validate_term(term, np.array([1.0 + delta]))


@pytest.mark.parametrize(
    "term",
    [
        QPTerm(upper=np.array([np.nan])),
        QPTerm(upper=np.array([1.0, 1.0])),
        QPTerm(A=np.array([[1.0]])),
        QPTerm(A=np.array([[np.inf]]), b=np.array([1.0])),
        QPTerm(A=np.array([[1.0]]), b=np.array([1.0, 2.0])),
        QPTerm(A=np.array([[1.0, 1.0]]), b=np.array([1.0])),
    ],
)
def test_bad_constraint_dimensions_or_values_fail_closed(term):
    from dam.guard.aggregators.motion_qp import validate_term

    with pytest.raises(ValueError):
        validate_term(term, np.array([0.0]))


def test_infeasible_coupled_constraints_fail_closed():
    from dam.guard.aggregators.motion_qp import motion_qp_aggregator

    proposal = ActionProposal(target_joint_positions=np.array([0.0, 0.0]))
    clamp = CallbackResult.clamp(
        "coupled",
        ValidatedAction(
            target_joint_positions=np.array([0.0, 0.0]),
            original_proposal=proposal,
        ),
        metadata={
            "motion_qp": QPTerm(
                A=np.array([[1.0, 1.0], [-1.0, -1.0]]),
                b=np.array([-1.0, -1.0]),
            )
        },
    )
    assert motion_qp_aggregator([clamp], None) is None


@pytest.mark.parametrize("returned", [None, np.array([np.nan]), np.array([2.0])])
def test_solver_failure_or_false_solution_is_not_forwarded(monkeypatch, returned):
    from dam.runtime import qp_solver

    monkeypatch.setattr(qp_solver, "solve_hard_constraints", lambda *a, **kw: returned)
    result = _check([2.0], [_container("joint_position_limits", lower=[0.0], upper=[1.0])])
    assert result.decision == GuardDecision.FAULT


def test_dict_matrix_keeps_rows_and_columns():
    from dam.guard.aggregators.motion_qp import _coerce, validate_term

    term = _coerce({"A": [[1.0, 1.0], [-1.0, 0.0]], "b": [1.0, 0.0]})
    validate_term(term, np.array([0.5, 0.5]))
    with pytest.raises(ValueError):
        validate_term(term, np.array([0.8, 0.8]))


def test_norm_check_rejects_tangent_plane_escape():
    from dam.guard.aggregators.motion_qp import validate_term

    term = QPTerm(
        A=np.array([[1.0, 0.0]]),
        b=np.array([1.0]),
        norm_A=np.eye(2),
        norm_b=np.zeros(2),
        norm_limit=1.0,
    )
    with pytest.raises(ValueError, match="norm"):
        validate_term(term, np.array([0.8, 0.8]))


@pytest.mark.parametrize("scale", [1e-12, 1.0, 1e308])
def test_linear_constraint_tolerance_is_invariant_to_row_scale(scale):
    from dam.guard.aggregators.motion_qp import validate_term

    term = QPTerm(A=np.array([[scale]]), b=np.array([scale]))
    validate_term(term, np.array([0.5]))
    with pytest.raises(ValueError):
        validate_term(term, np.array([2.0]))


def test_pass_without_constraints_cannot_be_silently_dropped():
    name = "opaque_" + uuid.uuid4().hex
    calls = []

    def opaque(*, action):
        calls.append(True)
        return bool(action.target_joint_positions[0] <= 0.5)

    dam.register_callback(name, opaque, layer="L1")
    result = _check(
        [0.1],
        [
            _container(name),
            _container("joint_position_limits", lower=[1.0], upper=[2.0]),
        ],
    )
    assert result.decision == GuardDecision.FAULT
    assert calls == [True]


def test_final_check_also_rejects_inconsistent_pass_evidence(monkeypatch):
    from dam.guard.layer import GuardLayer
    from dam.runtime.guard_runtime import GuardRuntime
    from dam.types.result import GuardResult

    runtime = GuardRuntime(guards=[], boundary_containers={}, task_config={})
    monkeypatch.setattr(
        runtime._engine,
        "_run_flat_filtered",
        lambda *args: [
            GuardResult.pass_result(
                "inconsistent", GuardLayer.L1, metadata={"motion_qp": QPTerm(upper=np.array([0.0]))}
            ),
        ],
    )
    try:
        output, results = runtime.validate(
            Observation(timestamp=0.0, joint_positions=np.array([0.0])),
            ActionProposal(target_joint_positions=np.array([1.0])),
            "test",
        )
        assert output is None
        assert results[-1].guard_name == "final_constraints"
        assert results[-1].decision == GuardDecision.FAULT
    finally:
        runtime.shutdown()


@pytest.mark.parametrize("current", [False, True])
def test_current_slow_constraints_are_checked_but_cached_gate_is_not_rebound(current):
    from dam.guard.aggregators.motion_qp import validate_motion_evidence
    from dam.guard.layer import GuardLayer
    from dam.types.result import GuardResult

    results = [
        GuardResult.pass_result(
            "slow",
            GuardLayer.L1,
            metadata={
                "motion_qp": QPTerm(upper=np.array([0.0])),
                "slow_lane": True,
                "current_cycle_checked": current,
            },
        )
    ]
    candidate = ValidatedAction(target_joint_positions=np.array([1.0]))
    if current:
        with pytest.raises(ValueError, match="upper bound"):
            validate_motion_evidence(results, candidate)
    else:
        validate_motion_evidence(results, candidate)


@pytest.mark.parametrize("motion_clamp", [False, True])
def test_later_layer_cannot_break_prior_motion_constraint(motion_clamp):
    from dam.config.schema import StackfileConfig
    from dam.guard.layer import GuardLayer
    from dam.runtime.guard_runtime import GuardRuntime
    from dam.types.result import GuardResult

    name = "later_" + uuid.uuid4().hex
    calls = []

    def later(*, action):
        calls.append(float(action.target_joint_positions[0]))
        return GuardResult.clamp(
            ValidatedAction(
                target_joint_positions=np.array([-0.5]),
                original_proposal=action,
            ),
            name,
            GuardLayer.L2,
        )

    dam.register_callback(name, later, layer="L2")
    dam.api._register_builtins()
    config = StackfileConfig(
        **{
            "tasks": {"default": {"boundaries": ["position", "later"]}},
            "boundaries": {
                "position": {
                    "layer": "L1",
                    "type": "single",
                    "nodes": [
                        {
                            "callback": "joint_position_limits",
                            "params": {"upper": [1.0], "lower": [0.0]},
                        }
                    ],
                },
                "later": {"layer": "L2", "type": "single", "nodes": [{"callback": name}]},
            },
        }
    )
    runtime = GuardRuntime._from_config(config)
    try:
        runtime.start_task("default")
        output, results = runtime.validate(
            Observation(timestamp=0.0, joint_positions=np.array([0.0])),
            ActionProposal(target_joint_positions=np.array([2.0 if motion_clamp else 0.5])),
            "test",
        )
        assert output is None
        assert any(r.decision == GuardDecision.FAULT for r in results)
        assert calls == [1.0 if motion_clamp else 0.5]
    finally:
        runtime.shutdown()
