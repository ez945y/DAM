"""Replay-through-guards jobs with Server-Sent Events progress streaming.

A POST starts a background worker thread that re-evaluates an MCAP session
against a chosen stackfile; the matching SSE endpoint streams incremental
progress so the console can monitor several stackfiles at once.
"""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import StreamingResponse

from dam.services.replay import iter_replay_through_guards
from dam.services.routers.stackfiles import _entry_path, _live_path

if TYPE_CHECKING:
    from dam.services.mcap_sessions import McapSessionService

_LIVE = "__live__"


class _Job:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.stop_event = threading.Event()
        self.finished = False

    def push(self, ev: dict[str, Any]) -> None:
        """Thread-safe hand-off from the worker thread to the asyncio queue."""
        self.loop.call_soon_threadsafe(self.queue.put_nowait, ev)


def _resolve_stack(stack: str) -> str:
    if stack == _LIVE:
        p = _live_path()
        if not p.is_file():
            raise HTTPException(404, "live .dam_stackfile.yaml does not exist")
        return str(p)
    p = _entry_path(stack)  # validates the name
    if not p.is_file():
        raise HTTPException(404, f"stackfile not found in library: {stack}")
    return str(p)


def create_replay_router(mcap_sessions: McapSessionService | None) -> APIRouter:
    router = APIRouter(prefix="/api/replay")
    jobs: dict[str, _Job] = {}

    @router.post("/jobs", responses={404: {"description": "mcap or stack not found"}})
    async def start_job(request: Request, body: Annotated[dict[str, Any], Body()]) -> Any:
        # The live service is attached to app.state at startup; the constructor
        # arg is a fallback (mirrors create_mcap_router).
        svc = mcap_sessions or getattr(request.app.state, "mcap_sessions", None)
        if svc is None:
            raise HTTPException(503, "MCAP sessions service not configured")
        mcap_name = str(body.get("mcap", ""))
        stack = str(body.get("stack", ""))
        if not mcap_name or not stack:
            raise HTTPException(400, "both 'mcap' and 'stack' are required")

        mcap_path = svc._resolve(mcap_name)
        if mcap_path is None or not mcap_path.is_file():
            raise HTTPException(404, f"MCAP session not found: {mcap_name}")
        stack_path = _resolve_stack(stack)

        loop = asyncio.get_running_loop()
        job = _Job(loop)
        job_id = uuid.uuid4().hex[:12]
        jobs[job_id] = job

        def worker() -> None:
            try:
                for ev in iter_replay_through_guards(
                    str(mcap_path), stack_path, should_stop=job.stop_event.is_set
                ):
                    job.push(ev)
            except Exception as exc:  # noqa: BLE001 — report worker crash to client
                job.push({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
            finally:
                job.push({"type": "__eof__"})

        threading.Thread(target=worker, name=f"replay-{job_id}", daemon=True).start()
        return {"job_id": job_id, "mcap": mcap_name, "stack": stack}

    @router.get("/jobs/{job_id}/events")
    async def stream_events(job_id: str) -> StreamingResponse:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, f"unknown replay job: {job_id}")

        async def gen() -> Any:
            try:
                while True:
                    ev = await job.queue.get()
                    if ev.get("type") == "__eof__":
                        break
                    yield f"data: {json.dumps(ev)}\n\n"
                    if ev.get("type") in ("done", "error"):
                        # Drain any trailing eof then stop.
                        continue
            finally:
                jobs.pop(job_id, None)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.post("/jobs/{job_id}/stop", responses={404: {"description": "unknown job"}})
    async def stop_job(job_id: str) -> Any:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, f"unknown replay job: {job_id}")
        job.stop_event.set()
        return {"stopping": job_id}

    return router
