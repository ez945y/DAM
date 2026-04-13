"""RuntimeFactory — constructs a fully-wired GuardRuntime from a Stackfile.

This factory handles:
  1. Parsing the Stackfile YAML
  2. Resolving the appropriate hardware adapters (LeRobot, ROS2, or Simulation)
  3. Configuring safety layers (L0-L4) and boundaries
  4. Wiring up the Source, Policy, and Sink
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import yaml

from dam.config.schema import StackfileConfig
from dam.runtime.guard_runtime import GuardRuntime

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class RuntimeFactory:
    @staticmethod
    def build_from_stackfile(path: str, use_sim_fallback: bool = False) -> GuardRuntime:
        """Build a GuardRuntime from the given stackfile path.

        If use_sim_fallback is True, it will attempt to use Simulation adapters
        if hardware validation fails.
        """
        with open(path) as f:
            raw = yaml.safe_load(f)

        config = StackfileConfig(**raw)
        runtime = GuardRuntime.from_stackfile(path)

        # 1. Determine Adapter Type
        adapter_type = "simulation"
        hw_config = config.hardware
        if hw_config and hw_config.sources:
            # Check the first source to determine type
            first_src = next(iter(hw_config.sources.values()))
            # HardwareSourceConfig is a Pydantic object, access via attribute
            adapter_type = str(first_src.type or "simulation").lower()

        logger.info("Building runtime with adapter type: %s", adapter_type)

        try:
            if adapter_type == "lerobot":
                source, policy, sink = RuntimeFactory._build_lerobot(config)
            elif adapter_type == "ros2":
                source, policy, sink = RuntimeFactory._build_ros2(config)
            else:
                source, policy, sink = RuntimeFactory._build_simulation(config)
        except Exception as e:
            if use_sim_fallback:
                logger.warning("Hardware initialization failed, falling back to simulation: %s", e)
                source, policy, sink = RuntimeFactory._build_simulation(config)
            else:
                raise

        runtime.register_source(source)
        if policy:
            runtime.register_policy(policy)
        runtime.register_sink(sink)

        return runtime

    @staticmethod
    def _build_lerobot(config: StackfileConfig):
        from dam.adapter.lerobot.adapter import LeRobotAdapter
        from dam.adapter.lerobot.builder import LeRobotBuilder
        from dam.adapter.lerobot.policy import LeRobotPolicyAdapter

        builder = LeRobotBuilder(config.hardware, config.policy)
        robot = builder.build_robot()

        # Determine if we should use a unified adapter (shared node)
        # We use a unified adapter if the sink is a reference to a source
        use_unified = False
        sinks = config.hardware.sinks or {}
        for sink_cfg in sinks.values():
            if hasattr(sink_cfg, "ref") and sink_cfg.ref and sink_cfg.ref.startswith("sources."):
                use_unified = True
                break

        if use_unified:
            logger.info("LeRobot: Using unified Source/Sink adapter (shared node)")
            hw_adapter = LeRobotAdapter(
                robot,
                joint_names=builder.joint_names,
                degrees_mode=builder.preset.degrees_mode,
                urdf_path=config.hardware.urdf_path,
            )
            source = hw_adapter
            sink = hw_adapter
        else:
            from dam.adapter.lerobot.sink import LeRobotSinkAdapter
            from dam.adapter.lerobot.source import LeRobotSourceAdapter

            source = LeRobotSourceAdapter(
                robot,
                joint_names=builder.joint_names,
                degrees_mode=builder.preset.degrees_mode,
                urdf_path=config.hardware.urdf_path,
            )
            sink = LeRobotSinkAdapter(
                robot, joint_names=builder.joint_names, degrees_mode=builder.preset.degrees_mode
            )

        policy_res = builder.build_policy()
        if policy_res:
            if isinstance(policy_res, tuple):
                p_obj, pre, post = policy_res
                policy = LeRobotPolicyAdapter(
                    p_obj,
                    preprocessor=pre,
                    postprocessor=post,
                    joint_names=builder.joint_names,
                    device=config.policy.device if config.policy else "cpu",
                )
            else:
                policy = LeRobotPolicyAdapter(policy_res, joint_names=builder.joint_names)
        else:
            policy = None

        return source, policy, sink

    @staticmethod
    def _build_ros2(config: StackfileConfig):
        # Implementation for ROS2 adapters would go here
        raise NotImplementedError("ROS2 adapter factory not implemented yet")

    @staticmethod
    def _build_simulation(config: StackfileConfig):
        from dam.testing.sim_adapters import SimPolicy, SimSink, SimSource

        hz = float(config.safety.control_frequency_hz) if config.safety else 10.0
        source = SimSource(hz=hz)
        policy = SimPolicy()
        sink = SimSink()

        return source, policy, sink
