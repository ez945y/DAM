from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Annotated, Any

if TYPE_CHECKING:
    from dam.services.runtime_control import RuntimeControlService

from fastapi import APIRouter, HTTPException, Query


def create_control_router(control: RuntimeControlService | None) -> APIRouter:
    router = APIRouter()

    @router.get("/api/control/status")
    async def control_status() -> Any:
        if control is None:
            raise HTTPException(503, "Runtime control service not available")
        return control.status()

    @router.get("/api/catalog/callbacks")
    async def get_callback_catalog(grouped: Annotated[bool, Query()] = False) -> Any:
        from dam.boundary.builtin_callbacks import get_catalog

        all_cbs = get_catalog()
        if not grouped:
            return {"callbacks": all_cbs}

        groups: dict[str, list[dict[str, Any]]] = {}
        for cb in all_cbs:
            layer = cb.get("layer", "L2")
            if layer not in groups:
                groups[layer] = []
            groups[layer].append(cb)

        sorted_keys = sorted(groups.keys())
        return {"groups": [{"layer": k, "callbacks": groups[k]} for k in sorted_keys]}

    @router.get("/api/catalog/guards")
    async def get_guard_catalog() -> Any:
        from dam.registry.guard import get_guard_registry

        reg = get_guard_registry()
        return {"guards": reg.list_all()}

    @router.get("/api/control/callbacks")
    async def control_callbacks() -> Any:
        from dam.boundary.builtin_callbacks import get_catalog

        return {"callbacks": get_catalog()}

    @router.get("/api/control/fallbacks")
    async def control_fallbacks() -> Any:
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

    @router.post("/api/control/start")
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

    @router.post("/api/control/recheck-hardware")
    async def control_recheck_hardware() -> Any:
        if control is None:
            raise HTTPException(503, "Runtime control service not available")
        success = control.recheck_hardware()
        return {"success": success, "state": control.state.value}

    @router.post("/api/control/pause")
    async def control_pause() -> Any:
        if control is None:
            raise HTTPException(503, "Runtime control service not available")
        ok = control.pause()
        return {"paused": ok, "state": control.state.value}

    @router.post("/api/control/resume")
    async def control_resume() -> Any:
        if control is None:
            raise HTTPException(503, "Runtime control service not available")
        ok = control.resume()
        return {"resumed": ok, "state": control.state.value}

    @router.post("/api/control/stop")
    async def control_stop() -> Any:
        if control is None:
            raise HTTPException(503, "Runtime control service not available")
        ok = control.stop()
        return {"stopped": ok, "state": control.state.value}

    @router.post("/api/control/estop")
    async def control_estop() -> Any:
        if control is None:
            raise HTTPException(503, "Runtime control service not available")
        ok = control.emergency_stop()
        return {"emergency_stop": ok, "state": control.state.value}

    @router.post("/api/control/reset")
    async def control_reset() -> Any:
        if control is None:
            raise HTTPException(503, "Runtime control service not available")
        ok = control.reset()
        return {"reset": ok, "state": control.state.value}

    @router.post("/api/control/confirm-fault")
    async def control_confirm_fault() -> Any:
        if control is None:
            raise HTTPException(503, "Runtime control service not available")
        ok = control.confirm_fault()
        return {"success": ok, "backend_state": control.status()["backend_state"]}

    @router.post("/api/control/restart")
    async def control_restart() -> Any:
        """Stop the runtime then replace this process via os.execv."""
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

    return router
