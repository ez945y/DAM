"""Disposable, hardware-free API for manual Console acceptance.

Run: .venv/bin/python -m tests.acceptance.safety_server
Only production safety/library/MCAP routes are mounted. No host control or
hardware adapter is constructed. Data lives in a newly created temporary root.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import uvicorn
import yaml
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from dam.api import _register_builtins
from dam.config.schema import StackfileConfig
from dam.runtime.guard_runtime import GuardRuntime
from dam.services.mcap_sessions import McapSessionService
from dam.services.routers import safety_authoring, safety_comparison, stackfiles
from dam.services.routers.mcap import create_mcap_router
from dam.services.routers.safety_switching import create_safety_switching_router
from dam.types.action import ActionProposal
from dam.types.observation import Observation
from examples.safety_comparison_demo import create_demo


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="dam-s1-") as folder:
        root = Path(folder)
        library = root / "data" / "stackfiles"
        recording, roomy, precise = create_demo(library)
        sessions = root / "sessions"
        sessions.mkdir()
        recording.rename(sessions / recording.name)
        plans = root / "data" / "safety-plans"
        plans.mkdir(parents=True)
        plan = json.loads(Path("examples/safety_plans/precision.json").read_text())
        (plans / "demo-precision.json").write_text(json.dumps(plan))
        live = root / ".dam_stackfile.yaml"
        live.write_text(roomy.read_text())
        _register_builtins()
        runtime = GuardRuntime._from_config(
            StackfileConfig.model_validate(yaml.safe_load(roomy.read_text()))
        )
        runtime.start_task("comparison_demo")
        control = SimpleNamespace(
            _runner=SimpleNamespace(runtime=runtime), state="running", _stack_path=str(live)
        )

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            async def cycles():
                while True:
                    # Execute only the offline validation path: no sink exists.
                    runtime.validate(
                        Observation(timestamp=time.monotonic(), joint_positions=np.zeros(1)),
                        ActionProposal(target_joint_positions=np.array([1.5])),
                        "acceptance",
                    )
                    await asyncio.sleep(0.1)

            task = asyncio.create_task(cycles())
            try:
                yield
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                runtime.shutdown()

        app = FastAPI(lifespan=lifespan)
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://127.0.0.1:13000"],
            allow_methods=["*"],
            allow_headers=["*"],
        )
        svc = McapSessionService(str(sessions))
        with (
            patch.object(stackfiles, "_root", lambda: root),
            patch.object(safety_authoring, "_library_dir", lambda: plans),
            patch.object(safety_comparison, "_root", lambda: root),
        ):
            app.include_router(stackfiles.create_stackfiles_router())
            app.include_router(safety_authoring.create_safety_authoring_router())
            app.include_router(safety_comparison.create_safety_comparison_router(svc))
            app.include_router(create_safety_switching_router(control, root))
            app.include_router(create_mcap_router(svc))
            print(f"Disposable acceptance data: {root}", flush=True)
            uvicorn.run(app, host="127.0.0.1", port=18080)


if __name__ == "__main__":
    main()
