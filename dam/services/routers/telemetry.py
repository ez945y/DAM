from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Annotated, Any

import msgspec

if TYPE_CHECKING:
    from dam.services.telemetry import TelemetryService

from fastapi import APIRouter, HTTPException, Query, Response, WebSocket, WebSocketDisconnect

from dam.services.serialization import MsgspecJSONResponse, msgspec_enc_hook

_SVC_UNAVAILABLE = "Telemetry service not available"

_BINARY_MAGIC_V2 = 0x02
_BINARY_MAGIC_V1 = 0x01


def _encode(event: Any) -> str:
    """Encode a dict/list/Struct as a JSON string for WebSocket send_text."""
    return msgspec.json.encode(event, enc_hook=msgspec_enc_hook).decode("utf-8")


def _parse_camera_name(payload: bytes) -> str | None:
    """Extract camera name from a binary protocol payload without copying the JPEG."""
    if len(payload) < 3:
        return None
    magic = payload[0]
    if magic == _BINARY_MAGIC_V2:
        if len(payload) < 7:
            return None
        name_len = payload[5]
        return payload[6 : 6 + name_len].decode("utf-8", errors="replace")
    if magic == _BINARY_MAGIC_V1:
        name_len = payload[1]
        return payload[2 : 2 + name_len].decode("utf-8", errors="replace")
    return None


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
            # Block until at least one item arrives
            try:
                first = await asyncio.wait_for(q.get(), timeout=30.0)
            except TimeoutError:
                if not await _send_event(websocket, {"type": "ping"}):
                    break
                continue

            # Drain all immediately available items so we can coalesce frames.
            # When the consumer falls behind (large JPEGs, slow network), this
            # skips stale frames and only sends the latest per camera.
            batch = [first]
            try:
                while True:
                    batch.append(q.get_nowait())
            except asyncio.QueueEmpty:
                pass

            # Coalesce: keep latest cycle JSON + latest binary per camera;
            # forward all non-cycle events (system_status, config_updated, …)
            latest_cycle: dict[str, Any] | None = None
            latest_binary: dict[str, bytes] = {}
            misc: list[Any] = []
            for item in batch:
                if isinstance(item, bytes):
                    cam = _parse_camera_name(item)
                    if cam is not None:
                        latest_binary[cam] = item
                    else:
                        misc.append(item)
                elif isinstance(item, dict) and item.get("type") == "cycle":
                    latest_cycle = item
                else:
                    misc.append(item)

            ok = True
            for ev in misc:
                if not await _send_event(websocket, ev):
                    ok = False
                    break
            if ok and latest_cycle is not None:
                ok = await _send_event(websocket, latest_cycle)
            if ok:
                for payload in latest_binary.values():
                    if not await _send_event(websocket, payload):
                        ok = False
                        break
            if not ok:
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
    )
    async def telemetry_history(n: Annotated[int, Query(ge=1, le=1000)] = 50) -> Response:
        if telemetry is None:
            raise HTTPException(503, _SVC_UNAVAILABLE)
        return MsgspecJSONResponse(
            {"events": telemetry.get_history(n), "total": telemetry.total_pushed}
        )

    @router.websocket("/ws/telemetry")
    async def ws_telemetry(websocket: WebSocket) -> None:
        if telemetry is None:
            await websocket.close(code=1011, reason=_SVC_UNAVAILABLE)
            return
        await _stream_telemetry(telemetry, websocket)

    return router
