#!/usr/bin/env python3
"""DAM Host — High-fidelity hardware runtime host.

This script boots the DAM safety stack synchronized with real hardware
(e.g., SoArm101, cameras) as defined in the .dam_stackfile.yaml.

Usage:
    python scripts/dam_host.py
"""
from __future__ import annotations

import logging
import os

import uvicorn

from dam.boundary.builtin_callbacks import register_all
from dam.guard.builtin import register_all as register_guard_classes
from dam.runtime.factory import RuntimeFactory
from dam.services.api import create_app
from dam.services.boundary_config import BoundaryConfigService
from dam.services.ood_trainer import OODTrainerService
from dam.services.risk_log import RiskLogService
from dam.services.runtime_control import RuntimeControlService
from dam.services.telemetry import TelemetryService

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)-7s] [%(name)-30s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("dam.host")

def _resolve_stackfile() -> str:
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    convention_path = os.path.join(project_root, ".dam_stackfile.yaml")
    return convention_path

def main() -> None:
    register_all()          # Register built-in boundary callbacks
    register_guard_classes()  # Register built-in guard classes (ood, motion, execution, hardware)

    stack_path = _resolve_stackfile()
    if not os.path.exists(stack_path):
        log.error("Stackfile not found at %s. Please create one via the dashboard.", stack_path)
        return

    log.info("Starting DAM Host...")
    log.info("Loading configuration from: %s", stack_path)

    # 1. Prepare Services
    telemetry = TelemetryService(history_size=1000)
    risk_log = RiskLogService()
    boundary = BoundaryConfigService()
    control = RuntimeControlService()
    control.set_stack_path(stack_path)
    ood_trainer = OODTrainerService()

    # 2. Build Runtime via Factory
    try:
        runtime = RuntimeFactory.build_from_stackfile(stack_path)
        
        # Connect to hardware if applicable
        source = getattr(runtime, "_source", None)
        if hasattr(source, "connect"):
            log.info("Connecting to hardware sources...")
            source.connect()
            
        # Define instrumentation wrapper (shared between initial boot and re-checks)
        def step_wrapper(orig_step):
            def _instrumented_step():
                res = orig_step()
                telemetry.push(res)
                risk_log.record(res)
                return res
            return _instrumented_step

        control.set_post_step_wrapper(step_wrapper)
        runtime.step = step_wrapper(runtime.step)
        
        control.attach_runtime(runtime, stack_path)
        log.info("Runtime successfully initialized and attached.")

    except Exception as e:
        log.error("Failed to initialize runtime: %s", e)
        control.set_startup_error(str(e))

    # 3. Launch API
    app = create_app(
        telemetry=telemetry,
        risk_log=risk_log,
        boundary=boundary,
        control=control,
        ood_trainer=ood_trainer
    )

    log.info("=" * 60)
    log.info("DAM HOST READY")
    log.info("  API:      http://localhost:8080")
    log.info("  Console:  http://localhost:3000")
    log.info("=" * 60)

    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="warning")

if __name__ == "__main__":
    main()
