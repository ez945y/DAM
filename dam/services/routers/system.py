from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Annotated, Any

if TYPE_CHECKING:
    from dam.services.runtime_control import RuntimeControlService

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import PlainTextResponse


def create_system_router(control: RuntimeControlService | None) -> APIRouter:
    router = APIRouter(prefix="/api/system")

    @router.get("/usb-devices")
    async def system_usb_devices() -> Any:
        """Scan the host for USB serial ports and video devices."""
        import glob as _glob
        import platform

        devices = []
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

    @router.get("/config")
    async def system_get_config() -> Any:
        """Read .dam_stackfile.yaml from the project root and return as text."""
        import os as _os

        project_root = _os.path.dirname(
            _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
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

    @router.post("/usb-scan")
    async def system_usb_scan() -> Any:
        """Alias for GET /api/system/usb-devices — triggers a fresh scan."""
        return await system_usb_devices()

    @router.post("/save-config")
    async def system_save_config(body: Annotated[dict[str, Any], Body()]) -> Any:
        """Write YAML config to .dam_stackfile.yaml in the project root."""
        import os as _os

        yaml_content = body.get("yaml", "")
        if not yaml_content:
            raise HTTPException(400, "yaml is required")
        project_root = _os.path.dirname(
            _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
        )
        stackfile_path = _os.path.join(project_root, ".dam_stackfile.yaml")
        try:
            with open(stackfile_path, "w") as f:
                f.write(yaml_content)
        except OSError as e:
            raise HTTPException(500, f"Failed to write stackfile: {e}")
        return {"success": True, "path": stackfile_path}

    @router.post("/restart")
    async def system_restart(body: Annotated[dict[str, Any], Body()]) -> Any:
        """Save config (if provided) then restart the process."""
        import os as _os
        import sys as _sys

        yaml_content = body.get("yaml", "")
        if yaml_content:
            project_root = _os.path.dirname(
                _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
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

    return router
