"""Offline safety comparisons and durable, executable regression cases."""

from __future__ import annotations

import json
import threading
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from dam.services.routers.replay import _resolve_stack
from dam.services.routers.stackfiles import _root
from dam.services.safety_comparison import (
    compare,
    config_snapshot,
    make_regression,
    run_regression,
    validate_regression,
)

if TYPE_CHECKING:
    from dam.services.mcap_sessions import McapSessionService

# Runtime callback registries and standalone motion caches are process-wide.
# Serialize comparison jobs while each candidate retains its own runtime history.
_EVALUATION_LOCK = threading.Lock()


class ComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mcap: str = Field(min_length=1, max_length=255)
    left_stack: str = Field(min_length=1, max_length=255)
    right_stack: str = Field(min_length=1, max_length=255)


class RegressionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    comparison_id: str
    name: str = Field(min_length=1, max_length=120)
    side: Literal["left", "right"]
    cycle_start: int = Field(ge=0)
    cycle_end: int = Field(ge=0)
    expected_decision: Literal["PASS", "CLAMP", "REJECT", "FAULT"] | None = None


def create_safety_comparison_router(mcap_sessions: McapSessionService | None) -> APIRouter:
    router = APIRouter(prefix="/api/safety")
    comparisons: OrderedDict[str, dict[str, Any]] = OrderedDict()
    cache_lock = threading.Lock()

    def session(request: Request, name: str) -> Path:
        svc = mcap_sessions or getattr(request.app.state, "mcap_sessions", None)
        if svc is None:
            raise HTTPException(503, "MCAP sessions service not configured")
        path = svc._resolve(name)
        if path is None or not path.is_file():
            raise HTTPException(404, "MCAP session not found")
        return Path(path)

    def cases_dir() -> Path:
        path = _root() / "data" / "safety_regressions"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def case_path(case_id: str) -> Path:
        try:
            if uuid.UUID(case_id).hex != case_id:
                raise ValueError("invalid case id")
        except ValueError as exc:
            raise HTTPException(400, "Invalid regression id") from exc
        return cases_dir() / f"{case_id}.json"

    def read_case(path: Path) -> dict[str, Any]:
        case = validate_regression(json.loads(path.read_text()))
        if case.get("id") != path.stem:
            raise ValueError("Regression identity does not match its filename")
        return case

    @router.post("/comparisons")
    def create_comparison(request: Request, body: ComparisonRequest) -> Any:
        path = session(request, body.mcap)
        if not _EVALUATION_LOCK.acquire(blocking=False):
            raise HTTPException(409, "Another offline comparison is running")
        try:
            left = config_snapshot(
                Path(_resolve_stack(body.left_stack)).read_text(), body.left_stack
            )
            right = config_snapshot(
                Path(_resolve_stack(body.right_stack)).read_text(), body.right_stack
            )
            result = compare(path, left, right)
            result["id"] = uuid.uuid4().hex
            # Retain the resolver's relative session identity, not just its basename.
            result["mcap"] = body.mcap
            with cache_lock:
                comparisons[result["id"]] = result
                while len(comparisons) > 8:
                    comparisons.popitem(last=False)
            return result
        except (ValueError, OSError) as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            _EVALUATION_LOCK.release()

    @router.get("/comparisons/{comparison_id}")
    def get_comparison(comparison_id: str) -> Any:
        with cache_lock:
            result = comparisons.get(comparison_id)
        if result is None:
            raise HTTPException(404, "Comparison expired; run it again (last 8 retained in memory)")
        return result

    @router.post("/regressions")
    def save_regression(body: RegressionRequest) -> Any:
        comparison = get_comparison(body.comparison_id)
        try:
            case = make_regression(
                comparison,
                name=body.name,
                side=body.side,
                cycle_start=body.cycle_start,
                cycle_end=body.cycle_end,
                expected_decision=body.expected_decision,
            )
            case["id"] = uuid.uuid4().hex
            path = case_path(case["id"])
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(case, indent=2, allow_nan=False))
            temp.replace(path)
            return case
        except (ValueError, OSError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/regressions")
    def list_regressions() -> Any:
        cases = []
        errors = []
        for path in sorted(cases_dir().glob("*.json")):
            try:
                cases.append(read_case(path))
            except (ValueError, OSError) as exc:
                errors.append({"id": path.stem, "error": str(exc)})
        return {"cases": cases, "errors": errors}

    @router.post("/regressions/{case_id}/run")
    def execute_regression(request: Request, case_id: str) -> Any:
        path = case_path(case_id)
        if not path.is_file():
            raise HTTPException(404, "Regression not found")
        if not _EVALUATION_LOCK.acquire(blocking=False):
            raise HTTPException(409, "Another offline comparison is running")
        try:
            case = read_case(path)
            result = run_regression(session(request, case["mcap"]), case)
            return {"id": case_id, **result}
        except (ValueError, OSError, KeyError, TypeError) as exc:
            return {"id": case_id, "status": "error", "issues": [str(exc)], "mismatches": []}
        finally:
            _EVALUATION_LOCK.release()

    return router
