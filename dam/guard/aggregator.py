from __future__ import annotations

from dam.types.action import ValidatedAction
from dam.types.result import GuardDecision, GuardResult


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
        clampers = [r for r in results if r.decision == GuardDecision.CLAMP]
        try:
            merged_action: ValidatedAction | None = None
            for result in clampers:
                action = result.clamped_action
                if not isinstance(action, ValidatedAction):
                    raise ValueError("CLAMP guard returned no valid action")
                shape = (
                    merged_action.target_joint_positions.shape
                    if merged_action is not None
                    else None
                )
                action.validate_payload(shape)
                merged_action = (
                    action if merged_action is None else merged_action.merge_restrictive(action)
                )
            assert merged_action is not None
            merged_action.validate_payload()
        except Exception as exc:
            return GuardResult.fault(exc, "guard_code", "aggregator", worst_rep.layer)

        return GuardResult.clamp(
            clamped_action=merged_action,
            guard_name="aggregator",
            layer=worst_rep.layer,
            reason="; ".join(dict.fromkeys(r.reason for r in clampers if r.reason)),
        )

    return worst_rep
