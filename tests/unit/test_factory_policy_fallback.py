"""Regression: _build_policy must tolerate stackfiles that reference a
preset name no longer in the registry (e.g. legacy stackfiles loaded after
a preset was deleted/renamed)."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def isolated_user_path(tmp_path, monkeypatch):
    monkeypatch.setenv("DAM_DATA_ROOT", str(tmp_path))
    yield tmp_path / "presets.yaml"


def _stackfile_with_preset(preset_name: str):
    """Minimal StackfileConfig stub with a policy + given preset name."""
    cfg = MagicMock()
    cfg.hardware = MagicMock()
    cfg.hardware.preset = preset_name
    cfg.policy = MagicMock()
    cfg.policy.pretrained_path = "ignored/repo"
    cfg.policy.device = "cpu"
    return cfg


def test_build_policy_falls_back_when_preset_not_in_registry(isolated_user_path, caplog):
    """A stackfile pointing at a since-deleted preset shouldn't crash —
    _build_policy should fall back to the first registered preset and warn."""
    from dam.runtime.factory import RuntimeFactory

    cfg = _stackfile_with_preset("generic_6dof")  # not in registry

    # Stub LeRobotBuilder so we don't actually load a policy.
    fake_builder = MagicMock()
    fake_builder.build_policy.return_value = None
    with (
        patch("dam.adapter.lerobot.builder.LeRobotBuilder", return_value=fake_builder),
        caplog.at_level("WARNING"),
    ):
        result = RuntimeFactory._build_policy(cfg)

    # No crash, no policy (because fake builder returned None), and a clear warning.
    assert result is None
    assert any("not in registry" in r.message for r in caplog.records)


def test_builder_falls_back_when_stackfile_preset_unknown(isolated_user_path, caplog):
    """LeRobotBuilder must also defend against a stackfile naming a preset
    that's not in the registry — otherwise the live .dam_stackfile loaded
    at restart crashes the server.
    """
    from dam.adapter.lerobot.builder import LeRobotBuilder
    from dam.config.schema import HardwareConfig

    hw = HardwareConfig(preset="generic_6dof")  # not in registry — falls back to first (sorted)
    with caplog.at_level("WARNING"):
        b = LeRobotBuilder(hw, policy=None)
    from dam.preset import list_presets

    assert b.preset.name == list_presets()[0]
    assert any("not in the registry" in r.message for r in caplog.records)


def test_build_policy_returns_none_when_registry_empty(isolated_user_path, monkeypatch, caplog):
    """If the registry is empty, skip the policy build with a warning rather
    than crashing."""
    from dam.runtime.factory import RuntimeFactory

    cfg = _stackfile_with_preset("anything")
    monkeypatch.setattr("dam.preset.list_presets", lambda: [])
    with caplog.at_level("WARNING"):
        result = RuntimeFactory._build_policy(cfg)
    assert result is None
    assert any("No presets registered" in r.message for r in caplog.records)
