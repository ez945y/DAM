from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Annotated, Any

import msgspec

if TYPE_CHECKING:
    from dam.services.telemetry import TelemetryService

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect

from dam.services.serialization import MsgspecJSONResponse, msgspec_enc_hook

_SVC_UNAVAILABLE = "Telemetry service not available"


def _encode(event: Any) -> str:
    """Encode a dict/list/Struct as a JSON string for WebSocket send_text."""
    return msgspec.json.encode(event, enc_hook=msgspec_enc_hook).decode("utf-8")


async def _send_event(websocket: WebSocket, event: Any) -> bool:
    """Serialise and send one event. Returns False if the connection broke."""
    try:
        if isinstance(event, dict | list):
            await websocket.send_text(_encode(event))
        elif isinstance(event, bytes):
            await websocket.send_bytes(event)
        else:
            await websocket.send_text(str(event))
        return True
    except Exception:  # noqa: BLE001 — any send error means disconnected; caller checks return value
        return False


async def _stream_telemetry(telemetry: TelemetryService, websocket: WebSocket) -> None:
    await websocket.accept()
    q = telemetry.subscribe()
    for ev in telemetry.get_history(50):
        if isinstance(ev, dict) and ev.get("type") == "cycle":
            continue
        try:
            await websocket.send_text(_encode(ev))
        except Exception:  # noqa: BLE001 — client disconnected during history replay; stop replaying
            break
    try:
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=30.0)
                if not await _send_event(websocket, event):
                    break
            except TimeoutError:
                if not await _send_event(websocket, {"type": "ping"}):
                    break
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception:  # noqa: BLE001 — any unexpected error closes the stream gracefully
        pass
    finally:
        telemetry.unsubscribe(q)


def create_telemetry_router(telemetry: TelemetryService | None) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/api/telemetry/history",
        responses={503: {"description": _SVC_UNAVAILABLE}},
        response_class=MsgspecJSONResponse,
    )
    async def telemetry_history(n: Annotated[int, Query(ge=1, le=1000)] = 50) -> Any:
        if telemetry is None:
            raise HTTPException(503, _SVC_UNAVAILABLE)
        return {"events": telemetry.get_history(n), "total": telemetry.total_pushed}

    @router.websocket("/ws/telemetry")
    async def ws_telemetry(websocket: WebSocket) -> None:
        if telemetry is None:
            await websocket.close(code=1011, reason=_SVC_UNAVAILABLE)
            return
        await _stream_telemetry(telemetry, websocket)

    return router
