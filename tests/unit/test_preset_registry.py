"""Unit tests for the file-backed preset registry."""

import pytest

from dam.preset import (
    delete_preset,
    get_preset,
    list_preset_entries,
    list_presets,
    upsert_preset,
)


@pytest.fixture
def isolated_user_path(tmp_path, monkeypatch):
    """Redirect DAM_DATA_ROOT so writes don't touch the real ./data dir."""
    monkeypatch.setenv("DAM_DATA_ROOT", str(tmp_path))
    yield tmp_path / "presets.yaml"


# ── list_presets / bundled seeds ─────────────────────────────────────────────


def test_list_presets_contains_seeded_so101(isolated_user_path):
    names = list_presets()
    assert "so101_follower" in names


def test_list_presets_is_sorted(isolated_user_path):
    names = list_presets()
    assert names == sorted(names)


# ── get_preset ────────────────────────────────────────────────────────────────


def test_get_preset_so101_by_name(isolated_user_path):
    p = get_preset("so101_follower")
    assert p.name == "so101_follower"
    assert p.degrees_mode is True
    assert p.asset_type() == "urdf"
    assert p.asset_path() is not None
    assert "arm_kinematics" in p.solvers
    assert "shoulder_pan" in p.joint_names
    assert p.joint_names[-1] == "gripper"


def test_get_preset_normalizes_hyphens(isolated_user_path):
    p = get_preset("so101-follower")
    assert p.name == "so101_follower"


def test_get_preset_unknown_raises_key_error(isolated_user_path):
    with pytest.raises(KeyError, match="Unknown robot preset"):
        get_preset("nonexistent_robot")


# ── list_preset_entries ──────────────────────────────────────────────────────


def test_list_preset_entries_returns_dicts(isolated_user_path):
    entries = list_preset_entries()
    so101 = next(e for e in entries if e["name"] == "so101_follower")
    assert so101["degrees_mode"] is True
    assert len(so101["joint_names"]) == 6
    assert so101["asset"]["type"] == "urdf"
    assert so101["asset"]["path"]
    assert "arm_kinematics" in so101["solvers"]
    assert so101["action_layout"][0]["name"] == "arm"


# ── upsert / delete round-trip ────────────────────────────────────────────────


def test_upsert_then_delete_user_preset(isolated_user_path):
    p = upsert_preset(
        "custom_test_arm",
        joint_names=["j0", "j1", "j2"],
        degrees_mode=False,
        asset={"type": "urdf", "path": "/abs/path/custom.urdf"},
    )
    assert p.name == "custom_test_arm"
    assert p.joint_names == ["j0", "j1", "j2"]
    assert "custom_test_arm" in list_presets()
    assert delete_preset("custom_test_arm") is True
    assert "custom_test_arm" not in list_presets()
    assert delete_preset("custom_test_arm") is False


def test_delete_bundled_preset_uses_tombstone(isolated_user_path):
    """Deleting a bundled preset should hide it via tombstone without
    touching the in-git bundled file."""
    assert "so101_follower" in list_presets()
    assert delete_preset("so101_follower") is True
    assert "so101_follower" not in list_presets()
    # Bundled file unchanged
    from dam.preset.registry import BUNDLED_PATH, _load_one

    assert "so101_follower" in _load_one(BUNDLED_PATH)


def test_upsert_overrides_bundled(isolated_user_path):
    """User can override a bundled preset by upserting the same name."""
    upsert_preset(
        "so101_follower",
        joint_names=["a", "b"],
        degrees_mode=False,
        asset=None,
    )
    p = get_preset("so101_follower")
    assert p.joint_names == ["a", "b"]
    assert p.degrees_mode is False


def test_upsert_after_tombstone_resurrects(isolated_user_path):
    """If a bundled preset was tombstoned, upserting brings it back."""
    delete_preset("so101_follower")
    assert "so101_follower" not in list_presets()
    upsert_preset(
        "so101_follower",
        joint_names=["x"],
        degrees_mode=True,
        asset=None,
    )
    assert "so101_follower" in list_presets()


def test_rename_via_upsert_removes_old_key(isolated_user_path):
    upsert_preset(
        "first_name",
        joint_names=["j0"],
        degrees_mode=True,
        asset=None,
    )
    upsert_preset(
        "second_name",
        joint_names=["j0"],
        degrees_mode=True,
        asset=None,
        rename_from="first_name",
    )
    names = list_presets()
    assert "second_name" in names
    assert "first_name" not in names


def test_upsert_rejects_empty_joint_names(isolated_user_path):
    with pytest.raises(ValueError, match="at least one joint name"):
        upsert_preset("x", joint_names=[], degrees_mode=True, asset=None)


def test_upsert_rejects_empty_name(isolated_user_path):
    with pytest.raises(ValueError, match="must not be empty"):
        upsert_preset("", joint_names=["j"], degrees_mode=True, asset=None)


def test_atomic_write_does_not_leave_tmp(isolated_user_path):
    """The .tmp swap should never leave a stray file behind."""
    upsert_preset(
        "atomic_test",
        joint_names=["j0"],
        degrees_mode=True,
        asset=None,
    )
    assert not isolated_user_path.with_suffix(isolated_user_path.suffix + ".tmp").exists()
