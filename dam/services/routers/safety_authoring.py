"""Offline safety plan library and authoring endpoints."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from dam.config.safety_plan import SafetyDraftRequest, SafetyPlan, validate_plan_name
from dam.services.safety_authoring import compile_plan, draft_plan


def _library_dir() -> Path:
    directory = Path(__file__).resolve().parents[3] / "data" / "safety-plans"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _entry_path(name: str) -> Path:
    try:
        validate_plan_name(name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    directory = _library_dir().resolve()
    path = directory / f"{name}.json"
    if path.is_symlink() or path.resolve().parent != directory:
        raise HTTPException(400, "Invalid safety plan path")
    return path


def _read_plan(name: str) -> SafetyPlan:
    path = _entry_path(name)
    if not path.is_file():
        raise HTTPException(404, "Safety plan not found")
    try:
        return SafetyPlan.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise HTTPException(422, "Stored safety plan is invalid") from exc


def create_safety_authoring_router() -> APIRouter:
    router = APIRouter(prefix="/api/safety")

    @router.get("/plans")
    def list_plans() -> dict[str, Any]:
        plans = []
        for path in sorted(_library_dir().glob("*.json")):
            if path.is_symlink():
                continue
            try:
                plan = _read_plan(path.stem)
            except HTTPException:
                continue
            plans.append(
                {
                    "name": plan.name,
                    "task": plan.task,
                    "components": len(plan.components),
                    "modified_at": path.stat().st_mtime,
                }
            )
        return {"plans": plans}

    @router.get("/plans/{name}")
    def get_plan(name: str) -> SafetyPlan:
        return _read_plan(name)

    @router.put("/plans/{name}")
    def put_plan(name: str, plan: SafetyPlan) -> dict[str, Any]:
        path = _entry_path(name)
        if name != plan.name:
            raise HTTPException(400, "Path and plan names must match")
        # Invalid/incomplete declarations may be saved for later editing. This
        # endpoint never writes a live Stackfile or mutates a runtime.
        existed = path.exists()
        fd, temporary = tempfile.mkstemp(prefix=".plan-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(plan.model_dump(mode="json"), stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return {"name": name, "created": not existed}

    @router.delete("/plans/{name}", status_code=204)
    def delete_plan(name: str) -> None:
        path = _entry_path(name)
        if not path.is_file():
            raise HTTPException(404, "Safety plan not found")
        path.unlink()

    @router.post("/compile")
    def compile_safety_plan(plan: SafetyPlan) -> dict[str, Any]:
        try:
            return compile_plan(plan)
        except (ValueError, ValidationError, yaml.YAMLError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/draft")
    def draft_safety_plan(request: SafetyDraftRequest) -> dict[str, Any]:
        return draft_plan(request)

    return router
