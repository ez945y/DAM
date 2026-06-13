"""Tests for the public programmatic API (dam.build_runner / dam.run)."""

from __future__ import annotations

import dataclasses
import uuid

import pytest

import dam
from dam.api import RunSummary, build_runner, run
from dam.boundary.callbacks import get_catalog
from dam.interface.registry import get_global_interface_registry
from dam.registry.callback import get_global_registry
from dam.runner.base import BaseRunner, RunnerStatus
from dam.solver.registry import get_global_solver_registry


class TestPublicSurface:
    def test_exported_symbols(self):
        for name in (
            "build_runner",
            "run",
            "RunSummary",
            "Runner",
            "RunnerStatus",
            "SensorAdapter",
            "ActionAdapter",
            "register_callback",
            "register_solver",
            "register_solver_factory",
            "register_read_interface",
            "register_write_interface",
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

        def factory(params):
            return Solver(params["gain"])

        returned = dam.register_solver_factory(
            solver_type,
            factory,
            capabilities=["kinematics"],
        )

        assert returned is factory
        solver = get_global_solver_registry().build(
            solver_name,
            solver_type,
            {"gain": 2.0},
        )
        assert solver.gain == 2.0
        assert get_global_solver_registry().get(solver_name) is solver

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
