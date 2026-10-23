"""Execute every reviewed offline example using the installed SDK outside checkout."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

OFFLINE = (
    "custom_callback.py",
    "guardrail_evaluation.py",
    "guardrail_preflight.py",
    "guardrail_session.py",
    "hello_guard.py",
    "isaac_franka_demo.py",
    "jetbot_guardrail.py",
    "mobile_base_guardrail.py",
    "safe_record.py",
    "safety_comparison_demo.py",
    "sdk_offline.py",
)
# This example launches Isaac Lab and requires a separately verified simulator.
SIMULATOR = {"isaac_jetbot_lane_demo.py"}


def main() -> None:
    import dam

    assert Path(dam.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), dam.__file__
    examples = Path(__file__).resolve().parents[2] / "examples"
    actual = {path.name for path in examples.glob("*.py")}
    assert actual == set(OFFLINE) | SIMULATOR, (
        "Review new examples before classifying them as offline"
    )
    with TemporaryDirectory(prefix="dam-examples-") as directory:
        env = dict(os.environ, DAM_DATA_ROOT=directory)
        for filename in OFFLINE:
            result = subprocess.run(
                [sys.executable, "-I", str(examples / filename)],
                cwd=directory,
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            print(
                json.dumps(
                    {
                        "example": filename,
                        "returncode": result.returncode,
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                    }
                ),
                flush=True,
            )
            result.check_returncode()
    print(
        json.dumps(
            {
                "status": "passed",
                "offline_examples": len(OFFLINE),
                "not_executed": sorted(SIMULATOR),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
