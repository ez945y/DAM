"""Run with an installed interpreter and -I, from outside the checkout."""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", choices=["cpu", "mps", "cuda"], default="cpu")
    args = parser.parse_args()
    with TemporaryDirectory(prefix="dam-installed-") as directory:
        os.environ["DAM_DATA_ROOT"] = directory
        import dam_rs
        import numpy as np
        import torch

        import dam
        from dam.preset import get_preset

        prefix = Path(sys.prefix).resolve()
        for module in (dam, dam_rs):
            assert Path(module.__file__).resolve().is_relative_to(prefix), module.__file__
        preset = get_preset("so101_follower")
        asset = Path(preset.asset_path())
        assert asset.is_file() and asset.is_relative_to(prefix), asset
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
        report = dam.preflight(config)
        report.raise_for_errors()
        if args.device == "mps":
            assert torch.backends.mps.is_available(), "MPS is unavailable"
        if args.device == "cuda":
            assert torch.cuda.is_available(), "CUDA is unavailable"
        dtypes = [torch.float32, torch.bfloat16]
        if args.device != "mps":
            dtypes.append(torch.float64)
        with dam.Guardrail(config, quiet=True) as rail:
            result = rail.evaluate({"joints": [0, 0], "action": np.array([2, 0.5])})
            np.testing.assert_allclose(result.command, [1, 0.5])
            assert result.decision == dam.GuardDecision.CLAMP
            for dtype in dtypes:
                action = torch.tensor(
                    [0.25, 0.5], dtype=dtype, device=args.device, requires_grad=True
                )
                output = rail({"joints": [0, 0], "action": action})
                assert output.dtype == dtype and output.device == action.device
                assert not output.requires_grad
                torch.testing.assert_close(output, action.detach())
            assert rail.reset().state == "open"

        @dam.callback("installed_smoke_reject", layer="L1")
        def reject(action):
            return False

        config["boundaries"]["range"]["nodes"] = [{"callback": "installed_smoke_reject"}]
        with dam.Guardrail(config, safe_action=[1e20, 1e20], quiet=True) as rail:
            try:
                rail(
                    {
                        "joints": [0, 0],
                        "action": torch.zeros(2, dtype=torch.float16, device=args.device),
                    }
                )
            except dam.ExecutionError:
                pass
            else:
                raise AssertionError("dtype overflow was not rejected")
        preset_config = {
            "hardware": {"preset": "so101_follower", "degrees_mode": False},
            "guards": [],
            "tasks": {"default": {"boundaries": []}},
        }
        with dam.Guardrail(preset_config, quiet=True) as rail:
            pose = rail.solvers["pinocchio_kinematics"].compute_fk(
                np.zeros(len(preset.joint_names))
            )
            assert pose.shape == (7,) and np.all(np.isfinite(pose))
        print(
            json.dumps(
                {
                    "status": "passed",
                    "python": platform.python_version(),
                    "system": platform.system(),
                    "machine": platform.machine(),
                    "device": args.device,
                    "dtypes": [str(dtype) for dtype in dtypes],
                    "versions": {
                        name: version(name)
                        for name in (
                            "robot-dam",
                            "dam-rs",
                            "numpy",
                            "torch",
                            "lerobot",
                            "pin",
                            "proxsuite",
                        )
                    },
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
