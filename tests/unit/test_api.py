"""Tests for the public programmatic API (dam.build_runner / dam.run)."""

from __future__ import annotations

import dataclasses
import inspect
import uuid

import pytest

import dam
import dam.api as dam_api
from dam.api import RunSummary, build_runner, run
from dam.boundary.callbacks import get_catalog
from dam.interface.registry import get_global_interface_registry
from dam.preset import get_preset
from dam.registry.callback import get_global_registry
from dam.runner.base import BaseRunner, RunnerStatus
from dam.solver.registry import get_global_solver_registry


class TestPublicSurface:
    def test_exported_symbols(self):
        public_api_names = sorted(
            name
            for name, value in inspect.getmembers(dam_api)
            if not name.startswith("_")
            and (inspect.isfunction(value) or inspect.isclass(value))
            and getattr(value, "__module__", None) == dam_api.__name__
        )
        for name in public_api_names:
            assert name in dam.__all__
            assert hasattr(dam, name)
            assert getattr(dam, name) is getattr(dam_api, name)

        for name in (
            "Runner",
            "RunnerStatus",
            "SensorAdapter",
            "ActionAdapter",
            "Guard",
            "GuardLayer",
            "GuardResult",
            "GuardDecision",
            "Observation",
            "ActionProposal",
            "ValidatedAction",
            "RiskLevel",
            "CycleResult",
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

    def test_register_solver_instance(self):
        name = f"solver_{uuid.uuid4().hex}"
        capability = f"base_dynamics_{uuid.uuid4().hex}"
        solver = object()

        returned = dam.register_solver(name, solver, capabilities=[capability])

        assert returned is solver
        registry = get_global_solver_registry()
        assert registry.get(name) is solver
        assert registry.select(capability) is solver

    def test_register_solver_factory(self):
        solver_type = f"solver_type_{uuid.uuid4().hex}"
        solver_name = f"solver_name_{uuid.uuid4().hex}"

        class Solver:
            def __init__(self, gain: float):
                self.gain = gain

        # Factory declares the param it needs by name; the registry injects it.
        @dam.solver_factory(solver_type, capabilities=["kinematics"])
        def factory(gain):
            return Solver(gain)

        assert callable(factory)
        solver = get_global_solver_registry().build(
            solver_name,
            solver_type,
            {"gain": 2.0},
        )
        assert solver.gain == pytest.approx(2.0)
        assert get_global_solver_registry().get(solver_name) is solver

    def test_register_preset_updates_registry(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DAM_DATA_ROOT", str(tmp_path))
        name = f"jetbot_{uuid.uuid4().hex}"

        preset = dam.register_preset(
            name,
            joint_names=["left_wheel", "right_wheel"],
            asset={"type": "usd", "path": "/robots/jetbot.usd"},
            solvers={"ackermann": {"capabilities": ["rollout"]}},
            action_layout=[{"name": "base", "keys": ["v", "omega"], "solver": "ackermann"}],
        )

        assert preset.name == name
        loaded = get_preset(name)
        assert loaded.joint_names == ["left_wheel", "right_wheel"]
        assert loaded.asset == {"type": "usd", "path": "/robots/jetbot.usd"}
        assert "ackermann" in loaded.solvers
        assert loaded.action_layout[0]["keys"] == ["v", "omega"]

    def test_register_read_and_write_interfaces(self):
        read_type = f"read_iface_{uuid.uuid4().hex}"
        write_type = f"write_iface_{uuid.uuid4().hex}"

        def read_factory(name, cfg, context):
            return {"role": "read", "name": name, "type": cfg.type, "context": context}

        def write_factory(name, cfg, context):
            return {"role": "write", "name": name, "type": cfg.type, "context": context}

        assert dam.register_read_interface(read_type, read_factory) is read_factory
        assert dam.register_write_interface(write_type, write_factory) is write_factory

        registry = get_global_interface_registry()
        assert registry.has_read(read_type)
        assert registry.has_write(write_type)

    def test_register_solver_factory_decorator(self):
        solver_type = f"deco_solver_{uuid.uuid4().hex}"

        # A factory with **kwargs receives every injected param.
        @dam.solver_factory(solver_type, capabilities=["rollout"])
        def make(**params):
            return {"type": solver_type, "params": dict(params)}

        # Decorator returns the factory unchanged, and the name is the type.
        assert callable(make)
        solver = get_global_solver_registry().build("inst", solver_type, {"k": 1})
        assert solver["type"] == solver_type
        assert solver["params"] == {"k": 1}

    def test_register_read_interface_decorator(self):
        read_type = f"deco_read_{uuid.uuid4().hex}"

        @dam.register_read_interface(read_type)
        def factory(name, cfg, context):
            return {"name": name}

        assert callable(factory)
        assert get_global_interface_registry().has_read(read_type)
