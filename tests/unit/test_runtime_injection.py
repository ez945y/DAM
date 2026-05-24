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
        "safety": {"control_frequency_hz": 30.0, "enforcement_mode": "enforce"},
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
    validated, results = runtime.validate(obs, action, "test-trace")

    # Find the result for test_kin
    kin_result = next(r for r in results if r.guard_name == "test_kin")
    assert kin_result.decision == GuardDecision.PASS

    # 2. Action outside injected limits (0.5 > 0.1)
    action_bad = ActionProposal(target_joint_positions=np.array([0.5] * 6))
    validated_bad, results_bad = runtime.validate(obs, action_bad, "test-trace")

    kin_result_bad = next(r for r in results_bad if r.guard_name == "test_kin")
    # MotionGuard returns CLAMP for joint_position_limits
    assert kin_result_bad.decision == GuardDecision.CLAMP
    assert np.allclose(validated_bad.target_joint_positions, np.array([0.1] * 6))


def test_hardware_callback_params_flow_from_stackfile(tmp_path):
    from dam.boundary.builtin_callbacks import register_all as reg_callbacks
    from dam.guard.builtin import register_all as reg_guards

    reg_callbacks()
    reg_guards()

    stack_content = {
        "version": "1",
        "guards": [{"hardware": "hardware"}],
        "boundaries": {
            "hardware_watchdog": {
                "layer": "L3",
                "type": "single",
                "nodes": [
                    {
                        "node_id": "n0",
                        "callback": "hardware_watchdog",
                        "params": {"max_staleness_ms": 1000.0},
                        "fallback": "emergency_stop",
                    }
                ],
            }
        },
        "tasks": {"default": {"boundaries": ["hardware_watchdog"]}},
        "safety": {"control_frequency_hz": 30.0, "enforcement_mode": "enforce"},
    }

    sf_path = tmp_path / "stack.yaml"
    with open(sf_path, "w") as f:
        yaml.dump(stack_content, f)

    runtime = GuardRuntime.from_stackfile(str(sf_path))
    runtime.start_task("default")

    now = 100.0
    obs = Observation(
        timestamp=now - 0.837,
        joint_positions=np.zeros(6),
        joint_velocities=np.zeros(6),
    )
    action = ActionProposal(target_joint_positions=np.zeros(6))

    validated, results = runtime.validate(obs, action, "test-trace", now=now)

    watchdog_result = next(r for r in results if r.guard_name == "hardware_watchdog")
    assert watchdog_result.decision == GuardDecision.PASS
    assert validated is not None


def test_l3_boundaries_receive_separate_metadata(tmp_path, monkeypatch):
    from dam.boundary.builtin_callbacks import register_all as reg_callbacks
    from dam.guard.builtin import register_all as reg_guards

    reg_callbacks()
    reg_guards()

    import importlib

    hardware_mod = importlib.import_module("dam.guard.builtin.hardware")

    monkeypatch.setattr(
        hardware_mod,
        "collect_host_health",
        lambda: {"cpu_percent": 12.0, "memory_percent": 30.0, "timestamp": 1.0},
    )

    stack_content = {
        "version": "1",
        "guards": [{"hardware": "hardware"}],
        "boundaries": {
            "hardware_watchdog": {
                "layer": "L3",
                "type": "single",
                "nodes": [
                    {
                        "node_id": "n0",
                        "callback": "hardware_watchdog",
                        "params": {
                            "max_staleness_ms": 1000.0,
                            "monitor_voltage": True,
                            "min_voltage_v": 10.0,
                            "max_voltage_v": 13.0,
                        },
                        "fallback": "emergency_stop",
                    }
                ],
            },
            "host_health": {
                "layer": "L3",
                "type": "single",
                "nodes": [
                    {
                        "node_id": "n0",
                        "callback": "host_health_limit",
                        "params": {"max_cpu_percent": 90.0, "max_memory_percent": 90.0},
                        "fallback": "emergency_stop",
                    }
                ],
            },
        },
        "tasks": {"default": {"boundaries": ["hardware_watchdog", "host_health"]}},
        "safety": {"control_frequency_hz": 30.0, "enforcement_mode": "enforce"},
    }

    sf_path = tmp_path / "stack.yaml"
    with open(sf_path, "w") as f:
        yaml.dump(stack_content, f)

    runtime = GuardRuntime.from_stackfile(str(sf_path))
    runtime.start_task("default")

    obs = Observation(
        timestamp=100.0,
        joint_positions=np.zeros(6),
        joint_velocities=np.zeros(6),
        metadata={"hardware_status": {"voltages": {"m1": 12.0, "m2": 12.1}}},
    )
    action = ActionProposal(target_joint_positions=np.zeros(6))

    _, results = runtime.validate(obs, action, "test-trace", now=100.0)

    watchdog_result = next(r for r in results if r.guard_name == "hardware_watchdog")
    host_result = next(r for r in results if r.guard_name == "host_health")
    assert "voltages" in watchdog_result.metadata
    assert "host_health" not in watchdog_result.metadata
    assert "host_health" in host_result.metadata
    assert "voltages" not in host_result.metadata
