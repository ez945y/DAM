"""LeRobotRunner — high-level runner wiring lerobot hardware to GuardRuntime.

Three construction paths, ordered from most to least automatic:

1. ``from_stackfile(path)``
   Reads hardware/policy config from the Stackfile and calls LeRobotBuilder to
   instantiate the real lerobot robot and policy objects.  Requires lerobot
   installed and correct hardware config in the YAML.

2. ``LeRobotRunner(runtime, source, sink, policy)``
   Fully manual — supply all adapters directly.  Useful for testing.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from dam.types.risk import CycleResult

if TYPE_CHECKING:
    from dam.runtime.guard_runtime import GuardRuntime

logger = logging.getLogger(__name__)


from dam.runner.base import RuntimeLoopRunner


class LeRobotRunner(RuntimeLoopRunner):
    def __init__(
        self,
        runtime: GuardRuntime,
        control_frequency_hz: float = 30.0,
        robot: Any | None = None,
    ) -> None:
        super().__init__(runtime, control_frequency_hz=control_frequency_hz)
        self._robot = robot

    def shutdown(self) -> None:
        """Graceful stop: stop task, disconnect robot."""
        already_shutdown = self._shutdown_complete
        super().shutdown()
        if not already_shutdown:
            logger.info("LeRobotRunner stopped.")

    # ── Construction helpers ───────────────────────────────────────────────

    @classmethod
    def from_stackfile(cls, path: str) -> LeRobotRunner:
        """Build robot + policy from Stackfile hardware/policy config.

        This is the primary production entry point.  The Stackfile must contain
        a ``hardware:`` section with at least ``preset`` and a
        lerobot-typed source with a ``port``.

        The robot is connected immediately.  Call ``runner.stop()`` to
        disconnect cleanly.
        """
        from dam.adapter.lerobot.builder import LeRobotBuilder
        from dam.adapter.lerobot.policy import LeRobotPolicyAdapter
        from dam.adapter.lerobot.adapter import LeRobotAdapter
        from dam.config.loader import StackfileLoader
        from dam.runtime.guard_runtime import GuardRuntime

        cfg = StackfileLoader.load(path)
        if cfg.hardware is None:
            raise ValueError(
                "Stackfile missing 'hardware:' section. "
                "Use from_stackfile(path, robot, policy_obj) to supply objects manually."
            )

        builder = LeRobotBuilder(cfg.hardware, cfg.policy)
        preset = builder.preset

        robot = builder.build_robot()
        # robot.connect() is now deferred to runner.connect()

        policy_obj = builder.build_policy()
        if policy_obj is None:
            from dam.testing.mocks import MockPolicyAdapter

            logger.warning(
                "No policy pretrained_path — using MockPolicyAdapter (zero actions). "
                "Set policy.pretrained_path in your Stackfile for real inference."
            )
            policy_adapter: Any = MockPolicyAdapter([])
        else:
            device = (cfg.policy.device or "cpu") if cfg.policy else "cpu"
            policy_name = (cfg.policy.type or "lerobot") if cfg.policy else "lerobot"
            policy_adapter = LeRobotPolicyAdapter(
                policy_obj,
                policy_name=policy_name,
                device=device,
                joint_names=preset.joint_names,
            )

        runtime = GuardRuntime.from_stackfile(path)

        adapter = LeRobotAdapter(
            robot,
            joint_names=preset.joint_names,
            degrees_mode=preset.degrees_mode,
        )

        runtime.register_source("arm", adapter)
        runtime.register_sink(adapter)
        if policy_adapter:
            runtime.register_policy(policy_adapter)

        hz_cfg = cfg.runtime.control_frequency_hz if cfg.runtime else None
        hz = hz_cfg or cfg.safety.control_frequency_hz

        return cls(
            runtime=runtime,
            control_frequency_hz=hz,
        )


    # ── Runtime control ────────────────────────────────────────────────────

    def connect(self) -> None:
        self._mark_connected()
        for src in self._runtime._sources.values():
            if hasattr(src, "connect"):
                src.connect()

    def verify(self) -> None:
        for src in self._runtime._sources.values():
            if hasattr(src, "verify"):
                src.verify()

    def step(self) -> CycleResult:
        return super().step()
