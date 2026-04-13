"""
dev_server_lerobot.py — DAM guard runtime backed by a real LeRobot arm.

Loads the Stackfile at STACKFILE_PATH, builds the robot and policy via
LeRobotBuilder, starts the guard runtime, and exposes the same
REST / WebSocket API as dev_server.py.

Usage
-----
    # With default Stackfile
    python scripts/dev_server_lerobot.py

    # With a specific Stackfile
    DAM_STACKFILE=examples/stackfiles/so101_act_pick_place.yaml \
        python scripts/dev_server_lerobot.py

    # Via docker compose (profile: lerobot)
    docker compose --profile lerobot up -d api-lerobot

Environment variables
---------------------
DAM_STACKFILE   Path to Stackfile.  Default: examples/stackfiles/so101_act_pick_place.yaml
DAM_API_HOST    Bind host.          Default: 0.0.0.0
DAM_API_PORT    Bind port.          Default: 8080
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

# ── Ensure project root on path ────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dam.runner.lerobot import LeRobotRunner
from dam.services.api import create_app
from dam.services.ood_trainer import OODTrainerService
from dam.services.runtime_control import RuntimeControlService
from dam.services.telemetry import TelemetryService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger("dev_server_lerobot")

# ── Config ────────────────────────────────────────────────────────────────
STACKFILE = os.environ.get(
    "DAM_STACKFILE",
    str(PROJECT_ROOT / "examples" / "stackfiles" / "so101_act_pick_place.yaml"),
)
API_HOST = os.environ.get("DAM_API_HOST", "0.0.0.0")
API_PORT = int(os.environ.get("DAM_API_PORT", "8080"))


def main() -> None:
    logger.info("DAM LeRobot server  stackfile=%s", STACKFILE)

    if not Path(STACKFILE).exists():
        logger.error("Stackfile not found: %s", STACKFILE)
        sys.exit(1)

    # ── Build runner from Stackfile ────────────────────────────────────────
    # Raises ImportError if lerobot is not installed, or ValueError if the
    # Stackfile is missing required hardware fields.
    runner = LeRobotRunner.from_stackfile_auto(STACKFILE)

    # ── Services ───────────────────────────────────────────────────────────
    telemetry = TelemetryService()
    ctrl = RuntimeControlService(runner._runtime)
    ood_trainer = OODTrainerService()

    # Instrument the runtime step to push telemetry
    _orig_step = runner._runtime.step

    def _instrumented_step():  # type: ignore[return]
        result = _orig_step()
        telemetry.push(result)
        return result

    runner._runtime.step = _instrumented_step  # type: ignore[method-assign]

    # ── FastAPI app ────────────────────────────────────────────────────────
    app = create_app(telemetry=telemetry, control=ctrl, ood_trainer=ood_trainer)

    # ── Start runner in background thread ─────────────────────────────────
    def _run_loop() -> None:
        try:
            runner.run(task="default")
        except Exception as e:
            logger.error("Runner loop exited with error: %s", e, exc_info=True)

    runner_thread = threading.Thread(target=_run_loop, daemon=True, name="lerobot-runner")

    logger.info(
        "Robot ready — open dashboard at http://localhost:3000  "
        "API at http://%s:%d",
        API_HOST,
        API_PORT,
    )

    # Start the runner *after* the API is up so /api/control/status responds
    # during the healthcheck polling phase.
    import uvicorn

    def _startup() -> None:
        runner_thread.start()

    app.add_event_handler("startup", _startup)

    uvicorn.run(app, host=API_HOST, port=API_PORT, log_level="info")


if __name__ == "__main__":
    main()
