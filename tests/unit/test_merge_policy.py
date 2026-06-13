"""Tests for the data-defined merge_policy registry."""

from __future__ import annotations

import numpy as np
import pytest

from dam.runtime import merge_policy
from dam.runtime.merge_policy import (
    elementwise_max,
    elementwise_min,
    overwrite,
    register,
    resolve,
    take_max,
    take_min,
)


def test_overwrite_returns_new():
    assert overwrite(1, 2) == 2
    assert overwrite([1, 2], [3]) == [3]


def test_scalar_min_max():
    assert take_min(3, 5) == 3
    assert take_max(3, 5) == 5


def test_elementwise_min_max_returns_python_list():
    assert elementwise_min([1.0, 5.0], [3.0, 4.0]) == [1.0, 4.0]
    assert elementwise_max([1.0, 5.0], [3.0, 4.0]) == [3.0, 5.0]
    # Not an ndarray — keeps CycleRecord serialisation simple
    assert isinstance(elementwise_min([1.0], [2.0]), list)


def test_resolve_returns_overwrite_by_default():
    assert resolve("totally_unknown_key") is overwrite


def test_builtin_safety_keys_resolve_to_tightest():
    # Caps tighten
    assert resolve("max_speed") is take_min
    assert resolve("upper") is elementwise_min
    assert resolve("max_velocity") is elementwise_min
    assert resolve("max_acceleration") is elementwise_min
    # Floors widen
    assert resolve("lower") is elementwise_max
    # Slack weight: more boundaries demanding stricter wins
    assert resolve("slack_weight") is take_max
    # CBF alpha: smaller is more conservative
    assert resolve("cbf_alpha") is take_min


def test_register_idempotent():
    def custom(old, new):
        return old

    register("__test_key__", custom)
    assert resolve("__test_key__") is custom
    # Re-registering overwrites silently
    register("__test_key__", overwrite)
    assert resolve("__test_key__") is overwrite


def test_pool_build_auto_injects_dt():
    """_build_config_pool reads control_hz and exposes dt to guards."""
    from dam.config.schema import SafetyConfig, StackfileConfig
    from dam.runtime.guard_runtime import GuardRuntime

    cfg = StackfileConfig(
        version="1",
        safety=SafetyConfig(control_hz=100.0, no_task_behavior="emergency_stop"),
        boundaries={},
        tasks={},
        guards=[],
    )
    pool = GuardRuntime._build_config_pool(cfg)
    assert "dt" in pool
    assert pool["dt"] == pytest.approx(0.01)


def test_pool_build_merges_via_registry():
    """Two boundaries with the same `upper` — elementwise minimum should win."""
    from dam.config.schema import ContainerConfig, NodeConfig, SafetyConfig, StackfileConfig
    from dam.runtime.guard_runtime import GuardRuntime

    b1 = ContainerConfig(
        type="single",
        layer="L1",
        nodes=[NodeConfig(node_id="a", params={"upper": [2.0, 2.0]})],
    )
    b2 = ContainerConfig(
        type="single",
        layer="L1",
        nodes=[NodeConfig(node_id="b", params={"upper": [1.5, 3.0]})],
    )
    cfg = StackfileConfig(
        version="1",
        safety=SafetyConfig(no_task_behavior="emergency_stop"),
        boundaries={"b1": b1, "b2": b2},
        tasks={},
        guards=[],
    )
    pool = GuardRuntime._build_config_pool(cfg)
    np.testing.assert_array_equal(pool["upper"], [1.5, 2.0])  # min per element


def test_pool_build_slack_weight_takes_max():
    """Slack weights — bigger boundaries' priority wins (must override take_min default)."""
    from dam.config.schema import ContainerConfig, NodeConfig, SafetyConfig, StackfileConfig
    from dam.runtime.guard_runtime import GuardRuntime

    b1 = ContainerConfig(
        type="single",
        layer="L1",
        nodes=[NodeConfig(node_id="a", params={"slack_weight": 100.0})],
    )
    b2 = ContainerConfig(
        type="single",
        layer="L1",
        nodes=[NodeConfig(node_id="b", params={"slack_weight": 1_000_000.0})],
    )
    cfg = StackfileConfig(
        version="1",
        safety=SafetyConfig(no_task_behavior="emergency_stop"),
        boundaries={"b1": b1, "b2": b2},
        tasks={},
        guards=[],
    )
    pool = GuardRuntime._build_config_pool(cfg)
    assert pool["slack_weight"] == 1_000_000.0


def test_unknown_param_overwrites_with_warning(caplog):
    """A key with no registered strategy goes through ``overwrite`` and logs."""
    from dam.config.schema import ContainerConfig, NodeConfig, SafetyConfig, StackfileConfig
    from dam.runtime.guard_runtime import GuardRuntime

    b1 = ContainerConfig(
        type="single",
        layer="L1",
        nodes=[NodeConfig(node_id="a", params={"unknown_param_xyz": 1})],
    )
    b2 = ContainerConfig(
        type="single",
        layer="L1",
        nodes=[NodeConfig(node_id="b", params={"unknown_param_xyz": 2})],
    )
    cfg = StackfileConfig(
        version="1",
        safety=SafetyConfig(no_task_behavior="emergency_stop"),
        boundaries={"b1": b1, "b2": b2},
        tasks={},
        guards=[],
    )
    with caplog.at_level("WARNING", logger="dam.runtime.guard_runtime"):
        pool = GuardRuntime._build_config_pool(cfg)
    assert pool["unknown_param_xyz"] == 2  # last-write-wins
    assert any("unknown_param_xyz" in r.message for r in caplog.records)
