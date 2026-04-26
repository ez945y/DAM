from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path as _Path
from typing import TYPE_CHECKING, Annotated, Any

if TYPE_CHECKING:
    from dam.services.runtime_control import RuntimeControlService

import anyio
from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import PlainTextResponse


def _stackfile_path() -> str:
    return str(_Path(__file__).resolve().parents[3] / ".dam_stackfile.yaml")


def _find_usb_devices() -> dict[str, Any]:
    import glob as _glob
    import platform

    devices: list[dict[str, Any]] = []
    is_mac = platform.system() == "Darwin"
    if is_mac:
        serial_patterns = ["/dev/cu.usbmodem*", "/dev/cu.usbserial*", "/dev/tty.usbmodem*"]
    else:
        serial_patterns = ["/dev/ttyACM*", "/dev/ttyUSB*"]
    for pat in serial_patterns:
        for path in sorted(_glob.glob(pat)):
            devices.append({"path": path, "type": "serial", "label": path.split("/")[-1]})
    for path in sorted(_glob.glob("/dev/video*")):
        try:
            idx = int(path.replace("/dev/video", ""))
            devices.append({"path": path, "type": "video", "label": f"Camera {idx} ({path})"})
        except ValueError:
            devices.append({"path": path, "type": "video", "label": path.split("/")[-1]})
    return {"devices": devices, "count": len(devices)}


def _read_stackfile(path: str) -> PlainTextResponse:
    if not os.path.exists(path):
        raise HTTPException(404, "Stackfile not found on disk")
    try:
        with open(path) as f:
            return PlainTextResponse(f.read())
    except Exception as e:
        raise HTTPException(500, f"Failed to read stackfile: {e}")


def _write_stackfile(path: str, content: str) -> None:
    try:
        with open(path, "w") as f:
            f.write(content)
    except OSError as e:
        raise HTTPException(500, f"Failed to write stackfile: {e}")


async def _write_stackfile_async(path: str, content: str) -> None:
    try:
        await anyio.Path(path).write_text(content)
    except OSError as e:
        raise HTTPException(500, f"Failed to write stackfile: {e}")


async def _do_restart() -> None:
    import sys

    await asyncio.sleep(0.5)
    os.execv(sys.executable, [sys.executable] + sys.argv)


def create_system_router(control: RuntimeControlService | None) -> APIRouter:
    router = APIRouter(prefix="/api/system")

    @router.get("/usb-devices")
    def system_usb_devices() -> Any:
        """Scan the host for USB serial ports and video devices."""
        return _find_usb_devices()

    @router.get(
        "/config",
        responses={
            404: {"description": "Stackfile not found"},
            500: {"description": "Failed to read stackfile"},
        },
    )
    def system_get_config() -> Any:
        """Read .dam_stackfile.yaml from the project root and return as text."""
        return _read_stackfile(_stackfile_path())

    # POST /usb-scan is an alias for GET /usb-devices — no separate function needed.
    router.post("/usb-scan")(system_usb_devices)

    @router.post("/save-config")
    def system_save_config(body: Annotated[dict[str, Any], Body()]) -> Any:
        """Write YAML config to .dam_stackfile.yaml in the project root."""
        yaml_content = body.get("yaml", "")
        if not yaml_content:
            raise HTTPException(400, "yaml is required")
        _write_stackfile(_stackfile_path(), yaml_content)
        return {"success": True, "path": _stackfile_path()}

    @router.post("/restart")
    async def system_restart(body: Annotated[dict[str, Any], Body()]) -> Any:
        """Save config (if provided) then restart the process."""
        yaml_content = body.get("yaml", "")
        if yaml_content:
            await _write_stackfile_async(_stackfile_path(), yaml_content)
        if control is not None:
            with contextlib.suppress(Exception):
                control.stop()
        _restart_task = asyncio.ensure_future(_do_restart())  # noqa: RUF006
        return {"restarting": True}

    return router
