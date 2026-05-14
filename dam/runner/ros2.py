"""ROS2Runner — high-level runner wiring ROS2 hardware to GuardRuntime."""

from __future__ import annotations

import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any

from dam.runner.base import RuntimeLoopRunner
from dam.types.risk import CycleResult

if TYPE_CHECKING:
    from dam.runtime.guard_runtime import GuardRuntime

logger = logging.getLogger(__name__)


class ROS2Runner(RuntimeLoopRunner):
    """
    High-level runner that:
    1. Builds GuardRuntime from a Stackfile
    2. Connects ROS2 Source/Sink/Policy adapters
    3. Runs the control loop at target frequency (using rclpy timer if available,
       or a plain Python loop as fallback)
    """

    def __init__(
        self,
        runtime: GuardRuntime,
        source: Any,
        sink: Any,
        policy: Any,
        node: Any = None,
        timer_period_s: float = 0.02,
        source_name: str = "ros2",
    ) -> None:
        super().__init__(runtime, control_frequency_hz=1.0 / timer_period_s)
        self._runtime.register_source(source_name, source)
        self._runtime.register_sink(sink)
        self._runtime.register_policy(policy)
        self._node = node
        self._timer_period_s = timer_period_s
        self._timer: Any | None = None

    def start_task(self, task_name: str) -> None:
        """Activate a task in the runtime."""
        super().start_task(task_name)

    def stop(self) -> bool:
        """Graceful stop: cancel timer, stop task."""
        if self._timer is not None:
            with contextlib.suppress(Exception):
                self._timer.cancel()
            self._timer = None
        stopped = super().stop()
        logger.info("ROS2Runner stopped.")
        return stopped

    def step(self) -> CycleResult:
        """Execute one control cycle via the runtime."""
        return super().step()

    # ── BaseRunner abstract methods ────────────────────────────────────────

    def connect(self) -> None:
        """Connect all source/sink adapters."""
        self._mark_connected()
        for src in self._runtime._sources.values():
            if hasattr(src, "connect"):
                src.connect()
        sink = getattr(self._runtime, "_sink", None)
        if sink is not None and hasattr(sink, "connect"):
            sink.connect()

    def verify(self) -> None:
        """No-op preflight — ROS2 connectivity is checked lazily on first read."""
        return None

    def shutdown(self) -> None:
        """Alias for stop() — BaseRunner contract."""
        if self._timer is not None:
            with contextlib.suppress(Exception):
                self._timer.cancel()
            self._timer = None
        super().shutdown()

    def run(self, task: str, n_cycles: int = -1) -> list[CycleResult]:
        """Run the control loop for ``n_cycles`` cycles (or forever if -1).

        If a rclpy node is available, uses a timer callback; otherwise uses
        a plain Python loop (suitable for testing and simulation).

        Args:
            task:     Task name to activate.
            n_cycles: Number of cycles to run (-1 = run until stop() called).

        Returns:
            List of CycleResult from each cycle.
        """
        self.start_task(task)
        results: list[CycleResult] = []
        cycle = 0

        # Try to use rclpy timer if node is available
        _rclpy_available = False
        try:
            import rclpy

            _rclpy_available = True
        except ImportError:
            pass

        if _rclpy_available and self._node is not None:
            # rclpy-based timer loop
            def _timer_cb() -> None:
                nonlocal cycle
                if self.status.value != "running":
                    return
                result = self.step()
                results.append(result)
                cycle += 1
                if n_cycles != -1 and cycle >= n_cycles:
                    self.stop()

            self._timer = self._node.create_timer(self._timer_period_s, _timer_cb)
            try:
                import rclpy

                rclpy.spin(self._node)
            except KeyboardInterrupt:
                logger.info("ROS2Runner: spin interrupted by user")
            finally:
                self.stop()
        else:
            # Plain Python loop fallback (no rclpy)
            try:
                while self.status.value == "running":
                    t0 = time.perf_counter()
                    result = self.step()
                    results.append(result)
                    cycle += 1
                    if n_cycles != -1 and cycle >= n_cycles:
                        break
                    elapsed = time.perf_counter() - t0
                    sleep = self._timer_period_s - elapsed
                    if sleep > 0:
                        time.sleep(sleep)
            except StopIteration:
                logger.info("ROS2Runner: source exhausted after %d cycles", cycle)
            except KeyboardInterrupt:
                logger.info("ROS2Runner: interrupted by user")
            finally:
                self.stop()

        return results
