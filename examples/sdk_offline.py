"""Seven bounded, offline SDK integrations using public APIs only.

Run: python -m examples.sdk_offline [--case numpy|torch|mobile-base|arm|processor|recording|recovery]
No device, network service, user configuration or existing recording is used.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import yaml

import dam


def configuration(count: int = 2) -> dict:
    return {
        "hardware": {"joint_names": [f"j{i}" for i in range(count)], "degrees_mode": False},
        "boundaries": {
            "range": {
                "layer": "L1",
                "type": "single",
                "nodes": [
                    {
                        "callback": "joint_position_limits",
                        "params": {"lower": [-1] * count, "upper": [1] * count},
                    }
                ],
            }
        },
        "tasks": {"default": {"boundaries": ["range"]}},
    }


def numpy_example() -> None:
    config = configuration()
    dam.preflight(config).raise_for_errors()
    with dam.Guardrail(config, quiet=True) as rail:
        result = rail.evaluate({"joints": np.zeros(2), "action": np.array([2, 0.5])})
        np.testing.assert_allclose(result.command, [1, 0.5])
        assert result.decision == dam.GuardDecision.CLAMP
        assert result.output_status == "returned"  # This example has no actuator.


def torch_example() -> None:
    import torch

    with dam.Guardrail(configuration(), quiet=True) as rail:
        action = torch.tensor([2, 0.5], dtype=torch.float32, requires_grad=True)
        output = rail({"joints": np.zeros(2), "action": action})
        torch.testing.assert_close(output, torch.tensor([1, 0.5]))
        assert output.dtype == action.dtype and output.device == action.device
        assert not output.requires_grad  # A control validation boundary is not a training graph.


@dam.callback("sdk_example_base_bounds", layer="L1")
def base_bounds(base_pose, action):
    v, omega = action
    next_x = base_pose[0] + float(v) * 0.02
    return abs(v) <= 1 and abs(omega) <= 2 and abs(next_x) <= 1


def mobile_base_example() -> None:
    config = {
        "hardware": {
            "joint_names": ["left", "right"],
            "degrees_mode": False,
            "action_layout": [{"name": "base", "keys": ["v", "omega"], "units": ["m/s", "rad/s"]}],
        },
        "boundaries": {
            "base": {
                "layer": "L1",
                "type": "single",
                "nodes": [{"callback": "sdk_example_base_bounds"}],
            }
        },
        "tasks": {"default": {"boundaries": ["base"]}},
    }
    # Zero twist is an explicit choice for this velocity command contract.
    dam.preflight(config, safe_action=[0, 0]).raise_for_errors()
    with dam.Guardrail(config, safe_action=[0, 0], quiet=True) as rail:
        assert rail({"base_pose": [0, 0, 0], "action": [0.2, 0.1]}) == [0.2, 0.1]
        rejected = rail.evaluate({"base_pose": [0, 0, 0], "action": [2, 0.1]})
        assert rejected.decision == dam.GuardDecision.REJECT
        assert rejected.command == [0, 0]


def arm_example() -> None:
    config = configuration(6)
    config["hardware"] = {"preset": "so101_follower", "degrees_mode": False}
    report = dam.preflight(config)
    report.raise_for_errors()
    assert len(report.joint_names) == 6
    with dam.Guardrail(config, quiet=True) as rail:
        result = rail.evaluate({"joints": np.zeros(6), "action": np.full(6, 2)})
        np.testing.assert_allclose(result.command, np.ones(6))
        pose = rail.solvers["pinocchio_kinematics"].compute_fk(np.zeros(6))
        assert pose.shape == (7,) and np.isfinite(pose).all()
    # Demonstration limits in radians; these are not calibrated device limits.


def processor_example() -> None:
    with TemporaryDirectory(prefix="dam-processor-example-") as directory:
        path = Path(directory) / "stack.yaml"
        path.write_text(yaml.safe_dump(configuration()))
        processor = dam.GuardrailProcessorStep(str(path), quiet=True)
        transition = {
            "observation": {"j0.pos": 0.25, "j1.pos": 0.25},
            "action": {"j0.pos": 2.0, "j1.pos": 0.5},
            "reward": None,
            "done": None,
            "truncated": None,
            "info": None,
            "complementary_data": None,
        }
        try:
            held = processor(copy.deepcopy(transition))["action"]
            assert held == {"j0.pos": 0.25, "j1.pos": 0.25}
            clamped = processor(copy.deepcopy(transition))["action"]
            assert clamped == {"j0.pos": 1.0, "j1.pos": 0.5}
            processor.reset()
            assert processor(copy.deepcopy(transition))["action"] == held
        finally:
            processor.close()


def recording_example() -> None:
    from mcap.reader import make_reader

    with TemporaryDirectory(prefix="dam-recording-example-") as directory:
        config = configuration()
        config["loopback"] = {"output_dir": directory}
        with dam.Guardrail(config, quiet=True) as rail:
            for action in ([0.25, 0.5], [2, 0.5]):
                rail.evaluate({"joints": [0, 0], "action": action})
        files = list(Path(directory).glob("*.mcap"))
        assert len(files) == 1
        # Read immediately after close: the footer and worker must be finished.
        with files[0].open("rb") as handle:
            reader = make_reader(handle)
            assert reader.get_summary() is not None
            assert (
                sum(channel.topic == "/dam/cycle" for _, channel, _ in reader.iter_messages()) == 2
            )


def recovery_example() -> None:
    broken = True

    @dam.callback("sdk_example_recovery", layer="L1")
    def check():
        if broken:
            raise RuntimeError("demonstration dependency failure")
        return True

    config = configuration()
    config["boundaries"]["range"]["nodes"] = [{"callback": "sdk_example_recovery"}]
    with dam.Guardrail(config, quiet=True) as rail:
        result = rail.evaluate({"joints": [0.25, 0.25], "action": [0.5, 0.5]})
        assert result.decision == dam.GuardDecision.FAULT
        assert result.command == [0.25, 0.25]
        assert rail.diagnostics()["guards"][0]["decision"] == "FAULT"
        broken = False  # Repair the dependency before resetting the session.
        rail.reset()
        assert (
            rail.evaluate({"joints": [0.25, 0.25], "action": [0.5, 0.5]}).decision
            == dam.GuardDecision.PASS
        )
        try:
            rail.evaluate({})
        except dam.InputContractError:
            pass
        else:
            raise AssertionError("missing action was accepted")


CASES = {
    "numpy": numpy_example,
    "torch": torch_example,
    "mobile-base": mobile_base_example,
    "arm": arm_example,
    "processor": processor_example,
    "recording": recording_example,
    "recovery": recovery_example,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=CASES)
    args = parser.parse_args()
    selected = [args.case] if args.case else list(CASES)
    for name in selected:
        CASES[name]()
        print(json.dumps({"example": name, "status": "passed"}), flush=True)


if __name__ == "__main__":
    main()
