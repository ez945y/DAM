"""Build from a mapping after a static, resource-free configuration check."""

import json

import dam


def main() -> None:
    configuration = {
        "hardware": {"joint_names": ["a", "b"], "degrees_mode": False},
        "guards": [],
        "tasks": {"default": {"boundaries": []}},
    }
    report = dam.preflight(configuration)
    print(json.dumps(report.to_dict()))
    report.raise_for_errors()
    # Empty boundaries demonstrate construction only, not a safety envelope.
    with dam.Guardrail(configuration, quiet=True) as rail:
        result = rail.evaluate({"joints": [0, 0], "action": [0.1, 0.2]})
        print(result.to_json())


if __name__ == "__main__":
    main()
