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

import logging
import sys
import time
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
        self._first_cycle_time: float | None = None

    def record_cycle(self, results: list[Any]) -> list[Any]:
        """Process one cycle's guard results. Returns notable (non-PASS) results."""
        if self._first_cycle_time is None:
            self._first_cycle_time = time.monotonic()
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
        if self._first_cycle_time is not None:
            elapsed = time.monotonic() - self._first_cycle_time
        else:
            elapsed = 0.0
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


# ── Edge-triggered printer ────────────────────────────────────────────────


class _EdgePrinter:
    """Only prints when a boundary transitions PASS→CLAMP or CLAMP→PASS.

    Prevents terminal flooding at 30Hz by tracking per-boundary state.
    When a sustained clamp ends, prints the duration and count.
    """

    def __init__(self) -> None:
        self._active: dict[str, tuple[float, int]] = {}

    def update(self, notable: list[Any]) -> None:
        now = time.monotonic()
        current_names = {r.guard_name for r in notable}

        for name in list(self._active):
            if name not in current_names:
                start, count = self._active.pop(name)
                dur = now - start
                if count > 1:
                    print(
                        f"[DAM] \033[32m  OK\033[0m {name}: "
                        f"resolved after {count} cycles ({dur:.1f}s)",
                        file=sys.stderr,
                    )

        for r in notable:
            if r.guard_name not in self._active:
                self._active[r.guard_name] = (now, 0)
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

        for r in notable:
            if r.guard_name in self._active:
                start, count = self._active[r.guard_name]
                self._active[r.guard_name] = (start, count + 1)


# ── Processor step ────────────────────────────────────────────────────────


class SafetyProcessorStep(_Base):  # type: ignore[valid-type,misc]
    """LeRobot processor step that validates actions through DAM guards.

    Features beyond basic safety checking:

    - **Live feedback**: edge-triggered — prints when a clamp starts and
      when it resolves (with duration), not on every cycle
    - **Session summary**: prints guard statistics when recording ends

    For full guard event logging, add a ``loopback:`` section to your
    stackfile — the existing MCAP pipeline records every cycle with guard
    decisions, and ``mcap_triage.py`` can analyse them post-hoc.
    """

    def __init__(
        self,
        stackfile: str = "safety.yaml",
        *,
        task: str | None = None,
        joint_names: list[str] | None = None,
        degrees_mode: bool | None = None,
        hold_first_cycle: bool = True,
        quiet: bool = False,
    ) -> None:
        self._stackfile = stackfile
        self._task = task
        self._joint_names = joint_names
        self._degrees_mode = degrees_mode
        self._hold_first_cycle = hold_first_cycle
        self._quiet = quiet
        self._guard: Any | None = None
        self._stats: _RecordingStats = _RecordingStats()
        self._printer: _EdgePrinter = _EdgePrinter()
        self._summary_printed = False
        self._has_guarded_cycle = False

    def _ensure_guard(self) -> Any:
        if self._guard is None:
            from dam.api import SafetyGuard

            self._guard = SafetyGuard(
                self._stackfile,
                task=self._task,
                joint_names=self._joint_names,
                degrees_mode=self._degrees_mode,
            )
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

        if self._hold_first_cycle and not self._has_guarded_cycle:
            result = guard(obs, obs)
        else:
            result = guard(action, obs)
        self._has_guarded_cycle = True

        notable = self._stats.record_cycle(guard.last_results)
        if not self._quiet:
            self._printer.update(notable)

        if isinstance(result, dict):
            return {**action, **result}
        return result  # type: ignore[no-any-return]

    def _print_summary(self) -> None:
        if self._summary_printed or self._stats.total_cycles == 0:
            return
        self._summary_printed = True

        if self._quiet:
            return

        print("\n[DAM] Recording session summary:", file=sys.stderr)
        for line in self._stats.summary_lines():
            print(line, file=sys.stderr)

    def transform_features(self, features: Any) -> Any:
        """Safety clamping does not alter feature shapes — pass through."""
        return features

    def get_config(self) -> dict[str, Any]:
        return {
            "stackfile": self._stackfile,
            "task": self._task,
            "joint_names": self._joint_names,
            "degrees_mode": self._degrees_mode,
            "hold_first_cycle": self._hold_first_cycle,
        }

    def reset(self) -> None:
        self._print_summary()
        self._guard = None
        self._stats = _RecordingStats()
        self._summary_printed = False
        self._has_guarded_cycle = False

    def __del__(self) -> None:
        self._print_summary()


def make_safe_processors(
    stackfile: str = "safety.yaml",
    *,
    task: str | None = None,
    joint_names: list[str] | None = None,
    degrees_mode: bool | None = None,
    hold_first_cycle: bool = True,
) -> tuple[Any, Any, Any]:
    """Drop-in replacement for lerobot's ``make_default_processors()``.

    Returns ``(teleop_action_processor, robot_action_processor,
    robot_observation_processor)`` with a :class:`SafetyProcessorStep`
    prepended to the robot action pipeline.
    """
    from lerobot.processor.factory import make_default_processors

    teleop, robot_action, obs_proc = make_default_processors()
    step = SafetyProcessorStep(
        stackfile,
        task=task,
        joint_names=joint_names,
        degrees_mode=degrees_mode,
        hold_first_cycle=hold_first_cycle,
    )
    robot_action.steps.insert(0, step)
    return teleop, robot_action, obs_proc
