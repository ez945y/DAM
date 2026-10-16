"""Safety switch previews and acknowledged applications."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from dam.services.safety_switching import SafetySwitchService


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    yaml: str = Field(min_length=1, max_length=1_000_000)
    task: str = Field(min_length=1, max_length=128)
    contract: dict[str, Any] | None = None


class ApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str


class RollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    contract: dict[str, Any] | None = None


def create_safety_switching_router(control: Any = None, root: Path | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/safety/switch", tags=["Safety switching"])
    service = SafetySwitchService(control, root)

    @router.post("/preview")
    def preview(body: PreviewRequest) -> Any:
        try:
            return service.preview(body.yaml, body.task, body.contract)
        except (ValueError, yaml.YAMLError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/apply")
    def apply(body: ApplyRequest) -> Any:
        try:
            return service.apply(body.token)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(500, f"Unable to save switch: {exc}") from exc

    @router.get("/status")
    def status() -> Any:
        return service.status()

    @router.post("/rollback/preview")
    def rollback(body: RollbackRequest) -> Any:
        try:
            return service.rollback_preview(body.id, body.contract)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    return router
