from dam.guard.aggregator import aggregate_decisions
from dam.guard.layer import GuardLayer
from dam.types.result import GuardDecision, GuardResult


def test_aggregator_empty_results():
    result = aggregate_decisions([])
    assert result.decision == GuardDecision.PASS
    assert result.guard_name == "aggregator"


def test_aggregator_reject_wins():
    results = [
        GuardResult.success("g1", GuardLayer.L2),
        GuardResult.reject("g2", GuardLayer.L2, "oops"),
    ]
    agg = aggregate_decisions(results)
    assert agg.decision == GuardDecision.REJECT


def test_aggregator_fault_wins_over_reject():
    results = [
        GuardResult.reject("g1", GuardLayer.L2, "oops"),
        GuardResult.fault(RuntimeError("fail"), "env", "g2", GuardLayer.L2),
    ]
    agg = aggregate_decisions(results)
    assert agg.decision == GuardDecision.FAULT


def test_aggregator_missing_clamp_payload_faults():
    results = [
        GuardResult.success("g1", GuardLayer.L2),
        GuardResult.clamp(None, "g2", GuardLayer.L2),  # type: ignore
    ]
    agg = aggregate_decisions(results)
    assert agg.decision == GuardDecision.FAULT


def test_missing_clamp_cannot_hide_behind_valid_clamp():
    import numpy as np

    from dam.types.action import ValidatedAction

    result = aggregate_decisions(
        [
            GuardResult.clamp(ValidatedAction(np.zeros(2)), "good", GuardLayer.L1),
            GuardResult.clamp(None, "bad", GuardLayer.L2),
        ]
    )
    assert result.decision == GuardDecision.FAULT


def test_cross_guard_shape_mismatch_is_a_fault():
    import numpy as np

    from dam.types.action import ValidatedAction

    result = aggregate_decisions(
        [
            GuardResult.clamp(ValidatedAction(np.zeros(2)), "first", GuardLayer.L1),
            GuardResult.clamp(ValidatedAction(np.zeros(1)), "second", GuardLayer.L2),
        ]
    )
    assert result.decision == GuardDecision.FAULT


def test_opposite_corrections_are_not_arbitrarily_chosen():
    import numpy as np

    from dam.types.action import ActionProposal, ValidatedAction

    proposal = ActionProposal(np.zeros(1))
    result = aggregate_decisions(
        [
            GuardResult.clamp(
                ValidatedAction(np.array([-1.0]), original_proposal=proposal), "left", GuardLayer.L1
            ),
            GuardResult.clamp(
                ValidatedAction(np.array([1.0]), original_proposal=proposal), "right", GuardLayer.L2
            ),
        ]
    )
    assert result.decision == GuardDecision.FAULT


def test_distinct_corrections_without_baseline_are_not_dropped():
    import numpy as np

    from dam.types.action import ValidatedAction

    result = aggregate_decisions(
        [
            GuardResult.clamp(ValidatedAction(np.zeros(1)), "first", GuardLayer.L1),
            GuardResult.clamp(ValidatedAction(np.ones(1)), "second", GuardLayer.L2),
        ]
    )
    assert result.decision == GuardDecision.FAULT
