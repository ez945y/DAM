"""ExecutionEngine — pure validation pipeline for GuardRuntime.

Owns the guard execution logic (flat and staged) and enforcement-mode decision
making.  Has **no hardware I/O dependencies**: it receives a per-cycle snapshot
of runtime state via ``ValidationContext`` and returns a deterministic result.

This makes the pipeline independently unit-testable without hardware adapters.
"""

from __future__ import annotations

import contextlib
import dataclasses
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import TYPE_CHECKING, Any, cast

from dam.fallback.base import FallbackContext
from dam.guard.aggregator import aggregate_decisions
from dam.types.action import ValidatedAction
from dam.types.enforcement import EnforcementMode
from dam.types.result import GuardDecision, GuardResult

if TYPE_CHECKING:
    from dam.boundary.container import BoundaryContainer
    from dam.bus import PipelineMetricBus, RiskController
    from dam.fallback.registry import FallbackRegistry
    from dam.guard.base import Guard
    from dam.guard.stage import Stage
    from dam.types.action import ActionProposal
    from dam.types.observation import Observation

logger = logging.getLogger(__name__)

# Default fallback handler name when no active container specifies one.
# Must match a registered fallback in dam.fallback.registry.
_DEFAULT_FALLBACK = "emergency_stop"


@dataclasses.dataclass
class ValidationContext:
    """Per-cycle snapshot of GuardRuntime state passed into ExecutionEngine.

    Constructed by ``GuardRuntime.validate()`` on every cycle.  The scalars
    (``cycle_id``, ``active_task``) and ``node_start_times`` are value-copies.
    The collections (``guards``, ``stages``, ``boundary_containers``, …) are
    **shared references** into GuardRuntime's own state — the ExecutionEngine
    contract is to **read them only**, never mutate.  We deliberately avoid
    deep-copying these on the hot path (50 Hz control loop).

    Fields
    ------
    cycle_id            : monotonic cycle counter at the time of the call
    guards              : full enabled guard list (read-only reference)
    stages              : Stage DAG built by start_task(), or None for flat mode
    active_containers   : BoundaryContainers active in the current task
    active_container_names : names of active_containers, same order
    boundary_containers : full boundary → container mapping (read-only reference)
    node_start_times    : copy of per-boundary node start timestamps (value-copy)
    active_task         : name of the currently running task
    kinematics_resolver : optional FK/IK resolver passed through to guards
    hardware_status     : merged hardware health dict from sink + obs metadata
    risk_controller     : shared RiskController; .record() is called by engine
    """

    cycle_id: int
    guards: list[Guard]
    stages: list[Stage] | None
    active_containers: list[BoundaryContainer]
    active_container_names: list[str]
    boundary_containers: dict[str, BoundaryContainer]
    node_start_times: dict[str, float]
    active_task: str | None
    kinematics_resolver: Any | None
    hardware_status: dict[str, Any]
    risk_controller: RiskController


def _make_dummy_node() -> Any:
    """Return a minimal BoundaryNode-like object used when no active container exists."""
    from dam.boundary.constraint import BoundaryConstraint
    from dam.boundary.node import BoundaryNode

    return BoundaryNode(
        node_id="__dummy__",
        constraint=BoundaryConstraint(params={}),
        fallback=_DEFAULT_FALLBACK,
    )


def _filter_kwargs(fn: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Return only the keyword arguments accepted by ``fn``."""
    import inspect

    try:
        sig = inspect.signature(fn)
        accepted = set(sig.parameters.keys())
        var_keyword = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        if var_keyword:
            return kwargs
        return {k: v for k, v in kwargs.items() if k in accepted}
    except (ValueError, TypeError):
        return kwargs


class ExecutionEngine:
    """Stateless-per-cycle validation pipeline.

    Holds three pieces of persistent state:
    - ``_enforcement_mode``: whether to block or merely observe violations
    - ``_fallback_registry``: maps fallback names to handler callables
    - ``_metric_bus``: shared latency bus (owned by GuardRuntime, shared here)

    All per-cycle data arrives via ``ValidationContext``.
    """

    def __init__(
        self,
        enforcement_mode: EnforcementMode,
        fallback_registry: FallbackRegistry,
        metric_bus: PipelineMetricBus,
    ) -> None:
        self._enforcement_mode = enforcement_mode
        self._fallback_registry = fallback_registry
        self._metric_bus = metric_bus

    # ── Public entry point ───────────────────────────────────────────────────

    def validate(
        self,
        obs: Observation,
        action: ActionProposal,
        trace_id: str,
        ctx: ValidationContext,
        now: float | None = None,
    ) -> tuple[ValidatedAction | None, list[GuardResult], str | None]:
        """Run the full guard pipeline and return (validated_action, results, fallback_name).

        Returns:
            (ValidatedAction, results, None)       — action passed or was clamped
            (None,           results, fallback_name) — action rejected and fallback fired
        """
        runtime_pool = self._build_runtime_pool(obs, action, trace_id, ctx, now)

        if ctx.stages is not None:
            all_results = self._run_staged(ctx.stages, runtime_pool)
        else:
            active_names = set(ctx.active_container_names)
            active_guards = [g for g in ctx.guards if g.get_name() in active_names]
            all_results = self._run_flat_filtered(active_guards, runtime_pool)

        aggregated = aggregate_decisions(all_results)

        if aggregated.decision in (GuardDecision.REJECT, GuardDecision.FAULT):
            return self._handle_violation(action, all_results, aggregated, ctx)

        if aggregated.decision == GuardDecision.CLAMP:
            ctx.risk_controller.record(was_clamped=True, was_rejected=False)
            return aggregated.clamped_action, all_results, None

        ctx.risk_controller.record(was_clamped=False, was_rejected=False)
        return self._make_validated_action(action), all_results, None

    # ── Private pipeline helpers ─────────────────────────────────────────────

    def _build_runtime_pool(
        self,
        obs: Observation,
        action: ActionProposal,
        trace_id: str,
        ctx: ValidationContext,
        now: float | None,
    ) -> dict[str, Any]:
        active = [
            ctx.boundary_containers[n]
            for n in ctx.active_container_names
            if n in ctx.boundary_containers
        ]
        return {
            "obs": obs,
            "action": action,
            "cycle_id": ctx.cycle_id,
            "trace_id": trace_id,
            "timestamp": obs.timestamp,
            "active_task": ctx.active_task,
            "active_boundaries": ctx.active_container_names,
            "active_containers": active,
            "active_map": {
                n: ctx.boundary_containers[n]
                for n in ctx.active_container_names
                if n in ctx.boundary_containers
            },
            "node_start_times": ctx.node_start_times,
            "hardware_status": ctx.hardware_status or None,
            "kinematics_resolver": ctx.kinematics_resolver,
            "now": now,
        }

    def _handle_violation(
        self,
        action: ActionProposal,
        all_results: list[GuardResult],
        aggregated: GuardResult,
        ctx: ValidationContext,
    ) -> tuple[ValidatedAction | None, list[GuardResult], str | None]:
        ctx.risk_controller.record(was_clamped=False, was_rejected=True)
        for g in ctx.guards:
            g.on_violation(aggregated)

        if self._enforcement_mode != EnforcementMode.ENFORCE:
            # MONITOR / LOG_ONLY — record violation but pass original action through
            return self._make_validated_action(action), all_results, None

        fallback_name = (
            ctx.active_containers[0].get_active_node().fallback
            if ctx.active_containers
            else _DEFAULT_FALLBACK
        )
        fallback_ctx = FallbackContext(
            rejected_proposal=action,
            guard_result=aggregated,
            current_node=self._resolve_current_node(ctx),
            cycle_id=ctx.cycle_id,
        )
        self._fallback_registry.execute_with_escalation(fallback_name, fallback_ctx, bus=None)
        return None, all_results, fallback_name

    def _resolve_current_node(self, ctx: ValidationContext) -> Any:
        if ctx.active_containers:
            return ctx.active_containers[0].get_active_node()
        if ctx.boundary_containers:
            return next(iter(ctx.boundary_containers.values())).get_active_node()
        return _make_dummy_node()

    def _make_validated_action(self, action: ActionProposal) -> ValidatedAction:
        return ValidatedAction(
            target_joint_positions=action.target_joint_positions.copy(),
            target_joint_velocities=action.target_joint_velocities.copy()
            if action.target_joint_velocities is not None
            else None,
            was_clamped=False,
            original_proposal=action,
        )

    def _run_flat_filtered(
        self, guards: list[Guard], runtime_pool: dict[str, Any]
    ) -> list[GuardResult]:
        """Flat guard loop — used when no Stage DAG is configured."""
        all_results: list[GuardResult] = []
        for g in guards:
            try:
                kwargs = dict(g._static_kwargs)
                kwargs.update({k: runtime_pool[k] for k in g._runtime_keys if k in runtime_pool})

                active_map = runtime_pool.get("active_map", {})
                if g.get_name() in active_map:
                    container = active_map[g.get_name()]
                    node = container.get_active_node()
                    if node and node.constraint:
                        kwargs.update(node.constraint.params)

                _t = time.perf_counter()
                result = g.check(**_filter_kwargs(g.check, kwargs))
                _latency_ms = (time.perf_counter() - _t) * 1000.0
                self._metric_bus.push_guard(g.get_name(), g.get_layer().value, _latency_ms)
                result = dataclasses.replace(
                    result, metadata={**result.metadata, "_latency_ms": _latency_ms}
                )
            except Exception as e:
                result = GuardResult.fault(e, "guard_code", g.get_name(), g.get_layer())
                logger.error("Guard '%s' raised exception: %s", g.get_name(), e)
            all_results.append(result)
        return all_results

    def _run_staged(
        self,
        stages: list[Stage],
        runtime_pool: dict[str, Any],
    ) -> list[GuardResult]:
        """Stage DAG execution: stages run sequentially; guards within each stage
        run in parallel (when stage.parallel=True) or sequentially."""
        all_results: list[GuardResult] = []
        for stage in stages:
            timeout_s = stage.timeout_ms / 1000.0
            n_pairs = (
                len(stage.guard_boundary_pairs) if stage.guard_boundary_pairs else len(stage.guards)
            )
            if stage.parallel and n_pairs > 1:
                stage_results = self._run_stage_parallel(stage, runtime_pool, timeout_s)
            else:
                stage_results = self._run_stage_sequential(stage, runtime_pool, timeout_s)
            all_results.extend(stage_results)
        return all_results

    def _run_one_guard(
        self,
        g: Guard,
        boundary_name: str | None,
        runtime_pool: dict[str, Any],
    ) -> GuardResult:
        """Execute a single guard with boundary-specific param injection."""
        kwargs = dict(g._static_kwargs)
        kwargs.update({k: runtime_pool[k] for k in g._runtime_keys if k in runtime_pool})

        node_timeout = None
        active_map = runtime_pool.get("active_map", {})
        lookup = boundary_name if boundary_name is not None else g.get_name()
        if lookup in active_map:
            container = active_map[lookup]
            node = container.get_active_node()
            if node:
                node_timeout = node.timeout_sec
                if node.constraint:
                    kwargs.update(node.constraint.params)

        result_name = boundary_name if boundary_name is not None else g.get_name()

        _t_start = time.perf_counter()
        result = g.check(**_filter_kwargs(g.check, kwargs))
        _t_elapsed = time.perf_counter() - _t_start
        _latency_ms = _t_elapsed * 1000.0

        self._metric_bus.push_guard(result_name, g.get_layer().value, _latency_ms)

        if node_timeout is not None and _t_elapsed > node_timeout:
            result = GuardResult.reject(
                reason=(
                    f"guard '{result_name}' computation timeout: "
                    f"{_t_elapsed:.3f}s > {node_timeout}s"
                ),
                guard_name=result_name,
                layer=g.get_layer(),
            )

        return dataclasses.replace(
            result,
            guard_name=result_name,
            metadata={**result.metadata, "_latency_ms": _latency_ms},
        )

    def _run_stage_sequential(
        self,
        stage: Stage,
        runtime_pool: dict[str, Any],
        timeout_s: float,
    ) -> list[GuardResult]:
        results: list[GuardResult] = []
        t_start = time.perf_counter()
        pairs: list[tuple[Guard, str | None]] = (
            stage.guard_boundary_pairs
            if stage.guard_boundary_pairs
            else [(g, cast("str | None", None)) for g in stage.guards]
        )
        for g, boundary_name in pairs:
            result_name = boundary_name if boundary_name is not None else g.get_name()
            if time.perf_counter() - t_start > timeout_s:
                results.append(
                    GuardResult(
                        decision=GuardDecision.FAULT,
                        guard_name=result_name,
                        layer=g.get_layer(),
                        reason=f"Stage '{stage.name}' timeout ({stage.timeout_ms}ms)",
                        fault_source="timeout",
                    )
                )
                continue
            try:
                result = self._run_one_guard(g, boundary_name, runtime_pool)
            except Exception as exc:
                result = GuardResult.fault(exc, "guard_code", result_name, g.get_layer())
                logger.error("Stage '%s' guard '%s' raised: %s", stage.name, result_name, exc)
            results.append(result)
        return results

    def _run_stage_parallel(
        self,
        stage: Stage,
        runtime_pool: dict[str, Any],
        timeout_s: float,
    ) -> list[GuardResult]:
        pairs: list[tuple[Guard, str | None]] = (
            stage.guard_boundary_pairs
            if stage.guard_boundary_pairs
            else [(g, cast("str | None", None)) for g in stage.guards]
        )
        if not pairs:
            # ThreadPoolExecutor(max_workers=0) raises ValueError — guard against it.
            logger.warning(
                "Stage '%s' has no guard pairs; skipping parallel execution.", stage.name
            )
            return []
        results: list[GuardResult | None] = [None] * len(pairs)

        with ThreadPoolExecutor(max_workers=len(pairs)) as executor:
            futures = {
                executor.submit(self._run_parallel_entry, stage.name, i, g, bn, runtime_pool): i
                for i, (g, bn) in enumerate(pairs)
            }
            try:
                for future in as_completed(futures, timeout=timeout_s):
                    idx, result = future.result()
                    results[idx] = result
            except FuturesTimeoutError:
                self._fill_timed_out_results(futures, pairs, results, stage)

        self._fill_missing_results(results, pairs, stage)
        return cast(list[GuardResult], results)

    def _run_parallel_entry(
        self,
        stage_name: str,
        idx: int,
        g: Guard,
        boundary_name: str | None,
        runtime_pool: dict[str, Any],
    ) -> tuple[int, GuardResult]:
        result_name = boundary_name if boundary_name is not None else g.get_name()
        try:
            result = self._run_one_guard(g, boundary_name, runtime_pool)
        except Exception as exc:
            result = GuardResult.fault(exc, "guard_code", result_name, g.get_layer())
            logger.error("Stage '%s' guard '%s' raised: %s", stage_name, result_name, exc)
        return idx, result

    def _fill_timed_out_results(
        self,
        futures: dict[Any, int],
        pairs: list[tuple[Guard, str | None]],
        results: list[GuardResult | None],
        stage: Stage,
    ) -> None:
        for future, idx in futures.items():
            if not future.done():
                g, bn = pairs[idx]
                results[idx] = GuardResult(
                    decision=GuardDecision.FAULT,
                    guard_name=bn if bn is not None else g.get_name(),
                    layer=g.get_layer(),
                    reason=f"Stage '{stage.name}' parallel timeout ({stage.timeout_ms}ms)",
                    fault_source="timeout",
                )

    def _fill_missing_results(
        self,
        results: list[GuardResult | None],
        pairs: list[tuple[Guard, str | None]],
        stage: Stage,
    ) -> None:
        for i, r in enumerate(results):
            if r is None:
                g, bn = pairs[i]
                results[i] = GuardResult(
                    decision=GuardDecision.FAULT,
                    guard_name=bn if bn is not None else g.get_name(),
                    layer=g.get_layer(),
                    reason=f"Stage '{stage.name}' guard did not complete",
                    fault_source="timeout",
                )
