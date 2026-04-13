import numpy as np
import yaml

from dam.runtime.guard_runtime import GuardRuntime
from dam.types.action import ActionProposal
from dam.types.observation import Observation
from dam.types.result import GuardDecision


def test_guard_runtime_param_injection(tmp_path):
    from dam.boundary.builtin_callbacks import register_all as reg_callbacks
    from dam.guard.builtin import register_all as reg_guards

    reg_callbacks()
    reg_guards()

    # Create a dummy stackfile with a boundary that uses flat params
    stack_content = {
        "version": "1",
        "boundaries": {
            "test_kin": {
                "type": "single",
                "nodes": [
                    {
                        "node_id": "n0",
                        "callback": "joint_position_limits",
                        "params": {
                            "upper": [0.1, 0.1, 0.1, 0.1, 0.1, 0.1],
                            "lower": [-0.1, -0.1, -0.1, -0.1, -0.1, -0.1],
                        },
                        "fallback": "emergency_stop",
                    }
                ],
            }
        },
        "tasks": {"default": {"boundaries": ["test_kin"]}},
        "safety": {"control_frequency_hz": 10.0, "enforcement_mode": "enforce"},
    }

    sf_path = tmp_path / "stack.yaml"
    with open(sf_path, "w") as f:
        yaml.dump(stack_content, f)

    runtime = GuardRuntime.from_stackfile(str(sf_path))
    runtime.start_task("default")

    # 1. Action within injected limits (0.05 < 0.1)
    obs = Observation(
        timestamp=0.0,
        joint_positions=np.zeros(6),
        joint_velocities=np.zeros(6),
        end_effector_pose=np.zeros(7),
    )
    action = ActionProposal(target_joint_positions=np.array([0.05] * 6))

    # Run a step
    validated, results, fallback = runtime.validate(obs, action, "test-trace")

    # Find the result for test_kin
    kin_result = next(r for r in results if r.guard_name == "test_kin")
    assert kin_result.decision == GuardDecision.PASS

    # 2. Action outside injected limits (0.5 > 0.1)
    action_bad = ActionProposal(target_joint_positions=np.array([0.5] * 6))
    validated_bad, results_bad, fallback_bad = runtime.validate(obs, action_bad, "test-trace")

    kin_result_bad = next(r for r in results_bad if r.guard_name == "test_kin")
    # MotionGuard returns CLAMP for joint_position_limits
    assert kin_result_bad.decision == GuardDecision.CLAMP
    assert np.allclose(validated_bad.target_joint_positions, np.array([0.1] * 6))
