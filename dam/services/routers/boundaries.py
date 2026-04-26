from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

if TYPE_CHECKING:
    from dam.services.boundary_config import BoundaryConfigService

from fastapi import APIRouter, Body, HTTPException, Path

_SVC_UNAVAILABLE = "Boundary config service not available"


def _require_boundary(svc: BoundaryConfigService | None) -> BoundaryConfigService:
    if svc is None:
        raise HTTPException(503, _SVC_UNAVAILABLE)
    return svc  # type: ignore[return-value]


def create_boundaries_router(boundary: BoundaryConfigService | None) -> APIRouter:
    router = APIRouter(prefix="/api/boundaries")

    @router.get("", responses={503: {"description": _SVC_UNAVAILABLE}})
    async def list_boundaries() -> Any:
        svc = _require_boundary(boundary)
        return {"boundaries": svc.list()}

    @router.get(
        "/{name}",
        responses={
            404: {"description": "Boundary not found"},
            503: {"description": _SVC_UNAVAILABLE},
        },
    )
    async def get_boundary(name: Annotated[str, Path()]) -> Any:
        svc = _require_boundary(boundary)
        cfg = svc.get(name)
        if cfg is None:
            raise HTTPException(404, f"Boundary '{name}' not found")
        return cfg

    @router.post(
        "",
        status_code=201,
        responses={
            409: {"description": "Invalid boundary configuration"},
            503: {"description": _SVC_UNAVAILABLE},
        },
    )
    async def create_boundary(body: Annotated[dict[str, Any], Body()]) -> Any:
        svc = _require_boundary(boundary)
        try:
            return svc.create(body)
        except ValueError as e:
            raise HTTPException(409, str(e))

    @router.put(
        "/{name}",
        responses={
            404: {"description": "Boundary not found"},
            503: {"description": _SVC_UNAVAILABLE},
        },
    )
    async def update_boundary(
        name: Annotated[str, Path()], body: Annotated[dict[str, Any], Body()]
    ) -> Any:
        svc = _require_boundary(boundary)
        try:
            return svc.update(name, body)
        except KeyError as e:
            raise HTTPException(404, str(e))

    @router.delete(
        "/{name}",
        status_code=204,
        responses={
            404: {"description": "Boundary not found"},
            503: {"description": _SVC_UNAVAILABLE},
        },
    )
    async def delete_boundary(name: Annotated[str, Path()]) -> None:
        svc = _require_boundary(boundary)
        deleted = svc.delete(name)
        if not deleted:
            raise HTTPException(404, f"Boundary '{name}' not found")
        return None

    return router
