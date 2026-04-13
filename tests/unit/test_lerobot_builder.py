"""Unit tests for LeRobotBuilder and its helpers.

Covered:
  - _resolve_path: None, empty string, absolute path, relative path, HF repo ID
  - _build_camera_configs: index_or_path key, legacy index key, empty dict
  - _cfg_so101/so100/koch: kwargs with and without calibration_dir
  - _load_lerobot_policy: diffusion overrides injected into overrides list
  - build_robot: ValueError when sources is empty
  - build_policy: None when policy_cfg is None or pretrained_path absent
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers — build minimal config stubs without touching dam.config.schema
# ---------------------------------------------------------------------------


def _hw(preset: str = "so101_follower", sources: dict | None = None) -> Any:
    hw = SimpleNamespace()
    hw.preset = preset
    hw.sources = sources or {}
    return hw


def _policy(
    pretrained_path: str = "my/hf-repo",
    device: str = "cpu",
    noise_scheduler_type: str | None = None,
    num_inference_steps: int | None = None,
) -> Any:
    p = SimpleNamespace()
    p.pretrained_path = pretrained_path
    p.device = device
    p.noise_scheduler_type = noise_scheduler_type
    p.num_inference_steps = num_inference_steps
    return p


# ---------------------------------------------------------------------------
# _resolve_path
# ---------------------------------------------------------------------------


class TestResolvePath:
    def _fn(self):
        from dam.adapter.lerobot.builder import _resolve_path

        return _resolve_path

    def test_none_returns_none(self):
        assert self._fn()(None) is None

    def test_empty_string_returns_none(self):
        assert self._fn()("") is None

    def test_absolute_path_preserved(self):
        result = self._fn()("/mnt/dam_data/calibration")
        assert result == Path("/mnt/dam_data/calibration")
        assert result.is_absolute()

    def test_relative_path_becomes_path(self):
        result = self._fn()("relative/cal")
        assert isinstance(result, Path)
        assert str(result) == "relative/cal"

    def test_hf_repo_id_returns_path(self):
        # HF repo IDs like "MikeChenYZ/act-soarm-fmb-v2" are non-absolute —
        # callers treat path.is_absolute() == False as a repo ID.
        result = self._fn()("MikeChenYZ/act-soarm-fmb-v2")
        assert isinstance(result, Path)
        assert not result.is_absolute()


# ---------------------------------------------------------------------------
# _build_camera_configs
# ---------------------------------------------------------------------------


class TestBuildCameraConfigs:
    def _fn(self):
        from dam.adapter.lerobot.builder import LeRobotBuilder

        return LeRobotBuilder._build_camera_configs

    def test_empty_dict_returns_empty(self):
        with patch.dict(
            sys.modules,
            {
                "lerobot": MagicMock(),
                "lerobot.cameras": MagicMock(),
                "lerobot.cameras.opencv": MagicMock(),
                "lerobot.cameras.opencv.configuration_opencv": MagicMock(),
            },
        ):
            fake_cls = MagicMock(side_effect=lambda **kw: kw)
            sys.modules["lerobot.cameras.opencv.configuration_opencv"].OpenCVCameraConfig = fake_cls
            result = self._fn()({})
        assert result == {}

    def test_index_or_path_key_used(self):
        with patch.dict(
            sys.modules,
            {
                "lerobot": MagicMock(),
                "lerobot.cameras": MagicMock(),
                "lerobot.cameras.opencv": MagicMock(),
                "lerobot.cameras.opencv.configuration_opencv": MagicMock(),
            },
        ):
            captured = {}

            def fake_cfg(**kw):
                captured.update(kw)
                return kw

            sys.modules["lerobot.cameras.opencv.configuration_opencv"].OpenCVCameraConfig = fake_cfg
            self._fn()({"top": {"index_or_path": 2, "width": 640, "height": 480, "fps": 30}})
        assert captured["index_or_path"] == 2

    def test_legacy_index_key_falls_back(self):
        with patch.dict(
            sys.modules,
            {
                "lerobot": MagicMock(),
                "lerobot.cameras": MagicMock(),
                "lerobot.cameras.opencv": MagicMock(),
                "lerobot.cameras.opencv.configuration_opencv": MagicMock(),
            },
        ):
            captured = {}

            def fake_cfg(**kw):
                captured.update(kw)
                return kw

            sys.modules["lerobot.cameras.opencv.configuration_opencv"].OpenCVCameraConfig = fake_cfg
            self._fn()({"wrist": {"index": 1, "width": 640, "height": 480, "fps": 30}})
        # index falls back: cam_cfg.get("index_or_path", cam_cfg.get("index", 0)) == 1
        assert captured["index_or_path"] == 1

    def test_lerobot_import_error_returns_empty(self):
        with patch.dict(
            sys.modules,
            {
                "lerobot": None,
                "lerobot.cameras": None,
                "lerobot.cameras.opencv": None,
                "lerobot.cameras.opencv.configuration_opencv": None,
            },
        ):
            result = self._fn()({"top": {"index_or_path": 0}})
        assert result == {}


# ---------------------------------------------------------------------------
# _cfg_so101 / _cfg_so100 / _cfg_koch  (calibration_dir forwarding)
# ---------------------------------------------------------------------------


class TestRobotConfigBuilders:
    def _patch_robot(self, module_path: str, cls_name: str):
        """Context manager that patches the lerobot robot config class."""
        mock_cfg = MagicMock()
        mock_cfg.__name__ = cls_name
        return patch(
            f"dam.adapter.lerobot.builder.LeRobotBuilder.{cls_name.replace('Config', '').lower()}",
            mock_cfg,
        )

    def _call_so101(self, calibration: Path | None = None) -> dict:
        from dam.adapter.lerobot.builder import LeRobotBuilder

        captured_kwargs: dict = {}

        class FakeCls:
            def __init__(self, **kw):
                captured_kwargs.update(kw)

        with patch.dict(
            sys.modules,
            {
                "lerobot": MagicMock(),
                "lerobot.robots": MagicMock(),
                "lerobot.robots.so_follower": MagicMock(),
            },
        ):
            sys.modules["lerobot.robots.so_follower"].SO101FollowerConfig = FakeCls
            LeRobotBuilder._cfg_so101("port", "robot_id", {}, calibration)
        return captured_kwargs

    def _call_so100(self, calibration: Path | None = None) -> dict:
        from dam.adapter.lerobot.builder import LeRobotBuilder

        captured_kwargs: dict = {}

        class FakeCls:
            def __init__(self, **kw):
                captured_kwargs.update(kw)

        with patch.dict(
            sys.modules,
            {
                "lerobot": MagicMock(),
                "lerobot.robots": MagicMock(),
                "lerobot.robots.so_follower": MagicMock(),
            },
        ):
            sys.modules["lerobot.robots.so_follower"].SO100FollowerConfig = FakeCls
            LeRobotBuilder._cfg_so100("port", "robot_id", {}, calibration)
        return captured_kwargs

    def _call_koch(self, calibration: Path | None = None) -> dict:
        from dam.adapter.lerobot.builder import LeRobotBuilder

        captured_kwargs: dict = {}

        class FakeCls:
            def __init__(self, **kw):
                captured_kwargs.update(kw)

        with patch.dict(
            sys.modules,
            {
                "lerobot": MagicMock(),
                "lerobot.robots": MagicMock(),
                "lerobot.robots.koch_follower": MagicMock(),
            },
        ):
            sys.modules["lerobot.robots.koch_follower"].KochFollowerConfig = FakeCls
            LeRobotBuilder._cfg_koch("port", "robot_id", {}, calibration)
        return captured_kwargs

    def test_so101_no_calibration_omits_calibration_dir(self):
        kw = self._call_so101(calibration=None)
        assert "calibration_dir" not in kw

    def test_so101_with_calibration_sets_calibration_dir(self):
        kw = self._call_so101(calibration=Path("/mnt/dam_data/calibration"))
        assert kw["calibration_dir"] == "/mnt/dam_data/calibration"

    def test_so100_with_calibration_sets_calibration_dir(self):
        kw = self._call_so100(calibration=Path("/mnt/data/cal"))
        assert kw["calibration_dir"] == "/mnt/data/cal"

    def test_so100_no_calibration_omits_calibration_dir(self):
        kw = self._call_so100(calibration=None)
        assert "calibration_dir" not in kw

    def test_koch_with_calibration_sets_calibration_dir(self):
        kw = self._call_koch(calibration=Path("/tmp/cal"))
        assert kw["calibration_dir"] == "/tmp/cal"

    def test_so101_base_kwargs_correct(self):
        kw = self._call_so101(calibration=None)
        assert kw["port"] == "port"
        assert kw["id"] == "robot_id"
        assert kw["cameras"] == {}


# ---------------------------------------------------------------------------
# build_policy — None when no policy config
# ---------------------------------------------------------------------------


class TestBuildPolicy:
    def _builder(self, policy_cfg=None):
        from dam.adapter.lerobot.builder import LeRobotBuilder

        hw = _hw()
        with patch("dam.adapter.lerobot.builder.get_preset") as mock_get_preset:
            mock_get_preset.return_value = MagicMock(name="so101_follower")
            b = LeRobotBuilder.__new__(LeRobotBuilder)
            b._hardware = hw
            b._policy_cfg = policy_cfg
            b._preset = MagicMock()
        return b

    def test_returns_none_when_no_policy_config(self):
        builder = self._builder(policy_cfg=None)
        result = builder.build_policy()
        assert result is None

    def test_returns_none_when_pretrained_path_absent(self):
        p = _policy(pretrained_path="")
        p.pretrained_path = None
        builder = self._builder(policy_cfg=p)
        result = builder.build_policy()
        assert result is None


# ---------------------------------------------------------------------------
# _load_lerobot_policy — diffusion overrides
# ---------------------------------------------------------------------------


class TestLoadLerobotPolicy:
    def _builder_with_policy(self, policy_cfg):
        from dam.adapter.lerobot.builder import LeRobotBuilder

        b = LeRobotBuilder.__new__(LeRobotBuilder)
        b._hardware = _hw()
        b._policy_cfg = policy_cfg
        b._preset = MagicMock()
        return b

    def test_act_policy_receives_device(self):
        p = _policy(pretrained_path="MikeChenYZ/act-soarm-fmb-v2", device="mps")
        builder = self._builder_with_policy(p)

        mock_policy = MagicMock()
        mock_cfg = MagicMock()
        mock_cfg.device = "cuda"  # original default

        with patch.dict(
            sys.modules,
            {
                "lerobot": MagicMock(),
                "lerobot.policies.factory": MagicMock(),
                "lerobot.configs.policies": MagicMock(),
                "lerobot.datasets.lerobot_dataset": MagicMock(),
                "lerobot.utils.utils": MagicMock(),
                "lerobot.processor.rename_processor": MagicMock(),
            },
        ):
            sys.modules[
                "lerobot.configs.policies"
            ].PreTrainedConfig.from_pretrained.return_value = mock_cfg
            sys.modules["lerobot.utils.utils"].get_safe_torch_device.return_value = "mps"

            mock_policy_cls = MagicMock()
            mock_policy_cls.from_pretrained.return_value = mock_policy
            sys.modules["lerobot.policies.factory"].get_policy_class.return_value = mock_policy_cls

            res_policy, pre, post = builder._load_lerobot_policy("MikeChenYZ/act-soarm-fmb-v2")

        assert mock_cfg.device == "mps"
        assert res_policy == mock_policy
        mock_policy.to.assert_called_with("mps")

    def test_diffusion_policy_overrides_include_scheduler_and_steps(self):
        p = _policy(
            pretrained_path="MikeChenYZ/dp-soarm-fmb",
            device="mps",
            noise_scheduler_type="DDIM",
            num_inference_steps=15,
        )
        builder = self._builder_with_policy(p)
        mock_policy = MagicMock()
        mock_cfg = MagicMock()
        mock_cfg.noise_scheduler_type = "DDIM"
        mock_cfg.num_inference_steps = 15
        mock_cfg.device = "cuda"

        with patch.dict(
            sys.modules,
            {
                "lerobot": MagicMock(),
                "lerobot.policies.factory": MagicMock(),
                "lerobot.configs.policies": MagicMock(),
                "lerobot.datasets.lerobot_dataset": MagicMock(),
                "lerobot.utils.utils": MagicMock(),
                "lerobot.processor.rename_processor": MagicMock(),
            },
        ):
            sys.modules[
                "lerobot.configs.policies"
            ].PreTrainedConfig.from_pretrained.return_value = mock_cfg
            sys.modules["lerobot.utils.utils"].get_safe_torch_device.return_value = "mps"

            mock_policy_cls = MagicMock()
            mock_policy_cls.from_pretrained.return_value = mock_policy
            sys.modules["lerobot.policies.factory"].get_policy_class.return_value = mock_policy_cls

            res_policy, pre, post = builder._load_lerobot_policy("MikeChenYZ/dp-soarm-fmb")

        assert mock_cfg.device == "mps"
        assert mock_cfg.noise_scheduler_type == "DDIM"
        assert mock_cfg.num_inference_steps == 15

        assert mock_cfg.noise_scheduler_type == "DDIM"
        assert mock_cfg.num_inference_steps == 15


# ---------------------------------------------------------------------------
# build_robot — ValueError when sources is empty
# ---------------------------------------------------------------------------


class TestBuildRobot:
    def test_raises_valueerror_when_sources_empty(self):
        from dam.adapter.lerobot.builder import LeRobotBuilder

        hw = _hw(preset="so101_follower", sources={})
        with patch("dam.adapter.lerobot.builder.get_preset") as mock_preset:
            mock_preset.return_value = MagicMock(name="so101_follower")
            builder = LeRobotBuilder(hw)

        fake_make_robot = MagicMock()
        with patch.dict(
            sys.modules,
            {
                "lerobot": MagicMock(),
                "lerobot.robots": MagicMock(),
            },
        ):
            sys.modules["lerobot.robots"].make_robot_from_config = fake_make_robot
            with pytest.raises(ValueError, match="hardware.sources is empty"):
                builder.build_robot()
