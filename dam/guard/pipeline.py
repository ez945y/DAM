"""Unified callback-driven pipeline shared by every Guard.

This module replaces the per-guard ``check()`` body with three composable
steps used by all four builtin guards:

    1. ``run_callbacks(...)`` — walk active boundary containers, look up each
       container's registered ``@boundary_callback``, inject kwargs from the
       runtime + config pools, and collect the results.

    2. (optional, per-guard) ``aggregate_clamps(...)`` — if any callback
       returned ``CLAMP``, fuse the proposed actions.  The default is naïve
       sequential application; guards that need real fusion (e.g. QP for L1)
       inject their own aggregator.

    3. ``aggregate(...)`` — collapse the list of ``CallbackResult`` into one
       ``GuardResult`` using uniform priority: FAULT → REJECT → CLAMP → PASS.

The shape of a CLAMP contribution is **deliberately opaque** to the framework.
Callbacks return a fully-formed ``ValidatedAction`` in ``clamped_action``;
anything fancier (e.g. QP constraint matrices for fusion) is the user's
responsibility to stash in ``metadata`` and consume in a custom aggregator.
The framework does not interpret constraint kinds.
"""

from __future__ import annotations

import dataclasses
import inspect
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from dam.types.result import GuardDecision, GuardResult

if TYPE_CHECKING:
    from dam.guard.layer import GuardLayer
    from dam.types.action import ValidatedAction

logger = logging.getLogger(__name__)


# ── Unified callback result ───────────────────────────────────────────────────


@dataclass(frozen=True)
class CallbackResult:
    """Per-boundary outcome of one ``@boundary_callback`` invocation.

    Every layer (L0–L3) emits this same shape.  The framework only branches on
    ``decision``; everything else — including how ``clamped_action`` was
    computed and what ``metadata`` carries — is defined by the callback author.
    """

    decision: GuardDecision
    boundary_name: str
    reason: str = ""
    clamped_action: ValidatedAction | None = None
    fault_source: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(
        cls,
        boundary_name: str,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> CallbackResult:
        return cls(
            decision=GuardDecision.PASS,
            boundary_name=boundary_name,
            reason=reason,
            metadata=metadata or {},
        )

    @classmethod
    def violate(
        cls,
        boundary_name: str,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> CallbackResult:
        return cls(
            decision=GuardDecision.REJECT,
            boundary_name=boundary_name,
            reason=reason,
            metadata=metadata or {},
        )

    @classmethod
    def clamp(
        cls,
        boundary_name: str,
        clamped_action: ValidatedAction,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> CallbackResult:
        return cls(
            decision=GuardDecision.CLAMP,
            boundary_name=boundary_name,
            reason=reason,
            clamped_action=clamped_action,
            metadata=metadata or {},
        )

    @classmethod
    def fault(
        cls,
        boundary_name: str,
        exc: Exception | str,
        fault_source: str = "guard_code",
    ) -> CallbackResult:
        return cls(
            decision=GuardDecision.FAULT,
            boundary_name=boundary_name,
            reason=str(exc),
            fault_source=fault_source,
        )


# ── Backward-compat coercion ──────────────────────────────────────────────────


def _coerce_legacy_return(
    raw: Any,
    *,
    boundary_name: str,
    callback_name: str,
    params: dict[str, Any],
    node_id: str,
) -> CallbackResult:
    """Accept the historical return shapes a callback may use today.

    Existing callbacks return ``bool`` (and a handful return ``(bool, reason)``
    or ``GuardResult``).  This shim coerces them so guards can migrate to the
    new pipeline before every callback has been rewritten.
    """
    if isinstance(raw, CallbackResult):
        return raw

    if isinstance(raw, GuardResult):
        # Legacy callback already produced a layered result; keep its decision
        # and reason, drop guard_name/layer (will be set during aggregate()).
        return CallbackResult(
            decision=raw.decision,
            boundary_name=boundary_name,
            reason=raw.reason,
            clamped_action=raw.clamped_action,
            fault_source=raw.fault_source,
            metadata=dict(raw.metadata),
        )

    ok: bool
    reason: str = ""
    if isinstance(raw, tuple) and len(raw) == 2:
        ok_raw, reason_raw = raw
        ok = bool(ok_raw)
        reason = str(reason_raw or "")
    else:
        ok = bool(raw)

    if ok:
        return CallbackResult.ok(boundary_name)
    if not reason:
        detail = ", ".join(f"{k}={v}" for k, v in params.items())
        suffix = f" ({detail})" if detail else ""
        reason = f"callback '{callback_name}' returned False at node '{node_id}'{suffix}"
    return CallbackResult.violate(boundary_name, reason)


# ── run_callbacks ─────────────────────────────────────────────────────────────


def _filter_kwargs(fn: Callable[..., Any], kwargs: dict[str, Any]) -> dict[str, Any]:
    sig = inspect.signature(fn)
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
        return kwargs
    return {k: v for k, v in kwargs.items() if k in sig.parameters}


def run_callbacks(
    *,
    containers: list[Any] | None,
    runtime_pool: dict[str, Any],
    expected_layer: str | None = None,
) -> list[CallbackResult]:
    """Walk active boundary containers and invoke each one's callback.

    The callback's static config sits on ``node.constraint.params`` (set when
    the stackfile was parsed); runtime values come from ``runtime_pool``.
    Static params override runtime values on collision, mirroring the existing
    ``evaluate_boundary_callbacks`` semantics.

    Callbacks may return any of: ``CallbackResult`` (preferred), ``bool``,
    ``(bool, reason)``, or ``GuardResult`` (legacy).  All are coerced here.

    The function does not raise; a callback exception is captured as a FAULT
    result so the caller can decide how to surface it.
    """
    if not containers:
        return []

    from dam.registry.callback import get_global_registry

    registry = get_global_registry()
    results: list[CallbackResult] = []

    for container in containers:
        boundary_name = getattr(container, "name", None) or getattr(container, "container_id", None)
        node = container.get_active_node()
        constraint = node.constraint
        callback_name = constraint.callback if constraint else None
        if not callback_name:
            continue

        try:
            callback = registry.get(callback_name)
        except KeyError:
            logger.warning("Boundary callback '%s' is not registered", callback_name)
            continue

        if expected_layer is not None and getattr(callback, "_cb_layer", None) != expected_layer:
            continue

        params = dict(constraint.params or {})
        kwargs = {**runtime_pool, **params}
        bname = boundary_name or callback_name

        try:
            raw = callback(**_filter_kwargs(callback, kwargs))
        except Exception as exc:  # noqa: BLE001 — converted to a structured FAULT result
            results.append(CallbackResult.fault(bname, exc, fault_source="guard_code"))
            continue

        results.append(
            _coerce_legacy_return(
                raw,
                boundary_name=bname,
                callback_name=callback_name,
                params=params,
                node_id=node.node_id,
            )
        )

    return results


# ── Aggregation ───────────────────────────────────────────────────────────────


ClampAggregator = Callable[
    [list[CallbackResult], "ValidatedAction | None"],
    "ValidatedAction | None",
]
"""User-injectable fusion strategy for multiple CLAMP results.

Receives the CLAMP-flavoured CallbackResults plus an optional running action
to fold into; must return the final clamped action (or None to skip CLAMP).
"""


def sequential_clamp_aggregator(
    clamps: list[CallbackResult], action_in: ValidatedAction | None
) -> ValidatedAction | None:
    """Default fusion: take the most-restrictive value per joint.

    Folds each CLAMP into a running action via ``ValidatedAction.merge_restrictive``,
    which picks the value furthest from the original proposal per joint — i.e.
    the strictest correction wins, regardless of which callback produced it.

    Adequate when callbacks each produce an independently-clamped action.
    Guards needing constraint-aware fusion (e.g. QP minimising perturbation
    subject to all constraints jointly) should inject their own aggregator.
    """
    out = action_in
    for c in clamps:
        if c.clamped_action is None:
            continue
        out = c.clamped_action if out is None else out.merge_restrictive(c.clamped_action)
    return out


def aggregate(
    results: list[CallbackResult],
    *,
    guard_name: str,
    guard_layer: GuardLayer,
    action_in: ValidatedAction | None = None,
    clamp_aggregator: ClampAggregator = sequential_clamp_aggregator,
) -> GuardResult:
    """Collapse per-boundary results into one GuardResult.

    Priority: FAULT > REJECT > CLAMP > PASS.  Reasons are concatenated when
    multiple results share the same winning decision.
    """
    faults = [r for r in results if r.decision == GuardDecision.FAULT]
    if faults:
        first = faults[0]
        reason = "; ".join(f"{r.boundary_name}: {r.reason}" for r in faults)
        return GuardResult(
            decision=GuardDecision.FAULT,
            guard_name=guard_name,
            layer=guard_layer,
            reason=reason,
            fault_source=first.fault_source or "guard_code",
            metadata=_merge_metadata(faults, all_results=results),
        )

    rejects = [r for r in results if r.decision == GuardDecision.REJECT]
    if rejects:
        reason = "; ".join(f"{r.boundary_name}: {r.reason}" for r in rejects)
        return GuardResult(
            decision=GuardDecision.REJECT,
            guard_name=guard_name,
            layer=guard_layer,
            reason=reason,
            metadata=_merge_metadata(rejects, all_results=results),
        )

    clamps = [r for r in results if r.decision == GuardDecision.CLAMP]
    if clamps:
        fused = clamp_aggregator(clamps, action_in)
        if fused is None:
            # Aggregator declined; degrade to PASS rather than emit a CLAMP
            # without a payload — that would be a contract violation.
            logger.warning(
                "Guard %s: clamp aggregator returned None despite %d clamp contribution(s)",
                guard_name,
                len(clamps),
            )
            return GuardResult.success(guard_name=guard_name, layer=guard_layer)
        reason = "; ".join(f"{r.boundary_name}: {r.reason}" for r in clamps if r.reason)
        return GuardResult(
            decision=GuardDecision.CLAMP,
            guard_name=guard_name,
            layer=guard_layer,
            reason=reason,
            clamped_action=fused,
            metadata=_merge_metadata(clamps, all_results=results),
        )

    # All callbacks passed — still surface per-boundary metadata so the
    # cycle inspector can render PASS callbacks with their constraint /
    # measured-value telemetry. Without this the fan-out for PASS would be
    # an empty card.
    return GuardResult(
        decision=GuardDecision.PASS,
        guard_name=guard_name,
        layer=guard_layer,
        metadata=_merge_metadata(results, all_results=results),
    )


# Reserved metadata key — execution engine reads this to produce a distinct
# GuardResult per boundary instead of replicating the aggregated one.
PER_BOUNDARY_KEY = "_per_boundary"


def _merge_metadata(
    results: list[CallbackResult],
    *,
    all_results: list[CallbackResult] | None = None,
) -> dict[str, Any]:
    """Flatten per-boundary metadata under each boundary's name and stash
    PER_BOUNDARY_KEY covering *every* callback (not just the winning
    decision), so the engine's fan-out can give each boundary its actual
    decision and metadata — PASS boundaries included."""
    out: dict[str, Any] = {}
    for r in results:
        if r.metadata:
            out[r.boundary_name] = r.metadata
    per_boundary: dict[str, dict[str, Any]] = {}
    for r in all_results if all_results is not None else results:
        per_boundary[r.boundary_name] = {
            "decision": r.decision.name,
            "reason": r.reason,
            "metadata": dict(r.metadata) if r.metadata else {},
        }
    out[PER_BOUNDARY_KEY] = per_boundary
    return out


# Convenience: full pipeline in one call when the guard does not need to
# inspect intermediate CallbackResults.


def run_and_aggregate(
    *,
    containers: list[Any] | None,
    runtime_pool: dict[str, Any],
    expected_layer: str | None,
    guard_name: str,
    guard_layer: GuardLayer,
    action_in: ValidatedAction | None = None,
    clamp_aggregator: ClampAggregator = sequential_clamp_aggregator,
) -> tuple[list[CallbackResult], GuardResult]:
    """Run callbacks and immediately aggregate. Returns both for diagnostics."""
    results = run_callbacks(
        containers=containers,
        runtime_pool=runtime_pool,
        expected_layer=expected_layer,
    )
    final = aggregate(
        results,
        guard_name=guard_name,
        guard_layer=guard_layer,
        action_in=action_in,
        clamp_aggregator=clamp_aggregator,
    )
    return results, final


# Preserve a tidy public surface even though Python doesn't enforce it.
__all__ = [
    "CallbackResult",
    "ClampAggregator",
    "aggregate",
    "run_and_aggregate",
    "run_callbacks",
    "sequential_clamp_aggregator",
]


# Module-internal helper used in dataclass replace operations elsewhere.
_replace = dataclasses.replace
