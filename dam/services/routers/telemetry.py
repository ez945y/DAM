from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Annotated, Any

if TYPE_CHECKING:
    from dam.services.telemetry import TelemetryService

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect


def create_telemetry_router(telemetry: TelemetryService | None) -> APIRouter:
    router = APIRouter()

    @router.get("/api/telemetry/history")
    async def telemetry_history(n: Annotated[int, Query(ge=1, le=1000)] = 50) -> Any:
        from fastapi import HTTPException

        if telemetry is None:
            raise HTTPException(503, "Telemetry service not available")
        return {"events": telemetry.get_history(n), "total": telemetry.total_pushed}

    @router.websocket("/ws/telemetry")
    async def ws_telemetry(websocket: WebSocket) -> None:
        if telemetry is None:
            await websocket.close(code=1011, reason="Telemetry service not available")
            return
        await websocket.accept()
        q = telemetry.subscribe()
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

    return router
