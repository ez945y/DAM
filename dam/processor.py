"""LeRobot processor integration — safety-guard actions during recording.

Provides :class:`SafetyProcessorStep`, a drop-in
``RobotActionProcessorStep`` that validates every action through DAM's
guard pipeline before it reaches the robot.

.. code-block:: python

    from lerobot.processor.factory import make_default_processors
    from dam import SafetyProcessorStep

    teleop, robot_action, obs = make_default_processors()
    robot_action.steps.insert(0, SafetyProcessorStep("safety.yaml"))
"""

from __future__ import annotations

import atexit
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _import_lerobot_base() -> type:
    """Import lerobot's base class lazily so ``import dam`` stays cheap."""
    from lerobot.processor.pipeline import RobotActionProcessorStep

    return RobotActionProcessorStep  # type: ignore[no-any-return]


# Build the class at import time only when lerobot is available.
try:
    _Base = _import_lerobot_base()
    _LEROBOT_AVAILABLE = True
except ImportError:
    from abc import ABC

    _Base = ABC
    _LEROBOT_AVAILABLE = False


# ── Recording stats tracker ──────────────────────────────────────────────


class _RecordingStats:
    """Accumulates guard events during a recording session."""

    def __init__(self) -> None:
        self.total_cycles: int = 0
        self.clamps: int = 0
        self.rejects: int = 0
        self.faults: int = 0
        self.boundary_counts: dict[str, int] = {}
        self.start_time: float = time.monotonic()

    def record_cycle(self, results: list[Any]) -> list[Any]:
        """Process one cycle's guard results. Returns notable (non-PASS) results."""
        self.total_cycles += 1
        notable = []
        for r in results:
            if r.decision.name == "CLAMP":
                self.clamps += 1
                self.boundary_counts[r.guard_name] = self.boundary_counts.get(r.guard_name, 0) + 1
                notable.append(r)
            elif r.decision.name == "REJECT":
                self.rejects += 1
                self.boundary_counts[r.guard_name] = self.boundary_counts.get(r.guard_name, 0) + 1
                notable.append(r)
            elif r.decision.name == "FAULT":
                self.faults += 1
                notable.append(r)
        return notable

    def summary_lines(self) -> list[str]:
        elapsed = time.monotonic() - self.start_time
        minutes = elapsed / 60

        lines = []
        lines.append(f"  {self.total_cycles} cycles in {minutes:.1f}min")

        if self.clamps == 0 and self.rejects == 0:
            lines.append("  No guard interventions — all actions passed cleanly")
            return lines

        pct = (self.clamps + self.rejects) / max(self.total_cycles, 1) * 100
        parts = []
        if self.clamps:
            parts.append(f"{self.clamps} clamps")
        if self.rejects:
            parts.append(f"{self.rejects} rejects")
        if self.faults:
            parts.append(f"{self.faults} faults")
        lines.append(f"  {', '.join(parts)} ({pct:.1f}% of cycles)")

        if self.boundary_counts:
            ranked = sorted(self.boundary_counts.items(), key=lambda x: -x[1])
            top = ranked[:3]
            lines.append(
                "  Top boundaries: " + ", ".join(f"{name} ({count})" for name, count in top)
            )

        return lines


# ── Guard log writer ─────────────────────────────────────────────────────


class _GuardLogWriter:
    """Writes notable guard events to a JSONL file next to the dataset."""

    def __init__(self, log_path: Path) -> None:
        self._path = log_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self._path.open("a")

    def write(self, cycle: int, results: list[Any]) -> None:
        for r in results:
            entry = {
                "cycle": cycle,
                "boundary": r.guard_name,
                "decision": r.decision.name,
                "reason": r.reason,
            }
            self._file.write(json.dumps(entry) + "\n")

    def close(self) -> None:
        self._file.close()


def _resolve_log_path(stackfile: str) -> Path | None:
    """Derive guard log path from the stackfile's recording.dataset.repo_id."""
    import os

    import yaml

    path = Path(stackfile)
    if not path.exists():
        return None
    try:
        with path.open() as f:
            data = yaml.safe_load(f) or {}
        recording = data.get("recording", {})
        dataset = recording.get("dataset", {}) if isinstance(recording, dict) else {}
        repo_id = os.path.expandvars(str(dataset.get("repo_id", "")))
        if not repo_id:
            return None
        cache_dir = Path.home() / ".cache" / "huggingface" / "lerobot" / repo_id
        return cache_dir / ".guard_log.jsonl"
    except Exception:  # noqa: BLE001
        return None


# ── Processor step ────────────────────────────────────────────────────────


class SafetyProcessorStep(_Base):  # type: ignore[valid-type,misc]
    """LeRobot processor step that validates actions through DAM guards.

    Features beyond basic safety checking:

    - **Live feedback**: prints a one-liner to stderr on clamp/reject events
    - **Session summary**: prints guard statistics when recording ends
    - **Guard log**: writes ``.guard_log.jsonl`` next to the dataset cache
    """

    def __init__(
        self,
        stackfile: str = "safety.yaml",
        *,
        task: str | None = None,
        joint_names: list[str] | None = None,
        degrees_mode: bool | None = None,
        quiet: bool = False,
    ) -> None:
        self._stackfile = stackfile
        self._task = task
        self._joint_names = joint_names
        self._degrees_mode = degrees_mode
        self._quiet = quiet
        self._guard: Any | None = None
        self._stats: _RecordingStats = _RecordingStats()
        self._log_writer: _GuardLogWriter | None = None
        self._summary_printed = False
        atexit.register(self._print_summary)

    def _ensure_guard(self) -> Any:
        if self._guard is None:
            from dam.api import SafetyGuard

            self._guard = SafetyGuard(
                self._stackfile,
                task=self._task,
                joint_names=self._joint_names,
                degrees_mode=self._degrees_mode,
            )

            log_path = _resolve_log_path(self._stackfile)
            if log_path is not None:
                self._log_writer = _GuardLogWriter(log_path)
                if not self._quiet:
                    print(f"[DAM] Guard log: {log_path}", file=sys.stderr)

        return self._guard

    def action(self, action: dict[str, Any]) -> dict[str, Any]:
        guard = self._ensure_guard()

        obs: dict[str, Any] | None = None
        if self._current_transition is not None:
            obs = self._current_transition.get("observation")

        if obs is None:
            logger.warning(
                "SafetyProcessorStep: no observation in transition, "
                "passing action through unguarded"
            )
            return action

        result = guard(action, obs)

        notable = self._stats.record_cycle(guard.last_results)
        if notable:
            if not self._quiet:
                self._print_notable(notable)
            if self._log_writer is not None:
                self._log_writer.write(self._stats.total_cycles, notable)

        return result  # type: ignore[no-any-return]

    def _print_notable(self, results: list[Any]) -> None:
        for r in results:
            tag = (
                "\033[33mCLAMP\033[0m"
                if r.decision.name == "CLAMP"
                else f"\033[31m{r.decision.name}\033[0m"
            )
            reason = r.reason[:80] if r.reason else ""
            print(
                f"[DAM] {tag} {r.guard_name}: {reason}",
                file=sys.stderr,
            )

    def _print_summary(self) -> None:
        if self._summary_printed or self._stats.total_cycles == 0:
            return
        self._summary_printed = True

        if self._log_writer is not None:
            self._log_writer.close()

        if self._quiet:
            return

        print("\n[DAM] Recording session summary:", file=sys.stderr)
        for line in self._stats.summary_lines():
            print(line, file=sys.stderr)

        if self._log_writer is not None:
            print(f"  Guard log saved: {self._log_writer._path}", file=sys.stderr)

    def transform_features(self, features: Any) -> Any:
        """Safety clamping does not alter feature shapes — pass through."""
        return features

    def get_config(self) -> dict[str, Any]:
        return {
            "stackfile": self._stackfile,
            "task": self._task,
            "joint_names": self._joint_names,
            "degrees_mode": self._degrees_mode,
        }

    def reset(self) -> None:
        self._print_summary()
        self._guard = None
        self._stats = _RecordingStats()
        self._summary_printed = False


def make_safe_processors(
    stackfile: str = "safety.yaml",
    *,
    task: str | None = None,
    joint_names: list[str] | None = None,
    degrees_mode: bool | None = None,
) -> tuple[Any, Any, Any]:
    """Drop-in replacement for lerobot's ``make_default_processors()``.

    Returns ``(teleop_action_processor, robot_action_processor,
    robot_observation_processor)`` with a :class:`SafetyProcessorStep`
    prepended to the robot action pipeline.
    """
    from lerobot.processor.factory import make_default_processors

    teleop, robot_action, obs_proc = make_default_processors()
    step = SafetyProcessorStep(
        stackfile, task=task, joint_names=joint_names, degrees_mode=degrees_mode
    )
    robot_action.steps.insert(0, step)
    return teleop, robot_action, obs_proc
