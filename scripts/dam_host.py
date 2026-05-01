#!/usr/bin/env python3
"""DAM Host — High-fidelity hardware runtime host.

Supports both real hardware and simulation modes using the same
asynchronous, reactive lifecycle.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import textwrap
import threading
import warnings

# Suppress benign resource_tracker warning: a single semaphore from the hardware
# stack (LeRobot/OpenCV camera pipeline) is cleaned up by resource_tracker itself
# on exit, but Python still prints the warning even though there's no actual leak.
warnings.filterwarnings(
    "ignore",
    message="resource_tracker: There appear to be",
    category=UserWarning,
)
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI

# ── Ensure project root on path ────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dam.boundary.builtin_callbacks import register_all
from dam.guard.builtin import register_all as register_guard_classes
from dam.logging.console import setup_colored_logging
from dam.runtime.factory import RuntimeFactory
from dam.services.api import create_app
from dam.services.boundary_config import BoundaryConfigService
from dam.services.mcap_sessions import McapSessionService
from dam.services.ood_trainer import OODTrainerService
from dam.services.risk_log import RiskLogService
from dam.services.runtime_control import BackendState, RuntimeControlService, RuntimeState
from dam.services.service_container import ServiceContainer
from dam.services.telemetry import TelemetryService

setup_colored_logging(level=logging.INFO)

log = logging.getLogger("dam.host")

# ── Default Simulation Configuration ──────────────────────────────────────────
_DEFAULT_SIM_STACK = textwrap.dedent("""\
    version: "1"
    hardware:
      sources:
        sim: { type: simulation }
    guards:
      - L0: ood
      - L2: motion
      - L3: execution
      - L4: hardware
    safety:
      control_frequency_hz: 10.0
      enforcement_mode: monitor
""")


def _resolve_stackfile() -> str:
    """Return path to stackfile or create a temporary simulation one."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    convention_path = os.path.join(project_root, ".dam_stackfile.yaml")

    if os.path.exists(convention_path):
        return convention_path

    # Fallback: Create a temporary simulation stackfile
    log.warning("No .dam_stackfile.yaml found. Creating temporary simulation stack.")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(_DEFAULT_SIM_STACK)
        return f.name


def main() -> None:
    register_all()
    register_guard_classes()

    stack_path_str = _resolve_stackfile()
    config = RuntimeFactory.load_config(stack_path_str)

    # 1. Prepare Shell Services
    risk_log = RiskLogService()
    boundary = BoundaryConfigService()

    boundary.load_from_stackfile(
        {name: cfg.model_dump() for name, cfg in config.boundaries.items()}
    )

    log.info("Starting DAM Host (Universal)...")

    control = RuntimeControlService()
    control.set_stack_path(stack_path_str)
    control.apply_config(config)
    ood_trainer = OODTrainerService()
    telemetry = TelemetryService(history_size=1000)

    # Wire Control status to Telemetry for real-time state broadcast
    control.set_status_callback(lambda msg: telemetry.push_raw(msg))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        # 0. Attach the async event loop to Telemetry for thread-safe broadcasting
        import asyncio

        telemetry.attach_loop(asyncio.get_running_loop())

        def _bg_init() -> None:
            try:
                log.info("Background: Initializing runtime...")
                # No longer setting control._state = STARTING here.
                # RuntimeControlService defaults to backend_state = LOADING.
                control._notify_state()

                # Build runner from pre-parsed config
                runner = RuntimeFactory.build_from_config(config)
                rt = runner.runtime

                # Instrumentation
                def step_wrapper(orig_step: Callable[[], Any]) -> Callable[[], Any]:
                    _state = {"warn_count": 0, "ok_logged": False}

                    def _instrumented() -> Any:
                        res = orig_step()
                        # Capture the latest camera frame (JPEG bytes) and include
                        # them in the WS telemetry event only when subscribers are
                        # connected — avoids JPEG encoding on every cycle otherwise.
                        live_imgs: dict[str, bytes] | None = None
                        n_subs = telemetry.subscriber_count
                        if n_subs > 0:
                            try:
                                live_imgs = rt.get_latest_images()
                            except Exception:
                                log.warning(
                                    "step_wrapper: get_latest_images() raised", exc_info=True
                                )
                            if live_imgs and not _state["ok_logged"]:
                                _state["ok_logged"] = True
                                log.info(
                                    "Live images OK — sending cameras %s to %d WS subscriber(s)",
                                    list(live_imgs.keys()),
                                    n_subs,
                                )
                            elif not live_imgs:
                                n = _state["warn_count"] = _state["warn_count"] + 1
                                if n <= 3 or n % 100 == 0:
                                    log.warning(
                                        "Live images unavailable (cycle %d, warn #%d, subs=%d) — "
                                        "check obs_bus has camera frames. "
                                        "Enable DEBUG logging on dam.runtime.guard_runtime for details.",
                                        res.cycle_id,
                                        n,
                                        n_subs,
                                    )
                        telemetry.push(res, live_images=live_imgs if live_imgs else None)
                        risk_log.record(res, perf=rt._metric_bus.snapshot())
                        return res

                    return _instrumented

                control.set_post_step_wrapper(step_wrapper)

                # Wire Telemetry with the new MetricBus
                telemetry.set_metric_bus(rt._metric_bus)
                telemetry.set_cycle_budget(1000.0 / rt._control_frequency_hz)

                # Attach to control service (This will set backend_state = READY)
                control.attach_runner(runner, stack_path_str)

                # Immediate initial hardware check
                try:
                    log.info("Background: Verifying hardware connection...")
                    runner.connect()
                    runner.verify()
                    with control._lock:
                        control._backend_state = BackendState.READY
                    control._notify_state()
                    log.info("Background: Hardware verified.")
                except Exception as e:
                    log.error("Background: Initial hardware verification failed: %s", e)
                    control.set_startup_error(str(e))

                # Wire MCAP Session Service
                if hasattr(rt, "_loopback") and rt._loopback is not None:
                    output_dir = getattr(rt._loopback, "_output_dir", None)
                    if output_dir:
                        app.state.mcap_sessions = McapSessionService(str(output_dir))

                log.info("Background: System ready.")

            except Exception as e:
                log.error("Background: Initialization failed: %s", e)
                log.exception(e)
                control.set_startup_error(str(e))

        threading.Thread(target=_bg_init, daemon=True).start()
        yield

        # Shutdown
        log.info("Shutting down...")
        with control._lock:
            if control._runner:
                with suppress(Exception):
                    control._runner.shutdown()

    # 3. Launch API
    services = ServiceContainer(
        telemetry=telemetry,
        risk_log=risk_log,
        boundary=boundary,
        control=control,
        ood_trainer=ood_trainer,
        mcap_sessions=None,  # Wired dynamically inside lifespan once MCAP dir is known
    )
    app = create_app(services)
    app.router.lifespan_context = lifespan

    log.info("=" * 60)
    log.info("DAM HOST API: http://localhost:8080")
    log.info("=" * 60)

    uvicorn.run(app, host="127.0.0.1", port=8080, log_level="warning")


if __name__ == "__main__":
    main()
