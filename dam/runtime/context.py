"""Runtime execution Context state-machine.

The runtime holds exactly one ``active_context`` at any time. NormalContext is
the default (policy → phased chassis → sink). Fallback Contexts (HoldPosition,
Retreat, SlowDown, WaitAndRetry, EmergencyStop) are siblings; they take
over via severity-based preemption when a guard rejects.

Each Context shares the same step template:

    obs → [source hook] → proposal → [chassis] → validated → [output hook] → sink

Subclasses customise which hooks they install — they never duplicate chassis
validation. See ``project_runtime_context_state_machine`` memory for the full
design rationale.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dam.runtime.guard_runtime import GuardRuntime
    from dam.types.action import ActionProposal, ValidatedAction
    from dam.types.observation import Observation
    from dam.types.result import GuardResult


# Severity ladder for preemption. Higher value preempts lower. NormalContext
# sits at 0 so any fallback wins. EmergencyStop is terminal.
SEVERITY_NORMAL: int = 0
SEVERITY_WAIT_AND_RETRY: int = 10
SEVERITY_SLOW_DOWN: int = 20
SEVERITY_RETREAT: int = 30
SEVERITY_HOLD_POSITION: int = 40
SEVERITY_EMERGENCY_STOP: int = 100


@dataclass(frozen=True)
class StepResult:
    """One cycle's outcome from a Context's ``step()``.

    Fields
    ------
    action          Final ValidatedAction to send to the sink, or ``None`` for
                    explicit don't-send (only WaitAndRetry / EmergencyStop).
    guard_results   Per-boundary GuardResults produced by the chassis this
                    cycle. Empty for bypass contexts.
    """

    action: ValidatedAction | None
    guard_results: list[GuardResult] = field(default_factory=list)


@dataclass(frozen=True)
class ContextEvent:
    """Transition record. Emitted on enter / exit / preempt; written to
    ``/dam/context_events`` by the loopback writer."""

    event: str  # "enter" | "exit" | "preempt"
    ctx_name: str
    ctx_severity: int
    from_ctx_name: str | None = None
    from_ctx_severity: int | None = None
    trigger_guard: str | None = None
    trigger_reason: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class StepContext(ABC):
    """Abstract per-step execution mode. Holds lifecycle, not cycle state.

    Subclass contract:
      - ``severity``: class-level int used for preemption comparison.
      - ``name``: identifier shown in logs and MCAP (``ctx_name``).
      - ``on_enter`` / ``on_exit``: bracket the lifetime; runtime invokes on
        every transition.
      - ``step``: produce one cycle's StepResult given the current obs and
        runtime handle.
      - ``is_done``: when True after a step, runtime transitions back to
        NormalContext on the next cycle.
    """

    name: str = "unnamed"
    severity: int = 0
    # When False the runtime skips ``policy.predict()`` before calling step()
    # and passes ``proposal=None``. Saves an expensive inference per cycle for
    # Contexts that ignore the policy (EmergencyStop / HoldPosition / etc.).
    requires_proposal: bool = True
    # When True, runtime keeps L3 / always-on hardware monitors active even if
    # this Context bypasses the normal chassis. Terminal contexts may disable
    # this to avoid repeated escalation noise after the robot is already stopped.
    monitors_hardware: bool = True
    # Auto-escalation timer. If set, runtime pushes ``escalate_to`` Context
    # when this Context has been active for ``escalate_after_seconds`` and
    # ``should_escalate()`` returns True. Resolution of ``escalate_to`` uses
    # the same fallbacks-dict lookup as node.fallback (Task #7).
    # Useful for handling ambiguous triggers — e.g. high motor current is
    # either heavy payload (clears under SlowDown) or collision (won't clear).
    # SlowDown for 5s; if trigger still violating → escalate to e_stop.
    escalate_after_seconds: float | None = None
    escalate_to: str | None = None

    def on_enter(
        self,
        runtime: GuardRuntime,
        trigger: GuardResult | None = None,
    ) -> None:  # noqa: B027 — optional hook
        """Initialise per-activation state. ``trigger`` is the GuardResult that
        caused entry (None for NormalContext at boot). Stateful Contexts
        capture what to watch here (e.g. SlowDown remembers the trigger guard
        name so is_done can check whether the underlying condition cleared).
        Default no-op."""

    @abstractmethod
    def step(
        self,
        obs: Observation,
        runtime: GuardRuntime,
        *,
        proposal: ActionProposal | None,
        trace_id: str,
        now: float,
    ) -> StepResult: ...

    def is_done(self, obs: Observation, runtime: GuardRuntime) -> bool:
        """True → runtime restores NormalContext on next cycle. Default never
        yields (resting/terminal states); plan-type Contexts override with
        their own completion check (retreat at home, velocity under threshold,
        wait counter expired, etc.)."""
        return False

    def should_escalate(self, elapsed_seconds: float) -> bool:
        """True → runtime pushes the configured ``escalate_to`` Context.

        ``elapsed_seconds`` is wall-clock time since this Context became
        active (or resumed via stack pop). Runtime owns the timer; Context
        only owns the policy.

        Default policy: fires once ``escalate_after_seconds`` has elapsed AND
        the Context isn't actively resolving (subclass-defined via
        ``_is_resolving``). SlowDown overrides ``_is_resolving`` so a trigger
        that has *started* clearing gets the cooldown rather than the
        escalation. Subclasses with no resolution concept (e.g. pure
        timeout-then-escalate) keep the default ``_is_resolving = False`` and
        escalate strictly on time."""
        if self.escalate_after_seconds is None or not self.escalate_to:
            return False
        if elapsed_seconds <= self.escalate_after_seconds:
            return False
        return not self._is_resolving()

    def _is_resolving(self) -> bool:
        """Subclass hook: True if the underlying trigger is actively clearing
        (e.g. SlowDown's healthy_streak > 0). Default False — escalate on time
        alone."""
        return False


class NormalContext(StepContext):
    """Default runtime mode: policy → phased chassis → sink, no hooks.

    Delegates to ``GuardRuntime.validate()`` so the chassis is the single
    source of truth for guard execution. Returns StepResult.action set to the
    chassis output (PASS or CLAMP); on REJECT/FAULT the chassis itself fires
    the legacy fallback path today — Task #6 will reroute that to Context
    preemption, but this seam already works.
    """

    name = "normal"
    severity = SEVERITY_NORMAL

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
            raise ValueError("NormalContext requires an action proposal")
        validated, results = runtime.validate(obs, proposal, trace_id, now=now)
        return StepResult(action=validated, guard_results=list(results))


CONTEXT_REGISTRY: dict[str, type[StepContext]] = {}


def register_context(name: str, cls: type[StepContext]) -> None:
    """Register a fallback Context type by stackfile-facing name."""
    CONTEXT_REGISTRY[name] = cls


def get_context_class(name: str) -> type[StepContext] | None:
    return CONTEXT_REGISTRY.get(name)


def iter_context_classes() -> dict[str, type[StepContext]]:
    return dict(CONTEXT_REGISTRY)


def make_context(name: str, **kwargs: Any) -> StepContext:
    """Instantiate a registered Context by name."""
    cls = CONTEXT_REGISTRY[name]
    return cls(**kwargs)
