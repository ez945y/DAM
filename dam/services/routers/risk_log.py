from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

if TYPE_CHECKING:
    from dam.services.risk_log import RiskLogService

from fastapi import APIRouter, HTTPException, Path, Query
from fastapi.responses import PlainTextResponse, Response


def create_risk_log_router(risk_log: RiskLogService | None) -> APIRouter:
    router = APIRouter(prefix="/api/risk-log")

    @router.get("")
    async def risk_log_query(
        since: Annotated[float | None, Query()] = None,
        until: Annotated[float | None, Query()] = None,
        min_risk_level: Annotated[str | None, Query()] = None,
        rejected_only: Annotated[bool, Query()] = False,
        clamped_only: Annotated[bool, Query()] = False,
        limit: Annotated[int, Query(ge=1, le=5000)] = 100,
    ) -> Any:
        if risk_log is None:
            raise HTTPException(503, "Risk log service not available")
        events = risk_log.query(
            since=since,
            until=until,
            min_risk_level=min_risk_level,
            rejected_only=rejected_only,
            clamped_only=clamped_only,
            limit=limit,
        )
        return {"events": [e.to_dict() for e in events], "count": len(events)}

    @router.get("/stats")
    async def risk_log_stats() -> Any:
        if risk_log is None:
            raise HTTPException(503, "Risk log service not available")
        return risk_log.stats()

    @router.get("/export/json")
    async def risk_log_export_json() -> Any:
        if risk_log is None:
            raise HTTPException(503, "Risk log service not available")
        return Response(
            content=risk_log.export_json(),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=risk_log.json"},
        )

    @router.get("/export/csv")
    async def risk_log_export_csv() -> Any:
        if risk_log is None:
            raise HTTPException(503, "Risk log service not available")
        return PlainTextResponse(
            content=risk_log.export_csv(),
            headers={"Content-Disposition": "attachment; filename=risk_log.csv"},
        )

    @router.get("/{event_id}")
    async def risk_log_get(event_id: Annotated[int, Path()]) -> Any:
        if risk_log is None:
            raise HTTPException(503, "Risk log service not available")
        ev = risk_log.get_by_id(event_id)
        if ev is None:
            raise HTTPException(404, f"Event {event_id} not found")
        return ev.to_dict()

    return router
