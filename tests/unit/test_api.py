"""Tests for the public programmatic API (dam.build_runner / dam.run)."""

from __future__ import annotations

import dataclasses

import pytest

import dam
from dam.api import RunSummary, build_runner, run
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
