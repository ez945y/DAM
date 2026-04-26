from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

if TYPE_CHECKING:
    from dam.services.risk_log import RiskLogService

from fastapi import APIRouter, HTTPException, Path, Query
from fastapi.responses import PlainTextResponse, Response

_SVC_UNAVAILABLE = "Risk log service not available"


def _require_risk_log(svc: RiskLogService | None) -> RiskLogService:
    if svc is None:
        raise HTTPException(503, _SVC_UNAVAILABLE)
    return svc  # type: ignore[return-value]


def create_risk_log_router(risk_log: RiskLogService | None) -> APIRouter:
    router = APIRouter(prefix="/api/risk-log")

    @router.get("", responses={503: {"description": _SVC_UNAVAILABLE}})
    async def risk_log_query(
        since: Annotated[float | None, Query()] = None,
        until: Annotated[float | None, Query()] = None,
        min_risk_level: Annotated[str | None, Query()] = None,
        rejected_only: Annotated[bool, Query()] = False,
        clamped_only: Annotated[bool, Query()] = False,
        limit: Annotated[int, Query(ge=1, le=5000)] = 100,
    ) -> Any:
        svc = _require_risk_log(risk_log)
        events = svc.query(
            since=since,
            until=until,
            min_risk_level=min_risk_level,
            rejected_only=rejected_only,
            clamped_only=clamped_only,
            limit=limit,
        )
        return {"events": [e.to_dict() for e in events], "count": len(events)}

    @router.get("/stats", responses={503: {"description": _SVC_UNAVAILABLE}})
    async def risk_log_stats() -> Any:
        svc = _require_risk_log(risk_log)
        return svc.stats()

    @router.get("/export/json", responses={503: {"description": _SVC_UNAVAILABLE}})
    async def risk_log_export_json() -> Any:
        svc = _require_risk_log(risk_log)
        return Response(
            content=svc.export_json(),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=risk_log.json"},
        )

    @router.get("/export/csv", responses={503: {"description": _SVC_UNAVAILABLE}})
    async def risk_log_export_csv() -> Any:
        svc = _require_risk_log(risk_log)
        return PlainTextResponse(
            content=svc.export_csv(),
            headers={"Content-Disposition": "attachment; filename=risk_log.csv"},
        )

    @router.get(
        "/{event_id}",
        responses={404: {"description": "Event not found"}, 503: {"description": _SVC_UNAVAILABLE}},
    )
    async def risk_log_get(event_id: Annotated[int, Path()]) -> Any:
        svc = _require_risk_log(risk_log)
        ev = svc.get_by_id(event_id)
        if ev is None:
            raise HTTPException(404, f"Event {event_id} not found")
        return ev.to_dict()

    return router
