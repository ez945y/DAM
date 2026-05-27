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


class SafetyProcessorStep(_Base):  # type: ignore[valid-type,misc]
    """LeRobot processor step that validates actions through DAM guards.

    Insert into the ``robot_action_processor`` pipeline to get
    safety-checked actions recorded in your IL dataset::

        from dam import SafetyProcessorStep
        robot_action_processor.steps.insert(0, SafetyProcessorStep("safety.yaml"))

    The output dict has the **same keys and shape** as the input — only
    values are clamped to satisfy safety constraints.
    """

    def __init__(
        self,
        stackfile: str = "safety.yaml",
        *,
        task: str | None = None,
        joint_names: list[str] | None = None,
        degrees_mode: bool | None = None,
    ) -> None:
        self._stackfile = stackfile
        self._task = task
        self._joint_names = joint_names
        self._degrees_mode = degrees_mode
        self._guard: Any | None = None  # lazy SafetyGuard

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

        result = guard(action, obs)
        return result  # type: ignore[no-any-return]

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
        self._guard = None


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
