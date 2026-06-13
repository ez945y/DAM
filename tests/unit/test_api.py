"""Tests for the public programmatic API (dam.build_runner / dam.run)."""

from __future__ import annotations

import dataclasses
import uuid

import pytest

import dam
from dam.api import RunSummary, build_runner, run
from dam.boundary.callbacks import get_catalog
from dam.preset.registry import get_preset
from dam.registry.callback import get_global_registry
from dam.runner.base import BaseRunner, RunnerStatus


class TestPublicSurface:
    def test_exported_symbols(self):
        for name in (
            "build_runner",
            "run",
            "RunSummary",
            "SafetyKinematicsResolver",
            "Runner",
            "RunnerStatus",
            "register_preset",
            "register_callback",
        ):
            assert name in dam.__all__
            assert hasattr(dam, name)

    def test_runner_alias_is_base_runner(self):
        assert dam.Runner is BaseRunner
        assert dam.RunnerStatus is RunnerStatus

    def test_run_summary_is_frozen_dataclass(self):
        assert dataclasses.is_dataclass(RunSummary)
        s = RunSummary(status="STOPPED", cycles=10, emergency=False)
        assert (s.status, s.cycles, s.emergency) == ("STOPPED", 10, False)
        with pytest.raises(dataclasses.FrozenInstanceError):
            s.cycles = 99  # type: ignore[misc]


class TestBuildRunner:
    def test_missing_stack_raises(self):
        with pytest.raises(FileNotFoundError):
            build_runner("/no/such/stack.yaml")

    def test_run_missing_stack_raises(self):
        # run() reaches build_runner first; build/connect failures propagate.
        with pytest.raises(FileNotFoundError):
            run("/no/such/stack.yaml", cycles=1)


class TestRegistrationAPI:
    def test_register_preset_writes_user_registry(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DAM_DATA_ROOT", str(tmp_path))

        preset = dam.register_preset(
            "pip_custom_arm",
            joint_names=["j0", "j1"],
            degrees_mode=False,
            urdf_path="/tmp/custom.urdf",
        )

        assert preset.name == "pip_custom_arm"
        loaded = get_preset("pip_custom_arm")
        assert loaded.joint_names == ["j0", "j1"]
        assert loaded.degrees_mode is False
        assert loaded.default_urdf_relpath == "/tmp/custom.urdf"

    def test_register_callback_direct_call_updates_registry_and_catalog(self):
        name = f"custom_direct_{uuid.uuid4().hex}"

        def callback(*, obs, action, threshold=1.0):
            return True

        returned = dam.register_callback(
            name,
            callback,
            layer="L2",
            category="execution",
            description="Direct custom callback",
            params={"threshold": "Limit value"},
        )

        assert returned is callback
        assert get_global_registry().get(name) is callback
        entry = next(item for item in get_catalog() if item["name"] == name)
        assert entry["layer"] == "L2"
        assert entry["category"] == "execution"
        assert entry["description"] == "Direct custom callback"
        assert entry["params"]["threshold"]["description"] == "Limit value"

    def test_register_callback_decorator_updates_registry(self):
        name = f"custom_decorator_{uuid.uuid4().hex}"

        @dam.register_callback(name, layer="L1", category="kinematics")
        def decorated_callback(*, action):
            return True

        assert get_global_registry().get(name) is decorated_callback

    def test_register_callback_rejects_unknown_layer(self):
        with pytest.raises(ValueError, match="Unknown callback layer"):
            dam.register_callback("bad_layer", lambda: True, layer="NOPE")
