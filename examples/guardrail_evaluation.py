"""Offline public-API example: command, decision evidence and classified errors."""

from pathlib import Path
from tempfile import TemporaryDirectory

import dam


def main() -> None:
    with TemporaryDirectory(prefix="dam-evaluation-") as directory:
        path = Path(directory) / "safety.yaml"
        path.write_text(
            """hardware: {joint_names: [a, b], degrees_mode: false}
guards: [{L1: motion}]
boundaries:
  range:
    layer: L1
    type: single
    nodes:
      - callback: joint_position_limits
        params: {lower: [-1, -1], upper: [1, 1]}
tasks: {default: {boundaries: [range]}}
"""
        )
        with dam.Guardrail(str(path), quiet=True) as rail:
            result = rail.evaluate({"joints": [0, 0], "action": [2, 0.5]})
            assert result.decision == dam.GuardDecision.CLAMP
            assert result.output_status == "returned"
            print(result.to_json())
            try:
                rail.evaluate({})
            except dam.InputContractError as exc:
                print(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
