"""DAM API Server — FastAPI app combining REST + WebSocket + static UI.

Usage::

    from dam.services.api import create_app
    app = create_app(telemetry_svc, risk_log_svc, boundary_svc, control_svc)

    # Run with:
    # uvicorn dam.services.api:app --host 0.0.0.0 --port 8080

Endpoints
---------
    GET  /                           → serves UI (index.html)
    GET  /static/*                   → serves static assets (app.js, style.css)

    GET  /api/telemetry/history      → last N cycle events
    WS   /ws/telemetry               → real-time cycle stream

    GET  /api/risk-log               → query risk events
    GET  /api/risk-log/{id}          → single event
    GET  /api/risk-log/export/json   → JSON export
    GET  /api/risk-log/export/csv    → CSV export
    GET  /api/risk-log/stats         → summary statistics

    GET  /api/boundaries             → list boundary configs
    GET  /api/boundaries/{name}      → single boundary config
    POST /api/boundaries             → create boundary config
    PUT  /api/boundaries/{name}      → update boundary config
    DELETE /api/boundaries/{name}    → delete boundary config

    GET  /api/control/status         → runtime state
    POST /api/control/start          → start runtime
    POST /api/control/pause          → pause runtime
    POST /api/control/resume         → resume runtime
    POST /api/control/stop           → stop runtime
    POST /api/control/estop          → emergency stop
    POST /api/control/reset          → reset to IDLE
    POST /api/control/restart        → restart process (reads from disk)

    POST /api/system/save-config     → write YAML to .dam_stackfile.yaml
    POST /api/system/restart         → save YAML (if provided) + restart process
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Annotated, Any

# Global OOD tasks registry for persistent tracking across WebSockets
ood_tasks: dict[str, Any] = {}

logger = logging.getLogger(__name__)

import contextlib

from dam.services.boundary_config import BoundaryConfigService
from dam.services.ood_trainer import OODTrainerService
from dam.services.risk_log import RiskLogService
from dam.services.runtime_control import RuntimeControlService
from dam.services.telemetry import TelemetryService

try:
    from fastapi import (
        Body,
        Depends,
        HTTPException,
        Path,
        Query,
        Request,
        WebSocket,
        WebSocketDisconnect,
    )
    from fastapi.responses import HTMLResponse, PlainTextResponse, Response
    from fastapi.staticfiles import StaticFiles

    _FASTAPI = True
except ImportError:
    _FASTAPI = False

_UI_DIR = Path(__file__).parent / "ui"


def create_app(
    telemetry: TelemetryService | None = None,
    risk_log: RiskLogService | None = None,
    boundary: BoundaryConfigService | None = None,
    control: RuntimeControlService | None = None,
    ood_trainer: OODTrainerService | None = None,
    mcap_sessions: Any | None = None,  # Optional[McapSessionService]
) -> Any:
    """Create and return the FastAPI application.

    Any service argument can be None; the corresponding routes will
    return 503 Service Unavailable.
    """
    if not _FASTAPI:
        raise ImportError("FastAPI is not installed. Run: pip install 'dam[services]'")

    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware

    app = FastAPI(
        title="DAM Console API",
        description="Detachable Action Monitor — Runtime REST + WebSocket API",
        version="0.3.0",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Attach loop to telemetry on startup ──────────────────────────────────
    @app.on_event("startup")
    async def _startup() -> None:
        # Trigger dynamic registration
        from dam.boundary.builtin_callbacks import register_all as reg_callbacks
        from dam.guard.builtin import register_all as reg_guards

        reg_callbacks()
        reg_guards()

        if telemetry is not None:
            telemetry.attach_loop(asyncio.get_event_loop())

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        if control is not None:
            logger.info("API: shutting down runtime control service...")
            control.stop()
            # Also shutdown the runtime to properly close loopback writer
            if (
                hasattr(control, "_runtime")
                and control._runtime is not None
                and hasattr(control._runtime, "shutdown")
            ):
                control._runtime.shutdown()

    # ── Static UI ────────────────────────────────────────────────────────────
    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def root() -> Any:
        index = _UI_DIR / "index.html"
        if index.exists():
            return HTMLResponse(index.read_text())
        return HTMLResponse("<h1>DAM Console</h1><p>UI not found. Run from project root.</p>")

    if _UI_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_UI_DIR)), name="static")

    # ── Telemetry ────────────────────────────────────────────────────────────
    @app.get("/api/telemetry/history")
    async def telemetry_history(n: Annotated[int, Query(ge=1, le=1000)] = 50) -> Any:
        if telemetry is None:
            raise HTTPException(503, "Telemetry service not available")
        return {"events": telemetry.get_history(n), "total": telemetry.total_pushed}

    @app.websocket("/ws/telemetry")
    async def ws_telemetry(websocket: WebSocket) -> None:
        if telemetry is None:
            await websocket.close(code=1011, reason="Telemetry service not available")
            return
        await websocket.accept()
        q = telemetry.subscribe()
        # Send recent history immediately on connect
        for ev in telemetry.get_history(50):
            try:
                await websocket.send_text(json.dumps(ev))
            except Exception:
                break
        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30.0)
                except TimeoutError:
                    event = {"type": "ping"}
                try:
                    if isinstance(event, (dict, list)):
                        await websocket.send_text(json.dumps(event))
                    elif isinstance(event, bytes):
                        await websocket.send_bytes(event)
                    else:
                        await websocket.send_text(str(event))
                except Exception:
                    break
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        except Exception:
            pass
        finally:
            telemetry.unsubscribe(q)

    # ── Risk Log ─────────────────────────────────────────────────────────────
    @app.get("/api/risk-log")
    async def risk_log_query(
        since: Annotated[float | None, Query()] = None,
        until: Annotated[float | None, Query()] = None,
        min_risk_level: Annotated[str | None, Query()] = None,
        rejected_only: Annotated[bool, Query()] = False,
        clamped_only: Annotated[bool, Query()] = False,
        limit: Annotated[int, Query(ge=1, le=5000)] = 100,
    ) -> Any:
        if risk_log is None:
            raise HTTPException(503, "Risk log service not available")
        events = risk_log.query(
            since=since,
            until=until,
            min_risk_level=min_risk_level,
            rejected_only=rejected_only,
            clamped_only=clamped_only,
            limit=limit,
        )
        return {"events": [e.to_dict() for e in events], "count": len(events)}

    @app.get("/api/risk-log/stats")
    async def risk_log_stats() -> Any:
        if risk_log is None:
            raise HTTPException(503, "Risk log service not available")
        return risk_log.stats()

    @app.get("/api/risk-log/export/json")
    async def risk_log_export_json() -> Any:
        if risk_log is None:
            raise HTTPException(503, "Risk log service not available")
        return Response(
            content=risk_log.export_json(),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=risk_log.json"},
        )

    @app.get("/api/risk-log/export/csv")
    async def risk_log_export_csv() -> Any:
        if risk_log is None:
            raise HTTPException(503, "Risk log service not available")
        return PlainTextResponse(
            content=risk_log.export_csv(),
            headers={"Content-Disposition": "attachment; filename=risk_log.csv"},
        )

    @app.get("/api/risk-log/{event_id}")
    async def risk_log_get(event_id: Annotated[int, Path()]) -> Any:
        if risk_log is None:
            raise HTTPException(503, "Risk log service not available")
        ev = risk_log.get_by_id(event_id)
        if ev is None:
            raise HTTPException(404, f"Event {event_id} not found")
        return ev.to_dict()

    # ── Boundary Config ───────────────────────────────────────────────────────
    @app.get("/api/boundaries")
    async def list_boundaries() -> Any:
        if boundary is None:
            raise HTTPException(503, "Boundary config service not available")
        return {"boundaries": boundary.list()}

    @app.get("/api/boundaries/{name}")
    async def get_boundary(name: Annotated[str, Path()]) -> Any:
        if boundary is None:
            raise HTTPException(503, "Boundary config service not available")
        cfg = boundary.get(name)
        if cfg is None:
            raise HTTPException(404, f"Boundary '{name}' not found")
        return cfg

    @app.post("/api/boundaries", status_code=201)
    async def create_boundary(body: Annotated[dict[str, Any], Body()]) -> Any:
        if boundary is None:
            raise HTTPException(503, "Boundary config service not available")
        try:
            return boundary.create(body)
        except ValueError as e:
            raise HTTPException(409, str(e))

    @app.put("/api/boundaries/{name}")
    async def update_boundary(
        name: Annotated[str, Path()], body: Annotated[dict[str, Any], Body()]
    ) -> Any:
        if boundary is None:
            raise HTTPException(503, "Boundary config service not available")
        try:
            return boundary.update(name, body)
        except KeyError as e:
            raise HTTPException(404, str(e))

    @app.delete("/api/boundaries/{name}", status_code=204)
    async def delete_boundary(name: Annotated[str, Path()]) -> None:
        if boundary is None:
            raise HTTPException(503, "Boundary config service not available")
        deleted = boundary.delete(name)
        if not deleted:
            raise HTTPException(404, f"Boundary '{name}' not found")
        return None

    # ── Runtime Control ───────────────────────────────────────────────────────
    @app.get("/api/control/status")
    async def control_status() -> Any:
        if control is None:
            raise HTTPException(503, "Runtime control service not available")
        return control.status()

    @app.get("/api/catalog/callbacks")
    async def get_callback_catalog(grouped: Annotated[bool, Query()] = False) -> Any:
        from dam.boundary.builtin_callbacks import get_catalog

        all_cbs = get_catalog()
        if not grouped:
            return {"callbacks": all_cbs}

        # Group by layer
        groups: dict[str, list[dict[str, Any]]] = {}
        for cb in all_cbs:
            layer = cb.get("layer", "L2")
            if layer not in groups:
                groups[layer] = []
            groups[layer].append(cb)

        # Sort layers L0..L4
        sorted_keys = sorted(groups.keys())
        return {"groups": [{"layer": k, "callbacks": groups[k]} for k in sorted_keys]}

    @app.get("/api/catalog/guards")
    async def get_guard_catalog() -> Any:
        from dam.registry.guard import get_guard_registry

        reg = get_guard_registry()
        return {"guards": reg.list_all()}

    @app.get("/api/control/callbacks")
    async def control_callbacks() -> Any:
        from dam.boundary.builtin_callbacks import get_catalog

        return {"callbacks": get_catalog()}

    @app.get("/api/control/fallbacks")
    async def control_fallbacks() -> Any:
        # Trigger decorator registration by importing builtins
        from dam.fallback.registry import get_global_registry

        reg = get_global_registry()
        fallbacks = []
        for name in reg.list_all():
            f = reg.get(name)
            fallbacks.append(
                {
                    "name": name,
                    "description": (f.__doc__ or "").strip(),
                    "escalates_to": getattr(f.__class__, "_escalates_to", None),
                }
            )
        return {"fallbacks": fallbacks}

    @app.post("/api/control/start")
    async def control_start(
        task_name: Annotated[str, Query()] = "default",
        n_cycles: Annotated[int, Query()] = -1,
        cycle_budget_ms: Annotated[float, Query()] = 20.0,
    ) -> Any:
        if control is None:
            raise HTTPException(503, "Runtime control service not available")
        try:
            started = control.start(task_name, n_cycles, cycle_budget_ms)
        except RuntimeError as e:
            raise HTTPException(400, str(e))
        return {"started": started, "state": control.state.value}

    @app.post("/api/control/recheck-hardware")
    async def control_recheck_hardware() -> Any:
        if control is None:
            raise HTTPException(503, "Runtime control service not available")
        success = control.recheck_hardware()
        return {"success": success, "state": control.state.value}

    @app.post("/api/control/pause")
    async def control_pause() -> Any:
        if control is None:
            raise HTTPException(503, "Runtime control service not available")
        ok = control.pause()
        return {"paused": ok, "state": control.state.value}

    @app.post("/api/control/resume")
    async def control_resume() -> Any:
        if control is None:
            raise HTTPException(503, "Runtime control service not available")
        ok = control.resume()
        return {"resumed": ok, "state": control.state.value}

    @app.post("/api/control/stop")
    async def control_stop() -> Any:
        if control is None:
            raise HTTPException(503, "Runtime control service not available")
        ok = control.stop()
        return {"stopped": ok, "state": control.state.value}

    @app.post("/api/control/estop")
    async def control_estop() -> Any:
        if control is None:
            raise HTTPException(503, "Runtime control service not available")
        ok = control.emergency_stop()
        return {"emergency_stop": ok, "state": control.state.value}

    @app.post("/api/control/reset")
    async def control_reset() -> Any:
        if control is None:
            raise HTTPException(503, "Runtime control service not available")
        ok = control.reset()
        return {"reset": ok, "state": control.state.value}

    @app.post("/api/control/confirm-fault")
    async def control_confirm_fault() -> Any:
        if control is None:
            raise HTTPException(503, "Runtime control service not available")
        ok = control.confirm_fault()
        return {"success": ok, "backend_state": control.status()["backend_state"]}

    @app.post("/api/control/restart")
    async def control_restart() -> Any:
        """Stop the runtime then replace this process via os.execv.

        The new process starts fresh and re-reads .dam_stackfile.yaml from disk,
        picking up any config changes written by the console's save-config route.
        Works identically in Docker (volume-mounted code) and local dev.
        """
        import asyncio
        import os
        import sys

        if control is not None:
            with contextlib.suppress(Exception):
                control.stop()

        async def _do_restart() -> None:
            await asyncio.sleep(0.5)
            os.execv(sys.executable, [sys.executable] + sys.argv)

        asyncio.ensure_future(_do_restart())
        return {"restarting": True}

    # ── System / Hardware discovery ───────────────────────────────────────────
    @app.get("/api/system/usb-devices")
    async def system_usb_devices() -> Any:
        """Scan the host for USB serial ports and video devices.

        Returns a list of ``{path, type, label}`` entries.  ``type`` is one of
        ``serial`` (ttyACM*, ttyUSB*, cu.usbmodem*) or ``video`` (video*).
        Always returns a result — unknown/unavailable devices return an empty list.
        """
        import glob as _glob
        import platform

        devices = []
        is_mac = platform.system() == "Darwin"

        # Serial patterns
        if is_mac:
            serial_patterns = ["/dev/cu.usbmodem*", "/dev/cu.usbserial*", "/dev/tty.usbmodem*"]
        else:
            serial_patterns = ["/dev/ttyACM*", "/dev/ttyUSB*"]

        for pat in serial_patterns:
            for path in sorted(_glob.glob(pat)):
                devices.append({"path": path, "type": "serial", "label": path.split("/")[-1]})

        # Video patterns
        for path in sorted(_glob.glob("/dev/video*")):
            try:
                idx = int(path.replace("/dev/video", ""))
                devices.append({"path": path, "type": "video", "label": f"Camera {idx} ({path})"})
            except ValueError:
                devices.append({"path": path, "type": "video", "label": path.split("/")[-1]})

        return {"devices": devices, "count": len(devices)}

    @app.get("/api/system/config")
    async def system_get_config() -> Any:
        """Read .dam_stackfile.yaml from the project root and return as text."""
        import os as _os

        project_root = _os.path.dirname(
            _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        )
        stackfile_path = _os.path.join(project_root, ".dam_stackfile.yaml")
        if not _os.path.exists(stackfile_path):
            raise HTTPException(404, "Stackfile not found on disk")
        try:
            with open(stackfile_path) as f:
                content = f.read()
            return PlainTextResponse(content)
        except Exception as e:
            raise HTTPException(500, f"Failed to read stackfile: {e}")

    @app.post("/api/system/usb-scan")
    async def system_usb_scan() -> Any:
        """Alias for GET /api/system/usb-devices — triggers a fresh scan."""
        return await system_usb_devices()

    @app.post("/api/system/save-config")
    async def system_save_config(body: Annotated[dict[str, Any], Body()]) -> Any:
        """Write YAML config to .dam_stackfile.yaml in the project root.

        The next process restart (via /api/system/restart or /api/control/restart)
        will re-read this file and apply the new configuration.
        """
        import os as _os

        yaml_content = body.get("yaml", "")
        if not yaml_content:
            raise HTTPException(400, "yaml is required")
        # Project root is two levels up from dam/services/
        project_root = _os.path.dirname(
            _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        )
        stackfile_path = _os.path.join(project_root, ".dam_stackfile.yaml")
        try:
            with open(stackfile_path, "w") as f:
                f.write(yaml_content)
        except OSError as e:
            raise HTTPException(500, f"Failed to write stackfile: {e}")
        return {"success": True, "path": stackfile_path}

    @app.post("/api/system/restart")
    async def system_restart(body: Annotated[dict[str, Any], Body()]) -> Any:
        """Save config (if provided) then restart the process.

        Body fields:
          yaml     (str, optional) — YAML content to write to .dam_stackfile.yaml first
          adapter  (str, optional) — informational, not used server-side
        """
        import os as _os
        import sys as _sys

        yaml_content = body.get("yaml", "")
        if yaml_content:
            project_root = _os.path.dirname(
                _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
            )
            stackfile_path = _os.path.join(project_root, ".dam_stackfile.yaml")
            try:
                with open(stackfile_path, "w") as f:
                    f.write(yaml_content)
            except OSError as e:
                raise HTTPException(500, f"Failed to write stackfile: {e}")

        if control is not None:
            with contextlib.suppress(Exception):
                control.stop()

        async def _do_restart() -> None:
            await asyncio.sleep(0.5)
            _os.execv(_sys.executable, [_sys.executable] + _sys.argv)

        asyncio.ensure_future(_do_restart())
        return {"restarting": True}

    # ── OOD Training ────────────────────────────────────────────────────────

    @app.post("/api/ood/train")
    async def train_ood_model(body: Annotated[dict[str, Any], Body()]) -> Any:
        if ood_trainer is None:
            raise HTTPException(503, "OOD Trainer service not available")

        repo_id = body.get("repo_id")
        if not repo_id:
            raise HTTPException(400, "repo_id is required")

        try:
            # We run this in a threadpool because dataset loading/training is blocking
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: ood_trainer.train_from_hf_dataset(
                    repo_id=repo_id,
                    episodes=body.get("episodes"),
                    backend=body.get("backend", "memory_bank"),
                    output_name=body.get("output_name", "ood_model"),
                    flow_epochs=body.get("flow_epochs", 50),
                    flow_lr=body.get("flow_lr", 1e-3),
                ),
            )
            return result
        except ImportError as e:
            raise HTTPException(500, str(e))
        except Exception as e:
            logger.error(f"OOD training failed: {e}", exc_info=True)
            raise HTTPException(500, f"Training failed: {e}")

    @app.websocket("/api/ood/train/ws")
    async def train_ood_model_ws(websocket: WebSocket) -> None:
        await websocket.accept()
        if ood_trainer is None:
            await websocket.send_json(
                {"status": "error", "message": "OOD Trainer service not available"}
            )
            await websocket.close()
            return

        try:
            # Reconnection logic: track this particular websocket connection
            current_task_id = None
            for tid, tinfo in ood_tasks.items():
                if tinfo["status"] == "running":
                    current_task_id = tid
                    break

            if current_task_id:
                await websocket.send_json(
                    {
                        "status": "exists",
                        "task_id": current_task_id,
                        "message": ood_tasks[current_task_id]["last_msg"],
                        "config": ood_tasks[current_task_id]["config"],
                    }
                )
            else:
                await websocket.send_json({"status": "idle"})

            while True:
                msg = await websocket.receive_json()
                action = msg.get("action")

                if action == "start":
                    if any(t["status"] == "running" for t in ood_tasks.values()):
                        await websocket.send_json(
                            {"status": "error", "message": "A task is already running"}
                        )
                        continue

                    repo_id = msg.get("repo_id")
                    if not repo_id:
                        await websocket.send_json(
                            {"status": "error", "message": "repo_id is required"}
                        )
                        continue

                    import threading

                    task_id = str(uuid.uuid4())
                    cancel_event = threading.Event()
                    current_task_id = task_id

                    # Logic: if name exists, add suffix _1, _2...
                    import os

                    base_dir = "data/ood_models"
                    os.makedirs(base_dir, exist_ok=True)
                    out_name = msg.get("output_name", "ood_model")
                    counter = 1
                    final_name = out_name
                    while os.path.exists(os.path.join(base_dir, f"{final_name}.pt")):
                        final_name = f"{out_name}_{counter}"
                        counter += 1

                    # Update config to use the final name
                    msg["output_name"] = final_name

                    ood_tasks[task_id] = {
                        "status": "running",
                        "last_msg": "Starting...",
                        "config": msg,
                        "cancel_event": cancel_event,
                        "ws": websocket,  # Store current active WS
                    }

                    loop = asyncio.get_event_loop()

                    def progress_cb(m: str):
                        if task_id in ood_tasks:
                            ood_tasks[task_id]["last_msg"] = m
                            ws = ood_tasks[task_id].get("ws")
                            if ws:
                                with contextlib.suppress(Exception):
                                    loop.call_soon_threadsafe(
                                        lambda: asyncio.create_task(
                                            ws.send_json({"status": "running", "message": m})
                                        )
                                    )

                    async def run_training():
                        try:
                            # Note: we use task_id from closure
                            res = await loop.run_in_executor(
                                None,
                                lambda: ood_trainer.train_from_hf_dataset(
                                    repo_id=repo_id,
                                    episodes=msg.get("episodes"),
                                    backend=msg.get("backend", "memory_bank"),
                                    output_name=msg.get("output_name", "ood_model"),
                                    flow_epochs=msg.get("flow_epochs", 50),
                                    flow_lr=msg.get("flow_lr", 1e-3),
                                    progress_callback=progress_cb,
                                    cancel_event=cancel_event,
                                ),
                            )
                            if task_id not in ood_tasks:
                                return

                            ws = ood_tasks[task_id].get("ws")
                            if cancel_event.is_set() or res.get("status") == "cancelled":
                                ood_tasks[task_id]["status"] = "cancelled"
                                if ws:
                                    await ws.send_json(
                                        {"status": "cancelled", "message": "Cancelled."}
                                    )
                            else:
                                ood_tasks[task_id]["status"] = "success"
                                if ws:
                                    await ws.send_json({"status": "success", "result": res})
                        except Exception as e:
                            if task_id in ood_tasks:
                                ood_tasks[task_id]["status"] = "error"
                                ood_tasks[task_id]["last_msg"] = str(e)
                                ws = ood_tasks[task_id].get("ws")
                                if ws:
                                    with contextlib.suppress(Exception):
                                        await ws.send_json({"status": "error", "message": str(e)})
                        finally:
                            # We keep the task in list for history until next start, but clear WS
                            if task_id in ood_tasks:
                                ood_tasks[task_id]["ws"] = None

                    asyncio.create_task(run_training())

                elif action == "cancel":
                    # Cancel whichever is running
                    for _tid, tinfo in ood_tasks.items():
                        if tinfo["status"] == "running":
                            tinfo["cancel_event"].set()
                            tinfo["status"] = "cancelled"
                            ws = tinfo.get("ws")
                            if ws:
                                with contextlib.suppress(Exception):
                                    await ws.send_json({"status": "cancelled"})

                elif action == "subscribe":
                    # Link current WS to the active task
                    for _tid, tinfo in ood_tasks.items():
                        if tinfo["status"] == "running":
                            tinfo["ws"] = websocket
                            await websocket.send_json(
                                {"status": "running", "message": tinfo["last_msg"]}
                            )
                            break
        except WebSocketDisconnect:
            # Unlink WS on disconnect so we don't try to send to it
            for _tid, tinfo in ood_tasks.items():
                if tinfo.get("ws") == websocket:
                    tinfo["ws"] = None
        except Exception:
            pass

    @app.get("/api/ood/models")
    async def list_ood_models() -> Any:
        import os

        base_dir = "data/ood_models"
        if not os.path.exists(base_dir):
            return {"models": []}

        models_dict = {}
        import json

        for f in os.listdir(base_dir):
            if f.endswith(".pt") or f.endswith(".npy"):
                base_name = f.replace(".pt", "").replace(".npy", "").replace("_flow", "")
                full_path = str(Path(base_dir) / f)

                if base_name not in models_dict:
                    models_dict[base_name] = {"name": base_name, "path": None, "metadata": {}}

                # Prioritize .pt as the main 'path' (model weights)
                if (
                    f.endswith(".pt")
                    and not f.endswith("_flow.pt")
                    or not models_dict[base_name]["path"]
                ):
                    models_dict[base_name]["path"] = full_path

                # Fill metadata
                meta_path = os.path.join(base_dir, f"{base_name}.json")
                if os.path.exists(meta_path) and not models_dict[base_name]["metadata"]:
                    try:
                        with open(meta_path) as mf:
                            models_dict[base_name]["metadata"] = json.load(mf)
                    except Exception:
                        pass

                # Explicitly link bank_path in metadata for the UI
                if os.path.exists(os.path.join(base_dir, f"{base_name}.npy")):
                    models_dict[base_name]["metadata"]["bank_path"] = os.path.join(
                        base_dir, f"{base_name}.npy"
                    )

        models = list(models_dict.values())
        models.sort(key=lambda x: x["name"])
        return {"models": models}

    @app.delete("/api/ood/models/{name}")
    async def delete_ood_model(name: Annotated[str, Path()]) -> Any:
        from pathlib import Path

        # Resolve the base directory once to a canonical path
        base_dir = Path("data/ood_models").resolve()
        if not base_dir.exists():
            raise HTTPException(404, "Model files not found")

        # Sanitize input to prevent directory traversal
        if ".." in name or "/" in name or "\\" in name:
            raise HTTPException(400, "Invalid model name")

        deleted = False
        # Clean up all possible related files (.pt, .npy, .json, _flow.pt)
        for suffix in [".pt", ".npy", ".json", "_flow.pt"]:
            # Construct path and resolve it to handle any relative components
            target_path = (base_dir / f"{name}{suffix}").resolve()

            # Security check: ensure the resolved path is still inside the base directory
            if target_path.is_relative_to(base_dir) and target_path.is_file():
                target_path.unlink()
                deleted = True

        if not deleted:
            raise HTTPException(404, "Model files not found")
        return {"status": "deleted"}

    def get_mcap_service(request: Request) -> Any:
        """Dependency to get mcap_sessions service."""
        return mcap_sessions or getattr(request.app.state, "mcap_sessions", None)

    # ── MCAP Sessions ────────────────────────────────────────────────────────

    @app.get("/api/mcap/sessions")
    def mcap_list_sessions(svc: Annotated[Any, Depends(get_mcap_service)]) -> Any:
        if svc is None:
            raise HTTPException(503, "MCAP session service not configured")
        return {"sessions": svc.list_sessions()}

    @app.get("/api/mcap/sessions/{filename}")
    def mcap_session_info(
        filename: Annotated[str, Path()], svc: Annotated[Any, Depends(get_mcap_service)]
    ) -> Any:
        if svc is None:
            raise HTTPException(503, "MCAP session service not configured")
        info = svc.get_session_info(filename)
        if info is None:
            raise HTTPException(404, f"Session not found: {filename}")
        return info

    @app.delete("/api/mcap/sessions/{filename}")
    def mcap_delete_session(
        filename: Annotated[str, Path()], svc: Annotated[Any, Depends(get_mcap_service)]
    ) -> Any:
        if svc is None:
            raise HTTPException(503, "MCAP session service not configured")
        success = svc.delete_session(filename)
        if not success:
            raise HTTPException(404, f"Session not found or failed to delete: {filename}")
        return {"success": True}

    @app.get("/api/mcap/sessions/{filename}/cycles")
    def mcap_list_cycles(
        filename: Annotated[str, Path()],
        svc: Annotated[Any, Depends(get_mcap_service)],
        since_cycle_id: Annotated[int | None, Query()] = None,
    ) -> Any:
        if svc is None:
            raise HTTPException(503, "MCAP session service not configured")
        cycles = svc.list_cycles(filename, since_cycle_id=since_cycle_id)
        return {"filename": filename, "count": len(cycles), "cycles": cycles}

    @app.get("/api/mcap/sessions/{filename}/cycles/{cycle_id}")
    def mcap_cycle_detail(
        filename: Annotated[str, Path()],
        cycle_id: Annotated[int, Path()],
        svc: Annotated[Any, Depends(get_mcap_service)],
        ts_ns: Annotated[int | None, Query()] = None,
    ) -> Any:
        if svc is None:
            raise HTTPException(503, "MCAP session service not configured")
        detail = svc.get_cycle_detail(filename, cycle_id, ts_ns)
        if detail is None:
            raise HTTPException(404, f"Cycle {cycle_id} not found in {filename}")
        return detail

    @app.get("/api/mcap/find")
    def mcap_find_session(
        cycle_id: Annotated[int, Query()], svc: Annotated[Any, Depends(get_mcap_service)]
    ) -> Any:
        if svc is None:
            raise HTTPException(503, "MCAP session service not configured")
        filename = svc.find_session_by_cycle(cycle_id)
        return {"cycle_id": cycle_id, "filename": filename, "found": filename is not None}

    @app.get("/api/mcap/live")
    def mcap_live_session(svc: Annotated[Any, Depends(get_mcap_service)]) -> Any:
        """Return the most-recently-modified MCAP session — the one currently being written."""
        if svc is None:
            raise HTTPException(503, "MCAP session service not configured")
        sessions = svc.list_sessions()
        if not sessions:
            return {"filename": None, "active": False}
        latest = sessions[0]
        return {"filename": latest["filename"], "active": True, "updated_at": latest["created_at"]}

    @app.get("/api/mcap/sessions/{filename}/frames/{cam_name}")
    def mcap_list_frames(
        filename: Annotated[str, Path()],
        cam_name: Annotated[str, Path()],
        svc: Annotated[Any, Depends(get_mcap_service)],
    ) -> Any:
        if svc is None:
            raise HTTPException(503, "MCAP session service not configured")
        frames = svc.list_frames(filename, cam_name)
        return {"camera": cam_name, "count": len(frames), "frames": frames}

    @app.get("/api/mcap/sessions/{filename}/frame/{cam_name}/{frame_idx}")
    def mcap_get_frame(
        filename: Annotated[str, Path()],
        cam_name: Annotated[str, Path()],
        frame_idx: Annotated[int, Path()],
        svc: Annotated[Any, Depends(get_mcap_service)],
    ) -> Any:
        if svc is None:
            raise HTTPException(503, "MCAP session service not configured")
        jpeg = svc.get_frame_jpeg(filename, cam_name, frame_idx)
        if jpeg is None:
            raise HTTPException(404, "Frame not found")
        return Response(content=jpeg, media_type="image/jpeg")

    @app.get("/api/mcap/sessions/{filename}/frame_at/{cam_name}")
    def mcap_get_frame_at(
        filename: Annotated[str, Path()],
        cam_name: Annotated[str, Path()],
        ts_ns: Annotated[int, Query()],
        svc: Annotated[Any, Depends(get_mcap_service)],
    ) -> Any:
        if svc is None:
            raise HTTPException(503, "MCAP session service not configured")
        jpeg = svc.get_frame_jpeg_at(filename, cam_name, ts_ns)
        if jpeg is None:
            raise HTTPException(404, "Frame not found")
        return Response(content=jpeg, media_type="image/jpeg")

    @app.get("/api/mcap/sessions/{filename}/download")
    def mcap_download(
        filename: Annotated[str, Path()], svc: Annotated[Any, Depends(get_mcap_service)]
    ) -> Any:
        from fastapi.responses import FileResponse

        if svc is None:
            raise HTTPException(503, "MCAP session service not configured")
        path = svc._resolve(filename)
        if not path or not path.exists():
            raise HTTPException(404, "Session file not found")
        return FileResponse(path, filename=filename, media_type="application/octet-stream")

    return app
