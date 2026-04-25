from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

if TYPE_CHECKING:
    from dam.services.boundary_config import BoundaryConfigService

from fastapi import APIRouter, Body, HTTPException, Path


def create_boundaries_router(boundary: BoundaryConfigService | None) -> APIRouter:
    router = APIRouter(prefix="/api/boundaries")

    @router.get("")
    async def list_boundaries() -> Any:
        if boundary is None:
            raise HTTPException(503, "Boundary config service not available")
        return {"boundaries": boundary.list()}

    @router.get("/{name}")
    async def get_boundary(name: Annotated[str, Path()]) -> Any:
        if boundary is None:
            raise HTTPException(503, "Boundary config service not available")
        cfg = boundary.get(name)
        if cfg is None:
            raise HTTPException(404, f"Boundary '{name}' not found")
        return cfg

    @router.post("", status_code=201)
    async def create_boundary(body: Annotated[dict[str, Any], Body()]) -> Any:
        if boundary is None:
            raise HTTPException(503, "Boundary config service not available")
        try:
            return boundary.create(body)
        except ValueError as e:
            raise HTTPException(409, str(e))

    @router.put("/{name}")
    async def update_boundary(
        name: Annotated[str, Path()], body: Annotated[dict[str, Any], Body()]
    ) -> Any:
        if boundary is None:
            raise HTTPException(503, "Boundary config service not available")
        try:
            return boundary.update(name, body)
        except KeyError as e:
            raise HTTPException(404, str(e))

    @router.delete("/{name}", status_code=204)
    async def delete_boundary(name: Annotated[str, Path()]) -> None:
        if boundary is None:
            raise HTTPException(503, "Boundary config service not available")
        deleted = boundary.delete(name)
        if not deleted:
            raise HTTPException(404, f"Boundary '{name}' not found")
        return None

    return router
