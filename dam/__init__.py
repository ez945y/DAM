import tomllib
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _package_version
from pathlib import Path

from dam import testing
from dam.api import RunSummary, build_runner, run
from dam.decorators import callback, fallback, guard
from dam.guard.aggregator import aggregate_decisions
from dam.guard.base import Guard
from dam.guard.layer import GuardLayer
from dam.runner.base import BaseRunner as Runner
from dam.runner.base import RunnerStatus
from dam.types.action import ActionProposal, ValidatedAction
from dam.types.observation import Observation
from dam.types.result import GuardDecision, GuardResult
from dam.types.risk import CycleResult, RiskLevel


def _resolve_version() -> str:
    version_file = Path(__file__).resolve().parents[1] / "pyproject.toml"
    if version_file.exists():
        with version_file.open("rb") as fh:
            data = tomllib.load(fh)
        version = data.get("project", {}).get("version")
        if version:
            return str(version)
    try:
        return _package_version("dam")
    except PackageNotFoundError:
        return "unknown"


__version__ = _resolve_version()

__all__ = [
    "__version__",
    "guard",
    "callback",
    "fallback",
    "Guard",
    "aggregate_decisions",
    "GuardLayer",
    "GuardResult",
    "GuardDecision",
    "Observation",
    "ActionProposal",
    "ValidatedAction",
    "RiskLevel",
    "CycleResult",
    "testing",
    "build_runner",
    "run",
    "RunSummary",
    "Runner",
    "RunnerStatus",
]
