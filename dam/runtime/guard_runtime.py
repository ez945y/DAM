from __future__ import annotations

import inspect
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import TYPE_CHECKING, Any

import numpy as np

from dam.fallback.base import FallbackContext
from dam.guard.aggregator import aggregate_decisions
from dam.injection.static import precompute_injection
from dam.types.action import ValidatedAction
from dam.types.result import GuardDecision, GuardResult
from dam.types.risk import CycleResult, RiskLevel

if TYPE_CHECKING:
    from dam.boundary.container import BoundaryContainer
    from dam.config.schema import StackfileConfig
    from dam.fallback.registry import FallbackRegistry
    from dam.guard.base import Guard
    from dam.guard.stage import Stage
    from dam.kinematics.resolver import KinematicsResolver
    from dam.types.action import ActionProposal
    from dam.types.observation import Observation

import contextlib

from dam.bus import MetricBus, ObservationBus, RiskController, WatchdogTimer

logger = logging.getLogger(__name__)


class GuardRuntime:
    def __init__(
        self,
        guards: list[Guard],
        boundary_containers: dict[str, BoundaryContainer],
        fallback_registry: FallbackRegistry,
        task_config: dict[str, list[str]],
        always_active: list[str] | None = None,
        config_pool: dict[str, Any] | None = None,
        control_frequency_hz: float = 50.0,
        enforcement_mode: str = "enforce",
        risk_controller_config: Any | None = None,  # Optional["RiskControllerConfig"]
        loopback_config: Any | None = None,  # Optional["LoopbackConfig"]
        kinematics_resolver: KinematicsResolver | None = None,
    ) -> None:
        if always_active is None:
            always_active = []
        if config_pool is None:
            config_pool = {}
        if enforcement_mode not in ("enforce", "monitor", "log_only"):
            raise ValueError(
                f"enforcement_mode must be enforce|monitor|log_only, got '{enforcement_mode}'"
            )
        # Store ALL guards; _guards is the active (enabled) subset rebuilt on config change
        self._all_guards: list[Guard] = sorted(guards, key=lambda g: g.get_layer().value)
        self._disabled_kinds: set[str] = set()
        self._guards = list(self._all_guards)
        self._boundary_containers = boundary_containers
        self._fallback_registry = fallback_registry
        self._task_config = task_config
        self._always_active = always_active
        self._control_frequency_hz = control_frequency_hz
        self._enforcement_mode = enforcement_mode
        self._cycle_id = 0
        self._active_task: str | None = None
        self._active_containers: list[BoundaryContainer] = []
        self._active_container_names: list[str] = []
        self._node_start_times: dict[str, float] = {}
        self._source: Any = None
        self._policy: Any = None
        self._sink: Any = None
        self._kinematics_resolver = kinematics_resolver

        # ── Rust bus components (fall back to Python when dam_rs not compiled) ──
        # RiskController: windowed reject/clamp counter → RiskLevel
        _rc_window_sec = risk_controller_config.window_sec if risk_controller_config else 10.0
        _rc_clamp_thr = risk_controller_config.clamp_threshold if risk_controller_config else 5
        _rc_reject_thr = risk_controller_config.reject_threshold if risk_controller_config else 2
        _rc_samples = max(1, round(_rc_window_sec * control_frequency_hz))
        self._risk_controller: RiskController = RiskController(
            _rc_samples,
            _rc_clamp_thr,
            _rc_reject_thr,
        )

        # MetricBus: per-guard latency / score history (ms)
        self._metric_bus: MetricBus = MetricBus()

        # ObservationBus: ring buffer for loopback capture (±window_sec at hz)
        _obs_window_sec = loopback_config.window_sec if loopback_config else 30.0
        _obs_capacity = max(100, int(_obs_window_sec * 2 * control_frequency_hz) + 50)
        self._obs_bus: ObservationBus = ObservationBus(capacity=_obs_capacity)

        # 3E: Stage DAG
        self._stages: list[Stage] | None = None

        # 3G: Hot reload double-buffer
        self._pending_config: StackfileConfig | None = None
        self._hot_reload_lock = threading.Lock()
        self._config_pool = dict(config_pool)

        # 3H: Dual-mode entry
        self._running = False

        # Startup: pre-compute injection for all guards
        for g in self._guards:
            precompute_injection(g, config_pool)

    # ── Guards config ───────────────────────────────────────────────────────

    def configure_from_stackfile(self, stackfile_config: StackfileConfig) -> None:
        """Apply guard enabled state and guard-specific params from a StackfileConfig.

        Call this once after construction with the loaded config.  Hot reload
        calls ``apply_pending_reload`` which invokes ``_apply_config_swap``
        and handles subsequent changes automatically.
        """
        self._apply_guards_config(stackfile_config, self._config_pool)
        # Re-compute injection for all active guards with updated pool
        for g in self._guards:
            precompute_injection(g, self._config_pool)

    def _apply_guards_config(
        self,
        cfg: StackfileConfig,
        pool: dict[str, Any],
    ) -> None:
        active_list = cfg.guards
        active_names = set()
        for item in active_list:
            if isinstance(item, dict):
                active_names.update(item.values())
            elif isinstance(item, str):
                active_names.add(item)

        self._disabled_kinds = {
            getattr(g, "_guard_kind", None)
            for g in self._all_guards
            if getattr(g, "_guard_kind", None)
            and getattr(g, "_guard_kind", None) not in active_names
        }
        self._guards = [
            g for g in self._all_guards if getattr(g, "_guard_kind", None) in active_names
        ]

        logger.info(
            "GuardRuntime: guards configured — active=%s",
            active_names or "none",
        )

    # ── Adapter registration ────────────────────────────────────────────────

    def register_source(self, source: Any) -> None:
        self._source = source

    def register_policy(self, policy: Any) -> None:
        self._policy = policy

    def register_sink(self, sink: Any) -> None:
        self._sink = sink

    # ── Task lifecycle ──────────────────────────────────────────────────────

    def start_task(self, name: str) -> None:
        if name not in self._task_config:
            raise KeyError(f"Task '{name}' not found. Available: {list(self._task_config.keys())}")
        self._active_task = name
        self._active_containers = []
        self._active_container_names = []
        self._node_start_times = {}
        now = time.monotonic()
        for cname in self._always_active:
            if cname in self._boundary_containers:
                self._active_containers.append(self._boundary_containers[cname])
                self._active_container_names.append(cname)
                self._node_start_times[cname] = now
        for cname in self._task_config[name]:
            if cname in self._boundary_containers:
                container = self._boundary_containers[cname]
                self._active_containers.append(container)
                self._active_container_names.append(cname)
                self._node_start_times[cname] = now

                # Preflight check: Allow guards to initialize early
                # Note: We find all guards associated with this container/callback
                # Since multiple guards might share a name if flattened, we check by name
                for g in self._guards:
                    if g.get_name() == cname:
                        try:
                            # Prepare kwargs similar to _run_flat_filtered
                            kwargs = dict(g._static_kwargs)
                            kwargs.update(
                                {
                                    k: self._config_pool[k]
                                    for k in g._runtime_keys
                                    if k in self._config_pool
                                }
                            )
                            # Add node params
                            node = container.get_active_node()
                            if node and node.constraint:
                                kwargs.update(node.constraint.params)

                            logger.debug("GuardRuntime: Preflighting guard '%s'...", cname)
                            g.preflight(**_filter_kwargs(g.preflight, kwargs))
                        except Exception as e:
                            logger.error("GuardRuntime: Preflight for '%s' failed: %s", cname, e)

    def stop_task(self) -> None:
        self._active_task = None
        self._active_containers = []
        self._active_container_names = []
        self._node_start_times = {}

    def advance_container(self, name: str) -> None:
        """Advance a named container to its next node and reset its start time."""
        if name in self._boundary_containers:
            self._boundary_containers[name].advance()
            self._node_start_times[name] = time.monotonic()

    def pause_task(self) -> None:
        pass  # Phase 1: no-op

    def resume_task(self) -> None:
        pass  # Phase 1: no-op

    # ── 3E: Stage DAG ──────────────────────────────────────────────────────

    def set_stages(self, stages: list[Stage]) -> None:
        """Configure stage DAG for this runtime.

        When stages are set, ``validate()`` uses ``_run_staged()`` instead of
        the flat guard loop.
        """
        self._stages = list(stages)

    # ── 3G: Hot Reload ─────────────────────────────────────────────────────

    def apply_pending_reload(self, new_config: StackfileConfig) -> None:
        """Store a new config for thread-safe application at the next cycle boundary.

        Called from the StackfileWatcher callback thread.  The actual swap
        happens inside ``step()`` before any guards run, so config is never
        changed mid-cycle.
        """
        with self._hot_reload_lock:
            self._pending_config = new_config

    def _apply_config_swap(self, new_config: StackfileConfig) -> None:
        """Rebuild configuration-based parameters for all guards from the new config."""
        import numpy as np

        new_config_pool: dict[str, Any] = {}

        # Extract guard params from boundary node params — single authoritative source.
        # All guard-specific parameters (motion limits, OOD model paths, …) live here,
        # not in the guards: section.
        def _to_arr(lst: Any, fill: float) -> np.ndarray:
            if not lst:
                return np.array([], dtype=float)
            return np.array([fill if x is None else float(x) for x in lst], dtype=float)

        for _bname, bcfg in new_config.boundaries.items():
            for ncfg in bcfg.nodes:
                c_params = ncfg.params
                for pk, pv in c_params.items():
                    if pk in new_config_pool:
                        old_v = new_config_pool[pk]
                        # ── Restrictive Merging for known safety parameters ──
                        if pk == "max_speed":
                            new_config_pool[pk] = min(old_v, pv)
                        elif pk in ("upper", "max_velocity", "max_acceleration"):
                            new_config_pool[pk] = np.minimum(
                                np.asarray(old_v, dtype=float), np.asarray(pv, dtype=float)
                            ).tolist()
                        elif pk == "lower":
                            new_config_pool[pk] = np.maximum(
                                np.asarray(old_v, dtype=float), np.asarray(pv, dtype=float)
                            ).tolist()
                        else:
                            # Generic overwrite with warning for unknown parameters
                            if not np.array_equal(old_v, pv):
                                logger.warning(
                                    "GuardRuntime: Parameter '%s' overwritten by boundary '%s' "
                                    "(prev: %s, new: %s)",
                                    pk,
                                    _bname,
                                    old_v,
                                    pv,
                                )
                            new_config_pool[pk] = pv
                    else:
                        new_config_pool[pk] = pv

        # Apply guard enabled and guard-specific params from guards: section
        self._apply_guards_config(new_config, new_config_pool)

        self._config_pool = new_config_pool
        # Re-run injection precompute for all guards with new pool
        for g in self._guards:
            precompute_injection(g, new_config_pool)

        # Reset node start times to 'now' upon config swap to avoid stale timeouts
        now = time.monotonic()
        for cname in self._node_start_times:
            self._node_start_times[cname] = now

        logger.info("GuardRuntime: config swap applied (hot reload) and timers reset")

    # ── Core validate ───────────────────────────────────────────────────────

    def shutdown(self) -> None:
        """Properly disconnect all hardware adapters."""
        logger.info("GuardRuntime: shutting down adapters...")
        if self._source is not None and hasattr(self._source, "disconnect"):
            try:
                self._source.disconnect()
            except Exception as e:
                logger.debug("GuardRuntime: source disconnect failed: %s", e)

        if self._sink is not None and hasattr(self._sink, "disconnect"):
            try:
                self._sink.disconnect()
            except Exception as e:
                logger.debug("GuardRuntime: sink disconnect failed: %s", e)

        if self._policy is not None and hasattr(self._policy, "disconnect"):
            try:
                self._policy.disconnect()
            except Exception as e:
                logger.debug("GuardRuntime: policy disconnect failed: %s", e)

    def validate(
        self,
        obs: Observation,
        action: ActionProposal,
        trace_id: str,
        now: float | None = None,
    ) -> tuple[ValidatedAction | None, list[GuardResult], str | None]:
        """Returns (validated_action, guard_results, fallback_name_triggered)."""
        # Collect hardware_status from sink and observation metadata
        hardware_status: dict[str, Any] = {}
        if self._sink is not None and hasattr(self._sink, "get_hardware_status"):
            with contextlib.suppress(Exception):
                sink_status = self._sink.get_hardware_status()
                if sink_status:
                    hardware_status.update(sink_status)

        # Merge status from observation metadata (set by sources on error)
        obs_hw_status = obs.metadata.get("hardware_status")
        if obs_hw_status:
            hardware_status.update(obs_hw_status)

        runtime_pool: dict[str, Any] = {
            "obs": obs,
            "action": action,
            "cycle_id": self._cycle_id,
            "trace_id": trace_id,
            "timestamp": obs.timestamp,
            "active_task": self._active_task,
            "active_boundaries": self._active_container_names,
            "active_containers": [
                self._boundary_containers[name]
                for name in self._active_container_names
                if name in self._boundary_containers
            ],
            "active_map": {
                name: self._boundary_containers[name]
                for name in self._active_container_names
                if name in self._boundary_containers
            },
            "node_start_times": dict(self._node_start_times),
            "hardware_status": hardware_status if hardware_status else None,
            "kinematics_resolver": self._kinematics_resolver,
            "now": now,
        }

        if self._stages is not None:
            all_results = self._run_staged(obs, action, trace_id, runtime_pool)
        else:
            # Filter flat guards to only those currently active in this task
            active_names = set(self._active_container_names)
            active_guards = [g for g in self._guards if g.get_name() in active_names]
            all_results = self._run_flat_filtered(active_guards, runtime_pool)

        aggregated = aggregate_decisions(all_results)

        if aggregated.decision in (GuardDecision.REJECT, GuardDecision.FAULT):
            self._risk_controller.record(was_clamped=False, was_rejected=True)
            for g in self._guards:
                g.on_violation(aggregated)
            should_enforce = self._enforcement_mode == "enforce"
            # In monitor/log_only modes guards run but do NOT block action dispatch
            if should_enforce:
                fallback_name = "emergency_stop"
                if self._active_containers:
                    fallback_name = self._active_containers[0].get_active_node().fallback
                current_node = (
                    self._active_containers[0].get_active_node()
                    if self._active_containers
                    else next(iter(self._boundary_containers.values())).get_active_node()
                    if self._boundary_containers
                    else _make_dummy_node()
                )
                ctx = FallbackContext(
                    rejected_proposal=action,
                    guard_result=aggregated,
                    current_node=current_node,
                    cycle_id=self._cycle_id,
                )
                self._fallback_registry.execute_with_escalation(fallback_name, ctx, bus=None)
                return None, all_results, fallback_name
            else:
                # monitor / log_only — record violation but pass original action through
                return (
                    ValidatedAction(
                        target_joint_positions=action.target_joint_positions.copy(),
                        target_joint_velocities=action.target_joint_velocities.copy()
                        if action.target_joint_velocities is not None
                        else None,
                        was_clamped=False,
                        original_proposal=action,
                    ),
                    all_results,
                    None,
                )

        if aggregated.decision == GuardDecision.CLAMP:
            self._risk_controller.record(was_clamped=True, was_rejected=False)
            return aggregated.clamped_action, all_results, None

        self._risk_controller.record(was_clamped=False, was_rejected=False)
        validated = ValidatedAction(
            target_joint_positions=action.target_joint_positions.copy(),
            target_joint_velocities=action.target_joint_velocities.copy()
            if action.target_joint_velocities is not None
            else None,
            was_clamped=False,
            original_proposal=action,
        )
        return validated, all_results, None

    def _run_flat_filtered(
        self, guards: list[Guard], runtime_pool: dict[str, Any]
    ) -> list[GuardResult]:
        """Original flat guard loop (used when no stages are configured)."""
        all_results: list[GuardResult] = []
        for g in guards:
            try:
                kwargs = dict(g._static_kwargs)
                kwargs.update({k: runtime_pool[k] for k in g._runtime_keys if k in runtime_pool})

                # Inject matching boundary node params
                active_map = runtime_pool.get("active_map", {})
                if g.get_name() in active_map:
                    container = active_map[g.get_name()]
                    node = container.get_active_node()
                    if node and node.constraint:
                        kwargs.update(node.constraint.params)

                _t = time.perf_counter()
                result = g.check(**_filter_kwargs(g.check, kwargs))
                self._metric_bus.push_guard(
                    g.get_name(), g.get_layer().value, (time.perf_counter() - _t) * 1000.0
                )
            except Exception as e:
                result = GuardResult.fault(e, "guard_code", g.get_name(), g.get_layer())
                logger.error("Guard '%s' raised exception: %s", g.get_name(), e)
            all_results.append(result)
        return all_results

    def _run_staged(
        self,
        obs: Observation,
        action: ActionProposal,
        trace_id: str,
        runtime_pool: dict[str, Any],
    ) -> list[GuardResult]:
        """Stage DAG execution: run stages sequentially; within each stage
        run guards in parallel (if stage.parallel=True) or sequentially."""
        all_results: list[GuardResult] = []

        for stage in self._stages:  # type: ignore[union-attr]
            timeout_s = stage.timeout_ms / 1000.0

            if stage.parallel and len(stage.guards) > 1:
                stage_results = self._run_stage_parallel(stage, runtime_pool, timeout_s)
            else:
                stage_results = self._run_stage_sequential(stage, runtime_pool, timeout_s)

            all_results.extend(stage_results)

        return all_results

    def _run_stage_sequential(
        self,
        stage: Stage,
        runtime_pool: dict[str, Any],
        timeout_s: float,
    ) -> list[GuardResult]:
        results: list[GuardResult] = []
        t_start = time.perf_counter()

        for g in stage.guards:
            # Check per-stage timeout
            if time.perf_counter() - t_start > timeout_s:
                results.append(
                    GuardResult(
                        decision=GuardDecision.FAULT,
                        guard_name=g.get_name(),
                        layer=g.get_layer(),
                        reason=f"Stage '{stage.name}' timeout ({stage.timeout_ms}ms)",
                        fault_source="timeout",
                    )
                )
                continue

            try:
                kwargs = dict(g._static_kwargs)
                kwargs.update({k: runtime_pool[k] for k in g._runtime_keys if k in runtime_pool})

                # Inject matching boundary node params
                node_timeout = None
                active_map = runtime_pool.get("active_map", {})
                if g.get_name() in active_map:
                    container = active_map[g.get_name()]
                    node = container.get_active_node()
                    if node:
                        node_timeout = node.timeout_sec
                        if node.constraint:
                            kwargs.update(node.constraint.params)

                _t_start = time.perf_counter()
                result = g.check(**_filter_kwargs(g.check, kwargs))
                _t_elapsed = time.perf_counter() - _t_start

                self._metric_bus.push_guard(g.get_name(), g.get_layer().value, _t_elapsed * 1000.0)

                # ── Global Computation Watchdog ──
                if node_timeout is not None and _t_elapsed > node_timeout:
                    result = GuardResult.reject(
                        reason=(
                            f"guard '{g.get_name()}' computation timeout: "
                            f"{_t_elapsed:.3f}s > {node_timeout}s"
                        ),
                        guard_name=g.get_name(),
                        layer=g.get_layer(),
                    )
            except Exception as e:
                result = GuardResult.fault(e, "guard_code", g.get_name(), g.get_layer())
                logger.error("Stage '%s' guard '%s' raised: %s", stage.name, g.get_name(), e)
            results.append(result)

        return results

    def _run_stage_parallel(
        self,
        stage: Stage,
        runtime_pool: dict[str, Any],
        timeout_s: float,
    ) -> list[GuardResult]:
        results: list[GuardResult] = [None] * len(stage.guards)  # type: ignore[list-item]

        def _run_guard(idx: int, g: Guard) -> tuple[int, GuardResult]:
            try:
                kwargs = dict(g._static_kwargs)
                kwargs.update({k: runtime_pool[k] for k in g._runtime_keys if k in runtime_pool})

                # Inject matching boundary node params (Parallel)
                node_timeout = None
                active_map = runtime_pool.get("active_map", {})
                if g.get_name() in active_map:
                    container = active_map[g.get_name()]
                    node = container.get_active_node()
                    if node:
                        node_timeout = node.timeout_sec
                        if node.constraint:
                            kwargs.update(node.constraint.params)

                _t_start = time.perf_counter()
                result = g.check(**_filter_kwargs(g.check, kwargs))
                _t_elapsed = time.perf_counter() - _t_start

                self._metric_bus.push_guard(g.get_name(), g.get_layer().value, _t_elapsed * 1000.0)

                # ── Global Computation Watchdog ──
                if node_timeout is not None and _t_elapsed > node_timeout:
                    result = GuardResult.reject(
                        reason=(
                            f"guard '{g.get_name()}' computation timeout: "
                            f"{_t_elapsed:.3f}s > {node_timeout}s"
                        ),
                        guard_name=g.get_name(),
                        layer=g.get_layer(),
                    )
            except Exception as e:
                result = GuardResult.fault(e, "guard_code", g.get_name(), g.get_layer())
                logger.error("Stage '%s' guard '%s' raised: %s", stage.name, g.get_name(), e)
            return idx, result

        with ThreadPoolExecutor(max_workers=len(stage.guards)) as executor:
            futures = {executor.submit(_run_guard, i, g): i for i, g in enumerate(stage.guards)}
            try:
                for future in as_completed(futures, timeout=timeout_s):
                    idx, result = future.result()
                    results[idx] = result
            except FuturesTimeoutError:
                # Mark any unfinished guards as FAULT
                for future, idx in futures.items():
                    if not future.done():
                        g = stage.guards[idx]
                        results[idx] = GuardResult(
                            decision=GuardDecision.FAULT,
                            guard_name=g.get_name(),
                            layer=g.get_layer(),
                            reason=f"Stage '{stage.name}' parallel timeout ({stage.timeout_ms}ms)",
                            fault_source="timeout",
                        )

        # Fill any None slots (shouldn't happen, but be safe)
        for i, r in enumerate(results):
            if r is None:
                g = stage.guards[i]
                results[i] = GuardResult(
                    decision=GuardDecision.FAULT,
                    guard_name=g.get_name(),
                    layer=g.get_layer(),
                    reason=f"Stage '{stage.name}' guard did not complete",
                    fault_source="timeout",
                )

        return results

    # ── step() — single cycle ───────────────────────────────────────────────

    def step(self) -> CycleResult:
        # 3G: Apply pending hot-reload config swap BEFORE the cycle runs
        with self._hot_reload_lock:
            pending = self._pending_config
            if pending is not None:
                self._pending_config = None

        if pending is not None:
            self._apply_config_swap(pending)

        t_start = time.monotonic()
        trace_id = str(uuid.uuid4())

        obs: Observation = self._source.read()
        self._obs_bus.write(obs)  # ring buffer for loopback / MCAP capture
        t_obs = time.monotonic()

        action: ActionProposal = self._policy.predict(obs)
        t_policy = time.monotonic()

        validated, guard_results, fallback_triggered = self.validate(
            obs, action, trace_id, now=t_start
        )
        t_validate = time.monotonic()

        if validated is not None and self._sink is not None:
            # Use apply() (ActionAdapter ABC). write() is a deprecated alias on legacy sinks.
            if hasattr(self._sink, "apply"):
                self._sink.apply(validated)
            else:
                self._sink.write(validated)  # backward-compat for non-ABC sinks
        t_sink = time.monotonic()

        risk = self._compute_risk()
        self._cycle_id += 1

        # ── Push pipeline-stage timing and commit layer aggregates ──────────
        # Deliberately placed after all guard execution so the MetricBus holds
        # a complete picture before commit_cycle() finalises the layer history.
        self._metric_bus.push_stage("source", (t_obs - t_start) * 1000.0)
        self._metric_bus.push_stage("policy", (t_policy - t_obs) * 1000.0)
        self._metric_bus.push_stage("guards", (t_validate - t_policy) * 1000.0)
        self._metric_bus.push_stage("sink", (t_sink - t_validate) * 1000.0)
        self._metric_bus.push_stage("total", (t_sink - t_start) * 1000.0)
        self._metric_bus.commit_cycle()

        return CycleResult(
            cycle_id=self._cycle_id - 1,
            trace_id=trace_id,
            validated_action=validated,
            original_proposal=action,
            was_clamped=validated.was_clamped if validated is not None else False,
            was_rejected=validated is None,
            guard_results=guard_results,
            fallback_triggered=fallback_triggered,
            latency_ms={
                "obs": (t_obs - t_start) * 1000,
                "policy": (t_policy - t_obs) * 1000,
                "validate": (t_validate - t_policy) * 1000,
                "sink": (t_sink - t_validate) * 1000,
                "total": (t_sink - t_start) * 1000,
            },
            risk_level=risk,
            active_task=self._active_task,
            active_boundaries=list(self._active_container_names),
        )

    # ── 3H: Dual-mode entry ────────────────────────────────────────────────

    def run(self, n_cycles: int = -1, cycle_budget_ms: float = 20.0) -> list[CycleResult]:
        """Managed control loop with timing and watchdog.

        Arms a WatchdogTimer at loop start, pings it each cycle, and sleeps to
        maintain ``cycle_budget_ms``.  Runs until ``stop()`` is called or
        ``n_cycles`` cycles have completed.

        Args:
            n_cycles:        Number of cycles to run (-1 = run until stop()).
            cycle_budget_ms: Target cycle time in milliseconds.

        Returns:
            List of CycleResult objects (one per cycle).
        """
        self._running = True
        results: list[CycleResult] = []
        cycle_budget_s = cycle_budget_ms / 1000.0
        watchdog = WatchdogTimer(deadline_ms=cycle_budget_ms * 3)
        watchdog.arm()

        cycle = 0
        try:
            while self._running:
                t0 = time.perf_counter()
                result = self.step()
                results.append(result)
                cycle += 1
                watchdog.ping()

                # Check watchdog emergency: fires on OS thread outside GIL when
                # a cycle exceeds 3× budget.  Escalate to RiskController and stop.
                if watchdog.is_emergency():
                    logger.error(
                        "GuardRuntime.run(): watchdog deadline exceeded after %d cycles "
                        "(%.1f ms elapsed since last ping) — triggering emergency stop",
                        cycle,
                        watchdog.elapsed_since_ping_ms(),
                    )
                    self._risk_controller.trigger_emergency()
                    break

                if n_cycles != -1 and cycle >= n_cycles:
                    break

                elapsed = time.perf_counter() - t0
                sleep = cycle_budget_s - elapsed
                if sleep > 0:
                    time.sleep(sleep)
        except StopIteration:
            logger.info("GuardRuntime.run(): source exhausted after %d cycles", cycle)
        except KeyboardInterrupt:
            logger.info("GuardRuntime.run(): interrupted by user")
        finally:
            watchdog.disarm()
            self._running = False

        return results

    def stop(self) -> None:
        """Signal ``run()`` to exit after the current cycle completes."""
        self._running = False

    # ── Risk computation ────────────────────────────────────────────────────

    def _compute_risk(self) -> RiskLevel:
        """Map RiskController level (0–3) to RiskLevel enum (NORMAL/ELEVATED/CRITICAL)."""
        level = self._risk_controller.risk_level()
        if level >= 2:
            return RiskLevel.CRITICAL
        if level == 1:
            return RiskLevel.ELEVATED
        return RiskLevel.NORMAL

    # ── Class constructor from Stackfile ──────────────────────────────�

    @classmethod
    def from_stackfile(cls, path: str) -> GuardRuntime:
        """Construct a GuardRuntime from a Stackfile YAML path.

        Guard type is determined by the ``_cb_layer`` attribute on registered
        callbacks (set by the @callback decorator).  Falls back to the boundary's
        own ``layer`` field when the callback is unknown or not yet registered.
        """
        from dam.boundary.constraint import BoundaryConstraint
        from dam.boundary.list_container import ListContainer
        from dam.boundary.node import BoundaryNode
        from dam.boundary.single import SingleNodeContainer
        from dam.config.loader import StackfileLoader
        from dam.decorators import guard as guard_decorator
        from dam.fallback.chain import build_escalation_chain
        from dam.kinematics.resolver import KinematicsResolver

        # Layer → Guard class mapping
        # L0: OOD           → OODGuard
        # L1: Preflight     → ExecutionGuard
        config = StackfileLoader.load(path)
        guards: list[Any] = []
        config_pool: dict[str, Any] = {}
        boundary_containers: dict[str, Any] = {}

        from dam.registry.callback import get_global_registry as _get_cb_registry

        _cb_reg = _get_cb_registry()

        kinematics_resolver = None
        if config.hardware and config.hardware.urdf_path:
            try:
                kinematics_resolver = KinematicsResolver(config.hardware.urdf_path)
            except Exception as e:
                logger.warning(
                    "GuardRuntime: failed to init KinematicsResolver from %s: %s",
                    config.hardware.urdf_path,
                    e,
                )

        for bname, bcfg in config.boundaries.items():
            nodes = []
            for ncfg in bcfg.nodes:
                layer_str = getattr(bcfg, "layer", None)

                # Use _cb_layer from the callback annotation; fall back to boundary layer.
                cb_layer = layer_str
                if ncfg.callback:
                    try:
                        fn = _cb_reg.get(ncfg.callback)
                        cb_layer = getattr(fn, "_cb_layer", layer_str)
                    except KeyError:
                        pass  # not yet registered — use boundary layer

                from dam.registry.guard import get_guard_registry

                _guard_reg = get_guard_registry()

                _LAYER_KIND_MAP = {
                    "L0": "ood",
                    "L1": "preflight",
                    "L2": "motion",
                    "L3": "execution",
                    "L4": "hardware",
                }
                guard_kind = _LAYER_KIND_MAP.get(cb_layer, "execution")

                special_guard = None
                GuardClass = _guard_reg.get(guard_kind)
                if GuardClass and (guard_kind != "execution" or ncfg.callback is not None):
                    Decorated = guard_decorator(cb_layer)(GuardClass)
                    special_guard = Decorated()

                if special_guard:
                    special_guard.set_name(bname)
                    # Label the guard with its functional kind for global lifecycle management
                    special_guard._guard_kind = guard_kind
                    guards.append(special_guard)

                # Merge any legacy top-level max_speed into params for backward compat
                params = dict(ncfg.params)

                # Global Parameter Inheritance:
                # If 'device' is not in node params, try to inherit from policy config.
                if "device" not in params and config.policy and config.policy.device:
                    params["device"] = config.policy.device

                extra = ncfg.model_extra or {}
                if "max_speed" in extra and "max_speed" not in params:
                    params["max_speed"] = extra["max_speed"]

                # For L2/OOD/HW guards the check logic runs internally;
                # clear callback on the constraint so ExecutionGuard won't re-invoke it.
                stores_callback = guard_kind == "exec"
                stored_callback = ncfg.callback if stores_callback else None
                constraint = BoundaryConstraint(
                    params=params,
                    callback=stored_callback,
                )
                node = BoundaryNode(
                    node_id=ncfg.node_id,
                    constraint=constraint,
                    fallback=ncfg.fallback,
                    timeout_sec=ncfg.timeout_sec,
                )
                nodes.append(node)

            if bcfg.type == "single":
                boundary_containers[bname] = SingleNodeContainer(nodes[0])
            elif bcfg.type == "list":
                boundary_containers[bname] = ListContainer(nodes, loop=bcfg.loop)
            else:
                raise ValueError(
                    f"Unsupported container type '{bcfg.type}' (graph requires Python setup)"
                )

        from dam.fallback.registry import get_global_registry

        fallback_registry = get_global_registry()
        build_escalation_chain(fallback_registry)

        task_config = {tname: tcfg.boundaries for tname, tcfg in config.tasks.items()}
        always_active = config.safety.always_active_list()

        return cls(
            guards=guards,
            boundary_containers=boundary_containers,
            fallback_registry=fallback_registry,
            task_config=task_config,
            always_active=always_active,
            config_pool=config_pool,
            control_frequency_hz=config.safety.control_frequency_hz,
            enforcement_mode=config.safety.enforcement_mode,
            risk_controller_config=config.risk_controller,
            loopback_config=config.loopback,
            kinematics_resolver=kinematics_resolver,
        )


def _make_dummy_node() -> Any:
    from dam.boundary.constraint import BoundaryConstraint
    from dam.boundary.node import BoundaryNode

    return BoundaryNode(node_id="dummy", constraint=BoundaryConstraint(), fallback="emergency_stop")


def _filter_kwargs(fn: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Return only the kwargs that *fn* actually accepts.

    If the function signature contains a ``**kwargs`` parameter (VAR_KEYWORD),
    the full dict is returned unchanged.  Otherwise only the keys that match
    declared parameters are kept, preventing ``TypeError: unexpected keyword
    argument`` when the runtime injects a superset of params into a guard.
    """
    try:
        sig = inspect.signature(fn)
    except (ValueError, TypeError):
        return kwargs  # can't introspect — pass everything and let it fail naturally

    params = sig.parameters
    # If there's a **kwargs param, the function accepts anything
    for p in params.values():
        if p.kind == inspect.Parameter.VAR_KEYWORD:
            return kwargs

    return {k: v for k, v in kwargs.items() if k in params}
