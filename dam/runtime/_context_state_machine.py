"""Context state-machine — stack-based push/pop runtime context.

Extracted from ``guard_runtime.py`` to keep the runtime orchestrator focused
on cycle execution.  This module owns the context stack, fallback resolution,
and transition recording.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from dam.runtime._stackfile_builder import ResolvedFallback
from dam.runtime.context import (
    ContextEvent,
    NormalContext,
    StepContext,
    get_context_class,
    make_context,
)
from dam.types.result import GuardDecision, GuardResult

if TYPE_CHECKING:
    from dam.boundary.container import BoundaryContainer
    from dam.config.schema import FallbackConfig

logger = logging.getLogger(__name__)


class ContextStateMachine:
    """Stack-based context state machine.

    NormalContext sits at the bottom permanently; fallback Contexts push on top
    via severity-based preemption.  ``is_done`` pops (resume the previous
    Context); popping past the bottom restores plain Normal operation.
    """

    def __init__(
        self,
        default_fallback: str = "emergency_stop",
        fallbacks_config: dict[str, FallbackConfig] | None = None,
    ) -> None:
        self._context_stack: list[StepContext] = [NormalContext()]
        self._active_context_since: float = time.monotonic()
        self._fallbacks_config: dict[str, FallbackConfig] = (
            dict(fallbacks_config) if fallbacks_config else {}
        )
        self._pending_context_event: ContextEvent | None = None
        self._default_fallback = default_fallback

    # ── Properties ─────────────────────────────────────────────────────────

    @property
    def active_context(self) -> StepContext:
        return self._context_stack[-1]

    @property
    def active_context_since(self) -> float:
        return self._active_context_since

    @property
    def stack_depth(self) -> int:
        return len(self._context_stack)

    @property
    def fallbacks_config(self) -> dict[str, FallbackConfig]:
        return self._fallbacks_config

    @fallbacks_config.setter
    def fallbacks_config(self, value: dict[str, Any]) -> None:
        self._fallbacks_config = value

    # ── Resolution ─────────────────────────────────────────────────────────

    def resolve_fallback_name(self, fallback_name: str) -> ResolvedFallback | None:
        """Turn a ``node.fallback`` string into a ResolvedFallback.

        Resolution order:
          1. Look up in stackfile ``fallbacks:`` dict → FallbackConfig fields
          2. Treat the string itself as a builtin Context name with empty params
             and no escalation
          3. None (unknown)
        """
        entry = self._fallbacks_config.get(fallback_name)
        if entry is not None:
            ctx_type = getattr(entry, "type", None)
            if isinstance(ctx_type, str):
                return ResolvedFallback(
                    context_type=ctx_type,
                    params=dict(getattr(entry, "params", {}) or {}),
                    escalate_after_seconds=getattr(entry, "escalate_after_seconds", None),
                    escalate_to=getattr(entry, "escalate_to", None),
                )
        if get_context_class(fallback_name) is not None:
            return ResolvedFallback(context_type=fallback_name)
        return None

    def make_context_from_fallback_name(self, fallback_name: str) -> StepContext | None:
        """Resolve a fallback name + instantiate the Context with all the
        config (params, escalation).  Returns None on unknown/invalid names."""
        resolved = self.resolve_fallback_name(fallback_name)
        if resolved is None:
            logger.warning("Unknown fallback '%s' — no Context picked", fallback_name)
            return None
        if get_context_class(resolved.context_type) is None:
            logger.warning(
                "fallbacks['%s'].type='%s' is not a registered fallback Context",
                fallback_name,
                resolved.context_type,
            )
            return None
        try:
            ctx = make_context(resolved.context_type, **resolved.params)
        except TypeError as e:
            logger.error(
                "Context '%s' rejected params %s: %s",
                resolved.context_type,
                resolved.params,
                e,
            )
            return None
        if resolved.escalate_after_seconds is not None:
            ctx.escalate_after_seconds = float(resolved.escalate_after_seconds)
        if resolved.escalate_to is not None:
            ctx.escalate_to = str(resolved.escalate_to)
        return ctx

    def pick_context_for(
        self,
        rejected: GuardResult,
        boundary_containers: dict[str, BoundaryContainer],
    ) -> StepContext | None:
        """Resolve the Context for a rejecting GuardResult."""
        fallback_name: str | None = None
        container = boundary_containers.get(rejected.guard_name)
        if container is not None:
            node = container.get_active_node()
            if node is not None and getattr(node, "fallback", None):
                fallback_name = node.fallback
        if fallback_name is None:
            fallback_name = self._default_fallback
        return self.make_context_from_fallback_name(fallback_name)

    # ── Stack operations ───────────────────────────────────────────────────

    def push_context(
        self, new_ctx: StepContext, runtime: Any, *, trigger: GuardResult | None, event: str
    ) -> None:
        """Push a new Context on top of the stack and fire on_enter."""
        from_ctx = self.active_context
        new_ctx.on_enter(runtime, trigger=trigger)
        self._context_stack.append(new_ctx)
        self._active_context_since = time.monotonic()
        self._record_transition(event=event, ctx=new_ctx, from_ctx=from_ctx, trigger=trigger)

    def pop_context(self) -> None:
        """Pop the top Context (resume the previous).  Never pops past
        the bottom NormalContext."""
        if len(self._context_stack) <= 1:
            return
        from_ctx = self._context_stack.pop()
        new_top = self.active_context
        self._active_context_since = time.monotonic()
        self._record_transition(event="exit", ctx=new_top, from_ctx=from_ctx, trigger=None)

    def reset_stack(self) -> None:
        """Collapse the context stack back to NormalContext.

        Called on stop_task so a fresh start always begins in normal mode.
        Fires exit transitions for each popped context so MCAP / telemetry
        records the unwind.
        """
        while len(self._context_stack) > 1:
            self.pop_context()

    # ── Events ─────────────────────────────────────────────────────────────

    def consume_pending_event(self) -> ContextEvent | None:
        ev = self._pending_context_event
        self._pending_context_event = None
        return ev

    def _record_transition(
        self,
        *,
        event: str,
        ctx: StepContext,
        from_ctx: StepContext,
        trigger: GuardResult | None,
    ) -> None:
        self._pending_context_event = ContextEvent(
            event=event,
            ctx_name=ctx.name,
            ctx_severity=ctx.severity,
            from_ctx_name=from_ctx.name,
            from_ctx_severity=from_ctx.severity,
            trigger_guard=trigger.guard_name if trigger is not None else None,
            trigger_reason=trigger.reason if trigger is not None else None,
        )
        logger.info(
            "Context %s: %s(sev=%d) → %s(sev=%d) | trigger=%s reason=%s | stack_depth=%d",
            event.upper(),
            from_ctx.name,
            from_ctx.severity,
            ctx.name,
            ctx.severity,
            trigger.guard_name if trigger is not None else None,
            trigger.reason if trigger is not None else None,
            len(self._context_stack),
        )

    # ── Static utilities ───────────────────────────────────────────────────

    @staticmethod
    def find_worst_reject(results: list[GuardResult]) -> GuardResult | None:
        """Return the highest-priority REJECT/FAULT (or None if all PASS/CLAMP)."""
        bad = [r for r in results if r.decision in (GuardDecision.REJECT, GuardDecision.FAULT)]
        if not bad:
            return None
        return max(bad, key=lambda r: (r.decision.value, r.layer.value))
