"""LeRobotBuilder — constructs lerobot robot and policy objects from DAM Stackfile config.

This module is the bridge between DAM's hardware-agnostic Stackfile format
and the concrete lerobot SDK objects.  It allows the runner to be built
entirely from a YAML file without hard-coding robot types in application code.

Supported robot presets → lerobot classes
------------------------------------------
  so101_follower  → So101FollowerConfig  (lerobot.robots.so_follower)
  so100_follower  → So100FollowerConfig
  koch_follower   → KochFollowerConfig   (lerobot.robots.koch_follower)

Supported policy types
----------------------
  Files ending in ``.pt`` / ``.ptl``        → torch.jit.load()  (JIT / Isaac Lab)
  HuggingFace repo ID or local directory    → lerobot make_policy()

Path handling
-------------
All ``calibration_path`` and ``pretrained_path`` values accept absolute paths
(e.g. ``/mnt/dam_data/calibration/``) as well as HuggingFace repo IDs and
relative paths.  Absolute paths are used as-is; relative paths are resolved
from the current working directory.

Usage::

    from dam.adapter.lerobot.builder import LeRobotBuilder

    builder = LeRobotBuilder(stackfile_config.hardware, stackfile_config.policy)
    robot   = builder.build_robot()    # call robot.connect() before use
    policy  = builder.build_policy()   # may be None if no policy in Stackfile
    preset  = builder.preset           # RobotPreset with joint names / limits
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import Any

from dam.adapter.lerobot.presets import RobotPreset, get_preset
from dam.config.schema import HardwareConfig
from dam.config.schema import PolicyConfig as DamPolicyConfig

logger = logging.getLogger(__name__)


def _resolve_path(raw: str | None) -> Path | None:
    """Return a resolved Path for *raw*, or None if raw is empty/None.

    Absolute paths (e.g. /mnt/dam_data/…) are returned as-is.
    Relative paths are resolved from the current working directory.
    HuggingFace repo IDs (no leading '/') are not touched — callers that use
    the path as a string should check ``path.is_absolute()`` if needed.
    """
    if not raw:
        return None
    return Path(raw)


class LeRobotBuilder:
    """Factory that maps DAM Stackfile config to lerobot robot + policy objects."""

    def __init__(
        self,
        hardware: HardwareConfig,
        policy: DamPolicyConfig | None = None,
        control_frequency_hz: float = 50.0,
    ) -> None:
        self._hardware = hardware
        self._policy_cfg = policy
        self._hz = control_frequency_hz
        self._cached_robot = None
        self._cached_policy_bundle = None  # (policy, pre, post)

        preset_name = hardware.preset or "generic_6dof"
        try:
            self._preset = get_preset(preset_name)
        except KeyError:
            logger.warning(
                "Unknown hardware preset '%s' — falling back to generic_6dof", preset_name
            )
            self._preset = get_preset("generic_6dof")

    @property
    def joint_names(self) -> list[str]:
        return self._preset.joint_names

    @property
    def preset(self) -> RobotPreset:
        return self._preset

    # ── Public factory methods ────────────────────────────────────────────

    def build_robot(self) -> Any:
        """Builds (and caches) a lerobot robot object."""
        if self._cached_robot is not None:
            return self._cached_robot
        try:
            from lerobot.robots import make_robot_from_config
        except ImportError as e:
            from dam.services.runtime_control import RuntimeControlService

            if RuntimeControlService._ensure_lerobot_installed():
                from lerobot.robots import make_robot_from_config  # retry after install
            else:
                raise ImportError(
                    "lerobot is not installed and automatic setup failed. "
                    "Run `make setup-lerobot` manually then restart."
                ) from e

        sources = self._hardware.sources or {}
        # Prefer 'follower_arm'; fall back to first defined source
        src_cfg = sources.get("follower_arm") or (
            next(iter(sources.values()), None) if sources else None
        )
        if src_cfg is None:
            raise ValueError(
                "hardware.sources is empty — cannot build robot. "
                "Add a 'follower_arm' source with port and id to your Stackfile."
            )

        robot_cfg = self._make_robot_config(src_cfg)

        # --- PREFLIGHT HARDWARE CHECK ---
        self._preflight_hardware_check(robot_cfg)

        # Ensure camera FPS matches control frequency if not explicitly overridden by user
        hz = self._hz
        for cam_cfg in robot_cfg.cameras.values():
            if cam_cfg.fps == 30:  # If it was the default we set in _build_camera_configs
                cam_cfg.fps = int(hz)

        self._cached_robot = make_robot_from_config(robot_cfg)
        return self._cached_robot

    def _preflight_hardware_check(self, robot_cfg: Any) -> None:
        """Verify that serial ports and cameras exist before handing off to lerobot."""
        import os

        # 1. Check Serial Port
        if hasattr(robot_cfg, "robot_type"):
            # Lerobot configs usually have a port somewhere.
            # We'll check common names like 'port' or 'arm_port'
            port = getattr(robot_cfg, "port", None)
            if port and not os.path.exists(port):
                raise RuntimeError(
                    f"Serial Port Failure: Device '{port}' not found. "
                    "Please ensure the robot is plugged in and the port matches your Stackfile."
                )

        # 2. Check Cameras
        if robot_cfg.cameras:
            try:
                import cv2
            except ImportError:
                return  # skip if cv2 not available

            for name, cam in robot_cfg.cameras.items():
                idx = cam.index_or_path
                if isinstance(idx, int):
                    # Quick try-open
                    cap = cv2.VideoCapture(idx)
                    is_opened = cap.isOpened()
                    cap.release()
                    if not is_opened:
                        raise RuntimeError(
                            f"Camera Failure: '{name}' (Index {idx}) could not be opened. "
                            "Ensure the camera is connected and permission is granted."
                        )
                elif isinstance(idx, str) and not os.path.exists(idx):
                    raise RuntimeError(f"Camera Failure: '{name}' path '{idx}' does not exist.")

    def build_policy(self) -> Any | None:
        """Build a policy object from ``policy.pretrained_path``.

        Returns None if no policy section is defined or pretrained_path is absent.
        Accepts both HuggingFace repo IDs and absolute local paths
        (e.g. ``/mnt/dam_data/models/act_policy.pt``).
        """
        if self._policy_cfg is None:
            return None
        pretrained = self._policy_cfg.pretrained_path
        if pretrained is None:
            logger.warning("policy.pretrained_path is not set — no policy loaded")
            return None

        path_obj = _resolve_path(pretrained)
        path_str = str(path_obj) if path_obj else pretrained

        if self._cached_policy_bundle is not None:
            return self._cached_policy_bundle

        if path_str.endswith(".pt") or path_str.endswith(".ptl"):
            self._cached_policy_bundle = self._load_jit(path_str)
        else:
            self._cached_policy_bundle = self._load_lerobot_policy(path_str)

        return self._cached_policy_bundle

    # ── Internal: robot config construction ──────────────────────────────

    def _make_robot_config(self, src_cfg: Any) -> Any:
        preset_name = self._preset.name
        port = src_cfg.port or "/dev/ttyUSB0"
        robot_id = src_cfg.id or "follower"
        cam_configs = self._build_camera_configs(src_cfg.cameras or {})
        # calibration_path is typed in HardwareSourceConfig; extra="allow" covers
        # any future keys.  _resolve_path handles absolute volume-mount paths.
        calibration = _resolve_path(getattr(src_cfg, "calibration_path", None))

        # Preflight check: Verify calibration path if provided
        if calibration:
            if not calibration.exists():
                raise ValueError(
                    f"LeRobot: Calibration path does not exist: {calibration}. "
                    "Please check your path in the Config panel."
                )
            if calibration.is_dir():
                json_files = list(calibration.glob("*.json"))
                if not json_files:
                    logger.warning(
                        "Calibration directory %s contains no .json files. "
                        "Calibration might be ignored.",
                        calibration,
                    )
            elif calibration.suffix != ".json":
                logger.warning(
                    "Calibration path %s is a file but not a .json. "
                    "Expected .json for calibration.",
                    calibration,
                )

        if "so101" in preset_name:
            return self._cfg_so101(port, robot_id, cam_configs, calibration)
        if "so100" in preset_name:
            return self._cfg_so100(port, robot_id, cam_configs, calibration)
        if "koch" in preset_name:
            return self._cfg_koch(port, robot_id, cam_configs, calibration)

        raise ValueError(
            f"No lerobot robot class mapping for preset '{preset_name}'. "
            "Supported: so101_follower, so100_follower, koch_follower. "
            "Add a mapping in LeRobotBuilder._make_robot_config()."
        )

    @staticmethod
    def _build_camera_configs(cameras_raw: dict) -> dict:
        try:
            from lerobot.cameras.opencv.configuration_opencv import OpenCVCameraConfig
        except ImportError:
            return {}

        out = {}
        for cam_name, cam_cfg in cameras_raw.items():
            if isinstance(cam_cfg, dict):
                # Accept both 'index_or_path' (lerobot CLI style) and plain 'index'
                index_or_path = cam_cfg.get("index_or_path", cam_cfg.get("index", 0))
                out[cam_name] = OpenCVCameraConfig(
                    index_or_path=index_or_path,
                    fps=cam_cfg.get("fps", 30),
                    width=cam_cfg.get("width"),
                    height=cam_cfg.get("height"),
                    color_mode=cam_cfg.get("color_mode", "rgb"),
                )
        return out

    @staticmethod
    def _cfg_so101(
        port: str,
        robot_id: str,
        cameras: dict,
        calibration: Path | None = None,
    ) -> Any:
        from lerobot.robots.so_follower import SO101FollowerConfig

        kwargs: dict[str, Any] = {"port": port, "id": robot_id, "cameras": cameras}
        if calibration is not None:
            kwargs["calibration_dir"] = str(calibration)
            logger.info("So101FollowerConfig: calibration_dir=%s", calibration)
        return SO101FollowerConfig(**kwargs)

    @staticmethod
    def _cfg_so100(
        port: str,
        robot_id: str,
        cameras: dict,
        calibration: Path | None = None,
    ) -> Any:
        from lerobot.robots.so_follower import SO100FollowerConfig

        kwargs: dict[str, Any] = {"port": port, "id": robot_id, "cameras": cameras}
        if calibration is not None:
            kwargs["calibration_dir"] = str(calibration)
            logger.info("So100FollowerConfig: calibration_dir=%s", calibration)
        return SO100FollowerConfig(**kwargs)

    @staticmethod
    def _cfg_koch(
        port: str,
        robot_id: str,
        cameras: dict,
        calibration: Path | None = None,
    ) -> Any:
        from lerobot.robots.koch_follower import KochFollowerConfig

        kwargs: dict[str, Any] = {"port": port, "id": robot_id, "cameras": cameras}
        if calibration is not None:
            kwargs["calibration_dir"] = str(calibration)
            logger.info("KochFollowerConfig: calibration_dir=%s", calibration)
        return KochFollowerConfig(**kwargs)

    # ── Internal: policy loading ──────────────────────────────────────────

    def _load_jit(self, path: str) -> Any:
        try:
            import torch
        except ImportError as e:
            raise ImportError("torch is required for JIT policy loading") from e
        device = self._policy_cfg.device if self._policy_cfg else "cpu"
        policy = torch.jit.load(path, map_location=device)
        policy.eval()
        logger.info("JIT policy loaded: %s  device=%s", path, device)
        return policy

    def _load_lerobot_policy(self, pretrained_path: str) -> tuple[Any, Any, Any]:
        """Loads policy and its associated pre/post processors.

        Returns:
            (policy, preprocessor, postprocessor)
        """
        try:
            from lerobot.configs.policies import PreTrainedConfig
            from lerobot.datasets.lerobot_dataset import LeRobotDatasetMetadata
            from lerobot.policies.factory import (
                get_policy_class,
                make_pre_post_processors,
            )
            from lerobot.processor.rename_processor import rename_stats
            from lerobot.utils.utils import get_safe_torch_device
        except ImportError as e:
            from dam.services.runtime_control import RuntimeControlService

            if RuntimeControlService._ensure_lerobot_installed():
                from lerobot.configs.policies import PreTrainedConfig
                from lerobot.datasets.lerobot_dataset import LeRobotDatasetMetadata
                from lerobot.policies.factory import get_policy_class, make_pre_post_processors
                from lerobot.processor.rename_processor import rename_stats
                from lerobot.utils.utils import get_safe_torch_device
            else:
                raise ImportError(
                    "lerobot is not installed and automatic setup failed. "
                    "Run `make setup-lerobot` manually then restart."
                ) from e

        # Resolve device using the canonical lerobot helper
        pcfg = self._policy_cfg
        requested_device = pcfg.device if pcfg else "cpu"
        device = get_safe_torch_device(requested_device)

        # Load config - Force device from our Stackfile to prevent 'cuda' warnings from model config
        policy_cfg = PreTrainedConfig.from_pretrained(pretrained_path)
        if hasattr(policy_cfg, "device"):
            policy_cfg.device = str(device)

        ds_meta = None
        try:
            # Only attempt metadata if explicitly needed or common consolidated repo
            from lerobot.datasets.lerobot_dataset import LeRobotDatasetMetadata

            with contextlib.suppress(Exception):
                ds_meta = LeRobotDatasetMetadata(pretrained_path)
        except ImportError:
            pass

        # 1. Get the policy class (e.g. ACTPolicy) directly
        try:
            policy_cls = get_policy_class(policy_cfg.type)
        except Exception as e:
            logger.error("LeRobot: Unknown policy type '%s'", policy_cfg.type)
            raise e

        # 2. Instantiate policy directly from pretrained path (bypassing factory metadata checks)
        try:
            # Most LeRobot policies accept 'config' and 'pretrained_name_or_path'
            policy = policy_cls.from_pretrained(
                pretrained_name_or_path=pretrained_path, config=policy_cfg
            )
            policy.to(device)
            logger.info("LeRobot policy loaded directly from %s", pretrained_path)
        except Exception as e:
            logger.error(
                "LeRobot: Direct policy loading failed for %s. Error: %s", pretrained_path, e
            )
            raise e

        # 3. Build processors
        # If ds_meta exists, use its stats; otherwise pass None and let make_pre_post_processors
        # try to find stats.json in the pretrained_path.
        preprocessor = None
        postprocessor = None
        stats = None
        if ds_meta and hasattr(ds_meta, "stats"):
            stats = rename_stats(ds_meta.stats, {})

        try:
            preprocessor, postprocessor = make_pre_post_processors(
                policy_cfg=policy_cfg,
                pretrained_path=pretrained_path,
                dataset_stats=stats,
                preprocessor_overrides={
                    "device_processor": {"device": str(device)},
                },
            )
            logger.info("LeRobot pre/post processors initialized.")
        except Exception as e:
            logger.warning("LeRobot processors initialization note: %s", e)

        return policy, preprocessor, postprocessor
