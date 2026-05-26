"""Built-in Context subclasses — fallback behaviours as state machines.

Each subclass extends :class:`StepContext` and implements one or more of
``on_enter`` / ``step`` / ``is_done``. They consume the same chassis as
NormalContext (no duplicate guard validation logic) but install one or two
hook points per Context:

    obs → [source]    → proposal → [chassis] → validated → [output] → sink
                        ▲                                  ▲
              Normal: policy                       Normal: identity
              Retreat:    planner                 SlowDown:  scale
              HoldPosition: replay prev            (rest:     identity)
              EmergencyStop / WaitAndRetry: bypass — skip sink entirely

See ``project_runtime_context_state_machine`` memory for the full design.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

from dam.decorators import fallback
from dam.runtime.context import (
    SEVERITY_EMERGENCY_STOP,
    SEVERITY_HOLD_POSITION,
    SEVERITY_RETREAT,
    SEVERITY_SLOW_DOWN,
    SEVERITY_WAIT_AND_RETRY,
    StepContext,
    StepResult,
    make_context,
)
from dam.types.action import ActionProposal, ValidatedAction
from dam.types.result import GuardDecision

if TYPE_CHECKING:
    from dam.runtime.guard_runtime import GuardRuntime
    from dam.types.observation import Observation
    from dam.types.result import GuardResult


logger = logging.getLogger(__name__)


def _hold_action_from(positions: list[float] | np.ndarray) -> ValidatedAction:
    """Build a ValidatedAction that re-sends the given joint positions and
    commands zero velocity. Used by HoldPosition / Retreat / SlowDown."""
    arr = np.asarray(positions, dtype=np.float64)
    return ValidatedAction(
        target_joint_positions=arr,
        target_joint_velocities=np.zeros_like(arr),
        was_clamped=False,
        original_proposal=None,
    )


# ── EmergencyStop ───────────────────────────────────────────────────────────


@fallback("emergency_stop", monitors_hardware=False)
class EmergencyStopContext(StepContext):
    """Terminal: call sink.emergency_stop() once on entry, send nothing
    afterwards. Requires external reset to leave (is_done always False)."""

    name = "emergency_stop"
    severity = SEVERITY_EMERGENCY_STOP
    requires_proposal = False
    monitors_hardware = False

    def on_enter(self, runtime: GuardRuntime, trigger: GuardResult | None = None) -> None:
        sink = getattr(runtime, "_sink", None)
        if sink is not None and hasattr(sink, "emergency_stop"):
            try:
                sink.emergency_stop()
            except Exception as e:  # noqa: BLE001 — best-effort, never propagate from e-stop
                logger.error("EmergencyStop: sink.emergency_stop() raised: %s", e)
        logger.critical(
            "EMERGENCY STOP entered | trigger=%s reason=%s",
            getattr(trigger, "guard_name", None),
            getattr(trigger, "reason", None),
        )

    def step(
        self,
        obs: Observation,
        runtime: GuardRuntime,
        *,
        proposal: ActionProposal | None,
        trace_id: str,
        now: float,
    ) -> StepResult:
        # Bypass — don't send anything. Sink already commanded to stop on entry.
        return StepResult(action=None, guard_results=[])


# ── HoldPosition ────────────────────────────────────────────────────────────


@fallback("hold_position")
class HoldPositionContext(StepContext):
    """Re-send the last validated joint positions every cycle. Stable hold;
    never voluntarily yields (preempt is the only exit)."""

    name = "hold_position"
    severity = SEVERITY_HOLD_POSITION
    requires_proposal = False

    def __init__(self) -> None:
        self._snapshot: list[float] | None = None

    def on_enter(self, runtime: GuardRuntime, trigger: GuardResult | None = None) -> None:
        prev = getattr(runtime, "_prev_validated_positions", None)
        self._snapshot = list(prev) if prev else None
        if self._snapshot is None:
            logger.warning(
                "HoldPosition entered with no prev_validated_positions — "
                "will not command sink until policy produces one"
            )

    def step(
        self,
        obs: Observation,
        runtime: GuardRuntime,
        *,
        proposal: ActionProposal | None,
        trace_id: str,
        now: float,
    ) -> StepResult:
        if self._snapshot is None:
            # No baseline to hold yet — skip this cycle's send.
            return StepResult(action=None, guard_results=[])
        return StepResult(action=_hold_action_from(self._snapshot), guard_results=[])


# ── WaitAndRetry ────────────────────────────────────────────────────────────


@fallback("wait_and_retry")
class WaitAndRetryContext(StepContext):
    """Pause for a configured duration, then yield. Sink receives nothing
    during the wait; on yield the runtime pops back to the previous Context.

    Configure with either ``wait_seconds`` (preferred — auto-converted using
    runtime control frequency on entry) or ``wait_cycles`` (fixed count).
    ``wait_seconds`` takes precedence when both are set.
    """

    name = "wait_and_retry"
    severity = SEVERITY_WAIT_AND_RETRY
    requires_proposal = False

    def __init__(
        self,
        wait_cycles: int = 5,
        wait_seconds: float | None = None,
    ) -> None:
        self._wait_cycles = max(1, int(wait_cycles))
        self._wait_seconds = wait_seconds
        self._remaining = self._wait_cycles

    def on_enter(self, runtime: GuardRuntime, trigger: GuardResult | None = None) -> None:
        if self._wait_seconds is not None:
            hz = float(getattr(runtime, "_control_frequency_hz", 50.0))
            self._remaining = max(1, int(round(self._wait_seconds * hz)))
        else:
            self._remaining = self._wait_cycles

    def step(
        self,
        obs: Observation,
        runtime: GuardRuntime,
        *,
        proposal: ActionProposal | None,
        trace_id: str,
        now: float,
    ) -> StepResult:
        self._remaining -= 1
        return StepResult(action=None, guard_results=[])

    def is_done(self, obs: Observation, runtime: GuardRuntime) -> bool:
        return self._remaining <= 0


# ── SlowDown ────────────────────────────────────────────────────────────────


@fallback("slow_down")
class SlowDownContext(StepContext):
    """Run policy through the chassis as usual, then scale the joint-position
    delta from prev → validated by ``scale``. Yields once the *triggering*
    guard reports no further violation for ``hysteresis_cycles`` consecutive
    cycles — i.e. the temperature / current / velocity that caused entry has
    actually cleared, not just one transient sample."""

    name = "slow_down"
    severity = SEVERITY_SLOW_DOWN

    def __init__(
        self,
        scale: float = 0.5,
        hysteresis_seconds: float | None = None,
    ) -> None:
        self._scale = max(0.0, min(1.0, float(scale)))
        # None → inherit from trigger.metadata["required_frames"] on enter.
        # The triggering boundary already declares how many frames a violation
        # must persist to count; reuse that as the symmetric exit debounce.
        self._hysteresis_seconds = hysteresis_seconds
        self._trigger_name: str | None = None
        self._healthy_streak: int = 0
        self._last_results: list[GuardResult] = []
        self._effective_hysteresis: int = 1

    def on_enter(self, runtime: GuardRuntime, trigger: GuardResult | None = None) -> None:
        self._trigger_name = trigger.guard_name if trigger is not None else None
        self._healthy_streak = 0
        self._last_results = []
        # Exit-hysteresis priority: explicit override > trigger metadata > 1.
        if self._hysteresis_seconds is not None:
            hz = float(getattr(runtime, "_control_frequency_hz", 50.0))
            self._effective_hysteresis = max(1, int(round(self._hysteresis_seconds * hz)))
        elif trigger is not None and isinstance(trigger.metadata, dict):
            inherited = trigger.metadata.get("required_frames")
            if isinstance(inherited, int) and inherited > 0:
                self._effective_hysteresis = inherited
            else:
                self._effective_hysteresis = 1
        else:
            self._effective_hysteresis = 1

    def step(
        self,
        obs: Observation,
        runtime: GuardRuntime,
        *,
        proposal: ActionProposal | None,
        trace_id: str,
        now: float,
    ) -> StepResult:
        if proposal is None:
            raise ValueError("SlowDownContext requires an action proposal")
        validated, results = runtime.validate(obs, proposal, trace_id, now=now)
        self._last_results = list(results)

        # Scale joint-position delta from prev → validated. If we have no prev
        # (first cycle in SlowDown without history) or chassis rejected,
        # surface action=None so the cycle is a passive hold.
        if validated is None:
            return StepResult(action=None, guard_results=results)

        prev = getattr(runtime, "_prev_validated_positions", None)
        if prev is None:
            return StepResult(action=validated, guard_results=results)

        prev_arr = np.asarray(prev, dtype=np.float64)
        target = np.asarray(validated.target_joint_positions, dtype=np.float64)
        n = min(prev_arr.shape[0], target.shape[0])
        scaled = prev_arr[:n] + self._scale * (target[:n] - prev_arr[:n])
        scaled_action = ValidatedAction(
            target_joint_positions=scaled,
            target_joint_velocities=(
                np.asarray(validated.target_joint_velocities, dtype=np.float64) * self._scale
                if validated.target_joint_velocities is not None
                else None
            ),
            was_clamped=True,
            original_proposal=validated.original_proposal,
        )
        return StepResult(action=scaled_action, guard_results=results)

    def is_done(self, obs: Observation, runtime: GuardRuntime) -> bool:
        if self._trigger_name is None:
            # No trigger to watch — defensively yield after hysteresis.
            self._healthy_streak += 1
        else:
            still_violating = any(
                r.guard_name == self._trigger_name
                and r.decision in (GuardDecision.REJECT, GuardDecision.FAULT)
                for r in self._last_results
            )
            if still_violating:
                self._healthy_streak = 0
            else:
                self._healthy_streak += 1
        return self._healthy_streak >= self._effective_hysteresis

    def _is_resolving(self) -> bool:
        # Block auto-escalation while the trigger is actively clearing — give
        # the hysteresis a chance to land before giving up on SlowDown.
        return self._healthy_streak > 0


# ── Retreat ─────────────────────────────────────────────────────────────────


@fallback("retreat")
class RetreatContext(StepContext):
    """Multi-cycle joint-space linear interpolation from the current joint
    position to a configured ``target_positions``. Plan computed on entry,
    advanced one step per cycle. Done when within ``arrival_tol`` rad of
    target or plan exhausted.

    Each planned waypoint is fed through the chassis as a proposal so the
    same guard pipeline that protects policy output also protects retreat
    motion (defense in depth — catches retreats that themselves cross a
    workspace bound or hit a joint limit).

    NOTE: Joint-space interpolation only. EE-space retreat via IK is a
    follow-up (needs kinematics resolver + target EE pose).

    NOTE on naming: called ``retreat`` not ``safe_retreat``. We can't
    guarantee safety of the planned path — the chassis enforces what it can,
    but obstacles or hardware limits beyond the guards' knowledge may still
    cause harm. The name shouldn't make promises the implementation can't
    keep.
    """

    name = "retreat"
    severity = SEVERITY_RETREAT
    # Retreat replaces the action source (planned waypoint), not the policy
    # output. But the chassis still needs an ActionProposal-shaped input, so
    # the runtime can skip policy.predict — we synthesise the proposal here.
    requires_proposal = False

    _DEFAULT_DURATION_SECONDS = 3.0
    _DEFAULT_ARRIVAL_TOL_RAD = 0.01

    def __init__(
        self,
        target_positions: list[float] | None = None,
        duration_seconds: float = _DEFAULT_DURATION_SECONDS,
        arrival_tol: float = _DEFAULT_ARRIVAL_TOL_RAD,
    ) -> None:
        self._target_positions = target_positions  # configured externally; None = zeros
        self._duration_seconds = max(0.0, float(duration_seconds))
        self._arrival_tol = float(arrival_tol)
        self._plan: np.ndarray | None = None
        self._idx: int = 0
        self._target: np.ndarray | None = None

    def on_enter(self, runtime: GuardRuntime, trigger: GuardResult | None = None) -> None:
        prev = getattr(runtime, "_prev_validated_positions", None)
        if prev is None or len(prev) == 0:
            logger.warning("Retreat entered with no prev_validated_positions — cannot plan")
            self._plan = None
            self._target = None
            self._idx = 0
            return
        start = np.asarray(prev, dtype=np.float64)
        if self._target_positions is None or len(self._target_positions) != len(start):
            target = np.zeros_like(start)
        else:
            target = np.asarray(self._target_positions, dtype=np.float64)
        # Derive cycle count from configured duration + runtime control rate.
        hz = float(getattr(runtime, "_control_frequency_hz", 50.0))
        steps = max(1, int(round(self._duration_seconds * hz)))
        # ``steps + 1`` waypoints including the start; drop the start so the
        # first step makes progress.
        self._plan = np.linspace(start, target, steps + 1)[1:]
        self._target = target
        self._idx = 0

    def step(
        self,
        obs: Observation,
        runtime: GuardRuntime,
        *,
        proposal: ActionProposal | None,
        trace_id: str,
        now: float,
    ) -> StepResult:
        # Plan unavailable (no prev positions at entry, or already exhausted).
        if self._plan is None or self._idx >= len(self._plan):
            return StepResult(action=None, guard_results=[])

        next_pos = self._plan[self._idx]
        self._idx += 1

        # Build a proposal so the chassis validates the planned waypoint.
        plan_proposal = ActionProposal(
            target_joint_positions=np.asarray(next_pos, dtype=np.float64),
        )
        validated, results = runtime.validate(obs, plan_proposal, trace_id, now=now)
        # If the chassis rejects the plan step, surface action=None and pass
        # the guard_results upstream — runtime sees the reject and may
        # preempt to a more severe Context (e.g. e_stop).
        return StepResult(action=validated, guard_results=list(results))

    def is_done(self, obs: Observation, runtime: GuardRuntime) -> bool:
        if self._plan is None or self._target is None:
            return True  # nothing to do; let runtime restore Normal
        if self._idx >= len(self._plan):
            return True
        prev = getattr(runtime, "_prev_validated_positions", None)
        if prev is None:
            return False
        return float(np.max(np.abs(np.asarray(prev) - self._target))) <= self._arrival_tol
