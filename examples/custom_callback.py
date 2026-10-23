"""Register a public boundary callback and reject excessive per-cycle motion.

Run: python -m examples.custom_callback
The 0.5 rad displacement limit is illustrative, not a calibrated speed limit.
"""

import numpy as np

import dam


@dam.callback("example_step_delta_limit", layer="L1")
def step_delta_limit(obs: dam.Observation, action: dam.ActionProposal, max_delta: float):
    delta = action.target_joint_positions - obs.joint_positions
    return bool(np.all(np.abs(delta) <= max_delta))


def main() -> None:
    config = {
        "hardware": {"joint_names": ["a", "b", "c"], "degrees_mode": False},
        "boundaries": {
            "step": {
                "layer": "L1",
                "type": "single",
                "nodes": [{"callback": "example_step_delta_limit", "params": {"max_delta": 0.5}}],
            }
        },
        "tasks": {"default": {"boundaries": ["step"]}},
    }
    dam.preflight(config).raise_for_errors()
    with dam.Guardrail(config, quiet=True) as rail:
        gentle = rail.evaluate({"joints": [0, 0, 0], "action": [0.1, 0.2, 0.3]})
        assert gentle.decision == dam.GuardDecision.PASS
        aggressive = rail.evaluate({"joints": [0, 0, 0], "action": [1, -2, 3]})
        assert aggressive.decision == dam.GuardDecision.REJECT
        assert aggressive.command == [0, 0, 0]
        print(gentle.to_json())
        print(aggressive.to_json())


if __name__ == "__main__":
    main()
