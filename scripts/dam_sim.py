#!/usr/bin/env python3
"""DAM dev server — full-stack simulation mode (no hardware required).

Boots the REST + WebSocket API on port 8080 with a synthetic simulation
runtime cycling at 10 Hz.  Use this to develop and test the console UI
without a physical robot arm.

What runs:
  • FastAPI on http://localhost:8080  (REST + WebSocket)
  • Swagger docs at http://localhost:8080/docs
  • Simulation runtime — idle on startup, start via the dashboard Start button
    or hit POST /api/control/start directly

Usage:
    cd /path/to/project
    python scripts/dev_server.py

In another terminal:
    cd dam-console && npm run dev
    # Open http://localhost:3000
"""

from __future__ import annotations

import logging
import os
import tempfile
import textwrap

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)s  %(levelname)s  %(message)s",
)
log = logging.getLogger("dam.devserver")

# ── Simulation stackfile ───────────────────────────────────────────────────────

_STACKFILE = textwrap.dedent("""\
    version: "1"
    guards:
      - L0: ood
      - L2: motion
      - L3: execution
      - L4: hardware
    boundaries:
      workspace:
        type: single
        nodes:
          - node_id: default
            constraint:
              max_speed: 0.8
              bounds:
                - [-0.4, 0.4]
                - [-0.4, 0.4]
                - [0.02, 0.6]
            fallback: emergency_stop
    tasks:
      default:
        boundaries: [workspace]
    safety:
      control_frequency_hz: 10.0
      enforcement_mode: monitor
""")

# ── Mock adapters ──────────────────────────────────────────────────────────────

try:
    from dam.types.action import ActionProposal
    from dam.types.observation import Observation
except ImportError as e:
    raise SystemExit(f"Cannot import DAM — is it installed?  (pip install -e .)\n{e}") from e


# ── Stackfile resolution ───────────────────────────────────────────────────────

def _resolve_stackfile() -> str:
    """Return YAML content to use: external user config takes precedence over built-in default.

    Resolution order:
      1. ``DAM_STACKFILE_PATH`` env var (explicit path)
      2. ``.dam_stackfile.yaml`` in the project root (written by the console UI)
      3. Built-in ``_STACKFILE`` constant (fallback)
    """
    # 1. Explicit env var
    env_path = os.environ.get("DAM_STACKFILE_PATH", "")
    if env_path and os.path.isfile(env_path):
        log.info("Loading stackfile from DAM_STACKFILE_PATH: %s", env_path)
        with open(env_path) as f:
            return f.read()

    # 2. Project-root convention: <project_root>/.dam_stackfile.yaml
    #    scripts/ is one level below project root
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    convention_path = os.path.join(project_root, ".dam_stackfile.yaml")
    if os.path.isfile(convention_path):
        log.info("Loading user stackfile: %s", convention_path)
        with open(convention_path) as f:
            return f.read()

    # 3. Built-in default
    log.info("Using built-in default stackfile")
    return _STACKFILE


# ── Hardware validation ────────────────────────────────────────────────────────

def _hardware_sources(stackfile_content: str) -> list[dict]:
    """Return list of hardware source dicts from the YAML (empty if none)."""
    try:
        import yaml  # type: ignore[import-untyped]
        cfg = yaml.safe_load(stackfile_content) or {}
        sources = cfg.get("hardware", {}).get("sources", {})
        return list(sources.values()) if isinstance(sources, dict) else []
    except Exception:
        return []


def _resolve_linux_port(port: str) -> str | None:
    """Map a macOS-style device path to its Linux equivalent, if needed.

    macOS names USB serial devices as ``/dev/tty.usbmodem*`` or ``/dev/cu.*``.
    - Running natively on macOS: the macOS path is correct; return as-is.
    - Inside a Linux container: the same device appears as ``/dev/ttyACM*`` or
      ``/dev/ttyUSB*`` and we scan for candidates.

    Returns ``None`` when running in a Linux container and no candidate is found.
    """
    import glob as _glob
    import sys as _sys

    if port.startswith("/dev/tty.") or port.startswith("/dev/cu."):
        # On native macOS the macOS-style path is directly accessible.
        if _sys.platform == "darwin":
            return port

        # Inside a Linux container, scan for the Linux device name.
        candidates = sorted(
            _glob.glob("/dev/ttyACM*") + _glob.glob("/dev/ttyUSB*")
        )
        if candidates:
            log.info("Mapped macOS port '%s' → Linux '%s'", port, candidates[0])
            return candidates[0]
        log.warning(
            "macOS-style port '%s' configured but no /dev/ttyACM* or "
            "/dev/ttyUSB* device found — is /dev passed through to the container?",
            port,
        )
        return None

    return port  # already a Linux/generic path


def _check_serial_port(port: str, errors: list[str]) -> None:
    """Append an error string if ``port`` cannot be opened.

    Automatically maps macOS ``/dev/tty.usbmodem*`` names to Linux
    ``/dev/ttyACM*`` / ``/dev/ttyUSB*`` equivalents when running inside a
    Linux container.
    """
    resolved = _resolve_linux_port(port)
    if resolved is None:
        errors.append(
            f"Serial port '{port}': macOS-style device name configured but no "
            "/dev/ttyACM* or /dev/ttyUSB* device found in the container "
            "(pass /dev through with 'volumes: - /dev:/dev' and 'privileged: true', "
            "or reconnect the USB cable)"
        )
        return

    try:
        import serial as _serial  # pyserial — installed with lerobot extras
        with _serial.Serial(resolved, timeout=0.5):
            pass
        log.info("Serial port OK: %s (configured as '%s')", resolved, port)
    except ImportError:
        errors.append(
            f"Serial port '{port}': pyserial is not installed "
            "(rebuild the container with EXTRAS=services,lerobot)"
        )
    except Exception as exc:
        errors.append(f"Serial port '{port}' (→ {resolved}) not accessible: {exc}")


def _check_cameras(cameras: dict, errors: list[str]) -> None:
    """Append an error string for every camera index that cannot be opened."""
    import threading

    for cam_name, cam_cfg in cameras.items():
        idx = cam_cfg.get("index_or_path", cam_cfg.get("index"))
        if idx is None:
            errors.append(f"Camera '{cam_name}': no index_or_path configured")
            continue
        log.info("Checking camera '%s' at index %s...", cam_name, idx)

        def _open_cam(index: int, result_dict: dict) -> None:
            try:
                import cv2
                cap = cv2.VideoCapture(index)
                if cap.isOpened():
                    result_dict["ok"] = True
                    cap.release()
                else:
                    result_dict["error"] = "could not open — check connection"
            except Exception as e:
                result_dict["error"] = str(e)

        res: dict = {"ok": False, "error": None}
        t = threading.Thread(target=_open_cam, args=(int(idx), res), daemon=True)
        t.start()
        # First camera might take longer due to import
        t.join(timeout=5.0)

        if t.is_alive():
            errors.append(f"Camera '{cam_name}' (index {idx}): timed out (import or open)")
            log.warning("Camera '%s' timed out — skipping remaining cameras to avoid further hangs", cam_name)
            break 
        elif res["error"]:
            errors.append(f"Camera '{cam_name}' (index {idx}): {res['error']}")
        elif res["ok"]:
            log.info("Camera OK: '%s' @ index %s", cam_name, idx)
        else:
            errors.append(f"Camera '{cam_name}' (index {idx}): unknown state")


def _validate_hardware(sources: list[dict]) -> None:
    """Raise RuntimeError listing every hardware component that cannot be opened.

    Checks for each non-simulation source:
      • Serial port reachability  (lerobot)
      • Camera index accessibility (lerobot cameras block, if declared)
      • rclpy availability         (ros2)
    """
    errors: list[str] = []
    for src in sources:
        src_type = str(src.get("type", "")).lower()
        if src_type not in ("lerobot", "ros2"):
            continue

        if src_type == "lerobot":
            port = src.get("port", "")
            if not port:
                errors.append("lerobot source has no 'port' configured")
            else:
                _check_serial_port(port, errors)

            cameras = src.get("cameras", {})
            if cameras:
                _check_cameras(cameras, errors)

        elif src_type == "ros2":
            try:
                import rclpy  # type: ignore[import-untyped]  # noqa: F401
                log.info("rclpy OK")
            except ImportError:
                errors.append("ROS2 source configured but rclpy is not installed")

    if errors:
        detail = "\n  • ".join(errors)
        raise RuntimeError(
            f"Hardware validation failed — {len(errors)} component(s) unreachable:"
            f"\n  • {detail}\n\n"
            "Connect the hardware and retry, or switch to 'simulation' adapter in Config."
        )


# ── Runtime builder ────────────────────────────────────────────────────────────

def _build_runtime() -> object:
    import yaml

    from dam.config.schema import StackfileConfig
    from dam.runtime.factory import RuntimeFactory

    stackfile_content = _resolve_stackfile()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(stackfile_content)
        path = f.name

    with open(path) as f:
        raw = yaml.safe_load(f)
    config = StackfileConfig(**raw)

    # FORCED SIMULATION for dam_sim.py
    from dam.runtime.guard_runtime import GuardRuntime
    runtime = GuardRuntime.from_stackfile(path)
    
    # We call the simulation builder directly from the factory
    source, policy, sink = RuntimeFactory._build_simulation(config)
    
    runtime.register_source(source)
    runtime.register_policy(policy)
    runtime.register_sink(sink)
    
    log.info("Built Sandbox Simulation Runtime (Forced Sim Mode)")
    return runtime


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    try:
        import uvicorn
    except ImportError:
        raise SystemExit(
            "uvicorn not installed — run: pip install 'dam[services]'"
        )

    from dam.boundary.builtin_callbacks import register_all
    from dam.guard.builtin import register_all as register_guard_classes
    from dam.services.api import create_app
    from dam.services.boundary_config import BoundaryConfigService
    from dam.services.ood_trainer import OODTrainerService
    from dam.services.risk_log import RiskLogService
    from dam.services.runtime_control import RuntimeControlService
    from dam.services.telemetry import TelemetryService
    from dam.types.risk import CycleResult

    log.info("Building simulation runtime…")
    register_all()          # Register built-in boundary callbacks
    register_guard_classes()  # Register built-in guard classes (ood, motion, execution, hardware)

    risk_log  = RiskLogService()
    boundary  = BoundaryConfigService()
    control   = RuntimeControlService()
    ood_trainer = OODTrainerService()

    # In Sim mode, we skip hardware validation and go straight to simulation
    runtime = _build_runtime()

    # Derive cycle budget from stackfile config so slack_ms is accurate.
    _cycle_budget_ms = 1000.0 / getattr(runtime, "_control_frequency_hz", 10.0)

    # Wire TelemetryService with the runtime's MetricBus so every cycle event
    # includes the `perf` breakdown (pipeline stages + guard layers + per-guard).
    telemetry = TelemetryService(
        history_size=500,
        metric_bus=getattr(runtime, "_metric_bus", None),
        cycle_budget_ms=_cycle_budget_ms,
    )

    # Patch step() to forward results to TelemetryService
    _orig_step = runtime.step

    def _instrumented_step() -> CycleResult:
        result: CycleResult = _orig_step()
        if result.active_task is None:
            result.active_task = getattr(runtime, "_active_task", None)
        if not result.active_boundaries:
            result.active_boundaries = list(getattr(runtime, "_active_container_names", []))
        telemetry.push(result)
        # Capture perf snapshot at the same cycle boundary as the cycle result
        # so per-guard latencies are aligned with this cycle's guard execution.
        _metric_bus = getattr(runtime, "_metric_bus", None)
        perf_snap = _metric_bus.snapshot() if _metric_bus is not None else None
        risk_log.record(result, perf=perf_snap)
        return result

    runtime.step = _instrumented_step
    control.attach_runtime(runtime)

    app = create_app(
        telemetry=telemetry,
        risk_log=risk_log,
        boundary=boundary,
        control=control,
        ood_trainer=ood_trainer,
    )

    log.info("=" * 60)
    log.info("DAM SANDBOX (SIMULATION) READY")
    log.info("  Runtime starts IDLE — press Start in the dashboard to cycle.")
    log.info("  API:      http://localhost:8080")
    log.info("  Console:  http://localhost:3000")
    log.info("=" * 60)

    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="warning")


if __name__ == "__main__":
    main()
