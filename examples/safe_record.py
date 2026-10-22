"""Three public integration levels, with disposable MCAP recordings.

Run: python -m examples.safe_record
Uses synthetic radians and temporary files; no hardware is opened.
"""

from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

import dam


def main() -> None:
    with TemporaryDirectory(prefix="dam-safe-record-") as directory:
        config = {
            "hardware": {"joint_names": ["a", "b"], "degrees_mode": False},
            "boundaries": {
                "range": {
                    "layer": "L1",
                    "type": "single",
                    "nodes": [
                        {
                            "callback": "joint_position_limits",
                            "params": {"lower": [-1, -1], "upper": [1, 1]},
                        }
                    ],
                }
            },
            "tasks": {"record": {"boundaries": ["range"]}},
            "loopback": {"output_dir": str(Path(directory) / "recordings")},
        }
        inputs = {"joints": [0.25, 0.25], "action": [2, 0.5]}
        # One-shot: constructs and closes a session on this call.
        assert dam.guardrail(inputs, config, task="record") == [1, 0.5]
        # Reused session: preserves history across a bounded control loop.
        with dam.Guardrail(config, task="record", quiet=True) as rail:
            for _ in range(3):
                result = rail.evaluate(inputs)
                assert result.decision == dam.GuardDecision.CLAMP
                assert result.command == [1, 0.5]
        # LeRobot transition: first cycle holds observation, later cycles clamp.
        path = Path(directory) / "stack.yaml"
        path.write_text(yaml.safe_dump(config))
        processor = dam.GuardrailProcessorStep(str(path), task="record", quiet=True)
        transition = {
            "observation": {"a.pos": 0.25, "b.pos": 0.25},
            "action": {"a.pos": 2.0, "b.pos": 0.5},
            "reward": None,
            "done": None,
            "truncated": None,
            "info": None,
            "complementary_data": None,
        }
        try:
            assert processor(transition)["action"] == {"a.pos": 0.25, "b.pos": 0.25}
            assert processor(transition)["action"] == {"a.pos": 1.0, "b.pos": 0.5}
        finally:
            processor.close()
        assert len(list((Path(directory) / "recordings").glob("*.mcap"))) == 3
        print("One-shot, session and processor completed; all three recordings closed.")


if __name__ == "__main__":
    main()
