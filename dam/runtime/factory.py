"""RuntimeFactory — constructs a fully-wired GuardRuntime from a Stackfile.

This factory handles:
  1. Parsing the Stackfile YAML
  2. Resolving the appropriate hardware adapters (LeRobot, ROS2, or Simulation)
  3. Configuring safety layers (L0-L3) and boundaries
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
    def _resolve_urdf_path(config: StackfileConfig, preset: Any) -> str | None:
        """Resolve URDF for workspace FK.

        Power-user escape hatch: ``hardware.urdf_path`` in the raw stackfile
        overrides the preset's URDF. The Console UI doesn't expose this
        field (URDFs are managed per-preset in the preset manager); it
        exists for users editing YAML directly. Otherwise we fall back to
        the preset's bundled URDF, resolved relative to the repo root."""
        from pathlib import Path

        if config.hardware is not None and config.hardware.urdf_path:
            return config.hardware.urdf_path
        relpath = getattr(preset, "default_urdf_relpath", None)
        if not relpath:
            return None
        # factory.py lives at dam/runtime/factory.py; parents[2] is the repo
        # root where the assets/ directory sits.
        candidate = Path(__file__).resolve().parents[2] / relpath
        return str(candidate) if candidate.exists() else None

    @staticmethod
    def build_from_stackfile(path: str, *, ros2_node: Any = None) -> BaseRunner:
        """Build a Runner from the given stackfile path.

        ``ros2_node`` is forwarded to the ROS2 adapter when the stackfile
        selects the ros2 adapter; ignored otherwise.  Passing ``None`` for a
        ros2 stackfile produces a mock-mode adapter that won't actually
        subscribe to any topics — fine for tests, broken for production.
        """
        config = RuntimeFactory.load_config(path)
        return RuntimeFactory.build_from_config(config, ros2_node=ros2_node)

    @staticmethod
    def build_from_config(config: StackfileConfig, *, ros2_node: Any = None) -> BaseRunner:
        """Build a Runner from a pre-parsed StackfileConfig object.

        See ``build_from_stackfile`` for ``ros2_node`` semantics.
        """
        from dam.runner.base import SimulationRunner

        # 1. Determine Adapter Type — pick the first source whose type is a
        # known adapter, so peer channel sources (current, effort, …) don't
        # accidentally drive routing.
        # "motor" is the canonical name; "lerobot" accepted for backward compat.
        _ADAPTER_TYPES = ("motor", "lerobot", "ros2")
        adapter_type = None
        hw_config = config.hardware
        if hw_config and hw_config.sources:
            source_types = {str(src.type or "").lower() for src in hw_config.sources.values()}
            if "dataset" in source_types and source_types.intersection({"motor", "lerobot"}):
                adapter_type = "dataset_motor"
            for src in hw_config.sources.values():
                if adapter_type:
                    break
                t = str(src.type or "").lower()
                if t in _ADAPTER_TYPES:
                    adapter_type = "motor" if t in ("motor", "lerobot") else t
                    break
                if t == "dataset":
                    adapter_type = "simulation"
                    break

        if not adapter_type:
            raise ValueError(
                "No valid hardware configuration found. Please specify a source "
                "with a recognized type (e.g., 'type: lerobot', 'type: ros2', "
                "'type: dataset') in the stackfile."
            )

        logger.info("Building runtime with adapter type: %s", adapter_type)

        if adapter_type == "motor":
            return RuntimeFactory._build_lerobot(config)
        elif adapter_type == "dataset_motor":
            return RuntimeFactory._build_dataset_hardware_replay(config)
        elif adapter_type == "ros2":
            return RuntimeFactory._build_ros2(config, ros2_node=ros2_node)

        # Explicit Simulation or fall-through — reuse already-parsed config.
        # Build the shared camera frame hub here too (mirrors _build_lerobot):
        # simulation/dataset sources deliver frames on the observation and the
        # runtime bridges them into this hub, so live preview and the Rust MCAP
        # image writer both see them. Without it the hub is None and frames are
        # silently dropped.
        from dam.camera.frame_hub import CameraFrameHub

        frame_hub = CameraFrameHub(
            window_sec=config.loopback.window_sec if config.loopback else 10.0
        )
        runtime = GuardRuntime._from_config(config, frame_hub=frame_hub)
        source, policy, sink = RuntimeFactory._build_simulation(config)
        runtime.register_source("main", source)
        if policy:
            runtime.register_policy(policy)
        runtime.register_sink(sink)

        hz = config.safety.control_frequency_hz if config.safety else 10.0
        return SimulationRunner(runtime, control_frequency_hz=hz, frame_hub=frame_hub)

    @staticmethod
    def _build_lerobot(config: StackfileConfig) -> BaseRunner:
        from dam.adapter.lerobot.builder import LeRobotBuilder
        from dam.adapter.lerobot.policy import LeRobotPolicyAdapter
        from dam.runner.lerobot import LeRobotRunner
        from dam.runtime.guard_runtime import GuardRuntime

        assert config.hardware is not None
        RuntimeFactory._validate_opencv_source_fields(config)

        from dam.camera.frame_hub import CameraFrameHub

        hz = config.safety.control_frequency_hz if config.safety else 30.0
        frame_hub = CameraFrameHub(
            window_sec=config.loopback.window_sec if config.loopback else 10.0
        )
        runtime = GuardRuntime._from_config(config, frame_hub=frame_hub)

        # Identify main source name (the first motor/lerobot-typed one)
        main_name = "arm"
        if config.hardware.sources:
            for name, s in config.hardware.sources.items():
                if str(s.type).lower() in ("motor", "lerobot"):
                    main_name = name
                    break
        main_source_cfg = (
            config.hardware.sources.get(main_name) if config.hardware.sources else None
        )

        # Peer-level opencv sources are registered as separate DAM
        # OpenCVSourceAdapter instances (see DISCOVER OTHER SOURCES below).
        # They do NOT go through lerobot's OpenCVCameraConfig — lerobot
        # enforces strict resolution matching which crashes when the
        # hardware returns a different native resolution.
        builder = LeRobotBuilder(config.hardware, config.policy, control_frequency_hz=hz)
        robot = builder.build_robot()

        # Build adapter
        from dam.adapter.lerobot.adapter import LeRobotAdapter

        adapter = LeRobotAdapter(
            robot,
            joint_names=builder.joint_names,
            degrees_mode=(
                builder.preset.degrees_mode
                if main_source_cfg is None or main_source_cfg.degrees_mode is None
                else main_source_cfg.degrees_mode
            ),
            urdf_path=RuntimeFactory._resolve_urdf_path(config, builder.preset),
        )

        supported = adapter.supported_channels()
        obs_channels = RuntimeFactory._collect_channels(config, main_name, supported)
        if obs_channels:
            adapter.set_observation_channels(obs_channels)

        # Resolve degrees_mode the same way the source adapter does so policy /
        # source / sink agree on units by construction — not by lucky default.
        degrees_mode = (
            builder.preset.degrees_mode
            if main_source_cfg is None or main_source_cfg.degrees_mode is None
            else main_source_cfg.degrees_mode
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
                    degrees_mode=degrees_mode,
                )
            else:
                policy = LeRobotPolicyAdapter(
                    policy_res,
                    joint_names=builder.joint_names,
                    degrees_mode=degrees_mode,
                )

        runtime.register_source(main_name, adapter)
        runtime.register_sink(adapter)
        if policy:
            runtime.register_policy(policy)

        auxiliary_sources = RuntimeFactory._build_camera_sources(
            config, frame_hub, excluded={main_name}, supported=supported
        )

        return LeRobotRunner(
            runtime=runtime,
            control_frequency_hz=hz,
            frame_hub=frame_hub,
            auxiliary_sources=auxiliary_sources,
        )

    @staticmethod
    def _build_dataset_hardware_replay(config: StackfileConfig) -> BaseRunner:
        """Replay recorded dataset actions through guards into a real motor sink."""
        from dam.adapter.dataset import DatasetReplayPolicy
        from dam.adapter.lerobot.adapter import LeRobotAdapter
        from dam.adapter.lerobot.builder import LeRobotBuilder
        from dam.camera.frame_hub import CameraFrameHub
        from dam.runner.lerobot import LeRobotRunner

        assert config.hardware is not None
        RuntimeFactory._validate_opencv_source_fields(config)
        hz = config.safety.control_frequency_hz if config.safety else 30.0
        frame_hub = CameraFrameHub(
            window_sec=config.loopback.window_sec if config.loopback else 10.0
        )
        runtime = GuardRuntime._from_config(config, frame_hub=frame_hub)

        sources = config.hardware.sources or {}
        dataset_name, dataset_cfg = next(
            item for item in sources.items() if str(item[1].type).lower() == "dataset"
        )
        motor_name, motor_cfg = next(
            item for item in sources.items() if str(item[1].type).lower() in ("motor", "lerobot")
        )

        dataset_repo = RuntimeFactory._resolve_dataset_repo(dataset_cfg, config.simulation)
        if not dataset_repo:
            raise ValueError(
                "Dataset hardware replay requires dataset_repo_id on its dataset source"
            )
        dataset_source = RuntimeFactory._build_sim_source(
            dataset_repo, dataset_cfg, config.simulation, float(hz), strict=True
        )

        builder = LeRobotBuilder(config.hardware, None, control_frequency_hz=hz)
        degrees_mode = (
            builder.preset.degrees_mode
            if motor_cfg.degrees_mode is None
            else motor_cfg.degrees_mode
        )
        motor = LeRobotAdapter(
            builder.build_robot(),
            joint_names=builder.joint_names,
            degrees_mode=degrees_mode,
            urdf_path=RuntimeFactory._resolve_urdf_path(config, builder.preset),
        )
        supported = motor.supported_channels()
        obs_channels = RuntimeFactory._collect_channels(config, motor_name, supported)
        if obs_channels:
            motor.set_observation_channels(obs_channels)

        # Keep the dataset first: its state/images are the replay observation;
        # the motor source contributes current hardware telemetry and is the sink.
        runtime.register_source(dataset_name, dataset_source)
        runtime.register_source(motor_name, motor)
        runtime.register_policy(DatasetReplayPolicy(dataset_source))
        runtime.register_sink(motor)

        auxiliary_sources = RuntimeFactory._build_camera_sources(
            config, frame_hub, excluded={dataset_name, motor_name}, supported=supported
        )
        return LeRobotRunner(
            runtime=runtime,
            control_frequency_hz=hz,
            frame_hub=frame_hub,
            auxiliary_sources=auxiliary_sources,
        )

    @staticmethod
    def _validate_opencv_source_fields(config: StackfileConfig) -> None:
        """Reject common camera fields under params before importing camera backends."""
        if config.hardware is None or not config.hardware.sources:
            return
        camera_fields = {"index", "index_or_path", "width", "height", "fps", "jpeg_fps"}
        for name, src_cfg in config.hardware.sources.items():
            type_str = str(src_cfg.type).lower()
            if type_str not in ("opencv", "camera", "usb"):
                continue
            extra = src_cfg.model_extra or {}
            nested_params = extra.get("params")
            if isinstance(nested_params, dict) and camera_fields.intersection(nested_params):
                raise ValueError(
                    f"OpenCV source '{name}' must declare camera fields at the source "
                    "top level (for example index_or_path, width, height, fps), not "
                    "under params."
                )

    @staticmethod
    def _build_camera_sources(
        config: StackfileConfig,
        frame_hub: Any,
        *,
        excluded: set[str],
        supported: set[str],
    ) -> dict[str, Any]:
        """Build peer OpenCV sources shared by motor and dataset-to-motor modes."""
        from dam.adapter.opencv.source import OpenCVSourceAdapter

        auxiliary_sources: dict[str, Any] = {}
        source_items = (config.hardware.sources or {}).items() if config.hardware else ()
        for name, src_cfg in source_items:
            if name in excluded:
                continue
            type_str = str(src_cfg.type).lower()
            if type_str in supported:
                continue
            if type_str not in ("opencv", "camera", "usb"):
                continue
            extra = src_cfg.model_extra or {}
            idx: int | str = (
                extra.get("index_or_path")
                or extra.get("index")
                or getattr(src_cfg, "index_or_path", None)
                or getattr(src_cfg, "index", None)
                or 0
            )
            auxiliary_sources[name] = OpenCVSourceAdapter(
                index=idx,
                name=name,
                width=extra.get("width"),
                height=extra.get("height"),
                jpeg_fps=float(extra.get("fps") or extra.get("jpeg_fps") or 30.0),
                frame_hub=frame_hub,
            )
            logger.info("Registered camera source: %s (type=%s)", name, type_str)
        return auxiliary_sources

    @staticmethod
    def _build_ros2(config: StackfileConfig, *, ros2_node: Any = None) -> BaseRunner:
        from dam.adapter.ros2._noop_policy import NoOpPolicyAdapter
        from dam.adapter.ros2.sink import ROS2SinkAdapter
        from dam.adapter.ros2.source import ROS2SourceAdapter
        from dam.runner.ros2 import ROS2Runner

        assert config.hardware is not None
        runtime = GuardRuntime._from_config(config)
        hz = config.safety.control_frequency_hz if config.safety else 30.0

        # Identify the main ROS2 source (type=ros2).
        main_name, main_cfg = "ros2", None
        if config.hardware.sources:
            for name, s in config.hardware.sources.items():
                if str(s.type).lower() == "ros2":
                    main_name, main_cfg = name, s
                    break

        # Main-source topic: prefer the declared `topic` field on
        # HardwareSourceConfig, fall back to `joint_topic` (extras), then default.
        joint_topic = "/joint_states"
        source_qos = "best_effort"  # sensors default to best_effort in ROS2
        source_qos_depth = 10
        if main_cfg is not None:
            extra = main_cfg.model_extra or {}
            joint_topic = main_cfg.topic or extra.get("joint_topic") or joint_topic
            source_qos = extra.get("qos", source_qos)
            source_qos_depth = int(extra.get("qos_depth", source_qos_depth))

        # Pick the sink that references the main source; fall back to the
        # first sink if no `ref` matches (legacy stackfiles).
        action_topic = "/arm_controller/joint_trajectory"
        sink_qos = "reliable"  # commands default to reliable
        sink_qos_depth = 10
        chosen_sink = RuntimeFactory._find_sink_for(config, main_name)
        if chosen_sink is not None:
            if chosen_sink.topic:
                action_topic = chosen_sink.topic
            sink_extra = chosen_sink.model_extra or {}
            sink_qos = sink_extra.get("qos", sink_qos)
            sink_qos_depth = int(sink_extra.get("qos_depth", sink_qos_depth))

        # Joint names for JointTrajectory publication.  Pulled from
        # hardware.joints (its keys are the joint names), then sink-level
        # `joint_names:` override (extras), then empty.
        joint_names = list((config.hardware.joints or {}).keys())
        if chosen_sink is not None:
            override = (chosen_sink.model_extra or {}).get("joint_names")
            if override:
                joint_names = list(override)

        # Collect per-channel topic overrides from the declared `topic` field
        # on each peer-source whose type matches a supported channel.
        supported = ROS2SourceAdapter(node=None).supported_channels()
        channel_topic_overrides: dict[str, str] = {}
        for _sname, scfg in (config.hardware.sources or {}).items():
            channel = str(scfg.type).lower()
            if channel in supported and scfg.topic:
                channel_topic_overrides[channel] = scfg.topic

        source = ROS2SourceAdapter(
            node=ros2_node,
            joint_state_topic=joint_topic,
            channel_topic_overrides=channel_topic_overrides or None,
            qos=source_qos,
            qos_depth=source_qos_depth,
        )
        sink = ROS2SinkAdapter(
            node=ros2_node,
            action_topic=action_topic,
            joint_names=joint_names,
            qos=sink_qos,
            qos_depth=sink_qos_depth,
        )

        obs_channels = RuntimeFactory._collect_channels(config, main_name, supported)
        if obs_channels:
            source.set_observation_channels(obs_channels)

        return ROS2Runner(
            runtime=runtime,
            source=source,
            sink=sink,
            policy=RuntimeFactory._build_policy(config) or NoOpPolicyAdapter(),
            node=ros2_node,
            timer_period_s=1.0 / hz,
            source_name=main_name,
        )

    @staticmethod
    def _find_sink_for(config: StackfileConfig, source_name: str) -> Any:
        """Return the sink whose `ref` points at *source_name*; None if absent."""
        if not config.hardware or not config.hardware.sinks:
            return None
        for sink_cfg in config.hardware.sinks.values():
            ref = sink_cfg.ref or ""
            if ref.startswith("sources."):
                ref = ref[len("sources.") :]
            if ref == source_name:
                return sink_cfg
        # No matching ref — fall back to the first sink so a single-sink
        # stackfile (the common case) still works without explicit refs.
        return next(iter(config.hardware.sinks.values()), None)

    @staticmethod
    def _build_policy(config: StackfileConfig) -> Any:
        """Construct a policy adapter from `config.policy`, or None.

        Shared between the lerobot and ros2 factory paths so any robot can
        load any policy (the underlying loader is hardware-agnostic).
        """
        if not config.policy or not config.policy.pretrained_path:
            return None
        try:
            from dam.adapter.lerobot.builder import LeRobotBuilder
            from dam.adapter.lerobot.policy import LeRobotPolicyAdapter
            from dam.config.schema import HardwareConfig

            # Simulation-only policy loading reuses LeRobotBuilder's lerobot
            # adapter; pick whichever preset the stackfile already names,
            # falling back to the first registered one if the name is missing
            # or no longer in the registry (e.g. a stackfile that references
            # a since-deleted preset).
            from dam.preset import list_presets

            registered = list_presets()
            if not registered:
                logger.warning("No presets registered — skipping policy build")
                return None
            preset_name = (
                config.hardware.preset if config.hardware and config.hardware.preset else None
            )
            if preset_name not in registered:
                if preset_name:
                    logger.warning(
                        "Preset '%s' not in registry — falling back to '%s' for policy build",
                        preset_name,
                        registered[0],
                    )
                preset_name = registered[0]
            fake_hw = HardwareConfig(preset=preset_name)
            builder = LeRobotBuilder(fake_hw, config.policy)
            policy_res = builder.build_policy()
            if not policy_res:
                return None
            if isinstance(policy_res, tuple):
                p_obj, pre, post = policy_res
            else:
                p_obj, pre, post = policy_res, None, None
            return LeRobotPolicyAdapter(
                p_obj,
                preprocessor=pre,
                postprocessor=post,
                joint_names=builder.joint_names,
                device=config.policy.device,
            )
        except Exception:  # noqa: BLE001 — policy is optional; fall back to no-op
            logger.warning("Policy load failed; runtime will use a no-op policy", exc_info=True)
            return None

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
                if str(s.type).lower() == "dataset":
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
        dataset_repo: str | None,
        source_cfg: Any,
        sim_cfg: Any,
        hz: float,
        *,
        strict: bool = False,
    ) -> Any:
        if dataset_repo:
            from dam.adapter.dataset import DatasetSimSource

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
                repo_id=dataset_repo,
                episode=episode,
                hz=hz,
                degrees_mode=degrees_mode,
                strict=strict,
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
