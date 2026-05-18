"""Stackfile library — a managed collection of named Stackfile configs.

Stored under ``<root>/data/stackfiles/<name>.yaml``, kept deliberately separate
from the live ``<root>/.dam_stackfile.yaml`` that the runtime actually executes.
The console uses this to keep experiment configs around and to replay MCAP
sessions against them without disturbing the running config.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import PlainTextResponse

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _root() -> Path:
    return Path(__file__).resolve().parents[3]


def _library_dir() -> Path:
    d = _root() / "data" / "stackfiles"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _live_path() -> Path:
    return _root() / ".dam_stackfile.yaml"


def _safe_name(name: str) -> str:
    """Validate a library entry name (no extension, no path traversal)."""
    if not name or ".." in name or "/" in name or "\\" in name or not _NAME_RE.match(name):
        raise HTTPException(400, f"invalid stackfile name: {name!r}")
    return name


def _entry_path(name: str) -> Path:
    return _library_dir() / f"{_safe_name(name)}.yaml"


def create_stackfiles_router() -> APIRouter:
    router = APIRouter(prefix="/api/stackfiles")

    @router.get("")
    def list_stackfiles() -> Any:
        entries = []
        for p in sorted(_library_dir().glob("*.yaml")):
            st = p.stat()
            entries.append(
                {
                    "name": p.stem,
                    "size_bytes": st.st_size,
                    "modified_at": st.st_mtime,
                }
            )
        return {"entries": entries, "live": {"exists": _live_path().exists()}}

    @router.get("/{name}", responses={404: {"description": "Not found"}})
    def get_stackfile(name: str) -> PlainTextResponse:
        p = _entry_path(name)
        if not p.is_file():
            raise HTTPException(404, f"stackfile not found: {name}")
        return PlainTextResponse(p.read_text())

    @router.put("/{name}", responses={400: {"description": "yaml required"}})
    def put_stackfile(name: str, body: Annotated[dict[str, Any], Body()]) -> Any:
        yaml_content = body.get("yaml", "")
        if not yaml_content:
            raise HTTPException(400, "yaml is required")
        p = _entry_path(name)
        existed = p.is_file()
        p.write_text(yaml_content)
        return {"name": _safe_name(name), "created": not existed}

    @router.post("/{name}/rename", responses={404: {"description": "Not found"}})
    def rename_stackfile(name: str, body: Annotated[dict[str, Any], Body()]) -> Any:
        src = _entry_path(name)
        if not src.is_file():
            raise HTTPException(404, f"stackfile not found: {name}")
        new_name = _safe_name(str(body.get("new_name", "")))
        dst = _entry_path(new_name)
        if dst.exists():
            raise HTTPException(409, f"stackfile already exists: {new_name}")
        src.rename(dst)
        return {"name": new_name}

    @router.delete("/{name}", status_code=204, responses={404: {"description": "Not found"}})
    def delete_stackfile(name: str) -> None:
        p = _entry_path(name)
        if not p.is_file():
            raise HTTPException(404, f"stackfile not found: {name}")
        p.unlink()

    @router.post("/{name}/apply", responses={404: {"description": "Not found"}})
    def apply_to_live(name: str) -> Any:
        p = _entry_path(name)
        if not p.is_file():
            raise HTTPException(404, f"stackfile not found: {name}")
        _live_path().write_text(p.read_text())
        return {"applied": _safe_name(name), "live_path": str(_live_path())}

    return router
