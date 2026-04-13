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
        GuardResult.reject("oops", "g2", GuardLayer.L2),
    ]
    agg = aggregate_decisions(results)
    assert agg.decision == GuardDecision.REJECT


def test_aggregator_fault_wins_over_reject():
    results = [
        GuardResult.reject("oops", "g1", GuardLayer.L2),
        GuardResult.fault(RuntimeError("fail"), "env", "g2", GuardLayer.L2),
    ]
    agg = aggregate_decisions(results)
    assert agg.decision == GuardDecision.FAULT


def test_aggregator_clamp_wins_over_pass():
    results = [
        GuardResult.success("g1", GuardLayer.L2),
        GuardResult.clamp(None, "g2", GuardLayer.L2),  # type: ignore
    ]
    agg = aggregate_decisions(results)
    assert agg.decision == GuardDecision.CLAMP
