"""RuntimeFactory — constructs a fully-wired GuardRuntime from a Stackfile.

This factory handles:
  1. Parsing the Stackfile YAML
  2. Resolving the appropriate hardware adapters (LeRobot, ROS2, or Simulation)
  3. Configuring safety layers (L0-L4) and boundaries
  4. Wiring up the Source, Policy, and Sink
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import yaml

from dam.config.schema import StackfileConfig
from dam.runtime.guard_runtime import GuardRuntime

if TYPE_CHECKING:
    from dam.runner.base import BaseRunner

logger = logging.getLogger(__name__)


class RuntimeFactory:
    @staticmethod
    def build_from_stackfile(path: str, use_sim_fallback: bool = False) -> BaseRunner:
        """Build a Runner from the given stackfile path."""
        from dam.runner.base import SimulationRunner

        with open(path) as f:
            raw = yaml.safe_load(f)

        config = StackfileConfig(**raw)

        # 1. Determine Adapter Type
        adapter_type = None
        hw_config = config.hardware
        if hw_config:
            if hw_config.preset == "simulation":
                adapter_type = "simulation"
            elif hw_config.sources:
                first_src = next(iter(hw_config.sources.values()))
                adapter_type = str(first_src.type or "simulation").lower()

        if not adapter_type:
            raise ValueError(
                "No valid hardware configuration found. Please specify 'preset: simulation' "
                "or a peripheral type (e.g., 'type: lerobot') in the stackfile."
            )

        logger.info("Building runtime with adapter type: %s", adapter_type)

        if adapter_type == "lerobot":
            return RuntimeFactory._build_lerobot(config, path)
        elif adapter_type == "ros2":
            raise NotImplementedError("ROS2 runner not implemented")

        # Explicit Simulation or fall-through — reuse already-parsed config
        runtime = GuardRuntime._from_config(config)
        source, policy, sink = RuntimeFactory._build_simulation(config)
        runtime.register_source("main", source)
        if policy:
            runtime.register_policy(policy)
        runtime.register_sink(sink)

        hz = config.safety.control_frequency_hz if config.safety else 10.0
        return SimulationRunner(runtime, control_frequency_hz=hz)

    @staticmethod
    def _build_lerobot(config: StackfileConfig, path: str) -> BaseRunner:
        from dam.adapter.lerobot.builder import LeRobotBuilder
        from dam.adapter.lerobot.policy import LeRobotPolicyAdapter
        from dam.runner.lerobot import LeRobotRunner
        from dam.runtime.guard_runtime import GuardRuntime

        assert config.hardware is not None
        runtime = GuardRuntime._from_config(config)  # reuse already-parsed config
        hz = config.safety.control_frequency_hz if config.safety else 50.0
        builder = LeRobotBuilder(config.hardware, config.policy, control_frequency_hz=hz)
        robot = builder.build_robot()

        # Build adapters
        use_unified = False
        sinks = config.hardware.sinks or {}
        for sink_cfg in sinks.values():
            if hasattr(sink_cfg, "ref") and sink_cfg.ref and sink_cfg.ref.startswith("sources."):
                use_unified = True
                break

        source: Any
        sink: Any
        if use_unified:
            from dam.adapter.lerobot.adapter import LeRobotAdapter

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
        policy = None
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

        # Identify main source name (usually the first lerobot one)
        main_name = "arm"
        if config.hardware.sources:
            for name, s in config.hardware.sources.items():
                if str(s.type).lower() == "lerobot":
                    main_name = name
                    break

        runtime.register_source(main_name, source)
        runtime.register_sink(sink)
        if policy:
            runtime.register_policy(policy)

        # ── DISCOVER OTHER SOURCES (e.g. External OpenCV Cameras) ───────
        if config.hardware.sources:
            for name, src_cfg in config.hardware.sources.items():
                if name == main_name:
                    continue  # already registered

                type_str = str(src_cfg.type).lower()
                if type_str in ("opencv", "camera", "usb"):
                    from dam.adapter.opencv.source import OpenCVSourceAdapter

                    # Robustly extract index from direct field, params, or model_extra
                    idx = 0
                    if hasattr(src_cfg, "index") and src_cfg.index is not None:
                        idx = src_cfg.index
                    elif hasattr(src_cfg, "index_or_path") and src_cfg.index_or_path is not None:
                        idx = src_cfg.index_or_path
                    elif (
                        hasattr(src_cfg, "params") and src_cfg.params and "index" in src_cfg.params
                    ):
                        idx = src_cfg.params["index"]
                    elif (
                        hasattr(src_cfg, "model_extra")
                        and src_cfg.model_extra
                        and "index" in src_cfg.model_extra
                    ):
                        idx = src_cfg.model_extra["index"]
                    elif isinstance(src_cfg, dict):
                        idx = src_cfg.get("index", src_cfg.get("index_or_path", 0))

                    cam_adapter = OpenCVSourceAdapter(index=idx, name=name)
                    runtime.register_source(name, cam_adapter)
                    logger.info("Registered extra source: %s (type=%s)", name, type_str)

        return LeRobotRunner(runtime=runtime, robot=robot, control_frequency_hz=hz)

    @staticmethod
    def _build_ros2(config: StackfileConfig) -> BaseRunner:
        raise NotImplementedError("ROS2 adapter factory not implemented yet")

    @staticmethod
    def _build_simulation(config: StackfileConfig) -> tuple[Any, Any, Any]:
        from dam.testing.sim_adapters import SimSink

        hz = float(config.safety.control_frequency_hz) if config.safety else 10.0

        # ── Source ────────────────────────────────────────────────────────
        # Try to find simulation source config in hardware.sources or legacy top-level simulation
        sim_cfg = config.simulation
        source_cfg = None
        if config.hardware and config.hardware.sources:
            # Find first source of type 'dataset' or 'simulation'
            for _name, s in config.hardware.sources.items():
                if str(s.type).lower() in ("dataset", "simulation", "mock"):
                    source_cfg = s
                    break

        dataset_repo = None
        if source_cfg:
            dataset_repo = getattr(source_cfg, "dataset_repo_id", None)
            extra = getattr(source_cfg, "model_extra", {})
            if not dataset_repo and extra:
                dataset_repo = extra.get("dataset_repo_id")

        if not dataset_repo and sim_cfg:
            dataset_repo = getattr(sim_cfg, "dataset_repo_id", None)

        source: Any
        if dataset_repo:
            from dam.testing.dataset_source import DatasetSimSource

            # Map parameters from either source_cfg or legacy sim_cfg
            episode = 0
            degrees_mode = True
            if source_cfg:
                episode = getattr(source_cfg, "episode", 0)
                degrees_mode = getattr(source_cfg, "degrees_mode", True)
                extra = getattr(source_cfg, "model_extra", {})
                if not episode and "episode" in extra:
                    episode = extra["episode"]
            elif sim_cfg:
                episode = getattr(sim_cfg, "episode", 0)
                degrees_mode = getattr(sim_cfg, "degrees_mode", True)

            source = DatasetSimSource(
                repo_id=dataset_repo,
                episode=episode,
                hz=hz,
                degrees_mode=degrees_mode,
            )
        else:
            # Fallback to random walk sim if no dataset provided
            from dam.testing.sim_adapters import SimSource

            logger.info("Simulation: using SimSource (random walk)")
            source = SimSource(hz=hz)

        # ── Policy ────────────────────────────────────────────────────────
        policy: Any = None
        if config.policy and config.policy.pretrained_path:
            try:
                from dam.adapter.lerobot.builder import LeRobotBuilder
                from dam.adapter.lerobot.policy import LeRobotPolicyAdapter
                from dam.config.schema import HardwareConfig

                # Build a stub HardwareConfig so LeRobotBuilder is satisfied
                fake_hw = HardwareConfig(preset="so101_follower")
                builder = LeRobotBuilder(fake_hw, config.policy)
                policy_res = builder.build_policy()
                if policy_res:
                    if isinstance(policy_res, tuple):
                        p_obj, pre, post = policy_res
                    else:
                        p_obj, pre, post = policy_res, None, None
                    policy = LeRobotPolicyAdapter(
                        p_obj,
                        preprocessor=pre,
                        postprocessor=post,
                        joint_names=builder.joint_names,
                        device=config.policy.device,
                    )
                    logger.info("Simulation: loaded real policy %s", config.policy.pretrained_path)
            except Exception as exc:
                logger.warning(
                    "Simulation: policy load failed (%s), falling back to SimPolicy", exc
                )

        if policy is None:
            from dam.testing.sim_adapters import SimPolicy

            policy = SimPolicy()
            logger.info("Simulation: using SimPolicy (random action fallback)")

        sink = SimSink()
        return source, policy, sink
