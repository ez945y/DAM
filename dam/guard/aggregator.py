from __future__ import annotations

from typing import TYPE_CHECKING

from dam.types.result import GuardDecision, GuardResult

if TYPE_CHECKING:
    pass


def aggregate_decisions(results: list[GuardResult]) -> GuardResult:
    if not results:
        from dam.guard.layer import GuardLayer

        return GuardResult.success(guard_name="aggregator", layer=GuardLayer.L0)

    # 1. Determine the overall worst decision level
    # FAULT(3) > REJECT(2) > CLAMP(1) > PASS(0)
    worst_decision = max(r.decision for r in results)

    # 2. Pick the representative 'worst' result for metadata/reasoning
    # (Focusing on higher layers for priority in ties)
    worst_rep = max(results, key=lambda r: (r.decision.value, r.layer.value))

    # 3. If the worst is CLAMP, we MUST merge all clamped actions
    # to avoid losing corrections from parallel guards.
    if worst_decision == GuardDecision.CLAMP:
        clampers = [r for r in results if r.decision == GuardDecision.CLAMP and r.clamped_action]
        if not clampers:
            return worst_rep

        # Start with the first clamper's action
        merged_action = clampers[0].clamped_action
        if merged_action is None:
            return worst_rep  # safety

        # Successively merge other clampers using 'Most Restrictive' logic
        for i in range(1, len(clampers)):
            next_act = clampers[i].clamped_action
            if next_act:
                merged_action = merged_action.merge_restrictive(next_act)

        return GuardResult.clamp(
            clamped_action=merged_action,
            guard_name="aggregator",
            layer=worst_rep.layer,
            reason="; ".join({r.reason for r in clampers if r.reason}),
        )

    return worst_rep
