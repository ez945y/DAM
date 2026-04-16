"""LeRobotRunner — high-level runner wiring lerobot hardware to GuardRuntime.

Three construction paths, ordered from most to least automatic:

1. ``from_stackfile_auto(path)``
   Reads hardware/policy config from the Stackfile and calls LeRobotBuilder to
   instantiate the real lerobot robot and policy objects.  Requires lerobot
   installed and correct hardware config in the YAML.

2. ``from_stackfile(path, robot, policy_obj)``
   Accepts pre-built robot and policy objects (e.g. constructed in your own
   deploy script) and wires them to a GuardRuntime built from the Stackfile.
   Use this when you need fine-grained control over robot construction.

3. ``LeRobotRunner(runtime, source, sink, policy)``
   Fully manual — supply all adapters directly.  Useful for testing.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from dam.types.risk import CycleResult

if TYPE_CHECKING:
    from dam.runtime.guard_runtime import GuardRuntime

logger = logging.getLogger(__name__)


from dam.runner.base import BaseRunner


class LeRobotRunner(BaseRunner):
    def __init__(
        self,
        runtime: GuardRuntime,
        robot: Any | None = None,
        control_frequency_hz: float = 50.0,
    ) -> None:
        self._runtime = runtime
        self._robot = robot
        self._control_frequency_hz = control_frequency_hz
        self._period_sec = 1.0 / control_frequency_hz
        self._running = False
        self._active_task: str | None = None

    @property
    def runtime(self) -> GuardRuntime:
        return self._runtime

    def connect(self) -> None:
        if self._robot and hasattr(self._robot, "connect"):
            self._robot.connect()
        logger.info("LeRobotRunner: connected hardware.")

    def verify(self) -> None:
        """Verify robot hardware and all external sources (cameras)."""
        if self._robot:
            self._preflight_check(self._robot)

        # Also verify external sources registered in the runtime (e.g. OpenCV cameras)
        if hasattr(self._runtime, "_sources"):
            for name, src in self._runtime._sources.items():
                if src is self._robot or src is getattr(self, "_robot", None):
                    continue
                if hasattr(src, "verify") and callable(src.verify):
                    logger.info("LeRobotRunner: verifying external source '%s'...", name)
                    src.verify()

    def shutdown(self) -> None:
        """Graceful stop: stop task, disconnect robot."""
        self._running = False
        try:
            self._runtime.stop_task()
        except Exception as e:
            logger.warning("LeRobotRunner: stop_task error: %s", e)
        if self._robot is not None and hasattr(self._robot, "disconnect"):
            try:
                self._robot.disconnect()
                logger.info("LeRobotRunner: robot disconnected")
            except Exception as e:
                logger.warning("LeRobotRunner: robot disconnect error: %s", e)
        self._active_task = None
        logger.info("LeRobotRunner stopped.")

    # ── Construction helpers ───────────────────────────────────────────────

    @classmethod
    def from_stackfile_auto(cls, path: str) -> LeRobotRunner:
        """Build robot + policy from Stackfile hardware/policy config.

        This is the primary production entry point.  The Stackfile must contain
        a ``hardware:`` section with at least ``preset`` and
        ``sources.follower_arm.port``.

        The robot is connected immediately.  Call ``runner.stop()`` to
        disconnect cleanly.
        """
        from dam.adapter.lerobot.builder import LeRobotBuilder
        from dam.adapter.lerobot.policy import LeRobotPolicyAdapter
        from dam.adapter.lerobot.sink import LeRobotSinkAdapter
        from dam.adapter.lerobot.source import LeRobotSourceAdapter
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

        source = LeRobotSourceAdapter(
            robot,
            joint_names=preset.joint_names,
            degrees_mode=preset.degrees_mode,
        )
        sink = LeRobotSinkAdapter(
            robot,
            joint_names=preset.joint_names,
            degrees_mode=preset.degrees_mode,
        )

        runtime.register_source("arm", source)
        runtime.register_sink(sink)
        if policy_adapter:
            runtime.register_policy(policy_adapter)

        hz_cfg = cfg.runtime.control_frequency_hz if cfg.runtime else None
        hz = hz_cfg or cfg.safety.control_frequency_hz

        return cls(
            runtime=runtime,
            robot=robot,
            control_frequency_hz=hz,
        )

    @classmethod
    def from_stackfile(
        cls,
        path: str,
        robot: Any,
        policy_obj: Any,
    ) -> LeRobotRunner:
        """Build runner from Stackfile + pre-built lerobot objects.

        Use this when you have already constructed and connected the robot
        in your own deploy script and want DAM to guard the policy output.

        The robot must be connected before passing it here.
        """
        from dam.adapter.lerobot.policy import LeRobotPolicyAdapter
        from dam.adapter.lerobot.presets import get_preset
        from dam.adapter.lerobot.sink import LeRobotSinkAdapter
        from dam.adapter.lerobot.source import LeRobotSourceAdapter
        from dam.config.loader import StackfileLoader
        from dam.runtime.guard_runtime import GuardRuntime

        cfg = StackfileLoader.load(path)
        runtime = GuardRuntime.from_stackfile(path)

        preset_name = (cfg.hardware.preset if cfg.hardware else None) or "generic_6dof"
        try:
            preset = get_preset(preset_name)
        except KeyError:
            preset = get_preset("generic_6dof")

        device = (cfg.policy.device or "cpu") if cfg.policy else "cpu"
        policy_name = (cfg.policy.type or "lerobot") if cfg.policy else "lerobot"

        source = LeRobotSourceAdapter(
            robot,
            joint_names=preset.joint_names,
            degrees_mode=preset.degrees_mode,
        )
        sink = LeRobotSinkAdapter(
            robot,
            joint_names=preset.joint_names,
            degrees_mode=preset.degrees_mode,
        )
        policy = LeRobotPolicyAdapter(
            policy_obj,
            policy_name=policy_name,
            device=device,
            joint_names=preset.joint_names,
        )

        runtime.register_source("arm", source)
        runtime.register_sink(sink)
        runtime.register_policy(policy)

        hz = (
            cfg.runtime.control_frequency_hz if cfg.runtime else None
        ) or cfg.safety.control_frequency_hz

        return cls(
            runtime=runtime,
            robot=robot,
            control_frequency_hz=hz,
        )

    # ── Hardware preflight check ───────────────────────────────────────────

    @staticmethod
    def _preflight_check(robot: Any) -> None:
        """Verify cameras and motors are responsive before the control loop starts.

        Checks performed
        ----------------
        1. **Cameras** — reads one frame from every camera attached to the robot.
           A ``None`` frame or any exception is reported as a failure.
        2. **Motors** — calls ``robot.get_observation()`` to confirm all motors
           respond without errors.

        Raises ``RuntimeError`` listing every failed check so the operator can
        diagnose all issues at once rather than one at a time.
        """
        errors: list[str] = []

        # 1. Camera check
        cameras: dict = {}
        if hasattr(robot, "cameras") and robot.cameras:
            cameras = robot.cameras
        for cam_name, cam in cameras.items():
            try:
                frame = cam.read() if hasattr(cam, "read") else cam.async_read()
                if frame is None:
                    errors.append(f"camera '{cam_name}': read() returned None — check USB / index")
            except Exception as exc:
                errors.append(f"camera '{cam_name}': {exc}")

        # 2. Motor check
        try:
            obs = (
                robot.get_observation()
                if hasattr(robot, "get_observation")
                else robot.capture_observation()
            )
            if obs is None:
                errors.append("motors: get_observation() returned None")
        except Exception as exc:
            errors.append(f"motors: {exc}")

        if errors:
            bullet_list = "\n".join(f"  • {e}" for e in errors)
            raise RuntimeError(
                f"Hardware preflight check failed ({len(errors)} issue(s)):\n{bullet_list}\n"
                "Fix the above before starting the control loop."
            )

        logger.info("Hardware preflight check passed (cameras=%d, motors OK).", len(cameras))

    # ── Runtime control ────────────────────────────────────────────────────

    def start_task(self, name: str) -> None:
        self._runtime.start_task(name)
        self._active_task = name
        self._running = True

    def stop(self) -> None:
        self.shutdown()

    def step(self) -> CycleResult:
        return self._runtime.step()

    def run(
        self,
        task: str,
        n_cycles: int | None = None,
    ) -> list[CycleResult]:
        """Run control loop. Blocks until n_cycles reached, stop() called, or Ctrl-C."""
        self.connect()
        logger.info("Running hardware preflight check…")
        self.verify()

        self.start_task(task)
        results: list[CycleResult] = []
        cycle = 0
        try:
            while self._running:
                t0 = time.perf_counter()
                result = self.step()
                results.append(result)
                cycle += 1
                if n_cycles is not None and cycle >= n_cycles:
                    break
                elapsed = time.perf_counter() - t0
                sleep_t = self._period_sec - elapsed
                if sleep_t > 0:
                    time.sleep(sleep_t)
        except StopIteration:
            logger.info("Source exhausted after %d cycles.", cycle)
        except KeyboardInterrupt:
            logger.info("Run interrupted by user.")
        finally:
            self.stop()
        return results
