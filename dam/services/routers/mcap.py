import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_SVC_UNAVAILABLE = "MCAP session service not configured"


class McapArchiveRequest(BaseModel):
    filenames: list[str] = Field(default_factory=list, min_length=1)


def _require_mcap_svc(svc: Any) -> Any:
    if svc is None:
        raise HTTPException(503, _SVC_UNAVAILABLE)
    return svc


def _require_mcap_path(path: Any) -> Any:
    if path is None or not path.exists():
        raise HTTPException(404, "Session file not found")
    return path


def create_mcap_router(mcap_sessions: Any | None, control_svc: Any | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/mcap")

    def get_mcap_service(request: Request) -> Any:
        return mcap_sessions or getattr(request.app.state, "mcap_sessions", None)

    def _control_svc_ready() -> bool:
        return (
            control_svc is not None
            and hasattr(control_svc, "status")
            and hasattr(control_svc, "force_save_mcap")
        )

    def _active_session_filename(svc: Any) -> str | None:
        if not _control_svc_ready():
            return None
        try:
            is_running = control_svc.status().get("state") == "running"
            sessions = svc.list_sessions()
            if is_running and sessions:
                return str(sessions[0]["filename"])
        except Exception:
            return None
        return None

    @router.get("/sessions", responses={503: {"description": _SVC_UNAVAILABLE}})
    def mcap_list_sessions(svc: Annotated[Any, Depends(get_mcap_service)]) -> Any:
        svc = _require_mcap_svc(svc)
        return {"sessions": svc.list_sessions()}

    @router.get(
        "/sessions/{filename}",
        responses={
            404: {"description": "Session not found"},
            503: {"description": _SVC_UNAVAILABLE},
        },
    )
    def mcap_session_info(
        filename: Annotated[str, Path()], svc: Annotated[Any, Depends(get_mcap_service)]
    ) -> Any:
        svc = _require_mcap_svc(svc)
        info = svc.get_session_info(filename)
        if info is None:
            raise HTTPException(404, f"Session not found: {filename}")
        return info

    @router.delete(
        "/sessions/{filename}",
        responses={
            404: {"description": "Session not found or failed to archive"},
            409: {"description": "Session is currently being recorded"},
            503: {"description": _SVC_UNAVAILABLE},
        },
    )
    def mcap_delete_session(
        filename: Annotated[str, Path()], svc: Annotated[Any, Depends(get_mcap_service)]
    ) -> Any:
        svc = _require_mcap_svc(svc)

        if _active_session_filename(svc) == filename:
            raise HTTPException(
                409,
                "This session is currently being recorded. Stop the system before archiving.",
            )

        archived = svc.archive_session(filename)
        if not archived:
            raise HTTPException(404, f"Session not found or failed to archive: {filename}")
        return {"success": True, "archived": archived}

    @router.post(
        "/sessions/archive",
        responses={
            207: {"description": "Some sessions could not be archived"},
            409: {"description": "A selected session is currently being recorded"},
            503: {"description": _SVC_UNAVAILABLE},
        },
    )
    def mcap_archive_sessions(
        body: McapArchiveRequest, svc: Annotated[Any, Depends(get_mcap_service)]
    ) -> Any:
        svc = _require_mcap_svc(svc)
        active = _active_session_filename(svc)
        filenames = list(dict.fromkeys(body.filenames))
        if active in filenames:
            raise HTTPException(
                409,
                "A selected session is currently being recorded. Stop the system before archiving.",
            )

        archived: list[dict[str, Any]] = []
        failed: list[str] = []
        for filename in filenames:
            result = svc.archive_session(filename)
            if result:
                archived.append(result)
            else:
                failed.append(filename)

        return {"success": len(failed) == 0, "archived": archived, "failed": failed}

    @router.get("/sessions/{filename}/cycles", responses={503: {"description": _SVC_UNAVAILABLE}})
    def mcap_list_cycles(
        filename: Annotated[str, Path()],
        svc: Annotated[Any, Depends(get_mcap_service)],
        since_cycle_id: Annotated[int | None, Query()] = None,
    ) -> Any:
        svc = _require_mcap_svc(svc)
        cycles = svc.list_cycles(filename, since_cycle_id=since_cycle_id)
        return {"filename": filename, "count": len(cycles), "cycles": cycles}

    @router.get(
        "/sessions/{filename}/cycles/{cycle_id}",
        responses={404: {"description": "Cycle not found"}, 503: {"description": _SVC_UNAVAILABLE}},
    )
    def mcap_cycle_detail(
        filename: Annotated[str, Path()],
        cycle_id: Annotated[int, Path()],
        svc: Annotated[Any, Depends(get_mcap_service)],
        ts_ns: Annotated[int | None, Query()] = None,
    ) -> Any:
        svc = _require_mcap_svc(svc)
        detail = svc.get_cycle_detail(filename, cycle_id, ts_ns)
        if detail is None:
            raise HTTPException(404, f"Cycle {cycle_id} not found in {filename}")
        return detail

    @router.get("/find", responses={503: {"description": _SVC_UNAVAILABLE}})
    def mcap_find_session(
        cycle_id: Annotated[int, Query()], svc: Annotated[Any, Depends(get_mcap_service)]
    ) -> Any:
        svc = _require_mcap_svc(svc)
        filename = svc.find_session_by_cycle(cycle_id)
        return {"cycle_id": cycle_id, "filename": filename, "found": filename is not None}

    @router.get(
        "/sessions/{filename}/frames/{cam_name}",
        responses={503: {"description": _SVC_UNAVAILABLE}},
    )
    def mcap_list_frames(
        filename: Annotated[str, Path()],
        cam_name: Annotated[str, Path()],
        svc: Annotated[Any, Depends(get_mcap_service)],
    ) -> Any:
        svc = _require_mcap_svc(svc)
        frames = svc.list_frames(filename, cam_name)
        return {"camera": cam_name, "count": len(frames), "frames": frames}

    @router.get(
        "/sessions/{filename}/frame/{cam_name}/{frame_idx}",
        responses={404: {"description": "Frame not found"}, 503: {"description": _SVC_UNAVAILABLE}},
    )
    def mcap_get_frame(
        filename: Annotated[str, Path()],
        cam_name: Annotated[str, Path()],
        frame_idx: Annotated[int, Path()],
        svc: Annotated[Any, Depends(get_mcap_service)],
    ) -> Any:
        svc = _require_mcap_svc(svc)
        jpeg = svc.get_frame_jpeg(filename, cam_name, frame_idx)
        if jpeg is None:
            raise HTTPException(404, "Frame not found")
        return Response(content=jpeg, media_type="image/jpeg")

    @router.get(
        "/sessions/{filename}/frame_at/{cam_name}",
        responses={404: {"description": "Frame not found"}, 503: {"description": _SVC_UNAVAILABLE}},
    )
    def mcap_get_frame_at(
        filename: Annotated[str, Path()],
        cam_name: Annotated[str, Path()],
        ts_ns: Annotated[int, Query()],
        svc: Annotated[Any, Depends(get_mcap_service)],
    ) -> Any:
        svc = _require_mcap_svc(svc)
        jpeg = svc.get_frame_jpeg_at(filename, cam_name, ts_ns)
        if jpeg is None:
            raise HTTPException(404, "Frame not found")
        return Response(content=jpeg, media_type="image/jpeg")

    @router.get(
        "/sessions/{filename}/download",
        responses={
            404: {"description": "Session file not found"},
            503: {"description": _SVC_UNAVAILABLE},
        },
    )
    def mcap_download(
        filename: Annotated[str, Path()], svc: Annotated[Any, Depends(get_mcap_service)]
    ) -> Any:
        from fastapi.responses import FileResponse

        svc = _require_mcap_svc(svc)
        path = _require_mcap_path(svc._resolve(filename))
        return FileResponse(path, filename=filename, media_type="application/octet-stream")

    return router
