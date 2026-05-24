from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import FileResponse


def create_experiments_router() -> APIRouter:
    router = APIRouter(prefix="/api/experiments")

    @router.get("")
    def list_available_experiments() -> Any:
        from dam.experiments import list_experiments

        return {"experiments": [exp.__dict__ for exp in list_experiments()]}

    @router.get("/artifacts")
    def list_experiment_artifacts() -> Any:
        root = Path.cwd().resolve()
        artifacts_root = root / "data" / "experiments"
        preview_suffixes = {".png", ".jpg", ".jpeg"}
        artifacts: list[dict[str, str]] = []
        if artifacts_root.is_dir():
            for path in sorted(artifacts_root.rglob("*")):
                if not path.is_file() or path.suffix.lower() not in preview_suffixes:
                    continue
                rel = path.relative_to(root)
                parts = rel.parts
                experiment_id = parts[2] if len(parts) > 2 else "experiments"
                artifacts.append(
                    {
                        "experiment_id": experiment_id,
                        "path": str(rel),
                    }
                )
        return {"artifacts": artifacts}

    @router.post("/{experiment_id}/run")
    def run_native_experiment(
        experiment_id: str,
        body: Annotated[dict[str, Any] | None, Body()] = None,
    ) -> Any:
        from dam.experiments import run_experiment

        try:
            result = run_experiment(experiment_id, (body or {}).get("params") or {})
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(500, f"{type(exc).__name__}: {exc}") from exc
        return result.__dict__

    @router.get("/artifact")
    def get_experiment_artifact(path: Annotated[str, Query()]) -> FileResponse:
        root = Path.cwd().resolve()
        artifact = (root / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
        try:
            artifact.relative_to(root)
        except ValueError as exc:
            raise HTTPException(403, "artifact path must stay inside the workspace") from exc
        if not artifact.is_file():
            raise HTTPException(404, f"artifact not found: {path}")
        media_type = {
            ".csv": "text/csv",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
        }.get(artifact.suffix.lower(), "application/octet-stream")
        return FileResponse(artifact, filename=artifact.name, media_type=media_type)

    return router
