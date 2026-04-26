from __future__ import annotations

import asyncio
import contextlib
import uuid
from typing import TYPE_CHECKING, Annotated, Any

if TYPE_CHECKING:
    from dam.services.ood_trainer import OODTrainerService

import logging

from fastapi import APIRouter, Body, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

logger = logging.getLogger(__name__)

_OOD_MODELS_DIR = "data/ood_models"
_SVC_UNAVAILABLE = "OOD Trainer service not available"
_MODEL_NOT_FOUND = "Model files not found"

# Persistent task registry — survives WebSocket reconnects within a process lifetime.
ood_tasks: dict[str, Any] = {}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _require_ood_trainer(svc: OODTrainerService | None) -> OODTrainerService:
    if svc is None:
        raise HTTPException(503, _SVC_UNAVAILABLE)
    return svc  # type: ignore[return-value]


def _find_running_task_id() -> str | None:
    for tid, tinfo in ood_tasks.items():
        if tinfo["status"] == "running":
            return tid
    return None


def _unique_output_name(out_name: str) -> str:
    import os

    counter = 1
    final_name = out_name
    while os.path.exists(os.path.join(_OOD_MODELS_DIR, f"{final_name}.pt")):
        final_name = f"{out_name}_{counter}"
        counter += 1
    return final_name


def _make_progress_cb(task_id: str, loop: Any) -> Any:
    def progress_cb(m: str) -> None:
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

    return progress_cb


def _cleanup_ws(websocket: WebSocket) -> None:
    for _tid, tinfo in ood_tasks.items():
        if tinfo.get("ws") == websocket:
            tinfo["ws"] = None


async def _notify_training_result(task_id: str, res: Any, cancel_event: Any) -> None:
    ws = ood_tasks[task_id].get("ws")
    if cancel_event.is_set() or res.get("status") == "cancelled":
        ood_tasks[task_id]["status"] = "cancelled"
        if ws:
            await ws.send_json({"status": "cancelled", "message": "Cancelled."})
    else:
        ood_tasks[task_id]["status"] = "success"
        if ws:
            await ws.send_json({"status": "success", "result": res})


async def _notify_training_error(task_id: str, exc: Exception) -> None:
    if task_id not in ood_tasks:
        return
    ood_tasks[task_id]["status"] = "error"
    ood_tasks[task_id]["last_msg"] = str(exc)
    ws = ood_tasks[task_id].get("ws")
    if ws:
        with contextlib.suppress(Exception):
            await ws.send_json({"status": "error", "message": str(exc)})


async def _execute_training(ood_trainer: Any, body: dict[str, Any]) -> Any:
    try:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: ood_trainer.train_from_hf_dataset(
                repo_id=body.get("repo_id"),
                episodes=body.get("episodes"),
                backend=body.get("backend", "memory_bank"),
                output_name=body.get("output_name", "ood_model"),
                flow_epochs=body.get("flow_epochs", 50),
                flow_lr=body.get("flow_lr", 1e-3),
            ),
        )
    except ImportError as e:
        raise HTTPException(500, str(e))
    except Exception as e:
        logger.error(f"OOD training failed: {e}", exc_info=True)
        raise HTTPException(500, f"Training failed: {e}")


async def _run_training_job(
    task_id: str, msg: dict[str, Any], ood_trainer: Any, loop: Any, cancel_event: Any
) -> None:
    try:
        res = await loop.run_in_executor(
            None,
            lambda: ood_trainer.train_from_hf_dataset(
                repo_id=msg.get("repo_id"),
                episodes=msg.get("episodes"),
                backend=msg.get("backend", "memory_bank"),
                output_name=msg.get("output_name", "ood_model"),
                flow_epochs=msg.get("flow_epochs", 50),
                flow_lr=msg.get("flow_lr", 1e-3),
                progress_callback=_make_progress_cb(task_id, loop),
                cancel_event=cancel_event,
            ),
        )
        if task_id not in ood_tasks:
            return
        await _notify_training_result(task_id, res, cancel_event)
    except Exception as e:
        await _notify_training_error(task_id, e)
    finally:
        if task_id in ood_tasks:
            ood_tasks[task_id]["ws"] = None


async def _ws_send_initial_state(websocket: WebSocket) -> None:
    current_task_id = _find_running_task_id()
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


async def _ws_handle_start(msg: dict[str, Any], websocket: WebSocket, ood_trainer: Any) -> None:
    import os
    import threading

    if any(t["status"] == "running" for t in ood_tasks.values()):
        await websocket.send_json({"status": "error", "message": "A task is already running"})
        return
    repo_id = msg.get("repo_id")
    if not repo_id:
        await websocket.send_json({"status": "error", "message": "repo_id is required"})
        return

    task_id = str(uuid.uuid4())
    cancel_event = threading.Event()
    msg["output_name"] = _unique_output_name(msg.get("output_name", "ood_model"))
    os.makedirs(_OOD_MODELS_DIR, exist_ok=True)
    ood_tasks[task_id] = {
        "status": "running",
        "last_msg": "Starting...",
        "config": msg,
        "cancel_event": cancel_event,
        "ws": websocket,
    }
    loop = asyncio.get_event_loop()
    _training_task = asyncio.create_task(  # noqa: RUF006
        _run_training_job(task_id, msg, ood_trainer, loop, cancel_event)
    )


async def _ws_handle_cancel() -> None:
    for _tid, tinfo in ood_tasks.items():
        if tinfo["status"] == "running":
            tinfo["cancel_event"].set()
            tinfo["status"] = "cancelled"
            ws = tinfo.get("ws")
            if ws:
                with contextlib.suppress(Exception):
                    await ws.send_json({"status": "cancelled"})


async def _ws_handle_subscribe(websocket: WebSocket) -> None:
    for _tid, tinfo in ood_tasks.items():
        if tinfo["status"] == "running":
            tinfo["ws"] = websocket
            await websocket.send_json({"status": "running", "message": tinfo["last_msg"]})
            break


async def _dispatch_ws_action(
    action: str | None, msg: dict[str, Any], websocket: WebSocket, ood_trainer: Any
) -> None:
    if action == "start":
        await _ws_handle_start(msg, websocket, ood_trainer)
    elif action == "cancel":
        await _ws_handle_cancel()
    elif action == "subscribe":
        await _ws_handle_subscribe(websocket)


async def _run_ws_loop(websocket: WebSocket, ood_trainer: Any) -> None:
    await _ws_send_initial_state(websocket)
    try:
        while True:
            msg = await websocket.receive_json()
            await _dispatch_ws_action(msg.get("action"), msg, websocket, ood_trainer)
    except WebSocketDisconnect:
        _cleanup_ws(websocket)
    except Exception:
        pass


def _scan_model_file(f: str, models_dict: dict[str, Any]) -> None:
    import json
    import os
    from pathlib import Path as FilePath

    base_name = f.replace(".pt", "").replace(".npy", "").replace("_flow", "")
    full_path = str(FilePath(_OOD_MODELS_DIR) / f)

    if base_name not in models_dict:
        models_dict[base_name] = {"name": base_name, "path": None, "metadata": {}}

    if (f.endswith(".pt") and not f.endswith("_flow.pt")) or not models_dict[base_name]["path"]:
        models_dict[base_name]["path"] = full_path

    meta_path = os.path.join(_OOD_MODELS_DIR, f"{base_name}.json")
    if os.path.exists(meta_path) and not models_dict[base_name]["metadata"]:
        try:
            with open(meta_path) as mf:
                models_dict[base_name]["metadata"] = json.load(mf)
        except Exception:
            pass

    npy_path = os.path.join(_OOD_MODELS_DIR, f"{base_name}.npy")
    if os.path.exists(npy_path):
        models_dict[base_name]["metadata"]["bank_path"] = npy_path


def _list_ood_models() -> Any:
    import os

    if not os.path.exists(_OOD_MODELS_DIR):
        return {"models": []}
    models_dict: dict[str, Any] = {}
    for f in os.listdir(_OOD_MODELS_DIR):
        if f.endswith(".pt") or f.endswith(".npy"):
            _scan_model_file(f, models_dict)
    models = sorted(models_dict.values(), key=lambda x: x["name"])
    return {"models": models}


def _delete_ood_model(name: str) -> Any:
    from pathlib import Path as FilePath

    base_dir = FilePath(_OOD_MODELS_DIR).resolve()
    if not base_dir.exists():
        raise HTTPException(404, _MODEL_NOT_FOUND)
    if ".." in name or "/" in name or "\\" in name:
        raise HTTPException(400, "Invalid model name")
    deleted = False
    for suffix in [".pt", ".npy", ".json", "_flow.pt"]:
        target_path = (base_dir / f"{name}{suffix}").resolve()
        if target_path.is_relative_to(base_dir) and target_path.is_file():
            target_path.unlink()
            deleted = True
    if not deleted:
        raise HTTPException(404, _MODEL_NOT_FOUND)
    return {"status": "deleted"}


# ── Router factory ────────────────────────────────────────────────────────────


def create_ood_router(ood_trainer: OODTrainerService | None) -> APIRouter:
    router = APIRouter(prefix="/api/ood")

    @router.post(
        "/train",
        responses={
            400: {"description": "repo_id is required"},
            500: {"description": "Training failed or missing dependency"},
            503: {"description": _SVC_UNAVAILABLE},
        },
    )
    async def train_ood_model(body: Annotated[dict[str, Any], Body()]) -> Any:
        svc = _require_ood_trainer(ood_trainer)
        repo_id = body.get("repo_id")
        if not repo_id:
            raise HTTPException(400, "repo_id is required")
        return await _execute_training(svc, body)

    @router.websocket("/train/ws")
    async def train_ood_model_ws(websocket: WebSocket) -> None:
        await websocket.accept()
        if ood_trainer is None:
            await websocket.send_json({"status": "error", "message": _SVC_UNAVAILABLE})
            await websocket.close()
            return
        await _run_ws_loop(websocket, ood_trainer)

    router.get("/models")(_list_ood_models)
    router.delete(
        "/models/{name}",
        responses={
            400: {"description": "Invalid model name"},
            404: {"description": _MODEL_NOT_FOUND},
        },
    )(_delete_ood_model)

    return router
