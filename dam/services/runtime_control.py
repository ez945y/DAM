"""Runtime Control Service — start/pause/resume/stop/E-Stop for GuardRuntime."""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable
from enum import StrEnum
from typing import Any

from dam.types.result import GuardDecision

logger = logging.getLogger(__name__)


class RuntimeState(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    EMERGENCY = "emergency"


class RuntimeControlService:
    """Thread-safe control wrapper around GuardRuntime.

    The service holds a reference to a GuardRuntime and exposes
    start / pause / resume / stop / emergency_stop methods that
    are safe to call from the REST API (a different thread from
    the control loop).

    Pause/Resume are implemented via a threading.Event gate that the
    run loop polls.  The actual ``GuardRuntime.run()`` call is launched
    in a daemon thread managed by this service.
    """

    def __init__(self) -> None:
        self._runtime: Any | None = None
        self._stack_path: str | None = None
        self._post_step_wrapper: Callable[[Callable], Callable] | None = None
        self._state = RuntimeState.IDLE
        self._lock = threading.Lock()
        self._pause_event = threading.Event()
        self._pause_event.set()  # not paused initially
        self._run_thread: threading.Thread | None = None
        self._on_state_change: Callable[[RuntimeState], None] | None = None
        self._cycle_count: int = 0
        self._error: str | None = None
        # Set by dev_server when hardware validation fails at startup.
        # While set, start() is blocked and the frontend shows a blocking overlay.
        self._startup_error: str | None = None

    def set_startup_error(self, message: str) -> None:
        """Mark the service as having a hardware/startup error.

        While this is set the runtime cannot be started.  The frontend will
        display a blocking overlay with ``message`` until the user resolves
        the issue (e.g. connects hardware and restarts).
        """
        with self._lock:
            self._startup_error = message
            logger.warning("RuntimeControlService: startup_error set: %s", message)

    # ── Registration ──────────────────────────────────────────────────────────

    def attach_runtime(self, runtime: Any, stack_path: str | None = None) -> None:
        """Attach a GuardRuntime instance and optionally its source stackfile path."""
        self._runtime = runtime
        if stack_path:
            self._stack_path = stack_path

    def set_stack_path(self, stack_path: str) -> None:
        """Explicitly set the stackfile path for recheck capability."""
        self._stack_path = stack_path

    def set_post_step_wrapper(self, wrapper: Callable[[Callable], Callable]) -> None:
        """Register a function that wraps runtime.step with instrumentation.

        This is used to ensure that telemetry/risk logging persist even after
        hardware is re-checked/re-initialized.
        """
        self._post_step_wrapper = wrapper

    def on_state_change(self, callback: Callable[[RuntimeState], None]) -> None:
        """Register a callback called when runtime state changes."""
        self._on_state_change = callback

    # ── Commands ──────────────────────────────────────────────────────────────

    def start(
        self, task_name: str = "default", n_cycles: int = -1, cycle_budget_ms: float = 20.0
    ) -> bool:
        """Launch the control loop in a background daemon thread.

        Returns:
            True if started, False if already running.
        """
        with self._lock:
            if self._startup_error:
                raise RuntimeError(f"Cannot start: {self._startup_error}")
            if self._state == RuntimeState.RUNNING:
                logger.warning("RuntimeControlService.start(): already running")
                return False
            if self._runtime is None:
                raise RuntimeError("No GuardRuntime attached. Call attach_runtime() first.")
            self._state = RuntimeState.RUNNING
            self._error = None
            self._pause_event.set()

        try:
            if hasattr(self._runtime, "start_task"):
                self._runtime.start_task(task_name)
        except KeyError:
            pass  # task not found, continue anyway

        self._run_thread = threading.Thread(
            target=self._run_loop,
            args=(n_cycles, cycle_budget_ms),
            daemon=False,
            name="dam-runtime-loop",
        )
        self._run_thread.start()
        self._notify_state()
        return True

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
            if self._state not in (RuntimeState.RUNNING, RuntimeState.PAUSED):
                return False
            self._state = RuntimeState.STOPPED
        self._pause_event.set()  # unblock if paused
        if self._runtime is not None and hasattr(self._runtime, "stop"):
            self._runtime.stop()
        self._notify_state()
        return True

    def emergency_stop(self) -> bool:
        """Immediate emergency stop — triggers sink emergency_stop if available."""
        with self._lock:
            self._state = RuntimeState.EMERGENCY
        self._pause_event.set()
        if self._runtime is not None:
            if hasattr(self._runtime, "stop"):
                self._runtime.stop()
            if hasattr(self._runtime, "_sink") and self._runtime._sink is not None:
                try:
                    self._runtime._sink.emergency_stop()
                except Exception as e:
                    logger.error("E-Stop sink error: %s", e)
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
        except Exception:
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

        # Reload site-packages so the newly installed lerobot is importable
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
            logger.error("RuntimeControlService: lerobot still not importable after setup.")
            return False

    def recheck_hardware(self, stack_path: str | None = None) -> bool:
        """Attempt to re-initialize hardware adapters from the stackfile.

        If the stackfile requires lerobot but it is not installed, the method
        automatically runs ``scripts/setup.sh --lerobot`` before proceeding.

        Returns:
            True if hardware was successfully re-initialized and connected.
        """
        import importlib

        import dam.runtime.factory

        path = stack_path or self._stack_path
        if not path:
            logger.error("RuntimeControlService: Cannot re-check hardware, no stack_path known.")
            return False

        # Detect adapter type and install missing dependencies before any import
        adapter_type = self._detect_adapter_type(path)
        if adapter_type == "lerobot" and not self._ensure_lerobot_installed():
            self.set_startup_error(
                "lerobot is not installed and automatic setup failed. "
                "Run `make setup-lerobot` manually and restart."
            )
            return False

        try:
            # Force reload modules to pick up potential code fixes (Import Cache busting)
            importlib.reload(dam.runtime.factory)
            if adapter_type == "lerobot":
                import dam.adapter.lerobot.builder
                import dam.adapter.lerobot.policy
                import dam.adapter.lerobot.sink
                import dam.adapter.lerobot.source

                importlib.reload(dam.adapter.lerobot.builder)
                importlib.reload(dam.adapter.lerobot.source)
                importlib.reload(dam.adapter.lerobot.sink)
                importlib.reload(dam.adapter.lerobot.policy)

            from dam.runtime.factory import RuntimeFactory

            logger.info("RuntimeControlService: Re-checking hardware from %s", path)

            # Properly shutdown old runtime to release hardware before re-init
            with self._lock:
                if self._runtime:
                    try:
                        self._runtime.shutdown()
                    except Exception as e:
                        logger.debug("Old runtime shutdown failed during recheck: %s", e)

            new_runtime = RuntimeFactory.build_from_stackfile(path)

            # Apply instrumentation if registered
            if self._post_step_wrapper:
                new_runtime.step = self._post_step_wrapper(new_runtime.step)

            with self._lock:
                self._runtime = new_runtime
                self._startup_error = None  # Clear previous error
                self._error = None
                self._state = RuntimeState.IDLE

            # Attempt connection
            source = getattr(new_runtime, "_source", None)
            if hasattr(source, "connect"):
                source.connect()

            self._notify_state()
            logger.info("RuntimeControlService: Hardware successfully re-connected.")
            return True

        except Exception as e:
            error_msg = str(e)
            logger.error("RuntimeControlService: Re-check failed: %s", error_msg)
            self.set_startup_error(error_msg)
            return False

    # ── Status ────────────────────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        """Return a JSON-serialisable status dict."""
        with self._lock:
            rt = self._runtime
            task_config: dict[str, Any] = getattr(rt, "_task_config", {})
            available_tasks = list(task_config.keys())
            # Determine the default planned task (used when idle)
            planned_task = (
                "default"
                if "default" in task_config
                else (available_tasks[0] if available_tasks else None)
            )
            planned_boundaries: list = (
                list(task_config.get(planned_task, [])) if planned_task else []
            )
            return {
                "state": self._state.value,
                "cycle_count": self._cycle_count,
                "error": self._error,
                "startup_error": self._startup_error,
                "has_runtime": rt is not None,
                "active_task": getattr(rt, "_active_task", None),
                "active_boundaries": list(getattr(rt, "_active_container_names", [])),
                "control_frequency_hz": getattr(rt, "_control_frequency_hz", 50.0),
                "available_tasks": available_tasks,
                "planned_task": planned_task,
                "planned_boundaries": planned_boundaries,
                "has_rust": True,  # dam_rs is mandatory; startup fails without it
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
                if state in (RuntimeState.STOPPED, RuntimeState.EMERGENCY):
                    break
                if state == RuntimeState.PAUSED:
                    self._pause_event.wait(timeout=0.1)
                    continue

                t0 = time.perf_counter()
                try:
                    result = self._runtime.step()
                    if result and any(
                        r.decision == GuardDecision.FAULT for r in result.guard_results
                    ):
                        # Find the reason from the faulting guard
                        fault_reason = next(
                            (
                                r.reason
                                for r in result.guard_results
                                if r.decision == GuardDecision.FAULT
                            ),
                            "Unknown hardware fault",
                        )
                        logger.error(
                            "RuntimeControlService: Guard FAULT detected: %s", fault_reason
                        )
                        with self._lock:
                            self._error = fault_reason
                        self.emergency_stop()
                        break

                    with self._lock:
                        self._cycle_count += 1
                except StopIteration:
                    logger.info("RuntimeControlService: source exhausted")
                    break
                except Exception as e:
                    logger.error("RuntimeControlService: critical cycle error: %s", e)
                    with self._lock:
                        self._error = str(e)
                    # Implementation of fail-safe: any critical error triggers emergency stop
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
                if self._state == RuntimeState.RUNNING:
                    self._state = RuntimeState.STOPPED

            # Keep hardware connected even after loop stops to allow fast restart.
            # Only shutdown() (disconnect) via host-level exit or manual recheck.

            self._notify_state()

    def _notify_state(self) -> None:
        if self._on_state_change is not None:
            with contextlib.suppress(Exception):
                self._on_state_change(self._state)
