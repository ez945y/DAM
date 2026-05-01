from enum import IntEnum


class GuardLayer(IntEnum):
    L0 = 0  # OOD Detection
    L1 = 1  # Physical Kinematics — joint limits, workspace, and physical feasibility
    L2 = 2  # Task Execution — mission progress and logical flow
    L3 = 3  # Hardware Monitoring — motor status, temperatures, and watchdogs
