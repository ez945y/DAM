from __future__ import annotations

import contextlib
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

import numpy as np

import dam.runtime.builtin_contexts  # noqa: F401 - imports builtin fallback Context registrations
from dam.bus import ObservationBus, PipelineMetricBus, RiskController
from dam.guard.layer import GuardLayer
from dam.guard.stage import Stage
from dam.injection.static import precompute_injection
from dam.runtime.context import (
    ContextEvent,
    NormalContext,
)
from dam.runtime.execution_engine import ExecutionEngine, ValidationContext, _filter_kwargs
from dam.types.action import ActionProposal, ValidatedAction
from dam.types.enforcement import EnforcementMode
from dam.types.observation import Observation
from dam.types.result import GuardDecision, GuardResult
from dam.types.risk import CycleResult, RiskLevel

if TYPE_CHECKING:
    from dam.boundary.container import BoundaryContainer
    from dam.config.schema import StackfileConfig
    from dam.guard.base import Guard

logger = logging.getLogger(__name__)


class GuardRuntime:
    def __init__(
        self,
        guards: list[Guard],
        boundary_containers: dict[str, BoundaryContainer],
        task_config: dict[str, list[str]],
        always_active: list[str] | None = None,
        config_pool: dict[str, Any] | None = None,
        control_frequency_hz: float = 50.0,
        enforcement_mode: EnforcementMode | str = EnforcementMode.ENFORCE,
        risk_controller_config: Any | None = None,  # Optional["RiskControllerConfig"]
        loopback_config: Any | None = None,  # Optional["LoopbackConfig"]
        solvers: dict[str, Any] | None = None,
        boundary_to_kind: dict[str, str] | None = None,
        frame_hub: Any | None = None,
        default_fallback: str = "emergency_stop",
        slow_lane_config: Any | None = None,  # Optional["SlowLaneConfig"]
    ) -> None:
        if always_active is None:
            always_active = []
        if config_pool is None:
            config_pool = {}
        try:
            enforcement_mode = EnforcementMode(enforcement_mode)
        except ValueError:
            raise ValueError(
                f"enforcement_mode must be one of {list(EnforcementMode)}, got '{enforcement_mode}'"
            )

        # Store ALL guards sorted by layer; _guards is the enabled subset.
        self._all_guards: list[Guard] = sorted(guards, key=lambda g: g.get_layer().value)
        self._disabled_kinds: set[str] = set()
        self._guards = list(self._all_guards)

        # Singleton pool: guard_kind → guard instance (one per kind)
        self._guards_by_kind: dict[str, Any] = {}
        for g in self._all_guards:
            kind = getattr(g, "_guard_kind", None)
            if kind:
                self._guards_by_kind[kind] = g

        # Boundary → guard kind mapping (populated by from_stackfile)
        self._boundary_to_kind: dict[str, str] = dict(boundary_to_kind) if boundary_to_kind else {}

        self._boundary_containers = boundary_containers
        self._task_config = task_config
        self._always_active = always_active
        self._layer_timeout_overrides: dict[int, float] = {}
        self._control_frequency_hz = control_frequency_hz
        self._enforcement_mode = enforcement_mode
        self._cycle_id = 0
        self._prev_validated_positions: list[float] | None = None
        self._prev_validated_velocities: list[float] | None = None
        # Context state machine — delegated to ContextStateMachine helper
        from dam.runtime._context_state_machine import ContextStateMachine

        self._ctx_sm = ContextStateMachine(
            default_fallback=default_fallback,
        )
        self._active_task: str | None = None
        self._active_containers: list[BoundaryContainer] = []
        self._active_container_names: list[str] = []
        self._node_start_times: dict[str, float] = {}
        self._sources: dict[str, Any] = {}
        self._policy: Any = None
        self._sink: Any = None
        self._solvers: dict[str, Any] = dict(solvers or {})
        self._stages: list[Any] | None = None
        # ── Slow lane (optional) — async evaluator for L0/L2-lane guards ──
        self._slow_lane_config = slow_lane_config
        self._slow_lane: Any | None = None  # SlowLaneWorker when enabled
        self._slow_stages: list[Any] | None = None
        self._slow_active_names: list[str] = []
        self._slow_stale_warned_at: float = 0.0
        self._frame_hub = frame_hub
        # Real camera shapes populated by the runner during verify(); reused
        # by start_task to warm up policy / guard PyTorch graphs at the actual
        # capture resolution. Empty until verify() runs.
        self._camera_shapes: dict[str, tuple[int, int]] = {}
        self._policy_warmed: bool = False

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

        # PipelineMetricBus: structured per-guard + per-stage latency tracking
        self._metric_bus: PipelineMetricBus = PipelineMetricBus()

        # ObservationBus: ring buffer for loopback capture (±window_sec at hz)
        _obs_window_sec = loopback_config.window_sec if loopback_config else 10.0
        _obs_capacity = max(100, int(_obs_window_sec * 2 * control_frequency_hz) + 50)
        self._obs_bus: ObservationBus = ObservationBus(capacity=_obs_capacity)

        # Cycle telemetry — loopback recording, frame hub, latency logging
        _loopback: Any | None = None
        if loopback_config is not None:
            from dam.logging.loopback_writer import LoopbackWriter

            _loopback = LoopbackWriter(
                output_dir=loopback_config.output_dir,
                obs_bus=self._obs_bus,
                control_frequency_hz=control_frequency_hz,
                window_sec=loopback_config.window_sec,
                rotate_mb=loopback_config.rotate_mb,
                rotate_minutes=loopback_config.rotate_minutes,
                max_queue_depth=loopback_config.max_queue_depth,
                capture_images_on_clamp=loopback_config.capture_images_on_clamp,
                frame_hub=frame_hub,
            )

        from dam.runtime._cycle_telemetry import CycleTelemetry

        self._telemetry = CycleTelemetry(
            loopback=_loopback,
            frame_hub=frame_hub,
            control_frequency_hz=control_frequency_hz,
        )

        # Hot reload double-buffer
        from dam.runtime._hot_reload import HotReloadManager

        self._hot_reload = HotReloadManager(config_pool)

        self._running = False
        self._shutdown_complete = False

        # Execution pipeline (pure compute — no hardware I/O)
        self._engine = ExecutionEngine(
            enforcement_mode=enforcement_mode,
            metric_bus=self._metric_bus,
            default_fallback=default_fallback,
            control_frequency_hz=control_frequency_hz,
        )

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
        self._apply_guards_config(stackfile_config, self._hot_reload.config_pool)
        # Re-compute injection for all active guards with updated pool
        for g in self._guards:
            precompute_injection(g, self._hot_reload.config_pool)

    def _apply_guards_config(
        self,
        cfg: StackfileConfig,
        pool: dict[str, Any],
    ) -> None:
        active_list = cfg.guards
        active_names: set[str] = set()
        for item in active_list:
            if isinstance(item, dict):
                active_names.update(item.values())
            elif isinstance(item, str):
                active_names.add(item)

        disabled: set[str] = set()
        for g in self._all_guards:
            kind: Any = getattr(g, "_guard_kind", None)
            if isinstance(kind, str) and kind not in active_names:
                disabled.add(kind)
        self._disabled_kinds = disabled
        self._guards = [
            g for g in self._all_guards if getattr(g, "_guard_kind", None) in active_names
        ]

        logger.info(
            "GuardRuntime: guards configured — active=%s",
            active_names or "none",
        )

    # ── Adapter registration ────────────────────────────────────────────────

    def register_source(self, name: str, source: Any) -> None:
        self._sources[name] = source

    def set_frame_hub(self, frame_hub: Any | None) -> None:
        self._frame_hub = frame_hub
        self._telemetry._frame_hub = frame_hub
        loopback = self._telemetry.loopback
        if loopback is not None and hasattr(loopback, "set_frame_hub"):
            loopback.set_frame_hub(frame_hub)

    def register_policy(self, policy: Any) -> None:
        self._policy = policy

    def register_sink(self, sink: Any) -> None:
        self._sink = sink

    def preflight(self, camera_shapes: dict[str, tuple[int, int]] | None = None) -> None:
        """One-shot warmup invoked by the runner after source verification.

        Caches the real camera shapes and runs the policy's preflight at those
        shapes so PyTorch compiles per-resolution kernels before the first
        cycle. Guard preflight stays in ``start_task`` because the active set
        can change per task; it reads the cached shapes from here.
        """
        self._camera_shapes = dict(camera_shapes or {})
        if self._policy is not None and not self._policy_warmed:
            try:
                self._policy.preflight(camera_shapes=self._camera_shapes)
                self._policy_warmed = True
            except Exception as exc:
                logger.error("GuardRuntime: policy preflight failed: %s", exc)

        # Eagerly import psutil so the first cycle doesn't pay the module
        # import cost. The cache itself is refreshed in start_task() because
        # its TTL (1s) is shorter than the gap from verify() to the first
        # cycle on a real boot.
        with contextlib.suppress(Exception):
            import psutil  # type: ignore[import-untyped]  # noqa: F401

    def start_task(self, name: str) -> None:
        if name not in self._task_config:
            raise KeyError(f"Task '{name}' not found. Available: {list(self._task_config.keys())}")
        self._active_task = name
        self._active_containers = []
        self._active_container_names = []
        # Clearing node_start_times is what gives HardwareGuard its first-cycle
        # grace after a (re)start — no guard reads cycle_id for that.
        self._node_start_times = {}
        # _cycle_id is intentionally NOT reset here: it is a process-lifetime
        # monotonic counter so telemetry/MCAP/console ordering never regresses
        # across Stop→Start. (A fresh runtime process still starts from 0.)
        now = time.monotonic()

        # Determine all active boundaries (always_active + task boundaries)
        active_bnames = list(self._always_active)
        for bname in self._task_config[name]:
            if bname not in active_bnames:
                active_bnames.append(bname)

        # Build active containers list
        for bname in active_bnames:
            if bname in self._boundary_containers:
                container = self._boundary_containers[bname]
                container._runtime_boundary_name = bname
                self._active_containers.append(container)
                self._active_container_names.append(bname)
                self._node_start_times[bname] = now

        # Rebuild Stage DAG when boundary_to_kind is available (from_stackfile path).
        # On the direct-construction path (tests, set_stages()), leave _stages untouched
        # so manually configured stages are respected.
        if self._boundary_to_kind:
            if self._slow_lane_config is not None:
                fast_bnames = [b for b in active_bnames if self._lane_of(b) == "fast"]
                slow_bnames = [b for b in active_bnames if self._lane_of(b) == "slow"]
                self._stages = self._build_stages_for_task(fast_bnames)
                self._slow_stages = (
                    self._build_stages_for_task(slow_bnames) if slow_bnames else None
                )
                self._slow_active_names = slow_bnames
                self._start_slow_lane()
            else:
                self._stages = self._build_stages_for_task(active_bnames)

        # Preflight: call each guard once per group
        stages_to_preflight = self._stages or []
        for stage in stages_to_preflight:
            if stage.guard_boundary_pairs:
                entries = [
                    (g, bnames[0] if bnames else None) for g, bnames in stage.guard_boundary_pairs
                ]
            else:
                entries = [(g, None) for g in stage.guards]
            for g, pair_bname in entries:
                try:
                    kwargs = dict(g._static_kwargs)
                    kwargs.update(
                        {
                            k: self._hot_reload.config_pool[k]
                            for k in g._runtime_keys
                            if k in self._hot_reload.config_pool
                        }
                    )
                    if pair_bname is not None and pair_bname in self._boundary_containers:
                        node = self._boundary_containers[pair_bname].get_active_node()
                        if node and node.constraint:
                            kwargs.update(node.constraint.params)
                    if self._camera_shapes:
                        kwargs["camera_shapes"] = self._camera_shapes
                    guard_kind = getattr(g, "_guard_kind", g.get_name())
                    logger.debug(
                        "GuardRuntime: preflight '%s' for boundary '%s'", guard_kind, pair_bname
                    )
                    g.preflight(**_filter_kwargs(g.preflight, kwargs))
                except Exception as exc:
                    logger.error(
                        "GuardRuntime: preflight '%s' (%s) failed: %s",
                        getattr(g, "_guard_kind", g.get_name()),
                        pair_bname,
                        exc,
                    )

        # Prime host_health cache as the very last preflight step so the
        # 1s TTL is fresh when the control loop fires its first cycle —
        # otherwise the host_health_limit callback pays ~90ms for psutil
        # virtual_memory() + nvidia-smi subprocess on the first call.
        try:
            from dam.boundary.callbacks.hardware import collect_host_health

            collect_host_health()
        except Exception as exc:
            logger.debug("GuardRuntime: host_health prewarm skipped: %s", exc)

    def _build_stages_for_task(self, active_bnames: list[str]) -> list[Any]:
        """Build Stage DAG from active boundaries using singleton guard instances.

        Groups active boundaries by the layer of their assigned guard, then
        creates one Stage per layer.  When multiple boundaries map to the
        **same guard instance** (e.g. joint_position_limits, joint_velocity_limit,
        bounds all map to MotionGuard), they form a **group**: the guard
        runs once with the merged config pool, and results are fanned out
        to every boundary name in the group.  No boundary is privileged.
        """
        from dam.guard.stage import Stage

        # Collect boundaries per layer, grouped by guard instance
        layer_to_groups: dict[int, dict[int, tuple[Any, list[str]]]] = {}
        layer_to_name: dict[int, str] = {}

        for bname in active_bnames:
            kind = self._boundary_to_kind.get(bname)
            if kind is None:
                continue
            guard = self._guards_by_kind.get(kind)
            if guard is None:
                continue
            layer_val = guard.get_layer().value
            layer_to_name[layer_val] = guard.get_layer().name
            groups = layer_to_groups.setdefault(layer_val, {})
            gid = id(guard)
            if gid not in groups:
                groups[gid] = (guard, [bname])
            else:
                groups[gid][1].append(bname)

        _default_timeout_ms = {0: 50.0, 1: 20.0, 2: 20.0, 3: 30.0}
        _overrides = self._layer_timeout_overrides

        stages = []
        for layer_val in sorted(layer_to_groups):
            groups = layer_to_groups[layer_val]
            pairs = [(guard, bnames) for guard, bnames in groups.values()]
            timeout = _overrides.get(layer_val, _default_timeout_ms.get(layer_val, 20.0))
            stages.append(
                Stage(
                    name=layer_to_name[layer_val],
                    guard_boundary_pairs=pairs,
                    parallel=(layer_val >= 2),
                    timeout_ms=timeout,
                )
            )
        return stages

    # ── Slow lane ──────────────────────────────────────────────────────────

    def _lane_of(self, bname: str) -> str:
        """Resolve a boundary's execution lane via its owning guard.

        The lane binds to the guard (stackfile ``guards:`` section, e.g.
        ``- L0: ood`` + ``lane: fast``); all boundaries dispatched to that
        guard share it.  Default by layer: L0/L2 → slow (expensive,
        low-frequency phenomena), L1/L3 → fast (must run at control rate).
        Unknown boundaries stay fast — conservative: never silently defer
        a check.
        """
        kind = self._boundary_to_kind.get(bname)
        guard = self._guards_by_kind.get(kind) if kind else None
        if guard is None:
            return "fast"
        override: object = getattr(guard, "_lane", None)
        if override in ("fast", "slow"):
            return str(override)
        return "slow" if guard.get_layer().value in (0, 2) else "fast"

    def _start_slow_lane(self) -> None:
        """(Re)start the slow-lane worker for the current task's slow stages."""
        self._stop_slow_lane()
        if not self._slow_stages or self._slow_lane_config is None:
            return
        from dam.runtime.slow_lane import SlowLaneWorker

        self._slow_lane = SlowLaneWorker(
            evaluate_fn=self._evaluate_slow_lane,
            frequency_hz=float(self._slow_lane_config.frequency_hz),
        )
        self._slow_lane.start()
        logger.info(
            "slow lane: %d boundary(s) at %.1f Hz (max_staleness=%.0f ms, stale_action=%s): %s",
            len(self._slow_active_names),
            self._slow_lane_config.frequency_hz,
            self._slow_lane_config.max_staleness_ms,
            self._slow_lane_config.stale_action,
            self._slow_active_names,
        )

    def _stop_slow_lane(self) -> None:
        if self._slow_lane is not None:
            self._slow_lane.stop()
            self._slow_lane = None

    def _evaluate_slow_lane(self, snapshot: Any) -> list[GuardResult]:
        """Run the slow-lane stage list against a snapshot (worker thread).

        Stage order preserves the layer contract (L0 before L2); the action
        in the snapshot is post-fast-lane, so L2 sees post-L1 clamps.  No
        enforcement side effects here — verdicts gate the *next* fast cycles.
        """
        slow_containers = [
            self._boundary_containers[n]
            for n in self._slow_active_names
            if n in self._boundary_containers
        ]
        ctx = ValidationContext(
            cycle_id=snapshot.cycle_id,
            guards=self._guards,
            stages=self._slow_stages,
            active_containers=slow_containers,
            active_container_names=list(self._slow_active_names),
            boundary_containers=self._boundary_containers,
            node_start_times=dict(self._node_start_times),
            active_task=self._active_task,
            solvers=self._solvers,
            risk_controller=self._risk_controller,
            sink=None,  # slow lane never touches actuation
            runtime=self,
            prev_validated_positions=self._prev_validated_positions,
            prev_validated_velocities=self._prev_validated_velocities,
            dynamics=self._select_dynamics(),
            config_pool=self._hot_reload.config_pool,
        )
        return self._engine.run_guard_checks(
            snapshot.obs,
            snapshot.action,
            snapshot.trace_id,
            ctx,
            stages=self._slow_stages,
        )

    def _slow_lane_extra_results(self, now: float | None) -> list[GuardResult] | None:
        """Latest slow verdict as GuardResults for this fast cycle's aggregate.

        - REJECT/FAULT/PASS results join as-is (REJECT latches until a newer
          verdict clears it — that's the gate semantic).
        - CLAMP is converted to REJECT: a clamped action computed against an
          older cycle cannot be applied to the current one; refusing is the
          conservative direction.
        - A verdict older than ``max_staleness_ms`` (or no verdict at all
          after that grace period) triggers ``stale_action``.
        """
        if self._slow_lane is None or not self._slow_stages:
            return None
        import dataclasses as _dc

        cfg = self._slow_lane_config
        if cfg is None:
            return None
        now_m = now if now is not None else time.monotonic()
        extra: list[GuardResult] = []

        verdict = self._slow_lane.latest_verdict()
        if verdict is not None:
            age_ms = (now_m - verdict.produced_at) * 1000.0
            for r in verdict.results:
                meta = {**r.metadata, "slow_lane": True, "verdict_age_ms": age_ms}
                if r.decision == GuardDecision.CLAMP:
                    extra.append(
                        _dc.replace(
                            r,
                            decision=GuardDecision.REJECT,
                            clamped_action=None,
                            reason=f"slow-lane clamp escalated to reject (stale basis): {r.reason}",
                            metadata=meta,
                        )
                    )
                else:
                    extra.append(_dc.replace(r, metadata=meta))

        age_s = self._slow_lane.verdict_age_s(now_m)
        if age_s is not None and age_s * 1000.0 > float(cfg.max_staleness_ms):
            reason = (
                f"slow-lane verdict stale: {age_s * 1000.0:.0f} ms "
                f"> max_staleness_ms={cfg.max_staleness_ms:.0f}"
            )
            if cfg.stale_action == "reject":
                extra.append(GuardResult.reject("slow_lane_watchdog", GuardLayer.L0, reason=reason))
            elif now_m - self._slow_stale_warned_at > 5.0:
                self._slow_stale_warned_at = now_m
                logger.warning("%s (stale_action=warn)", reason)

        return extra or None

    def stop_task(self) -> None:
        self._active_task = None
        self._active_containers = []
        self._active_container_names = []
        self._node_start_times = {}
        self._slow_stages = None
        self._slow_active_names = []
        self._stop_slow_lane()
        self._ctx_sm.reset_stack()
        self.stop_recording()

    def start_recording(self) -> None:
        """Start loopback recording for an active runner loop."""
        self._telemetry.start_recording()

    def stop_recording(self) -> None:
        """Stop loopback recording if it was started."""
        self._telemetry.stop_recording()

    def advance_container(self, name: str) -> None:
        """Advance a named container to its next node and reset its start time."""
        if name in self._boundary_containers:
            container = self._boundary_containers[name]
            container._runtime_boundary_name = name
            container.advance()
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
        """Store a new config for thread-safe application at the next cycle boundary."""
        self._hot_reload.queue_reload(new_config)

    @staticmethod
    def _build_config_pool(new_config: StackfileConfig) -> dict[str, Any]:
        from dam.runtime._hot_reload import HotReloadManager

        return HotReloadManager.build_config_pool(new_config)

    def _apply_config_swap(self, new_config: StackfileConfig) -> None:
        try:
            candidate_pool = self._build_config_pool(new_config)
        except Exception:
            logger.error(
                "GuardRuntime: config swap REJECTED; keeping previous config (v%d)",
                self._hot_reload.config_version,
                exc_info=True,
            )
            return
        self._hot_reload.apply_swap(
            new_config,
            candidate_pool=candidate_pool,
            guards=self._guards,
            apply_guards_config=self._apply_guards_config,
            node_start_times=self._node_start_times,
        )

    # ── Core validate (thin wrapper → ExecutionEngine) ───────────────────────

    def validate(
        self,
        obs: Observation,
        action: ActionProposal,
        trace_id: str,
        now: float | None = None,
        *,
        commit_state: bool = True,
        advance_cycle: bool = True,
        emit_side_effects: bool = True,
    ) -> tuple[ValidatedAction | None, list[GuardResult]]:
        """Returns (validated_action, guard_results).

        Delegates all guard execution to the ExecutionEngine. ``None`` validated
        action means the chassis rejected; the Runtime Context state machine
        in ``step()`` picks a fallback via the rejecting boundary's
        ``node.fallback``.

        If loopback recording is active, the cycle is also submitted to
        the MCAP writer — so ``validate()``-only callers (e.g.
        ``SafetyGuard``) get guard event logging for free.
        """
        if advance_cycle:
            self._cycle_id += 1
        cycle_id = self._cycle_id
        ctx = ValidationContext(
            cycle_id=cycle_id,
            guards=self._guards,
            stages=self._stages,
            active_containers=self._active_containers,
            active_container_names=self._active_container_names,
            boundary_containers=self._boundary_containers,
            node_start_times=dict(self._node_start_times),
            active_task=self._active_task,
            solvers=self._solvers,
            risk_controller=self._risk_controller,
            sink=self._sink,
            runtime=self,
            prev_validated_positions=self._prev_validated_positions,
            prev_validated_velocities=self._prev_validated_velocities,
            dynamics=self._select_dynamics(),
            config_pool=self._hot_reload.config_pool,
        )
        # Slow-lane gating: the latest async verdict (L0/L2) joins this
        # cycle's aggregate; staleness degrades per slow_lane.stale_action.
        extra_results = self._slow_lane_extra_results(now) if emit_side_effects else None
        validated, results = self._engine.validate(
            obs, action, trace_id, ctx, now=now, extra_results=extra_results
        )
        if commit_state:
            self._remember_validated_action(validated)

        # Publish the post-fast-lane state for the slow lane: L2 evaluates
        # what was actually commanded (post-L1 clamps), at its own cadence.
        if emit_side_effects and self._slow_lane is not None and self._slow_stages:
            from dam.runtime.slow_lane import SlowSnapshot

            self._slow_lane.submit(
                SlowSnapshot(
                    obs=obs,
                    action=validated if validated is not None else action,
                    trace_id=trace_id,
                    cycle_id=self._cycle_id,
                    published_at=time.monotonic(),
                )
            )

        if emit_side_effects and self._telemetry.loopback is not None:
            self._telemetry.submit_loopback(
                obs=obs,
                action=action,
                validated=validated,
                guard_results=results,
                fallback_triggered=None,
                trace_id=trace_id,
                latency_stages={},
                cycle_id=self._cycle_id,
                active_task=self._active_task,
                active_container_names=self._active_container_names,
                config_version=self._hot_reload.config_version,
            )

        return validated, results

    def _remember_validated_action(
        self,
        validated: ValidatedAction | None,
    ) -> None:
        """Remember the last command produced by the shared validation path.

        The fallback velocity is command-to-command: (target_t − target_{t−1})/dt.
        Never derive it from the observation — (target − measured)/dt folds the
        follower's physical tracking lag into "velocity", and the acceleration
        limiter then preserves that phantom momentum: commands overshoot the
        operator's actual position and only slowly converge back (inertia).
        """
        if validated is None or validated.target_joint_positions is None:
            return

        target = np.asarray(validated.target_joint_positions, dtype=np.float64)
        prev_target = self._prev_validated_positions
        self._prev_validated_positions = target.tolist()

        if validated.target_joint_velocities is not None:
            self._prev_validated_velocities = np.asarray(
                validated.target_joint_velocities, dtype=np.float64
            ).tolist()
            return

        if prev_target is None:
            self._prev_validated_velocities = None
            return

        prev = np.asarray(prev_target, dtype=np.float64)
        n = min(target.shape[0], prev.shape[0])
        if n == 0:
            self._prev_validated_velocities = None
            return
        dt = float(self._hot_reload.config_pool.get("dt", 1.0 / self._control_frequency_hz))
        dt_safe = max(dt, 1e-6)
        velocities = np.zeros_like(target, dtype=np.float64)
        velocities[:n] = (target[:n] - prev[:n]) / dt_safe
        self._prev_validated_velocities = velocities.tolist()

    def _run_context_hardware_monitors(
        self,
        obs: Observation,
        trace_id: str,
        existing_results: list[GuardResult],
        now: float | None,
    ) -> list[GuardResult]:
        """Run L3 / always-on monitors for Contexts that bypass the chassis."""
        if not self._ctx_sm.active_context.monitors_hardware:
            return []
        if any(r.layer == GuardLayer.L3 for r in existing_results):
            return []

        monitor_guards = [
            g for g in self._guards if g.get_layer() == GuardLayer.L3 or g.is_always_on()
        ]
        if not monitor_guards:
            return []

        monitor_stages: list[Stage] | None = None
        if self._stages is not None:
            monitor_stages = []
            for stage in self._stages:
                pairs = [
                    (g, bnames)
                    for g, bnames in stage.guard_boundary_pairs
                    if g.get_layer() == GuardLayer.L3 or g.is_always_on()
                ]
                guards = [
                    g for g in stage.guards if g.get_layer() == GuardLayer.L3 or g.is_always_on()
                ]
                if pairs or guards:
                    monitor_stages.append(
                        Stage(
                            name=stage.name,
                            guards=guards,
                            guard_boundary_pairs=pairs,
                            parallel=stage.parallel,
                            timeout_ms=stage.timeout_ms,
                        )
                    )
            if not monitor_stages:
                return []

        action = ActionProposal(target_joint_positions=np.asarray(obs.joint_positions))
        ctx = ValidationContext(
            cycle_id=self._cycle_id,
            guards=monitor_guards,
            stages=monitor_stages,
            active_containers=self._active_containers,
            active_container_names=self._active_container_names,
            boundary_containers=self._boundary_containers,
            node_start_times=dict(self._node_start_times),
            active_task=self._active_task,
            solvers=self._solvers,
            risk_controller=self._risk_controller,
            sink=self._sink,
            runtime=self,
            prev_validated_positions=self._prev_validated_positions,
            prev_validated_velocities=self._prev_validated_velocities,
            dynamics=self._select_dynamics(),
        )
        return self._engine.run_guard_checks(
            obs,
            action,
            trace_id,
            ctx,
            guards=monitor_guards,
            stages=monitor_stages,
            now=now,
        )

    def _select_dynamics(self) -> Any:
        """Return the first source's ``dynamics`` context that's available.

        Each adapter that owns kinematic state exposes a ``DynamicsContext``
        via a ``dynamics`` property.  Sources without dynamics (mock,
        dataset, ros2 today) just don't have the attribute, or expose the
        sentinel ``unavailable()``.  Guards see ``None`` and skip gracefully.
        """
        for src in self._sources.values():
            d = getattr(src, "dynamics", None)
            if d is not None and getattr(d, "available", False):
                return d
        solver = self._select_solver("dynamics") or self._select_solver("base_dynamics")
        if solver is not None and getattr(solver, "available", True):
            return solver
        return None

    def _select_solver(self, capability: str) -> Any | None:
        capability = capability.lower()
        for name, solver in self._solvers.items():
            if name.lower() == capability:
                return solver
            caps = getattr(solver, "_dam_solver_capabilities", ())
            if capability in caps:
                return solver
        return None

    @staticmethod
    def _build_hardware_snapshot(
        obs: Observation,
    ) -> dict[str, Any] | None:
        """Assemble a hardware telemetry snapshot from observation metadata.

        Both sources are injected into ``obs.metadata`` at the observation
        phase — adapter-provided motor readings and the built-in host health
        source — so this method is a pure pass-through with no I/O.

        Returns a flat dict with top-level keys from the adapter's
        ``hardware_status`` (temperatures, currents, voltages, …) plus
        ``host_health`` from the built-in source.
        """
        snapshot: dict[str, Any] = {}

        hw_status = obs.metadata.get("hardware_status")
        if hw_status and isinstance(hw_status, dict):
            snapshot.update(hw_status)

        host = obs.metadata.get("host_health")
        if host:
            snapshot["host_health"] = host

        return snapshot or None

    # ── Context state machine delegators (backward compat for tests) ─────────

    @property
    def _active_context(self) -> NormalContext:
        return self._ctx_sm.active_context  # type: ignore[return-value]

    @property
    def _context_stack(self) -> list[Any]:
        return self._ctx_sm._context_stack

    @property
    def _fallbacks_config(self) -> dict[str, Any]:
        return self._ctx_sm._fallbacks_config

    @_fallbacks_config.setter
    def _fallbacks_config(self, value: dict[str, Any]) -> None:
        self._ctx_sm._fallbacks_config = value

    def _push_context(self, new_ctx: Any, *, trigger: GuardResult | None, event: str) -> None:
        self._ctx_sm.push_context(new_ctx, self, trigger=trigger, event=event)

    def _pop_context(self) -> None:
        self._ctx_sm.pop_context()

    def _reset_context_stack(self) -> None:
        self._ctx_sm.reset_stack()

    def _consume_pending_context_event(self) -> ContextEvent | None:
        return self._ctx_sm.consume_pending_event()

    @staticmethod
    def _find_worst_reject(results: list[GuardResult]) -> GuardResult | None:
        from dam.runtime._context_state_machine import ContextStateMachine

        return ContextStateMachine.find_worst_reject(results)

    # ── Telemetry delegators (backward compat for tests) ─────────────────

    def _build_failure_harvest(self, **kwargs: Any) -> dict[str, Any]:
        kwargs.setdefault("cycle_id", self._cycle_id - 1)
        kwargs.setdefault("active_task", self._active_task)
        kwargs.setdefault("active_container_names", self._active_container_names)
        from dam.runtime._cycle_telemetry import CycleTelemetry

        return CycleTelemetry._build_failure_harvest(**kwargs)

    @property
    def _loopback(self) -> Any:
        return self._telemetry.loopback

    # ── Hot reload delegators (backward compat for tests) ──────────────────

    @property
    def _hot_reload_lock(self) -> Any:
        return self._hot_reload.lock

    @property
    def _pending_config(self) -> Any:
        return self._hot_reload._pending_config

    @_pending_config.setter
    def _pending_config(self, value: Any) -> None:
        self._hot_reload._pending_config = value

    @property
    def _config_pool(self) -> dict[str, Any]:
        return self._hot_reload.config_pool

    @property
    def _config_version(self) -> int:
        return self._hot_reload.config_version

    @_config_version.setter
    def _config_version(self, value: int) -> None:
        self._hot_reload._config_version = value

    # ── step() — single cycle ───────────────────────────────────────────────

    def step(self) -> CycleResult:
        # 3G: Apply pending hot-reload config swap BEFORE the cycle runs
        pending = self._hot_reload.consume_pending()
        if pending is not None:
            self._apply_config_swap(pending)

        t_start = time.monotonic()
        trace_id = str(uuid.uuid4())

        # ── Read and Merge Multi-Source Observations ───────────────────────
        full_obs = None
        extra_images: dict[str, np.ndarray] = {}
        extra_channels: dict[str, np.ndarray] = {}
        extra_metadata: dict[str, Any] = {}
        source_latencies: dict[str, float] = {}
        for name, src in self._sources.items():
            t_source = time.monotonic()
            s_obs = src.read()
            source_latencies[name] = (time.monotonic() - t_source) * 1000.0
            if full_obs is None:
                full_obs = s_obs
            else:
                if hasattr(s_obs, "images") and s_obs.images:
                    extra_images.update(s_obs.images)
                if not hasattr(s_obs, "images") and hasattr(s_obs, "frame"):
                    extra_images[name] = s_obs.frame
                if s_obs.metadata:
                    extra_metadata.update(s_obs.metadata)
                if s_obs.channels:
                    extra_channels.update(s_obs.channels)

        if full_obs is None:
            raise RuntimeError("No hardware sources registered to GuardRuntime")

        # Images from source adapters only (published to hub, not re-publishing hub frames).
        source_embedded_images = {**(full_obs.images or {}), **extra_images}
        all_images = dict(source_embedded_images)
        if self._frame_hub is not None and hasattr(self._frame_hub, "latest_arrays"):
            camera_images = self._frame_hub.latest_arrays()
            if camera_images:
                collisions = set(source_embedded_images).intersection(camera_images)
                if collisions:
                    names = ", ".join(sorted(collisions))
                    raise RuntimeError(
                        "Camera stream name collision while composing observation: "
                        f"{names}. Configure image_namespace on the dataset source "
                        "or give live camera sources unique names."
                    )
                all_images.update(camera_images)
        active_camera_names = tuple(all_images.keys()) if all_images else ()

        t_obs = time.monotonic()

        # Built-in host health source
        from dam.boundary.callbacks.hardware import collect_host_health

        extra_metadata["host_health"] = collect_host_health()
        _host_health_ms = (time.monotonic() - t_obs) * 1000.0

        obs = full_obs.merged(
            images=all_images or None,
            channels=extra_channels or None,
            metadata=extra_metadata,
        )

        # ── Context state machine: auto-escalate then pop done contexts ──
        # Only active in ENFORCE mode — monitor / log_only must observe without
        # intervening in the action stream.
        state_machine_on = self._enforcement_mode == EnforcementMode.ENFORCE
        if state_machine_on:
            # Auto-escalation: if current Context's timer fired AND it isn't
            # resolving (trigger still violating), push the escalate_to target.
            # Runs before pop so a stuck Context graduates to something stricter
            # rather than yielding back down.
            if not isinstance(self._ctx_sm.active_context, NormalContext):
                elapsed = t_start - self._ctx_sm.active_context_since
                if self._ctx_sm.active_context.should_escalate(elapsed):
                    target_name = self._ctx_sm.active_context.escalate_to
                    if target_name:
                        target = self._ctx_sm.make_context_from_fallback_name(target_name)
                        if (
                            target is not None
                            and target.severity > self._ctx_sm.active_context.severity
                        ):
                            self._ctx_sm.push_context(target, self, trigger=None, event="escalate")

            # Pop cascading: if a popped Context's predecessor is also done,
            # keep popping. Limit iterations to stack depth as a safety net.
            for _ in range(self._ctx_sm.stack_depth):
                if isinstance(self._ctx_sm.active_context, NormalContext):
                    break
                if not self._ctx_sm.active_context.is_done(obs, self):
                    break
                self._ctx_sm.pop_context()

        t_ctx = time.monotonic()

        # ── Action proposal (skipped when active Context doesn't need it) ──
        action: ActionProposal | None = (
            self._policy.predict(obs) if self._ctx_sm.active_context.requires_proposal else None
        )
        t_policy = time.monotonic()

        # ── Run the active Context's step ──
        step_result = self._ctx_sm.active_context.step(
            obs, self, proposal=action, trace_id=trace_id, now=t_start
        )
        guard_results = step_result.guard_results
        guard_results.extend(
            self._run_context_hardware_monitors(obs, trace_id, guard_results, now=t_start)
        )

        # ── Check for reject/fault → push higher-severity Context if any ──
        # Suppressed in non-ENFORCE modes — monitor must not intervene.
        rejected = self._ctx_sm.find_worst_reject(guard_results) if state_machine_on else None
        if rejected is not None:
            new_ctx = self._ctx_sm.pick_context_for(rejected, self._boundary_containers)
            if new_ctx is not None and new_ctx.severity > self._ctx_sm.active_context.severity:
                event = (
                    "preempt"
                    if not isinstance(self._ctx_sm.active_context, NormalContext)
                    else "enter"
                )
                self._ctx_sm.push_context(new_ctx, self, trigger=rejected, event=event)
                # Re-step in the new Context this cycle so the sink gets a
                # fallback action immediately rather than a silent cycle.
                if new_ctx.requires_proposal and action is None:
                    action = self._policy.predict(obs)
                step_result = new_ctx.step(
                    obs, self, proposal=action, trace_id=trace_id, now=t_start
                )
                # guard_results stays as the original chassis output — the
                # re-step's results (if any) would duplicate or differ from
                # what the cycle truly decided. Logging prefers the trigger.

        validated = step_result.action
        t_validate = time.monotonic()

        # Track the final action this cycle will send. NormalContext already
        # passes through validate(); fallback contexts may post-process that
        # output or generate their own waypoint, so commit the final action here.
        self._remember_validated_action(validated)

        if validated is not None and self._sink is not None:
            # Use apply() (ActionAdapter ABC). write() is a deprecated alias on legacy sinks.
            if hasattr(self._sink, "apply"):
                self._sink.apply(validated)
            else:
                self._sink.write(validated)  # backward-compat for non-ABC sinks
        t_sink = time.monotonic()

        t_bus = time.monotonic()
        if source_embedded_images:
            # Source-embedded frames (simulation / dataset). Bridge them into
            # the shared hub so live preview and the Rust MCAP image writer
            # both see them — exactly as the hardware camera-adapter path
            # already does, but owned here once instead of per source.
            self._telemetry.publish_frames_to_hub(source_embedded_images, obs.timestamp)
        bus_obs = obs.merged(images=None) if obs.images else obs
        self._obs_bus.write(bus_obs)  # scalar observation ring buffer for loopback / MCAP capture
        _obs_bus_ms = (time.monotonic() - t_bus) * 1000.0

        risk = self._compute_risk()
        self._cycle_id += 1

        # ── Push pipeline-stage timing and commit layer aggregates ──────────
        # Deliberately placed after all guard execution so the MetricBus holds
        # a complete picture before commit_cycle() finalises the layer history.
        _src_ms = (t_obs - t_start) * 1000.0
        _ctx_ms = (t_ctx - t_obs) * 1000.0
        _policy_ms = (t_policy - t_ctx) * 1000.0
        _guard_ms = (t_validate - t_policy) * 1000.0
        _sink_ms = (t_sink - t_validate) * 1000.0
        _total_ms = (t_sink - t_start) * 1000.0

        self._metric_bus.push_stage("source", _src_ms)
        for _source_name, _source_ms in source_latencies.items():
            self._metric_bus.push_stage(f"source.{_source_name}", _source_ms)
        self._metric_bus.push_stage("host_health", _host_health_ms)
        self._metric_bus.push_stage("obs_bus", _obs_bus_ms)
        self._metric_bus.push_stage("context", _ctx_ms)
        self._metric_bus.push_stage("policy", _policy_ms)
        self._metric_bus.push_stage("guards", _guard_ms)
        self._metric_bus.push_stage("sink", _sink_ms)
        self._metric_bus.push_stage("total", _total_ms)
        self._metric_bus.commit_cycle()
        self._telemetry.log_source_latency_if_slow(_src_ms, source_latencies)

        # Active Context name: None when in NormalContext, the Context's name
        # otherwise.
        fallback_triggered = (
            self._ctx_sm.active_context.name
            if not isinstance(self._ctx_sm.active_context, NormalContext)
            else None
        )
        context_event = self._ctx_sm.consume_pending_event()

        # ── Loopback: build CycleRecord and hand off to writer thread ────────
        if self._telemetry.loopback is not None:
            self._telemetry.submit_loopback(
                obs=obs,
                action=action,
                validated=validated,
                guard_results=guard_results,
                fallback_triggered=fallback_triggered,
                trace_id=trace_id,
                latency_stages={
                    "source": _src_ms,
                    "policy": _policy_ms,
                    "guards": _guard_ms,
                    "sink": _sink_ms,
                    "total": _total_ms,
                },
                cycle_id=self._cycle_id - 1,
                active_task=self._active_task,
                active_container_names=self._active_container_names,
                config_version=self._hot_reload.config_version,
                active_cameras=active_camera_names,
                active_context=self._ctx_sm.active_context.name,
                context_severity=self._ctx_sm.active_context.severity,
                context_event=context_event,
            )

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
                "context": (t_ctx - t_obs) * 1000,
                "policy": (t_policy - t_ctx) * 1000,
                "validate": (t_validate - t_policy) * 1000,
                "sink": (t_sink - t_validate) * 1000,
                "total": (t_sink - t_start) * 1000,
            },
            risk_level=risk,
            active_task=self._active_task,
            active_boundaries=list(self._active_container_names),
            mcap_filename=self._telemetry.loopback.current_filename
            if self._telemetry.loopback
            else None,
            hardware_snapshot=self._build_hardware_snapshot(obs),
            observation=obs,
        )

    def get_latest_images(self) -> dict[str, bytes]:
        """Return latest camera JPEGs for live preview."""
        return self._telemetry.get_latest_images()

    def stop(self) -> None:
        """Signal ``run()`` to exit after the current cycle completes."""
        self._running = False

    def shutdown(self) -> None:
        """Disconnect from hardware and stop background threads.

        Must be called before discarding the runtime instance to prevent
        resource leaks (semaphores, camera handles).
        """
        if self._shutdown_complete:
            return
        self._shutdown_complete = True
        self._running = False
        self._stop_slow_lane()
        if hasattr(self, "_engine") and self._engine is not None:
            self._engine.shutdown()
        if hasattr(self, "_watchdog") and self._watchdog is not None:
            with contextlib.suppress(Exception):
                self._watchdog.disarm()

        disconnected: set[int] = set()
        for name, src in self._sources.items():
            obj_id = id(src)
            if obj_id in disconnected:
                continue
            disconnected.add(obj_id)
            if hasattr(src, "disconnect"):
                try:
                    src.disconnect()
                except Exception as exc:
                    logger.debug("GuardRuntime: source '%s' disconnect failed: %s", name, exc)

        if self._sink is not None:
            sink_id = id(self._sink)
            if sink_id not in disconnected:
                if hasattr(self._sink, "shutdown"):
                    try:
                        self._sink.shutdown()
                    except Exception as exc:
                        logger.debug("GuardRuntime: sink shutdown failed: %s", exc)
                elif hasattr(self._sink, "disconnect"):
                    try:
                        self._sink.disconnect()
                    except Exception as exc:
                        logger.debug("GuardRuntime: sink disconnect failed: %s", exc)

        if self._telemetry.loopback is not None:
            try:
                self._telemetry.loopback.shutdown()
            except Exception as exc:
                logger.debug("GuardRuntime: loopback shutdown failed: %s", exc)

    # ── Risk computation ────────────────────────────────────────────────────

    def _compute_risk(self) -> RiskLevel:
        """Map RiskController level (0–3) to RiskLevel enum (NORMAL/ELEVATED/CRITICAL)."""
        level = self._risk_controller.risk_level()
        if level >= 2:
            return RiskLevel.CRITICAL
        if level == 1:
            return RiskLevel.ELEVATED
        return RiskLevel.NORMAL

    # ── Class constructors ─────────────────────────────────────────────

    @classmethod
    def from_stackfile(cls, path: str) -> GuardRuntime:
        """Construct a GuardRuntime from a Stackfile YAML path."""
        from dam.runtime import _stackfile_builder

        result: GuardRuntime = _stackfile_builder.from_stackfile(cls, path)
        return result

    @classmethod
    def _from_config(cls, config: StackfileConfig, frame_hub: Any | None = None) -> GuardRuntime:
        """Construct a GuardRuntime from an already-parsed StackfileConfig."""
        from dam.runtime import _stackfile_builder

        result: GuardRuntime = _stackfile_builder.from_config(cls, config, frame_hub)
        return result

    @staticmethod
    def _configure_stackfile_guard_instances(config: Any, guards_by_kind: dict[str, Any]) -> None:
        from dam.runtime._stackfile_builder import _configure_stackfile_guard_instances

        _configure_stackfile_guard_instances(config, guards_by_kind)

    @staticmethod
    def _build_all_boundaries(
        config: Any, _cb_reg: Any, _guard_reg: Any
    ) -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
        from dam.runtime._stackfile_builder import _build_all_boundaries

        return _build_all_boundaries(config, _cb_reg, _guard_reg)

    @staticmethod
    def _build_boundary_node(ncfg: Any, guard_kind: str, config: Any) -> Any:
        from dam.runtime._stackfile_builder import _build_boundary_node

        return _build_boundary_node(ncfg, guard_kind, config)
