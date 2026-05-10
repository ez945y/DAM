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
    def load_config(path: str) -> StackfileConfig:
        """Parse the Stackfile YAML into a structured Config object."""
        with open(path) as f:
            raw = yaml.safe_load(f)
        return StackfileConfig(**raw)

    @staticmethod
    def build_from_stackfile(path: str) -> BaseRunner:
        """Build a Runner from the given stackfile path."""
        config = RuntimeFactory.load_config(path)
        return RuntimeFactory.build_from_config(config)

    @staticmethod
    def build_from_config(config: StackfileConfig) -> BaseRunner:
        """Build a Runner from a pre-parsed StackfileConfig object."""
        from dam.runner.base import SimulationRunner

        # 1. Determine Adapter Type — pick the first source whose type is a
        # known adapter, so peer channel sources (current, effort, …) don't
        # accidentally drive routing.
        _ADAPTER_TYPES = ("lerobot", "ros2")
        adapter_type = None
        hw_config = config.hardware
        if hw_config:
            if hw_config.preset == "simulation":
                adapter_type = "simulation"
            elif hw_config.sources:
                for src in hw_config.sources.values():
                    t = str(src.type or "").lower()
                    if t in _ADAPTER_TYPES:
                        adapter_type = t
                        break

        if not adapter_type:
            raise ValueError(
                "No valid hardware configuration found. Please specify 'preset: simulation' "
                "or a peripheral type (e.g., 'type: lerobot') in the stackfile."
            )

        logger.info("Building runtime with adapter type: %s", adapter_type)

        if adapter_type == "lerobot":
            return RuntimeFactory._build_lerobot(config)
        elif adapter_type == "ros2":
            return RuntimeFactory._build_ros2(config)

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
    def _build_lerobot(config: StackfileConfig) -> BaseRunner:
        from dam.adapter.lerobot.builder import LeRobotBuilder
        from dam.adapter.lerobot.policy import LeRobotPolicyAdapter
        from dam.runner.lerobot import LeRobotRunner
        from dam.runtime.guard_runtime import GuardRuntime

        assert config.hardware is not None
        runtime = GuardRuntime._from_config(config)  # reuse already-parsed config
        hz = config.safety.control_frequency_hz if config.safety else 50.0
        builder = LeRobotBuilder(config.hardware, config.policy, control_frequency_hz=hz)
        robot = builder.build_robot()

        # Identify main source name (usually the first lerobot one)
        main_name = "arm"
        if config.hardware.sources:
            for name, s in config.hardware.sources.items():
                if str(s.type).lower() == "lerobot":
                    main_name = name
                    break

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

        supported = source.supported_channels()
        obs_channels = RuntimeFactory._collect_channels(config, main_name, supported)
        if obs_channels:
            source.set_observation_channels(obs_channels)

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
                if type_str in supported:
                    continue  # observation channel, handled above
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
        from dam.adapter.ros2._noop_policy import NoOpPolicyAdapter
        from dam.adapter.ros2.sink import ROS2SinkAdapter
        from dam.adapter.ros2.source import ROS2SourceAdapter
        from dam.runner.ros2 import ROS2Runner

        assert config.hardware is not None
        runtime = GuardRuntime._from_config(config)
        hz = config.safety.control_frequency_hz if config.safety else 50.0

        # Identify main ros2 source (the one with type=ros2)
        main_name = "ros2"
        main_cfg: Any = None
        if config.hardware.sources:
            for name, s in config.hardware.sources.items():
                if str(s.type).lower() == "ros2":
                    main_name = name
                    main_cfg = s
                    break

        joint_topic = "/joint_states"
        ee_topic = "/tool_pose"
        action_topic = "/arm_controller/joint_trajectory"
        if main_cfg is not None:
            extra = main_cfg.model_extra or {}
            joint_topic = extra.get("joint_topic") or extra.get("topic") or joint_topic
            ee_topic = extra.get("ee_topic") or ee_topic

        # Sink topic comes from the matching sink config if present
        if config.hardware.sinks:
            for sink_cfg in config.hardware.sinks.values():
                action_topic = (
                    (sink_cfg.model_extra or {}).get("cmd_topic")
                    or (sink_cfg.model_extra or {}).get("topic")
                    or sink_cfg.topic
                    or action_topic
                )
                break

        # Pre-collect channel topic overrides so the adapter can resolve them
        # itself when set_observation_channels is later invoked.  Only channel
        # sources (whose type is in the adapter's supported channels) contribute.
        _probe_supported = ROS2SourceAdapter(node=None).supported_channels()
        channel_topic_overrides: dict[str, str] = {}
        if config.hardware.sources:
            for _sname, scfg in config.hardware.sources.items():
                channel = str(scfg.type).lower()
                if channel not in _probe_supported:
                    continue
                topic_override = scfg.topic or (scfg.model_extra or {}).get("topic")
                if topic_override:
                    channel_topic_overrides[channel] = topic_override

        source = ROS2SourceAdapter(
            node=None,
            joint_state_topic=joint_topic,
            ee_topic=ee_topic,
            channel_topic_overrides=channel_topic_overrides or None,
        )
        sink = ROS2SinkAdapter(node=None, action_topic=action_topic)

        obs_channels = RuntimeFactory._collect_channels(
            config, main_name, source.supported_channels()
        )
        if obs_channels:
            source.set_observation_channels(obs_channels)

        return ROS2Runner(
            runtime=runtime,
            source=source,
            sink=sink,
            policy=NoOpPolicyAdapter(),
            timer_period_s=1.0 / hz,
            source_name=main_name,
        )

    @staticmethod
    def _collect_channels(
        config: StackfileConfig, parent_name: str, supported: set[str]
    ) -> list[str]:
        """Return peer-source channels whose type is in *supported* and whose
        `ref` points at *parent_name*."""
        if not config.hardware or not config.hardware.sources:
            return []
        result: list[str] = []
        for _sname, scfg in config.hardware.sources.items():
            channel = str(scfg.type).lower()
            if channel not in supported:
                continue
            ref = (scfg.model_extra or {}).get("ref") or parent_name
            if ref.startswith("sources."):
                ref = ref[len("sources.") :]
            if ref == parent_name:
                result.append(channel)
        return result

    @staticmethod
    def _build_simulation(config: StackfileConfig) -> tuple[Any, Any, Any]:
        from dam.testing.sim_adapters import SimSink

        hz = float(config.safety.control_frequency_hz) if config.safety else 10.0
        sim_cfg = config.simulation
        source_cfg = RuntimeFactory._find_sim_source_cfg(config)
        dataset_repo = RuntimeFactory._resolve_dataset_repo(source_cfg, sim_cfg)
        source = RuntimeFactory._build_sim_source(dataset_repo, source_cfg, sim_cfg, hz)
        policy = RuntimeFactory._build_sim_policy(config)
        return source, policy, SimSink()

    @staticmethod
    def _find_sim_source_cfg(config: StackfileConfig) -> Any:
        if config.hardware and config.hardware.sources:
            for _name, s in config.hardware.sources.items():
                if str(s.type).lower() in ("dataset", "simulation", "mock"):
                    return s
        return None

    @staticmethod
    def _resolve_dataset_repo(source_cfg: Any, sim_cfg: Any) -> str | None:
        dataset_repo = None
        if source_cfg:
            dataset_repo = getattr(source_cfg, "dataset_repo_id", None)
            extra = getattr(source_cfg, "model_extra", {})
            if not dataset_repo and extra:
                dataset_repo = extra.get("dataset_repo_id")
        if not dataset_repo and sim_cfg:
            dataset_repo = getattr(sim_cfg, "dataset_repo_id", None)
        return dataset_repo

    @staticmethod
    def _build_sim_source(
        dataset_repo: str | None, source_cfg: Any, sim_cfg: Any, hz: float
    ) -> Any:
        if dataset_repo:
            from dam.testing.dataset_source import DatasetSimSource

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
            return DatasetSimSource(
                repo_id=dataset_repo, episode=episode, hz=hz, degrees_mode=degrees_mode
            )
        from dam.testing.sim_adapters import SimSource

        logger.info("Simulation: using SimSource (random walk)")
        return SimSource(hz=hz)

    @staticmethod
    def _build_sim_policy(config: StackfileConfig) -> Any:
        if config.policy and config.policy.pretrained_path:
            try:
                from dam.adapter.lerobot.builder import LeRobotBuilder
                from dam.adapter.lerobot.policy import LeRobotPolicyAdapter
                from dam.config.schema import HardwareConfig

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
                    return policy
            except Exception as exc:
                logger.warning(
                    "Simulation: policy load failed (%s), falling back to SimPolicy", exc
                )
        from dam.testing.sim_adapters import SimPolicy

        logger.info("Simulation: using SimPolicy (random action fallback)")
        return SimPolicy()
