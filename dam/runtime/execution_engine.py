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

from dam.guard.aggregator import aggregate_decisions
from dam.guard.pipeline import PER_BOUNDARY_KEY
from dam.types.action import ValidatedAction
from dam.types.enforcement import EnforcementMode
from dam.types.result import GuardDecision, GuardResult

if TYPE_CHECKING:
    from dam.boundary.container import BoundaryContainer
    from dam.bus import PipelineMetricBus, RiskController
    from dam.guard.base import Guard
    from dam.guard.stage import Stage
    from dam.types.action import ActionProposal
    from dam.types.observation import Observation

logger = logging.getLogger(__name__)


def _fan_out_per_boundary(result: GuardResult, boundary_names: list[str]) -> list[GuardResult]:
    """Produce one GuardResult per boundary in the group.

    Pipeline-driven guards (MotionGuard, ExecutionGuard, OODGuard) run their
    callbacks across all active boundaries and return a single aggregated
    GuardResult. When multiple boundaries share that guard instance, the
    cycle inspector wants to see each boundary's *own* decision/reason/
    metadata, not N copies of the aggregated text.

    The aggregator stashes per-callback info under
    ``metadata[PER_BOUNDARY_KEY]``; this helper materialises one
    GuardResult per boundary using that entry. The fused ``clamped_action``
    stays on every CLAMP result (it represents the joint QP solve, identical
    across boundaries; ``aggregate_decisions.merge_restrictive`` is
    idempotent on equal actions).
    """
    metadata = result.metadata or {}
    per_boundary = metadata.get(PER_BOUNDARY_KEY) if isinstance(metadata, dict) else None
    if not isinstance(per_boundary, dict) or not per_boundary:
        return [dataclasses.replace(result, guard_name=bn) for bn in boundary_names]

    out: list[GuardResult] = []
    for bn in boundary_names:
        info = per_boundary.get(bn)
        if not isinstance(info, dict):
            out.append(dataclasses.replace(result, guard_name=bn))
            continue
        decision_name = info.get("decision", result.decision.name)
        try:
            decision = GuardDecision[decision_name]
        except KeyError:
            decision = result.decision
        boundary_meta = info.get("metadata") or {}
        if not isinstance(boundary_meta, dict):
            boundary_meta = {}
        out.append(
            dataclasses.replace(
                result,
                guard_name=bn,
                decision=decision,
                reason=str(info.get("reason") or ""),
                metadata=boundary_meta,
                # Only attach the fused clamped_action on CLAMP boundaries; a
                # PASS boundary in the group shouldn't appear to carry a
                # corrective action.
                clamped_action=result.clamped_action if decision == GuardDecision.CLAMP else None,
            )
        )
    return out


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
    risk_controller: RiskController
    sink: Any | None = None
    runtime: Any | None = None
    prev_validated_positions: list[float] | None = None
    # DynamicsContext (FK + cached Jacobians) refreshed by the sensor adapter
    # in its read() path.  Guards consume it via the ``dynamics`` injection key.
    dynamics: Any | None = None


def _make_dummy_node(fallback_name: str) -> Any:
    """Return a minimal BoundaryNode-like object used when no active container exists."""
    from dam.boundary.constraint import BoundaryConstraint
    from dam.boundary.node import BoundaryNode

    return BoundaryNode(
        node_id="__dummy__",
        constraint=BoundaryConstraint(params={}),
        fallback=fallback_name,
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
    - ``_metric_bus``: shared latency bus (owned by GuardRuntime, shared here)
    - ``_default_fallback``: legacy fallback name used by validate() when no
      boundary node carries one. Runtime Context state machine routes via
      ``node.fallback`` directly; this is kept for back-compat reporting.

    All per-cycle data arrives via ``ValidationContext``.
    """

    def __init__(
        self,
        enforcement_mode: EnforcementMode,
        metric_bus: PipelineMetricBus,
        default_fallback: str = "emergency_stop",
    ) -> None:
        self._enforcement_mode = enforcement_mode
        self._metric_bus = metric_bus
        self._default_fallback = default_fallback
        self._thread_pool: ThreadPoolExecutor | None = None

    def _get_executor(self, max_workers: int) -> ThreadPoolExecutor:
        if self._thread_pool is None:
            self._thread_pool = ThreadPoolExecutor(
                max_workers=max_workers, thread_name_prefix="ExecutionEngine"
            )
        return self._thread_pool

    def shutdown(self) -> None:
        if self._thread_pool is not None:
            self._thread_pool.shutdown(wait=True)
            self._thread_pool = None

    # ── Public entry point ───────────────────────────────────────────────────

    def validate(
        self,
        obs: Observation,
        action: ActionProposal,
        trace_id: str,
        ctx: ValidationContext,
        now: float | None = None,
    ) -> tuple[ValidatedAction | None, list[GuardResult]]:
        """Run the full guard pipeline.

        Returns:
            (ValidatedAction, results) — action passed, was clamped, or
                MONITOR/LOG_ONLY mode let it through despite a reject.
            (None, results)            — action rejected and ENFORCE mode in
                effect. Runtime Context state machine then picks a fallback
                based on the rejecting boundary's ``node.fallback``.
        """
        if self._enforcement_mode == EnforcementMode.LOG_ONLY:
            ctx.risk_controller.record(was_clamped=False, was_rejected=False)
            return self._make_validated_action(action), []

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
            if self._enforcement_mode != EnforcementMode.ENFORCE:
                return self._make_validated_action(action), all_results
            return aggregated.clamped_action, all_results

        ctx.risk_controller.record(was_clamped=False, was_rejected=False)
        return self._make_validated_action(action), all_results

    def run_guard_checks(
        self,
        obs: Observation,
        action: ActionProposal,
        trace_id: str,
        ctx: ValidationContext,
        *,
        guards: list[Guard] | None = None,
        stages: list[Stage] | None = None,
        now: float | None = None,
    ) -> list[GuardResult]:
        """Run selected guards without enforcement side effects."""
        runtime_pool = self._build_runtime_pool(obs, action, trace_id, ctx, now)
        if stages:
            return self._run_staged(stages, runtime_pool)
        return self._run_flat_filtered(guards or [], runtime_pool)

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
            "kinematics_resolver": ctx.kinematics_resolver,
            "dynamics": ctx.dynamics,
            "prev_validated_positions": ctx.prev_validated_positions,
            "now": now,
        }

    def _handle_violation(
        self,
        action: ActionProposal,
        all_results: list[GuardResult],
        aggregated: GuardResult,
        ctx: ValidationContext,
    ) -> tuple[ValidatedAction | None, list[GuardResult]]:
        """Record risk + fire on_violation hooks. Fallback execution is owned
        by the Runtime Context state machine — runtime.step() reads the
        rejecting boundary's ``node.fallback`` and pushes the matching
        Context onto its stack. The chassis just surfaces the reject here.
        """
        ctx.risk_controller.record(was_clamped=False, was_rejected=True)

        if self._enforcement_mode != EnforcementMode.ENFORCE:
            # MONITOR / LOG_ONLY — record the violation result but do not mutate
            # output actions and do not fire violation hooks.
            return self._make_validated_action(action), all_results

        for g in ctx.guards:
            g.on_violation(aggregated)

        return None, all_results

    def _resolve_current_node(self, ctx: ValidationContext) -> Any:
        if ctx.active_containers:
            return ctx.active_containers[0].get_active_node()
        if ctx.boundary_containers:
            return next(iter(ctx.boundary_containers.values())).get_active_node()
        return _make_dummy_node(self._default_fallback)

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
        """Phased guard pipeline — used when no Stage DAG is configured.

        Layout (controlled by ``@guard(phase=..., always=...)`` decorator):
          - phased guards run in ascending phase order; same phase runs in
            registration order (parallelism within a phase is a later
            optimisation — see Task #4 follow-up).
          - between phases, CLAMP results from this phase are merged via
            ``merge_restrictive`` and the fused action is written back into
            ``runtime_pool["action"]`` so the next phase sees the cumulative
            safety chassis output, not the raw policy proposal.
          - always-on guards run AFTER all phases for now (functionally "always
            evaluated"). Moving them to a background ThreadPoolExecutor is a
            latency optimisation deferred until profiling demands it.
        """
        phased: list[Guard] = [g for g in guards if not g.is_always_on()]
        always_on: list[Guard] = [g for g in guards if g.is_always_on()]

        by_phase: dict[int, list[Guard]] = {}
        for g in phased:
            phase = g.get_phase()
            if phase is None:
                # Defensive: a non-always guard with phase=None falls back to
                # its layer value so it still has a defined position.
                phase = g.get_layer().value
            by_phase.setdefault(phase, []).append(g)

        all_results: list[GuardResult] = []
        for phase_num in sorted(by_phase):
            phase_results = self._run_guards_sequential(by_phase[phase_num], runtime_pool)
            all_results.extend(phase_results)
            self._accumulate_phase_clamps(phase_results, runtime_pool)

        # Always-on guards see the post-phase action — that's the same view a
        # mid-cycle hardware check would have. Their reject/fault joins the
        # aggregate at the end (chose B = no mid-cycle preemption, see memory).
        if always_on:
            all_results.extend(self._run_guards_sequential(always_on, runtime_pool))

        return all_results

    def _run_guards_sequential(
        self, guards: list[Guard], runtime_pool: dict[str, Any]
    ) -> list[GuardResult]:
        """Run a list of guards in order against the current runtime_pool."""
        results: list[GuardResult] = []
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
            results.append(result)
        return results

    @staticmethod
    def _accumulate_phase_clamps(
        phase_results: list[GuardResult], runtime_pool: dict[str, Any]
    ) -> None:
        """Merge this phase's CLAMP actions into ``runtime_pool["action"]``.

        The merged ValidatedAction becomes the input action for the next phase
        — callbacks/guards downstream read ``.target_joint_positions`` etc.
        via duck typing, so swapping ActionProposal for ValidatedAction in the
        pool is transparent.
        """
        clamps = [
            r
            for r in phase_results
            if r.decision == GuardDecision.CLAMP and r.clamped_action is not None
        ]
        if not clamps:
            return
        merged = cast(ValidatedAction, clamps[0].clamped_action)
        for r in clamps[1:]:
            if r.clamped_action is not None:
                merged = merged.merge_restrictive(r.clamped_action)
        runtime_pool["action"] = merged

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
            kwargs["active_containers"] = [container]
            if node:
                node_timeout = node.timeout_sec
                if node.constraint:
                    kwargs.update(node.constraint.params)

        result_name = boundary_name if boundary_name is not None else g.get_name()

        _t_start = time.perf_counter()
        result = cast(GuardResult, g.check(**_filter_kwargs(g.check, kwargs)))
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

        # New group-based path: each pair is (guard, [boundary_names])
        if stage.guard_boundary_pairs:
            for g, bnames in stage.guard_boundary_pairs:
                primary = bnames[0] if bnames else g.get_name()
                if time.perf_counter() - t_start > timeout_s:
                    for bn in bnames:
                        results.append(
                            GuardResult(
                                decision=GuardDecision.FAULT,
                                guard_name=bn,
                                layer=g.get_layer(),
                                reason=f"Stage '{stage.name}' timeout ({stage.timeout_ms}ms)",
                                fault_source="timeout",
                            )
                        )
                    continue
                try:
                    if self._is_boundary_specific_guard(g):
                        for bn in bnames:
                            results.append(self._run_one_guard(g, bn, runtime_pool))
                        continue
                    # Pipeline-driven guards (MotionGuard/ExecutionGuard) need
                    # to see *all* active containers for their layer at once so
                    # their clamp aggregator can fuse contributions across
                    # boundaries.  Pass ``boundary_name=None`` to skip the
                    # per-boundary active_containers narrowing in
                    # _run_one_guard; the result is still fanned out below for
                    # per-boundary metric reporting.
                    result = self._run_one_guard(g, None, runtime_pool)
                except Exception as exc:
                    result = GuardResult.fault(exc, "guard_code", primary, g.get_layer())
                    logger.error("Stage '%s' guard '%s' raised: %s", stage.name, primary, exc)
                results.extend(_fan_out_per_boundary(result, bnames))
            return results

        # Legacy flat-guard path
        for g in stage.guards:
            name = g.get_name()
            if time.perf_counter() - t_start > timeout_s:
                results.append(
                    GuardResult(
                        decision=GuardDecision.FAULT,
                        guard_name=name,
                        layer=g.get_layer(),
                        reason=f"Stage '{stage.name}' timeout ({stage.timeout_ms}ms)",
                        fault_source="timeout",
                    )
                )
                continue
            try:
                result = self._run_one_guard(g, None, runtime_pool)
            except Exception as exc:
                result = GuardResult.fault(exc, "guard_code", name, g.get_layer())
                logger.error("Stage '%s' guard '%s' raised: %s", stage.name, name, exc)
            results.append(result)
        return results

    def _run_stage_parallel(
        self,
        stage: Stage,
        runtime_pool: dict[str, Any],
        timeout_s: float,
    ) -> list[GuardResult]:
        # Build a flat list for the thread pool — one entry per group
        if stage.guard_boundary_pairs:
            flat_pairs: list[tuple[Guard, str]] = []
            for g, bnames in stage.guard_boundary_pairs:
                if self._is_boundary_specific_guard(g):
                    flat_pairs.extend((g, bn) for bn in bnames)
                else:
                    flat_pairs.append((g, bnames[0] if bnames else g.get_name()))
        else:
            flat_pairs = [(g, g.get_name()) for g in stage.guards]

        if not flat_pairs:
            logger.warning(
                "Stage '%s' has no guard pairs; skipping parallel execution.", stage.name
            )
            return []

        raw_results: list[GuardResult | None] = [None] * len(flat_pairs)
        executor = self._get_executor(max_workers=max(len(flat_pairs), 8))
        futures = {
            executor.submit(self._run_parallel_entry, stage.name, i, g, bn, runtime_pool): i
            for i, (g, bn) in enumerate(flat_pairs)
        }
        try:
            for future in as_completed(futures, timeout=timeout_s):
                idx, result = future.result()
                raw_results[idx] = result
        except FuturesTimeoutError:
            # Fill timed-out entries
            for _fut, idx in futures.items():
                if raw_results[idx] is None:
                    g, _ = flat_pairs[idx]
                    raw_results[idx] = GuardResult(
                        decision=GuardDecision.FAULT,
                        guard_name=flat_pairs[idx][1],
                        layer=g.get_layer(),
                        reason=f"Stage '{stage.name}' parallel timeout ({stage.timeout_ms}ms)",
                        fault_source="timeout",
                    )

        # Fan out each group result to all boundary names
        results: list[GuardResult] = []
        if stage.guard_boundary_pairs:
            i = 0
            for _g, bnames in stage.guard_boundary_pairs:
                if self._is_boundary_specific_guard(_g):
                    for bn in bnames:
                        r = raw_results[i]
                        i += 1
                        if r is None:
                            r = GuardResult(
                                decision=GuardDecision.FAULT,
                                guard_name=bn,
                                layer=_g.get_layer(),
                                reason=f"Stage '{stage.name}' guard did not complete",
                                fault_source="timeout",
                            )
                        results.append(r)
                    continue
                r = raw_results[i]
                i += 1
                if r is None:
                    r = GuardResult(
                        decision=GuardDecision.FAULT,
                        guard_name=bnames[0] if bnames else "unknown",
                        layer=_g.get_layer(),
                        reason=f"Stage '{stage.name}' guard did not complete",
                        fault_source="timeout",
                    )
                results.extend(_fan_out_per_boundary(r, bnames))
        else:
            for r in raw_results:
                if r is not None:
                    results.append(r)

        return results

    @staticmethod
    def _is_boundary_specific_guard(g: Guard) -> bool:
        layer = g.get_layer()
        return int(layer) == 3

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
