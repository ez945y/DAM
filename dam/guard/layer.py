from enum import IntEnum


class GuardLayer(IntEnum):
    L0 = 0  # OOD Detection
    L1 = 1  # Preflight Simulation
    L2 = 2  # Motion Safty — joint limits, workspace, and physical feasibility
    L3 = 3  # Task Execution — mission progress and logical flow
    L4 = 4  # Hardware Monitoring — motor status, temperatures, and watchdogs
