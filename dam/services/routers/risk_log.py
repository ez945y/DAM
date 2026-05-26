from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

if TYPE_CHECKING:
    from dam.services.mcap_sessions import McapSessionService
    from dam.services.risk_log import RiskLogService

from fastapi import APIRouter, HTTPException, Path, Query
from fastapi.responses import PlainTextResponse, Response

from dam.services.serialization import MsgspecJSONResponse

_SVC_UNAVAILABLE = "Risk log service not available"
_MCAP_UNAVAILABLE = "MCAP session service not available"
_RISK_RANK = {"NORMAL": 0, "ELEVATED": 1, "CRITICAL": 2, "EMERGENCY": 3}


def _require_risk_log(svc: RiskLogService | None) -> RiskLogService:
    if svc is None:
        raise HTTPException(503, _SVC_UNAVAILABLE)
    return svc  # type: ignore[return-value]


def create_risk_log_router(
    risk_log: RiskLogService | None, mcap_sessions: McapSessionService | None = None
) -> APIRouter:
    router = APIRouter(prefix="/api/risk-log")

    # NOTE: We return MsgspecJSONResponse(...) *instances* (not plain dicts
    # with response_class=). FastAPI runs serialize_response → jsonable_encoder
    # → Pydantic on any return value that isn't already a Response, and
    # Pydantic chokes on msgspec.Struct fields. Returning a Response instance
    # bypasses serialize_response entirely.

    @router.get("", responses={503: {"description": _SVC_UNAVAILABLE}})
    async def risk_log_query(
        since: Annotated[float | None, Query()] = None,
        until: Annotated[float | None, Query()] = None,
        min_risk_level: Annotated[str | None, Query()] = None,
        rejected_only: Annotated[bool, Query()] = False,
        clamped_only: Annotated[bool, Query()] = False,
        outcome: Annotated[str | None, Query(pattern="^(reject|clamp|pass)$")] = None,
        failure_type: Annotated[str | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=5000)] = 100,
    ) -> Response:
        svc = _require_risk_log(risk_log)
        events = svc.query(
            since=since,
            until=until,
            min_risk_level=min_risk_level,
            rejected_only=rejected_only,
            clamped_only=clamped_only,
            outcome=outcome,
            failure_type=failure_type,
            limit=limit,
        )
        return MsgspecJSONResponse({"events": events, "count": len(events)})

    @router.get("/stats", responses={503: {"description": _SVC_UNAVAILABLE}})
    async def risk_log_stats() -> Response:
        svc = _require_risk_log(risk_log)
        return MsgspecJSONResponse(svc.stats())

    @router.post("/clear", responses={503: {"description": _SVC_UNAVAILABLE}})
    async def risk_log_clear() -> Any:
        svc = _require_risk_log(risk_log)
        svc.clear()
        return {"cleared": True}

    @router.get("/mcap/{filename}", responses={503: {"description": _MCAP_UNAVAILABLE}})
    async def risk_log_from_mcap(
        filename: Annotated[str, Path()],
        min_risk_level: Annotated[str | None, Query()] = None,
        outcome: Annotated[str | None, Query(pattern="^(reject|clamp|pass)$")] = None,
        failure_type: Annotated[str | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=5000)] = 100,
    ) -> Response:
        if mcap_sessions is None:
            raise HTTPException(503, _MCAP_UNAVAILABLE)
        events: list[dict[str, Any]] = []
        for cycle in reversed(mcap_sessions.list_cycles(filename)):
            if not cycle.get("has_violation") and not cycle.get("has_clamp"):
                continue
            detail = mcap_sessions.get_cycle_detail(
                filename, int(cycle["cycle_id"]), cycle.get("timestamp_ns")
            )
            if detail is None:
                continue
            resolved_outcome = (
                "reject"
                if detail.get("has_violation")
                else "clamp"
                if detail.get("has_clamp")
                else "pass"
            )
            risk_level = "CRITICAL" if resolved_outcome == "reject" else "ELEVATED"
            if outcome and resolved_outcome != outcome:
                continue
            if failure_type and detail.get("failure_type") != failure_type:
                continue
            if min_risk_level and _RISK_RANK.get(risk_level, 0) < _RISK_RANK.get(min_risk_level, 0):
                continue

            raw_guard_results = detail.get("guard_results", [])
            guard_results = []
            per_guard_latency: dict[str, float] = {}
            hardware: dict[str, Any] = {}
            for result in raw_guard_results:
                meta = result.get("metadata") or {}
                lat = result.get("latency_ms")
                gname = result.get("guard_name", "")
                if lat is not None:
                    per_guard_latency[gname] = lat
                for hw_key in ("temperatures", "currents", "voltages"):
                    if hw_key in meta:
                        hardware[hw_key] = meta[hw_key]
                if "host_health" in meta:
                    hardware["host_health"] = meta["host_health"]
                guard_results.append(
                    {
                        "name": gname,
                        "layer": result.get("layer_name", ""),
                        "decision": result.get("decision_name", "PASS"),
                        "reason": result.get("reason", ""),
                        "latency_ms": lat,
                        "metadata": meta,
                    }
                )

            latency_raw = detail.get("latency", {})
            stages = {
                k: latency_raw.get(k, 0.0)
                for k in ("source", "policy", "guards", "sink", "total")
                if k in latency_raw
            }
            layers = {
                k: latency_raw.get(k, 0.0) for k in ("L0", "L1", "L2", "L3") if k in latency_raw
            }
            perf = (
                {
                    "stages": stages,
                    "layers": layers,
                    "guards": per_guard_latency,
                }
                if stages
                else None
            )

            obs_data = detail.get("observation") or {}
            for hw_key in ("temperatures", "currents", "voltages"):
                if hw_key not in hardware and hw_key in obs_data:
                    hardware[hw_key] = obs_data[hw_key]

            events.append(
                {
                    "event_id": -(int(cycle["cycle_id"]) + 1),
                    "timestamp": detail.get("timestamp", cycle.get("timestamp", 0.0)),
                    "cycle_id": cycle["cycle_id"],
                    "trace_id": f"mcap:{filename}:{cycle['cycle_id']}",
                    "risk_level": risk_level,
                    "outcome": resolved_outcome,
                    "was_clamped": bool(detail.get("has_clamp")),
                    "was_rejected": bool(detail.get("has_violation")),
                    "fallback_triggered": (detail.get("action") or {}).get("fallback_triggered"),
                    "guard_results": guard_results,
                    "latency_ms": latency_raw,
                    "perf": perf,
                    "mcap_filename": filename,
                    "failure_type": detail.get("failure_type"),
                    "failure_guard_names": detail.get("failure_guard_names", []),
                    "failure_layers": detail.get("failure_layers", []),
                    "failure_decisions": detail.get("failure_decisions", []),
                    "failure_reasons": detail.get("failure_reasons", []),
                    "failure_tuple": detail.get("failure_tuple"),
                    "observation": detail.get("observation"),
                    "action": detail.get("action"),
                    "hardware": hardware or None,
                }
            )
            if len(events) >= limit:
                break
        return MsgspecJSONResponse({"events": events, "count": len(events), "filename": filename})

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
    async def risk_log_get(event_id: Annotated[int, Path()]) -> Response:
        svc = _require_risk_log(risk_log)
        ev = svc.get_by_id(event_id)
        if ev is None:
            raise HTTPException(404, f"Event {event_id} not found")
        return MsgspecJSONResponse(ev)

    return router
