"""Runtime Control Service — start/pause/resume/stop/E-Stop for GuardRuntime."""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable
from enum import StrEnum
from typing import Any

from dam.config.schema import StackfileConfig
from dam.runner.base import BaseRunner
from dam.types.result import GuardDecision

logger = logging.getLogger(__name__)


class RuntimeState(StrEnum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    EMERGENCY = "emergency"


class BackendState(StrEnum):
    LOADING = "loading"
    READY = "ready"
    ERROR = "error"
    FAULTED = "faulted"


class RuntimeControlService:
    """Thread-safe control wrapper around a BaseRunner.

    The service holds a reference to a BaseRunner and exposes
    start / pause / resume / stop / emergency_stop methods that
    are safe to call from the REST API (a different thread from
    the control loop).
    """

    def __init__(self) -> None:
        self._runner: BaseRunner | None = None
        self._config: StackfileConfig | None = None
        self._stack_path: str | None = None
        self._post_step_wrapper: Callable[[Callable], Callable] | None = None
        self._state = RuntimeState.IDLE
        self._backend_state = BackendState.LOADING
        self._lock = threading.Lock()
        self._pause_event = threading.Event()
        self._pause_event.set()  # not paused initially
        self._run_thread: threading.Thread | None = None
        self._on_state_change: Callable[[RuntimeState], None] | None = None
        self._on_status_broadcast: Callable[[dict[str, Any]], None] | None = None
        self._cycle_count: int = 0
        self._error: str | None = None
        # Set by dev_server when hardware validation fails at startup.
        # While set, start() is blocked and the frontend shows a blocking overlay.
        self._startup_error: str | None = None

    def set_startup_error(self, message: str) -> None:
        """Mark the service as having a hardware/startup error."""
        with self._lock:
            self._startup_error = message
            self._state = RuntimeState.EMERGENCY
            self._backend_state = BackendState.ERROR
            logger.warning("RuntimeControlService: startup_error set: %s", message)
        self._notify_state()

    # ── Registration ──────────────────────────────────────────────────────────

    def attach_runner(self, runner: BaseRunner, stack_path: str | None = None) -> None:
        """Attach a Runner instance and optionally its source stackfile path."""
        with self._lock:
            self._runner = runner
            if stack_path:
                self._stack_path = stack_path
            self._startup_error = None
            self._state = RuntimeState.IDLE
            self._backend_state = BackendState.LOADING
            self._target_hz = getattr(runner.runtime, "_control_frequency_hz", 50.0)
        self._notify_state()

        # Auto-apply instrumentation if a wrapper is already registered
        if self._post_step_wrapper and self._runner and hasattr(self._runner.runtime, "step"):
            logger.info("RuntimeControlService: Applying instrumentation wrapper to runtime.step")
            self._runner.runtime.step = self._post_step_wrapper(self._runner.runtime.step)

    def set_stack_path(self, stack_path: str) -> None:
        """Explicitly set the stackfile path for recheck capability."""
        self._stack_path = stack_path

    def apply_config(self, config: StackfileConfig) -> None:
        """Apply a parsed StackfileConfig to the service."""
        with self._lock:
            self._config = config
            # Note: We don't need to manually update HZ here,
            # status() will read it from self._config dynamically.

    def set_post_step_wrapper(self, wrapper: Callable[[Callable], Callable]) -> None:
        """Register a function that wraps runtime.step with instrumentation."""
        self._post_step_wrapper = wrapper
        # Apply immediately if runner is already here
        if self._runner and hasattr(self._runner.runtime, "step"):
            logger.info("RuntimeControlService: Applying newly registered wrapper to runtime.step")
            self._runner.runtime.step = self._post_step_wrapper(self._runner.runtime.step)

    def on_state_change(self, callback: Callable[[RuntimeState], None]) -> None:
        """Register a callback called when runtime state changes."""
        self._on_state_change = callback

    def attach_runtime(self, runtime: Any) -> None:
        """Attach a bare GuardRuntime directly, marking the system as ready immediately.

        Unlike ``attach_runner``, this does not attempt hardware verification and is
        intended for simulation and unit-test scenarios where the runtime is already
        fully constructed and ready to use.
        """

        class _RuntimeAdapter:
            """Minimal BaseRunner-compatible wrapper around a raw GuardRuntime."""

            def __init__(self, rt: Any) -> None:
                self.runtime = rt

            def step(self) -> Any:
                return self.runtime.step()

            def connect(self) -> None:
                # Runtime is pre-built; no connection step needed.
                pass

            def verify(self) -> None:
                # Runtime is pre-built; no verification step needed.
                pass

            def shutdown(self) -> None:
                # Runtime is pre-built; no shutdown step needed.
                pass

        with self._lock:
            self._runner = _RuntimeAdapter(runtime)
            self._startup_error = None
            self._state = RuntimeState.IDLE
            self._backend_state = BackendState.READY
            self._target_hz = getattr(runtime, "_control_frequency_hz", 50.0)
        self._notify_state()

    # ── Commands ──────────────────────────────────────────────────────────────

    def start(
        self, task_name: str = "default", n_cycles: int = -1, cycle_budget_ms: float | None = None
    ) -> bool:
        """Launch the control loop in a background daemon thread."""
        with self._lock:
            # Priority order: startup_error → no runner → backend not ready
            if self._startup_error:
                raise RuntimeError(f"Cannot start: {self._startup_error}")
            if self._runner is None:
                raise RuntimeError(
                    "No GuardRuntime attached. Call attach_runtime() or attach_runner() first."
                )
            if self._backend_state != BackendState.READY:
                raise RuntimeError(
                    f"Cannot start: System is {self._backend_state}. Needs confirmation or recheck"
                )
            if self._state in (RuntimeState.RUNNING, RuntimeState.STARTING):
                logger.warning("RuntimeControlService.start(): already running")
                return False
            self._state = RuntimeState.STARTING
            self._error = None
            self._pause_event.set()
        self._notify_state()

        try:
            if hasattr(self._runner.runtime, "start_task"):
                self._runner.runtime.start_task(task_name)
        except Exception:  # noqa: BLE001 — start_task is best-effort
            pass

        # Use the provided budget, or fallback to the runner's internal frequency
        if cycle_budget_ms is None:
            hz = getattr(self._runner.runtime, "_control_frequency_hz", 50.0)
            cycle_budget_ms = 1000.0 / hz

        self._run_thread = threading.Thread(
            target=self._run_loop,
            args=(n_cycles, cycle_budget_ms),
            daemon=False,
            name="dam-runtime-loop",
        )
        with self._lock:
            self._state = RuntimeState.RUNNING
        self._run_thread.start()
        self._notify_state()
        return True

    def set_status_callback(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Register a callback to broadcast simplified status updates (e.g., via WS)."""
        self._on_status_broadcast = callback

    def pause(self) -> bool:
        """Pause the control loop after the current cycle."""
        with self._lock:
            if self._state != RuntimeState.RUNNING:
                return False
            self._state = RuntimeState.PAUSED
        self._pause_event.clear()
        self._notify_state()
        return True

    def resume(self) -> bool:
        """Resume a paused control loop."""
        with self._lock:
            if self._state != RuntimeState.PAUSED:
                return False
            self._state = RuntimeState.RUNNING
        self._pause_event.set()
        self._notify_state()
        return True

    def stop(self) -> bool:
        """Gracefully stop the control loop."""
        with self._lock:
            if self._state not in (
                RuntimeState.RUNNING,
                RuntimeState.PAUSED,
                RuntimeState.STARTING,
            ):
                return False
            self._state = RuntimeState.STOPPING
        self._pause_event.set()  # unblock if paused
        self._notify_state()

        if self._runner is not None and hasattr(self._runner.runtime, "stop"):
            self._runner.runtime.stop()

        return True

    def emergency_stop(self) -> bool:
        """Immediate emergency stop — triggers sink emergency_stop if available."""
        with self._lock:
            self._state = RuntimeState.EMERGENCY
        self._pause_event.set()
        if self._runner is not None:
            # shutdown the runner to be safe
            try:
                self._runner.shutdown()
            except Exception as e:
                logger.error("E-Stop runner shutdown error: %s", e)

        with self._lock:
            if not self._error:
                self._error = "Emergency Stop Triggered"

        self._notify_state()
        logger.warning("RuntimeControlService: EMERGENCY STOP triggered")
        return True

    def reset(self) -> bool:
        """Reset to IDLE (only from STOPPED or EMERGENCY)."""
        with self._lock:
            if self._state not in (RuntimeState.STOPPED, RuntimeState.EMERGENCY, RuntimeState.IDLE):
                return False
            self._state = RuntimeState.IDLE
            self._error = None
        self._notify_state()
        return True

    @staticmethod
    def _detect_adapter_type(stack_path: str) -> str:
        """Read the stackfile and return the adapter type ('lerobot', 'ros2', 'simulation')."""
        try:
            import yaml

            with open(stack_path) as f:
                raw = yaml.safe_load(f)
            hw = raw.get("hardware", {}) or {}
            sources = hw.get("sources", {}) or {}
            if sources:
                first = next(iter(sources.values()), {})
                return str(first.get("type", "simulation")).lower()
        except Exception:  # noqa: BLE001 — config read failure is non-fatal; default to simulation
            pass
        return "simulation"

    @staticmethod
    def _ensure_lerobot_installed() -> bool:
        """Return True if lerobot is importable; if not, run setup-lerobot and retry."""
        try:
            import lerobot  # noqa: F401

            return True
        except ImportError:
            pass

        import os
        import subprocess
        import sys

        logger.info("RuntimeControlService: lerobot not found — running setup-lerobot…")
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        setup = os.path.join(root, "scripts", "setup.sh")
        result = subprocess.run(
            ["bash", setup, "--lerobot"],
            cwd=root,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.error("setup-lerobot failed:\n%s", result.stderr)
            return False

        import importlib
        import site

        importlib.invalidate_caches()
        for path in site.getsitepackages():
            if path not in sys.path:
                sys.path.insert(0, path)

        try:
            import lerobot  # noqa: F401

            logger.info("RuntimeControlService: lerobot installed successfully.")
            return True
        except ImportError:
            return False

    def recheck_hardware(self, stack_path: str | None = None) -> bool:
        """Attempt to re-initialize hardware via the Runner model."""
        import importlib

        import dam.runtime.factory

        path = stack_path or self._stack_path
        if not path:
            logger.error("RuntimeControlService: Cannot re-check hardware, no stack_path known.")
            return False

        adapter_type = self._detect_adapter_type(path)

        with self._lock:
            self._backend_state = BackendState.LOADING
        self._notify_state()

        if adapter_type == "lerobot" and not self._ensure_lerobot_installed():
            self.set_startup_error("lerobot setup failed.")
            with self._lock:
                self._backend_state = BackendState.ERROR
            return False

        try:
            importlib.reload(dam.runtime.factory)
            if adapter_type == "lerobot":
                import dam.adapter.lerobot.builder
                import dam.adapter.lerobot.source

                importlib.reload(dam.adapter.lerobot.builder)
                importlib.reload(dam.adapter.lerobot.source)

            from dam.runtime.factory import RuntimeFactory

            logger.info("RuntimeControlService: Re-checking hardware from %s", path)

            # 1. Shutdown old
            with self._lock:
                if self._runner:
                    try:
                        self._runner.shutdown()
                    except Exception as e:
                        logger.debug("Old runner shutdown failed: %s", e)

            # 2. Build new runner
            new_runner = RuntimeFactory.build_from_stackfile(path)

            if self._post_step_wrapper:
                new_runner.runtime.step = self._post_step_wrapper(new_runner.runtime.step)

            # 3. Attach and Connect
            self.attach_runner(new_runner, path)

            try:
                self._runner.connect()
                self._runner.verify()
                with self._lock:
                    self._backend_state = BackendState.READY
            except Exception as e:
                logger.error("RuntimeControlService: Connection/Verify failed: %s", e)
                self.set_startup_error(str(e))
                return False

            self._notify_state()
            return True

        except Exception as e:
            error_msg = str(e)
            logger.error("RuntimeControlService: Re-check failed: %s", error_msg)
            self.set_startup_error(error_msg)
            with self._lock:
                self._backend_state = BackendState.ERROR
            return False

    def confirm_fault(self) -> bool:
        """Transitions back from FAULTED to READY."""
        with self._lock:
            if self._backend_state != BackendState.FAULTED:
                return False
            self._backend_state = BackendState.READY
            self._error = None
            self._startup_error = None  # Also clear startup error if any
        self._notify_state()
        return True

    # ── Status ────────────────────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        """Return a JSON-serialisable status dict."""
        with self._lock:
            rt = self._runner.runtime if self._runner else None

            # Base config values
            hz = 50.0
            task_config = {}

            # 1. Use live runtime if it exists (it's the most real-time truth)
            if rt is not None:
                hz = getattr(rt, "_control_frequency_hz", 50.0)
                task_config = getattr(rt, "_task_config", {})
                available_tasks = list(task_config.keys())
                planned_task = (
                    "default"
                    if "default" in task_config
                    else (available_tasks[0] if available_tasks else None)
                )
                planned_boundaries = list(task_config.get(planned_task, [])) if planned_task else []

            # 2. Otherwise, use the structured config object (SSOT)
            elif self._config:
                hz = self._config.safety.control_frequency_hz
                # config.tasks is a dict[str, TaskConfig]
                task_dict = {tid: tcfg.boundaries for tid, tcfg in self._config.tasks.items()}
                available_tasks = list(self._config.tasks.keys())
                planned_task = (
                    "default"
                    if "default" in task_dict
                    else (available_tasks[0] if available_tasks else None)
                )
                if planned_task:
                    planned_boundaries = task_dict.get(planned_task, [])

            # 3. Last resort defaults
            else:
                available_tasks = []
                planned_task = None
                planned_boundaries = []

            active_task = getattr(rt, "_active_task", None)
            active_boundaries = list(getattr(rt, "_active_container_names", []))

            return {
                "state": self._state.value,
                "backend_state": self._backend_state.value,
                "cycle_count": self._cycle_count,
                "error": self._error,
                "startup_error": self._startup_error,
                "has_runtime": rt is not None,
                "active_task": active_task if active_task else planned_task,
                "active_boundaries": active_boundaries if active_boundaries else planned_boundaries,
                "control_frequency_hz": hz,
                "available_tasks": available_tasks,
                "planned_task": planned_task,
                "planned_boundaries": planned_boundaries,
                "has_rust": True,
            }

    @property
    def state(self) -> RuntimeState:
        return self._state

    # ── Internal ─────────────────────────────────────────────────────────────

    def _run_loop(self, n_cycles: int, cycle_budget_ms: float) -> None:
        """Background thread target."""
        import time

        cycle_budget_s = cycle_budget_ms / 1000.0
        cycle = 0
        try:
            while True:
                with self._lock:
                    state = self._state
                    runner = self._runner
                if state in (RuntimeState.STOPPING, RuntimeState.STOPPED, RuntimeState.EMERGENCY):
                    break
                if state == RuntimeState.PAUSED:
                    self._pause_event.wait(timeout=0.1)
                    continue

                t0 = time.perf_counter()
                try:
                    result = runner.step()
                    if result and any(
                        r.decision == GuardDecision.FAULT for r in result.guard_results
                    ):
                        fault_reason = next(
                            (
                                r.reason
                                for r in result.guard_results
                                if r.decision == GuardDecision.FAULT
                            ),
                            "Fault",
                        )
                        with self._lock:
                            self._error = fault_reason
                            self._backend_state = BackendState.FAULTED
                        self.emergency_stop()
                        break

                    with self._lock:
                        self._cycle_count += 1
                except StopIteration:
                    break
                except Exception as e:
                    with self._lock:
                        self._error = str(e)
                    self.emergency_stop()
                    break

                cycle += 1
                if n_cycles != -1 and cycle >= n_cycles:
                    break

                elapsed = time.perf_counter() - t0
                sleep = cycle_budget_s - elapsed
                if sleep > 0:
                    time.sleep(sleep)

        finally:
            with self._lock:
                if self._state in (RuntimeState.RUNNING, RuntimeState.STOPPING):
                    self._state = RuntimeState.STOPPED
            self._notify_state()

    def _notify_state(self) -> None:
        if self._on_state_change is not None:
            with contextlib.suppress(Exception):
                self._on_state_change(self._state)

        if self._on_status_broadcast is not None:
            with self._lock:
                runner = self._runner
                rt = runner.runtime if runner else None
                task_config: dict[str, Any] = getattr(rt, "_task_config", {})
                available_tasks = list(task_config.keys())
                planned_task = (
                    "default"
                    if "default" in task_config
                    else (available_tasks[0] if available_tasks else None)
                )
                planned_boundaries = list(task_config.get(planned_task, [])) if planned_task else []
            msg = {
                "type": "system_status",
                "state": self._state,
                "backend_state": self._backend_state,
                "error": self._error or self._startup_error,
                "message": self._error or self._startup_error or f"System state: {self._state}",
                "cycle_count": self._cycle_count,
                "planned_task": planned_task,
                "planned_boundaries": planned_boundaries,
            }
            with contextlib.suppress(Exception):
                self._on_status_broadcast(msg)
