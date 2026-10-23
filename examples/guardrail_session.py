"""Offline session reload/reset example using only public SDK APIs."""

import copy

import dam


def main() -> None:
    config = {
        "hardware": {"joint_names": ["a", "b"], "degrees_mode": False},
        "guards": [{"L1": "motion"}],
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
        "tasks": {"default": {"boundaries": ["range"]}},
    }
    with dam.Guardrail(config, quiet=True) as rail:
        update = copy.deepcopy(config)
        update["boundaries"]["range"]["nodes"][0]["params"]["upper"] = [0.2, 0.2]
        receipt = rail.request_reload(update)
        assert receipt.status == "pending"
        print(rail.evaluate({"joints": [0, 0], "action": [0.5, 0.5]}).to_json())
        applied = rail.reload_receipt(receipt.id)
        assert applied.status == "applied"
        print(applied.to_dict())
        print(rail.reset().to_dict())


if __name__ == "__main__":
    main()
