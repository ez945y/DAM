"""ROS2Runner — high-level runner wiring ROS2 hardware to GuardRuntime."""

from __future__ import annotations

import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any

from dam.types.risk import CycleResult

if TYPE_CHECKING:
    from dam.runtime.guard_runtime import GuardRuntime

logger = logging.getLogger(__name__)


class ROS2Runner:
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
    ) -> None:
        self._runtime = runtime
        self._runtime.register_source(source)
        self._runtime.register_sink(sink)
        self._runtime.register_policy(policy)
        self._node = node
        self._timer_period_s = timer_period_s
        self._running = False
        self._active_task: str | None = None
        self._timer: Any | None = None

    @classmethod
    def from_stackfile(cls, path: str, node: Any = None, policy_obj: Any = None) -> ROS2Runner:
        """Build a ROS2Runner from a Stackfile YAML.

        Reads ``hardware.ros2.*`` keys if present.  Gracefully degrades if
        rclpy is not installed — uses mock adapters.

        Args:
            path:       Path to stackfile.yaml
            node:       Optional rclpy Node object (duck-typed; None for testing)
            policy_obj: Optional policy object with predict() / select_action()
        """
        from dam.adapter.ros2.sink import ROS2SinkAdapter
        from dam.adapter.ros2.source import ROS2SourceAdapter
        from dam.config.loader import StackfileLoader
        from dam.runtime.guard_runtime import GuardRuntime

        config = StackfileLoader.load(path)
        runtime = GuardRuntime.from_stackfile(path)

        # Extract ROS2-specific config if present
        joint_state_topic = "/joint_states"
        action_topic = "/arm_controller/joint_trajectory"
        ee_topic = "/tool_pose"
        timer_period_s = 0.02

        if config.hardware is not None:
            hw = config.hardware
            # Look for ros2 sub-config via model's extra fields
            ros2_cfg = getattr(hw, "ros2", None)
            if ros2_cfg is not None:
                joint_state_topic = getattr(ros2_cfg, "joint_state_topic", joint_state_topic)
                action_topic = getattr(ros2_cfg, "action_topic", action_topic)
                ee_topic = getattr(ros2_cfg, "ee_topic", ee_topic)
                timer_period_s = getattr(ros2_cfg, "timer_period_s", timer_period_s)

        if config.runtime is not None:
            timer_period_s = 1.0 / config.runtime.control_frequency_hz

        source = ROS2SourceAdapter(
            node=node,
            joint_state_topic=joint_state_topic,
            ee_topic=ee_topic,
        )
        sink = ROS2SinkAdapter(node=node, action_topic=action_topic)

        # Use policy_obj if provided; otherwise use a no-op policy
        if policy_obj is None:
            from dam.adapter.ros2._noop_policy import NoOpPolicyAdapter

            policy = NoOpPolicyAdapter()
        else:
            policy = policy_obj

        return cls(
            runtime=runtime,
            source=source,
            sink=sink,
            policy=policy,
            node=node,
            timer_period_s=timer_period_s,
        )

    def start_task(self, task_name: str) -> None:
        """Activate a task in the runtime."""
        self._runtime.start_task(task_name)
        self._active_task = task_name
        self._running = True

    def stop(self) -> None:
        """Graceful stop: cancel timer, stop task."""
        self._running = False
        if self._timer is not None:
            with contextlib.suppress(Exception):
                self._timer.cancel()
            self._timer = None
        self._runtime.stop_task()
        self._active_task = None
        logger.info("ROS2Runner stopped.")

    def step(self) -> CycleResult:
        """Execute one control cycle via the runtime."""
        return self._runtime.step()

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
                if not self._running:
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
                while self._running:
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
