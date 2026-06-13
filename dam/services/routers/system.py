from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from pathlib import Path as _Path
from typing import TYPE_CHECKING, Annotated, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from dam.services.runtime_control import RuntimeControlService

import anyio
from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import PlainTextResponse

from dam.preset import (
    delete_preset,
    list_preset_entries,
    upsert_preset,
)


def _stackfile_path() -> str:
    return str(_Path(__file__).resolve().parents[3] / ".dam_stackfile.yaml")


def _find_usb_devices() -> dict[str, Any]:
    import glob as _glob
    import platform

    devices: list[dict[str, Any]] = []

    # Cross-platform serial enumeration via pyserial.  Returns ListPortInfo with
    # `.device` = "/dev/tty.usbmodem...", "/dev/ttyACM0", "COM3" etc.
    try:
        from serial.tools import list_ports
    except ImportError:
        logger.warning(
            "pyserial not available — USB scan disabled. "
            "Install with `pip install pyserial` (declared in pyproject.toml)."
        )
    else:
        is_mac = platform.system() == "Darwin"
        for info in sorted(list_ports.comports(), key=lambda p: p.device):
            path = info.device
            # macOS exposes every USB-CDC device twice (/dev/cu.* and /dev/tty.*).
            # LeRobot/Feetech requires the /dev/tty.* variant — drop the cu dupes.
            if is_mac and path.startswith("/dev/cu."):
                continue
            label = info.description if info.description and info.description != "n/a" else path
            devices.append({"path": path, "type": "serial", "label": label})

    # Video cameras: Linux exposes /dev/video*.  macOS/Windows enumerate cameras
    # through other APIs not covered here.
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

    @router.post(
        "/save-config",
        responses={
            400: {"description": "yaml is required"},
            500: {"description": "Failed to write stackfile"},
        },
    )
    def system_save_config(body: Annotated[dict[str, Any], Body()]) -> Any:
        """Write YAML config to .dam_stackfile.yaml in the project root."""
        yaml_content = body.get("yaml", "")
        if not yaml_content:
            raise HTTPException(400, "yaml is required")
        _write_stackfile(_stackfile_path(), yaml_content)
        return {"success": True, "path": _stackfile_path()}

    @router.get("/presets")
    def system_list_presets() -> Any:
        """Return all registered robot presets."""
        return {"presets": list_preset_entries()}

    @router.post(
        "/presets",
        responses={
            400: {"description": "Invalid preset payload"},
            500: {"description": "Failed to write registry"},
        },
    )
    def system_upsert_preset(body: Annotated[dict[str, Any], Body()]) -> Any:
        """Create or update a preset by name.

        Pass ``rename_from`` to atomically remove the previous key in the
        same write — the Console uses this when the user edits a preset's
        name so we don't leave half-state on partial failure.
        """
        name = str(body.get("name", "")).strip()
        joint_names = body.get("joint_names") or []
        if not name:
            raise HTTPException(400, "name is required")
        if not isinstance(joint_names, list) or not joint_names:
            raise HTTPException(400, "joint_names must be a non-empty list")
        try:
            preset = upsert_preset(
                name,
                joint_names=list(joint_names),
                asset=dict(body.get("asset") or {}) or None,
                solvers=dict(body.get("solvers") or {}),
                action_layout=list(body.get("action_layout") or []),
                rename_from=body.get("rename_from") or None,
            )
        except (ValueError, OSError) as e:
            raise HTTPException(400, str(e)) from e
        return {
            "name": preset.name,
            "joint_names": preset.joint_names,
            "asset": preset.asset,
            "solvers": preset.solvers,
            "action_layout": preset.action_layout,
        }

    @router.delete(
        "/presets/{name}",
        responses={404: {"description": "Unknown preset"}},
    )
    def system_delete_preset(name: str) -> Any:
        """Delete a preset by name."""
        if not delete_preset(name):
            raise HTTPException(404, f"Unknown preset '{name}'")
        return {"deleted": name}

    @router.post("/restart", responses={500: {"description": "Failed to write stackfile"}})
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
