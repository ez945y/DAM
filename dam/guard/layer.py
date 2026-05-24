from enum import IntEnum


class GuardLayer(IntEnum):
    L0 = 0  # OOD Detection
    L1 = 1  # Physical Kinematics — joint limits, workspace, and physical feasibility
    L2 = 2  # Task Execution — mission progress and logical flow
    L3 = 3  # Hardware Monitoring — motor status, temperatures, and watchdogs


# Default event class per layer. Used by GuardResult.resolved_event_class()
# when a result doesn't carry an explicit event_class. Callbacks/guards may
# override by setting `event_class` on the GuardResult directly.
LAYER_TO_EVENT_CLASS: dict[GuardLayer, str] = {
    GuardLayer.L0: "perception",
    GuardLayer.L1: "motion",
    GuardLayer.L2: "task",
    GuardLayer.L3: "hardware",
}


# Default pipeline placement per layer. L0+L1 share phase 0 (run in parallel);
# L2 follows in phase 1 with phase-0 clamp accumulation applied. L3 is
# always-on (parallel to all phases, joined at cycle end). Override per-guard
# via @guard(phase=..., always=...) or per-instance via stackfile.
LAYER_DEFAULT_PHASE: dict[GuardLayer, int] = {
    GuardLayer.L0: 0,
    GuardLayer.L1: 0,
    GuardLayer.L2: 1,
}
LAYER_DEFAULT_ALWAYS: dict[GuardLayer, bool] = {
    GuardLayer.L3: True,
}
