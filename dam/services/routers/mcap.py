from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import Response


def create_mcap_router(mcap_sessions: Any | None) -> APIRouter:
    router = APIRouter(prefix="/api/mcap")

    def get_mcap_service(request: Request) -> Any:
        return mcap_sessions or getattr(request.app.state, "mcap_sessions", None)

    @router.get("/sessions")
    def mcap_list_sessions(svc: Annotated[Any, Depends(get_mcap_service)]) -> Any:
        if svc is None:
            raise HTTPException(503, "MCAP session service not configured")
        return {"sessions": svc.list_sessions()}

    @router.get("/sessions/{filename}")
    def mcap_session_info(
        filename: Annotated[str, Path()], svc: Annotated[Any, Depends(get_mcap_service)]
    ) -> Any:
        if svc is None:
            raise HTTPException(503, "MCAP session service not configured")
        info = svc.get_session_info(filename)
        if info is None:
            raise HTTPException(404, f"Session not found: {filename}")
        return info

    @router.delete("/sessions/{filename}")
    def mcap_delete_session(
        filename: Annotated[str, Path()], svc: Annotated[Any, Depends(get_mcap_service)]
    ) -> Any:
        if svc is None:
            raise HTTPException(503, "MCAP session service not configured")
        success = svc.delete_session(filename)
        if not success:
            raise HTTPException(404, f"Session not found or failed to delete: {filename}")
        return {"success": True}

    @router.get("/sessions/{filename}/cycles")
    def mcap_list_cycles(
        filename: Annotated[str, Path()],
        svc: Annotated[Any, Depends(get_mcap_service)],
        since_cycle_id: Annotated[int | None, Query()] = None,
    ) -> Any:
        if svc is None:
            raise HTTPException(503, "MCAP session service not configured")
        cycles = svc.list_cycles(filename, since_cycle_id=since_cycle_id)
        return {"filename": filename, "count": len(cycles), "cycles": cycles}

    @router.get("/sessions/{filename}/cycles/{cycle_id}")
    def mcap_cycle_detail(
        filename: Annotated[str, Path()],
        cycle_id: Annotated[int, Path()],
        svc: Annotated[Any, Depends(get_mcap_service)],
        ts_ns: Annotated[int | None, Query()] = None,
    ) -> Any:
        if svc is None:
            raise HTTPException(503, "MCAP session service not configured")
        detail = svc.get_cycle_detail(filename, cycle_id, ts_ns)
        if detail is None:
            raise HTTPException(404, f"Cycle {cycle_id} not found in {filename}")
        return detail

    @router.get("/find")
    def mcap_find_session(
        cycle_id: Annotated[int, Query()], svc: Annotated[Any, Depends(get_mcap_service)]
    ) -> Any:
        if svc is None:
            raise HTTPException(503, "MCAP session service not configured")
        filename = svc.find_session_by_cycle(cycle_id)
        return {"cycle_id": cycle_id, "filename": filename, "found": filename is not None}

    @router.get("/live")
    def mcap_live_session(svc: Annotated[Any, Depends(get_mcap_service)]) -> Any:
        """Return the most-recently-modified MCAP session — the one currently being written."""
        if svc is None:
            raise HTTPException(503, "MCAP session service not configured")
        sessions = svc.list_sessions()
        if not sessions:
            return {"filename": None, "active": False}
        latest = sessions[0]
        return {"filename": latest["filename"], "active": True, "updated_at": latest["created_at"]}

    @router.get("/sessions/{filename}/frames/{cam_name}")
    def mcap_list_frames(
        filename: Annotated[str, Path()],
        cam_name: Annotated[str, Path()],
        svc: Annotated[Any, Depends(get_mcap_service)],
    ) -> Any:
        if svc is None:
            raise HTTPException(503, "MCAP session service not configured")
        frames = svc.list_frames(filename, cam_name)
        return {"camera": cam_name, "count": len(frames), "frames": frames}

    @router.get("/sessions/{filename}/frame/{cam_name}/{frame_idx}")
    def mcap_get_frame(
        filename: Annotated[str, Path()],
        cam_name: Annotated[str, Path()],
        frame_idx: Annotated[int, Path()],
        svc: Annotated[Any, Depends(get_mcap_service)],
    ) -> Any:
        if svc is None:
            raise HTTPException(503, "MCAP session service not configured")
        jpeg = svc.get_frame_jpeg(filename, cam_name, frame_idx)
        if jpeg is None:
            raise HTTPException(404, "Frame not found")
        return Response(content=jpeg, media_type="image/jpeg")

    @router.get("/sessions/{filename}/frame_at/{cam_name}")
    def mcap_get_frame_at(
        filename: Annotated[str, Path()],
        cam_name: Annotated[str, Path()],
        ts_ns: Annotated[int, Query()],
        svc: Annotated[Any, Depends(get_mcap_service)],
    ) -> Any:
        if svc is None:
            raise HTTPException(503, "MCAP session service not configured")
        jpeg = svc.get_frame_jpeg_at(filename, cam_name, ts_ns)
        if jpeg is None:
            raise HTTPException(404, "Frame not found")
        return Response(content=jpeg, media_type="image/jpeg")

    @router.get("/sessions/{filename}/download")
    def mcap_download(
        filename: Annotated[str, Path()], svc: Annotated[Any, Depends(get_mcap_service)]
    ) -> Any:
        from fastapi.responses import FileResponse

        if svc is None:
            raise HTTPException(503, "MCAP session service not configured")
        path = svc._resolve(filename)
        if not path or not path.exists():
            raise HTTPException(404, "Session file not found")
        return FileResponse(path, filename=filename, media_type="application/octet-stream")

    return router
