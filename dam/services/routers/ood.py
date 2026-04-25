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

# Persistent task registry — survives WebSocket reconnects within a process lifetime.
ood_tasks: dict[str, Any] = {}


def create_ood_router(ood_trainer: OODTrainerService | None) -> APIRouter:
    router = APIRouter(prefix="/api/ood")

    @router.post("/train")
    async def train_ood_model(body: Annotated[dict[str, Any], Body()]) -> Any:
        if ood_trainer is None:
            raise HTTPException(503, "OOD Trainer service not available")

        repo_id = body.get("repo_id")
        if not repo_id:
            raise HTTPException(400, "repo_id is required")

        try:
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

    @router.websocket("/train/ws")
    async def train_ood_model_ws(websocket: WebSocket) -> None:
        await websocket.accept()
        if ood_trainer is None:
            await websocket.send_json(
                {"status": "error", "message": "OOD Trainer service not available"}
            )
            await websocket.close()
            return

        try:
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

                    import os
                    import threading

                    task_id = str(uuid.uuid4())
                    cancel_event = threading.Event()
                    current_task_id = task_id

                    base_dir = "data/ood_models"
                    os.makedirs(base_dir, exist_ok=True)
                    out_name = msg.get("output_name", "ood_model")
                    counter = 1
                    final_name = out_name
                    while os.path.exists(os.path.join(base_dir, f"{final_name}.pt")):
                        final_name = f"{out_name}_{counter}"
                        counter += 1
                    msg["output_name"] = final_name

                    ood_tasks[task_id] = {
                        "status": "running",
                        "last_msg": "Starting...",
                        "config": msg,
                        "cancel_event": cancel_event,
                        "ws": websocket,
                    }

                    loop = asyncio.get_event_loop()

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

                    async def run_training() -> None:
                        try:
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
                            if task_id in ood_tasks:
                                ood_tasks[task_id]["ws"] = None

                    asyncio.create_task(run_training())

                elif action == "cancel":
                    for _tid, tinfo in ood_tasks.items():
                        if tinfo["status"] == "running":
                            tinfo["cancel_event"].set()
                            tinfo["status"] = "cancelled"
                            ws = tinfo.get("ws")
                            if ws:
                                with contextlib.suppress(Exception):
                                    await ws.send_json({"status": "cancelled"})

                elif action == "subscribe":
                    for _tid, tinfo in ood_tasks.items():
                        if tinfo["status"] == "running":
                            tinfo["ws"] = websocket
                            await websocket.send_json(
                                {"status": "running", "message": tinfo["last_msg"]}
                            )
                            break

        except WebSocketDisconnect:
            for _tid, tinfo in ood_tasks.items():
                if tinfo.get("ws") == websocket:
                    tinfo["ws"] = None
        except Exception:
            pass

    @router.get("/models")
    async def list_ood_models() -> Any:
        import json
        import os
        from pathlib import Path as FilePath

        base_dir = "data/ood_models"
        if not os.path.exists(base_dir):
            return {"models": []}

        models_dict: dict[str, Any] = {}
        for f in os.listdir(base_dir):
            if f.endswith(".pt") or f.endswith(".npy"):
                base_name = f.replace(".pt", "").replace(".npy", "").replace("_flow", "")
                full_path = str(FilePath(base_dir) / f)

                if base_name not in models_dict:
                    models_dict[base_name] = {"name": base_name, "path": None, "metadata": {}}

                if (
                    f.endswith(".pt")
                    and not f.endswith("_flow.pt")
                    or not models_dict[base_name]["path"]
                ):
                    models_dict[base_name]["path"] = full_path

                meta_path = os.path.join(base_dir, f"{base_name}.json")
                if os.path.exists(meta_path) and not models_dict[base_name]["metadata"]:
                    try:
                        with open(meta_path) as mf:
                            models_dict[base_name]["metadata"] = json.load(mf)
                    except Exception:
                        pass

                if os.path.exists(os.path.join(base_dir, f"{base_name}.npy")):
                    models_dict[base_name]["metadata"]["bank_path"] = os.path.join(
                        base_dir, f"{base_name}.npy"
                    )

        models = list(models_dict.values())
        models.sort(key=lambda x: x["name"])
        return {"models": models}

    @router.delete("/models/{name}")
    async def delete_ood_model(name: str) -> Any:
        from pathlib import Path as FilePath

        base_dir = FilePath("data/ood_models").resolve()
        if not base_dir.exists():
            raise HTTPException(404, "Model files not found")

        if ".." in name or "/" in name or "\\" in name:
            raise HTTPException(400, "Invalid model name")

        deleted = False
        for suffix in [".pt", ".npy", ".json", "_flow.pt"]:
            target_path = (base_dir / f"{name}{suffix}").resolve()
            if target_path.is_relative_to(base_dir) and target_path.is_file():
                target_path.unlink()
                deleted = True

        if not deleted:
            raise HTTPException(404, "Model files not found")
        return {"status": "deleted"}

    return router
