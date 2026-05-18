"""Runtime Control Service — start/pause/resume/stop/E-Stop for GuardRuntime."""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable
from enum import StrEnum
from typing import Any

from dam.config.schema import StackfileConfig
from dam.runner.base import BaseRunner, RunnerStatus

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
        self._ros2_node: Any = None
        self._post_step_wrapper: Callable[[Callable], Callable] | None = None
        self._state = RuntimeState.IDLE
        self._backend_state = BackendState.LOADING
        self._lock = threading.Lock()
        self._on_state_change: Callable[[RuntimeState], None] | None = None
        self._on_status_broadcast: Callable[[dict[str, Any]], None] | None = None
        self._cycle_count: int = 0
        self._error: str | None = None
        # Set by dev_server when hardware validation fails at startup.
        # While set, start() is blocked and the frontend shows a blocking overlay.
        self._startup_error: str | None = None

    def set_startup_error(self, message: str) -> None:
        """Mark the service as having a hardware/startup error.

        Callers are expected to have already logged the underlying error at
        ERROR level — every current call site does, so duplicating it here
        would produce two stack-sized log entries per failure. Kept at DEBUG
        so the state transition is still traceable if needed.
        """
        with self._lock:
            self._startup_error = message
            self._state = RuntimeState.EMERGENCY
            self._backend_state = BackendState.ERROR
            logger.debug("RuntimeControlService: startup_error set: %s", message)
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
            self._cycle_count = 0
            if hasattr(runner, "set_lifecycle_callbacks"):
                runner.set_lifecycle_callbacks(
                    on_cycle=self._handle_runner_cycle,
                    on_fault=self._handle_runner_fault,
                    on_finished=self._handle_runner_finished,
                )
        self._notify_state()
        if self._post_step_wrapper and hasattr(runner, "set_step_wrapper"):
            logger.info("RuntimeControlService: Applying instrumentation wrapper to runner step")
            runner.set_step_wrapper(self._post_step_wrapper)

    def set_stack_path(self, stack_path: str) -> None:
        """Explicitly set the stackfile path for recheck capability."""
        self._stack_path = stack_path

    def apply_config(self, config: StackfileConfig) -> None:
        """Apply a parsed StackfileConfig to the service."""
        with self._lock:
            self._config = config
            # Note: We don't need to manually update HZ here,
            # status() will read it from self._config dynamically.

    def build_runner_from_config(
        self,
        config: StackfileConfig,
        *,
        stack_path: str | None = None,
        ros2_node: Any = None,
    ) -> BaseRunner:
        """Build and attach a complete Runner from config via RuntimeFactory."""
        from dam.runtime.factory import RuntimeFactory

        self._ros2_node = ros2_node
        runner = RuntimeFactory.build_from_config(config, ros2_node=ros2_node)
        self.apply_config(config)
        self.attach_runner(runner, stack_path)
        return runner

    def build_runner_from_stackfile(self, stack_path: str, *, ros2_node: Any = None) -> BaseRunner:
        """Load config, build a complete Runner, and attach it to the service."""
        from dam.runtime.factory import RuntimeFactory

        self._ros2_node = ros2_node
        config = RuntimeFactory.load_config(stack_path)
        self.set_stack_path(stack_path)
        return self.build_runner_from_config(config, stack_path=stack_path, ros2_node=ros2_node)

    def set_post_step_wrapper(self, wrapper: Callable[[Callable], Callable]) -> None:
        """Register a function that wraps the runner-owned step callable."""
        self._post_step_wrapper = wrapper
        # Apply immediately if runner is already here
        if self._runner and hasattr(self._runner, "set_step_wrapper"):
            logger.info("RuntimeControlService: Applying newly registered wrapper to runner step")
            self._runner.set_step_wrapper(self._post_step_wrapper)

    def on_state_change(self, callback: Callable[[RuntimeState], None]) -> None:
        """Register a callback called when runtime state changes."""
        self._on_state_change = callback

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
                raise RuntimeError("No Runner attached. Call attach_runner() first.")
            if self._backend_state != BackendState.READY:
                raise RuntimeError(
                    f"Cannot start: System is {self._backend_state}. Needs confirmation or recheck"
                )
            if self._state in (RuntimeState.RUNNING, RuntimeState.STARTING):
                logger.warning("RuntimeControlService.start(): already running")
                return False
            self._state = RuntimeState.STARTING
            self._error = None
            runner = self._runner
        self._notify_state()

        try:
            ok = runner.start(task_name, n_cycles=n_cycles, cycle_budget_ms=cycle_budget_ms)
        except Exception:
            with self._lock:
                self._state = RuntimeState.IDLE
            self._notify_state()
            raise

        with self._lock:
            self._state = self._runtime_state_for_runner(runner.status)
        self._notify_state()
        return ok

    def set_status_callback(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Register a callback to broadcast simplified status updates (e.g., via WS)."""
        self._on_status_broadcast = callback

    def pause(self) -> bool:
        """Pause the control loop after the current cycle."""
        with self._lock:
            runner = self._runner
            if runner is None or self._state != RuntimeState.RUNNING:
                return False
        ok = runner.pause()
        with self._lock:
            self._state = self._runtime_state_for_runner(runner.status)
        self._notify_state()
        return ok

    def resume(self) -> bool:
        """Resume a paused control loop."""
        with self._lock:
            runner = self._runner
            if runner is None or self._state != RuntimeState.PAUSED:
                return False
        ok = runner.resume()
        with self._lock:
            self._state = self._runtime_state_for_runner(runner.status)
        self._notify_state()
        return ok

    def stop(self) -> bool:
        """Gracefully stop the control loop."""
        with self._lock:
            runner = self._runner
            if (
                self._state
                not in (
                    RuntimeState.RUNNING,
                    RuntimeState.PAUSED,
                    RuntimeState.STARTING,
                )
                or runner is None
            ):
                return False
            self._state = RuntimeState.STOPPING
        self._notify_state()
        ok = runner.stop()
        with self._lock:
            self._state = self._runtime_state_for_runner(runner.status)
        self._notify_state()
        return ok

    def force_save_mcap(self) -> None:
        """Force the loopback writer to rotate the MCAP file immediately (zero-downtime save)."""
        with self._lock:
            runner = self._runner
        if runner is not None and hasattr(runner, "force_save_mcap"):
            runner.force_save_mcap()

    def emergency_stop(self) -> bool:
        """Immediate emergency stop — triggers sink emergency_stop if available."""
        with self._lock:
            self._state = RuntimeState.EMERGENCY
            runner = self._runner
        if runner is not None:
            # shutdown the runner to be safe
            try:
                runner.shutdown()
            except Exception as e:
                logger.error("E-Stop runner shutdown error: %s", e)

        with self._lock:
            if not self._error:
                self._error = "Emergency Stop Triggered"

        self._notify_state()
        logger.warning("RuntimeControlService: EMERGENCY STOP triggered")
        return True

    def reset(self) -> bool:
        """Reset to IDLE (only from STOPPED or EMERGENCY).

        When resetting from EMERGENCY, hardware is reconnected so the next
        ``start()`` doesn't fail with "not connected". ``emergency_stop()``
        always calls ``runner.shutdown()`` (which disconnects the robot) so
        we always need to reconnect — ``runner.connect()`` is idempotent,
        so this is safe even if ``confirm_fault()`` already reconnected.
        ``runner.verify()`` lazily reconnects external sources (cameras)
        via their own ``verify()`` paths.
        """
        with self._lock:
            if self._state not in (RuntimeState.STOPPED, RuntimeState.EMERGENCY, RuntimeState.IDLE):
                return False
            was_emergency = self._state == RuntimeState.EMERGENCY
            runner = self._runner

        if was_emergency and runner is not None:
            try:
                runner.connect()
                runner.verify()
                if hasattr(runner, "clear_fault"):
                    runner.clear_fault()
                logger.info("RuntimeControlService: hardware reconnected after reset")
                with self._lock:
                    self._backend_state = BackendState.READY
            except Exception as exc:
                logger.error("RuntimeControlService: reconnect on reset failed: %s", exc)
                self.set_startup_error(str(exc))
                return False

        with self._lock:
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
                import dam.adapter.lerobot.adapter
                import dam.adapter.lerobot.builder

                importlib.reload(dam.adapter.lerobot.adapter)
                importlib.reload(dam.adapter.lerobot.builder)

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
            new_runner = RuntimeFactory.build_from_stackfile(path, ros2_node=self._ros2_node)

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
        """Transitions back from FAULTED to READY, reconnecting hardware.

        After an emergency stop the runner is shut down (robot disconnected).
        We must reconnect before the next start() otherwise the first cycle
        will fail with "not connected".
        """
        with self._lock:
            if self._backend_state != BackendState.FAULTED:
                return False
            self._error = None
            self._startup_error = None
            runner = self._runner

        # Reconnect hardware — mirrors the recheck_hardware() connect path
        if runner is not None:
            try:
                runner.connect()
                runner.verify()
                if hasattr(runner, "clear_fault"):
                    runner.clear_fault()
                logger.info("RuntimeControlService: hardware reconnected after fault confirmation")
            except Exception as exc:
                logger.error("RuntimeControlService: reconnect after confirm_fault failed: %s", exc)
                self.set_startup_error(str(exc))
                return False

        with self._lock:
            self._backend_state = BackendState.READY
            if self._state == RuntimeState.EMERGENCY:
                self._state = RuntimeState.IDLE
        self._notify_state()
        return True

    # ── Status ────────────────────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        """Return a JSON-serialisable status dict."""
        with self._lock:
            runner = self._runner
            runner_info = (
                runner.status_snapshot() if runner and hasattr(runner, "status_snapshot") else None
            )

            # Base config values
            hz = 30.0

            # 1. Use live runtime if it exists (it's the most real-time truth)
            if runner_info is not None:
                hz = runner_info["control_frequency_hz"]
                available_tasks = runner_info["available_tasks"]
                planned_task = runner_info["planned_task"]
                planned_boundaries = runner_info["planned_boundaries"]
                active_task = runner_info["active_task"]
                active_boundaries = runner_info["active_boundaries"]
                cycle_count = runner_info["cycle_count"]

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
                active_task = planned_task
                active_boundaries = planned_boundaries
                cycle_count = self._cycle_count

            # 3. Last resort defaults
            else:
                available_tasks = []
                planned_task = None
                planned_boundaries = []
                active_task = None
                active_boundaries = []
                cycle_count = self._cycle_count

            return {
                "state": self._state.value,
                "backend_state": self._backend_state.value,
                "cycle_count": cycle_count,
                "error": self._error,
                "startup_error": self._startup_error,
                "has_runtime": runner is not None,
                "active_task": active_task,
                "active_boundaries": active_boundaries,
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

    @staticmethod
    def _runtime_state_for_runner(status: RunnerStatus) -> RuntimeState:
        return RuntimeState(status.value)

    def _handle_runner_cycle(self, _result: Any) -> None:
        with self._lock:
            if self._runner is not None and hasattr(self._runner, "cycle_count"):
                self._cycle_count = self._runner.cycle_count

    def _handle_runner_fault(self, reason: str) -> None:
        with self._lock:
            self._error = reason
            self._backend_state = BackendState.FAULTED
            self._state = RuntimeState.EMERGENCY
        self._notify_state()

    def _handle_runner_finished(self, status: RunnerStatus) -> None:
        with self._lock:
            if self._state == RuntimeState.EMERGENCY and status == RunnerStatus.STOPPED:
                return
            self._state = self._runtime_state_for_runner(status)
            if self._runner is not None and hasattr(self._runner, "cycle_count"):
                self._cycle_count = self._runner.cycle_count
        self._notify_state()

    def _notify_state(self) -> None:
        if self._on_state_change is not None:
            with contextlib.suppress(Exception):
                self._on_state_change(self._state)

        if self._on_status_broadcast is not None:
            with self._lock:
                runner = self._runner
                info = (
                    runner.status_snapshot()
                    if runner and hasattr(runner, "status_snapshot")
                    else {}
                )
                planned_task = info.get("planned_task")
                planned_boundaries = info.get("planned_boundaries", [])
                cycle_count = info.get("cycle_count", self._cycle_count)
            msg = {
                "type": "system_status",
                "state": self._state,
                "backend_state": self._backend_state,
                "error": self._error or self._startup_error,
                # Send startup_error explicitly (even when None) so a successful
                # recheck clears the frontend's blocking-overlay flag — without
                # this key the spread-merge on the client side keeps the stale
                # value and the UI stays stuck on "hardware not connected".
                "startup_error": self._startup_error,
                "message": self._error or self._startup_error or f"System state: {self._state}",
                "cycle_count": cycle_count,
                "planned_task": planned_task,
                "planned_boundaries": planned_boundaries,
            }
            with contextlib.suppress(Exception):
                self._on_status_broadcast(msg)
