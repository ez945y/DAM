from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Annotated, Any

if TYPE_CHECKING:
    from dam.services.runtime_control import RuntimeControlService

from fastapi import APIRouter, HTTPException, Query

_SVC_UNAVAILABLE = "Runtime control service not available"


def _require_control(svc: RuntimeControlService | None) -> RuntimeControlService:
    if svc is None:
        raise HTTPException(503, _SVC_UNAVAILABLE)
    return svc  # type: ignore[return-value]


def _group_callbacks_by_layer(all_cbs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for cb in all_cbs:
        layer = cb.get("layer", "L2")
        if layer not in groups:
            groups[layer] = []
        groups[layer].append(cb)
    return [{"layer": k, "callbacks": groups[k]} for k in sorted(groups.keys())]


def create_control_router(control: RuntimeControlService | None) -> APIRouter:
    router = APIRouter()

    @router.get("/api/control/status", responses={503: {"description": _SVC_UNAVAILABLE}})
    async def control_status() -> Any:
        return _require_control(control).status()

    @router.get("/api/catalog/callbacks")
    async def get_callback_catalog(grouped: Annotated[bool, Query()] = False) -> Any:
        from dam.boundary.builtin_callbacks import get_catalog

        all_cbs = get_catalog()
        if not grouped:
            return {"callbacks": all_cbs}
        return {"groups": _group_callbacks_by_layer(all_cbs)}

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

    @router.post(
        "/api/control/start",
        responses={
            400: {"description": "Runtime already started or invalid state"},
            503: {"description": _SVC_UNAVAILABLE},
        },
    )
    async def control_start(
        task_name: Annotated[str, Query()] = "default",
        n_cycles: Annotated[int, Query()] = -1,
        cycle_budget_ms: Annotated[float, Query()] = 20.0,
    ) -> Any:
        svc = _require_control(control)
        try:
            started = svc.start(task_name, n_cycles, cycle_budget_ms)
        except RuntimeError as e:
            raise HTTPException(400, str(e))
        return {"started": started, "state": svc.state.value}

    @router.post(
        "/api/control/recheck-hardware", responses={503: {"description": _SVC_UNAVAILABLE}}
    )
    async def control_recheck_hardware() -> Any:
        svc = _require_control(control)
        return {"success": svc.recheck_hardware(), "state": svc.state.value}

    @router.post("/api/control/pause", responses={503: {"description": _SVC_UNAVAILABLE}})
    async def control_pause() -> Any:
        svc = _require_control(control)
        return {"paused": svc.pause(), "state": svc.state.value}

    @router.post("/api/control/resume", responses={503: {"description": _SVC_UNAVAILABLE}})
    async def control_resume() -> Any:
        svc = _require_control(control)
        return {"resumed": svc.resume(), "state": svc.state.value}

    @router.post("/api/control/stop", responses={503: {"description": _SVC_UNAVAILABLE}})
    async def control_stop() -> Any:
        svc = _require_control(control)
        return {"stopped": svc.stop(), "state": svc.state.value}

    @router.post("/api/control/estop", responses={503: {"description": _SVC_UNAVAILABLE}})
    async def control_estop() -> Any:
        svc = _require_control(control)
        return {"emergency_stop": svc.emergency_stop(), "state": svc.state.value}

    @router.post("/api/control/reset", responses={503: {"description": _SVC_UNAVAILABLE}})
    async def control_reset() -> Any:
        svc = _require_control(control)
        return {"reset": svc.reset(), "state": svc.state.value}

    @router.post("/api/control/confirm-fault", responses={503: {"description": _SVC_UNAVAILABLE}})
    async def control_confirm_fault() -> Any:
        svc = _require_control(control)
        return {"success": svc.confirm_fault(), "backend_state": svc.status()["backend_state"]}

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

        _restart_task = asyncio.ensure_future(_do_restart())  # noqa: RUF006
        return {"restarting": True}

    return router
