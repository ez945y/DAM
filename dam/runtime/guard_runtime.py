from __future__ import annotations

import contextlib
import dataclasses
import logging
import threading
import time
import uuid
from typing import TYPE_CHECKING, Any, cast

import numpy as np

import dam.runtime.contexts  # noqa: F401 - imports builtin fallback Context registrations
from dam.bus import ObservationBus, PipelineMetricBus, RiskController, WatchdogTimer
from dam.guard.layer import GuardLayer
from dam.guard.stage import Stage
from dam.injection.static import precompute_injection
from dam.runtime.context import (
    ContextEvent,
    NormalContext,
    StepContext,
    get_context_class,
    make_context,
)
from dam.runtime.execution_engine import ExecutionEngine, ValidationContext, _filter_kwargs
from dam.runtime.failure_classify import classify_failure, select_failure_results
from dam.types.action import ActionProposal, ValidatedAction
from dam.types.enforcement import EnforcementMode
from dam.types.observation import Observation
from dam.types.result import GuardDecision, GuardResult
from dam.types.risk import CycleResult, RiskLevel

if TYPE_CHECKING:
    from dam.boundary.container import BoundaryContainer
    from dam.config.schema import FallbackConfig, StackfileConfig
    from dam.guard.base import Guard
    from dam.kinematics.resolver import KinematicsResolver

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class ResolvedFallback:
    """Parsed stackfile fallback entry and optional auto-escalation."""

    context_type: str
    params: dict[str, Any] = dataclasses.field(default_factory=dict)
    escalate_after_seconds: float | None = None
    escalate_to: str | None = None


# Note: fallback routing is no longer a separate global map. The rejecting
# boundary's ``node.fallback`` field IS the routing — each boundary owns its
# own reaction. See _pick_context_for below.


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
        kinematics_resolver: KinematicsResolver | None = None,
        boundary_to_kind: dict[str, str] | None = None,
        frame_hub: Any | None = None,
        default_fallback: str = "emergency_stop",
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
        self._control_frequency_hz = control_frequency_hz
        self._enforcement_mode = enforcement_mode
        self._cycle_id = 0
        self._prev_validated_positions: list[float] | None = None
        # Execution context state machine — STACK. NormalContext sits at the
        # bottom permanently; fallback Contexts push on top via severity-based
        # preemption. is_done pops (resume the previous Context); popping past
        # the bottom restores plain Normal operation. See
        # project_runtime_context_state_machine memory for the full design.
        self._context_stack: list[StepContext] = [NormalContext()]
        # Wall-clock activation time of the current top-of-stack Context.
        # Updated on every push AND pop (= whenever the active Context changes).
        # Runtime owns the timer; Contexts ask via ``should_escalate(elapsed)``.
        self._active_context_since: float = time.monotonic()
        # Named fallback configurations from the stackfile (``fallbacks:``
        # dict in YAML). Each entry has a ``type`` matching a registered
        # fallback Context name plus optional ``params``. Populated by from_stackfile;
        # empty for runtimes built directly (defaults to inline-name shorthand
        # — node.fallback that doesn't match an entry here is tried as a
        # builtin Context name with empty params).
        self._fallbacks_config: dict[str, FallbackConfig] = {}
        # Set when a transition happens this cycle; consumed by _submit_loopback
        # and reset back to None. Used by /dam/context_events MCAP topic.
        self._pending_context_event: ContextEvent | None = None
        self._active_task: str | None = None
        self._active_containers: list[BoundaryContainer] = []
        self._active_container_names: list[str] = []
        self._node_start_times: dict[str, float] = {}
        self._sources: dict[str, Any] = {}
        self._policy: Any = None
        self._sink: Any = None
        self._kinematics_resolver = kinematics_resolver
        self._default_fallback = default_fallback
        self._stages: list[Any] | None = None
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

        # LoopbackWriter: streaming MCAP writer in a dedicated daemon thread.
        # Receives every CycleRecord non-blocking; does all I/O off the hot path.
        self._loopback: Any | None = None
        if loopback_config is not None:
            from dam.logging.loopback_writer import LoopbackWriter

            self._loopback = LoopbackWriter(
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

        # Hot reload double-buffer.  config_version is bumped on every
        # successful swap so MCAP readers can correlate cycles ↔ config.
        self._pending_config: StackfileConfig | None = None
        self._hot_reload_lock = threading.Lock()
        self._config_pool = dict(config_pool)
        # 0 = initial config, never hot-reloaded.  Bumped on every successful
        # swap; readers can use it to distinguish "no swap yet" from "swap N".
        self._config_version: int = 0

        self._running = False
        self._live_img_no_data_warned = False  # one-shot warning for missing camera images
        self._shutdown_complete = False
        self._source_latency_log_t = 0.0

        # Execution pipeline (pure compute — no hardware I/O)
        self._engine = ExecutionEngine(
            enforcement_mode=enforcement_mode,
            metric_bus=self._metric_bus,
            default_fallback=default_fallback,
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
        if self._loopback is not None and hasattr(self._loopback, "set_frame_hub"):
            self._loopback.set_frame_hub(frame_hub)

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
                        {k: self._config_pool[k] for k in g._runtime_keys if k in self._config_pool}
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

        stages = []
        for layer_val in sorted(layer_to_groups):
            groups = layer_to_groups[layer_val]
            pairs = [(guard, bnames) for guard, bnames in groups.values()]
            stages.append(
                Stage(
                    name=layer_to_name[layer_val],
                    guard_boundary_pairs=pairs,
                    parallel=(layer_val >= 2),
                )
            )
        return stages

    def stop_task(self) -> None:
        self._active_task = None
        self._active_containers = []
        self._active_container_names = []
        self._node_start_times = {}
        self._reset_context_stack()
        self.stop_recording()

    def start_recording(self) -> None:
        """Start loopback recording for an active runner loop.

        Task activation alone must not create MCAP sessions; the runner calls
        this only when the user starts execution.
        """
        if self._loopback is not None:
            self._loopback.start()

    def stop_recording(self) -> None:
        """Stop loopback recording if it was started."""
        if self._loopback is not None:
            self._loopback.shutdown()

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
        """Store a new config for thread-safe application at the next cycle boundary.

        Called from the StackfileWatcher callback thread.  The actual swap
        happens inside ``step()`` before any guards run, so config is never
        changed mid-cycle.
        """
        with self._hot_reload_lock:
            self._pending_config = new_config

    @staticmethod
    def _build_config_pool(new_config: StackfileConfig) -> dict[str, Any]:
        """Pure function: merge boundary node params into a fresh config pool.

        Merge strategy is data-defined: each parameter name resolves to a
        :mod:`dam.runtime.merge_policy` callable via the registry.  Unknown
        names default to last-write-wins with a warning when values disagree.

        Auto-injects ``dt`` from ``safety.control_frequency_hz`` so any guard
        declaring ``dt`` in its check() signature receives the control period
        without the stackfile having to repeat it inside boundary params.

        Returns a brand-new dict; never touches runtime state.  May raise
        ``TypeError`` / ``ValueError`` from a registered merge strategy when
        shapes mismatch — caller treats that as a validation failure.
        """
        from dam.runtime.merge_policy import resolve as resolve_merge

        pool: dict[str, Any] = {}

        # Auto-inject dt so QP / CBF / any time-aware guard can pick it up
        # without the stackfile manually duplicating it in every boundary.
        if new_config.safety and new_config.safety.control_frequency_hz > 0:
            pool["dt"] = 1.0 / new_config.safety.control_frequency_hz

        # Structural keys that belong to Container/Node config, never to the
        # guard pool.  Defence-in-depth against parser bugs that might
        # accidentally leak these into node params.
        _STRUCTURAL = {
            "layer",
            "type",
            "callback",
            "fallback",
            "timeout_sec",
            "node_id",
            "nodes",
            "loop",
            "params",
        }

        from dam.boundary.callbacks._registry import normalize_unit_params

        for bname, bcfg in new_config.boundaries.items():
            if bcfg.type == "list":
                continue
            for ncfg in bcfg.nodes:
                # Normalise per-node: degrees→radians once, strip use_degrees
                # so the hot path never sees a unit flag.
                cb_name = getattr(ncfg, "callback", None) or ""
                normalised = normalize_unit_params(cb_name, ncfg.params)
                for pk, pv in normalised.items():
                    if pk in _STRUCTURAL:
                        continue
                    if pk not in pool:
                        pool[pk] = pv
                        continue
                    merge_fn = resolve_merge(pk)
                    merged = merge_fn(pool[pk], pv)
                    # Only the default ``overwrite`` strategy can silently
                    # drop information — warn so the user notices a real
                    # conflict (registered strategies are deliberate).
                    if merge_fn.__name__ == "overwrite" and not np.array_equal(pool[pk], pv):
                        logger.warning(
                            "GuardRuntime: Parameter '%s' overwritten by boundary "
                            "'%s' (prev: %s, new: %s).  Register a merge strategy "
                            "in dam.runtime.merge_policy if this isn't desired.",
                            pk,
                            bname,
                            pool[pk],
                            pv,
                        )
                    pool[pk] = merged
        return pool

    def _apply_config_swap(self, new_config: StackfileConfig) -> None:
        """Validate then commit a new config.

        Build the candidate pool as a pure computation first; only mutate
        runtime state once it succeeds.  A failed swap leaves the runtime on
        the previous config — no half-applied state.  ``_build_config_pool``
        already logs per-parameter overwrites, so this method stays quiet
        unless something interesting (commit / reject) happens.
        """
        try:
            candidate_pool = self._build_config_pool(new_config)
        except Exception:
            logger.error(
                "GuardRuntime: config swap REJECTED; keeping previous config (v%d)",
                self._config_version,
                exc_info=True,
            )
            return

        # Commit: from here on, mutations are simple assignments.
        self._config_pool = candidate_pool
        self._apply_guards_config(new_config, candidate_pool)
        for g in self._guards:
            precompute_injection(g, candidate_pool)
        now = time.monotonic()
        for cname in self._node_start_times:
            self._node_start_times[cname] = now

        self._config_version += 1
        logger.info("GuardRuntime: config swap committed (v%d)", self._config_version)

    # ── Core validate (thin wrapper → ExecutionEngine) ───────────────────────

    def validate(
        self,
        obs: Observation,
        action: ActionProposal,
        trace_id: str,
        now: float | None = None,
    ) -> tuple[ValidatedAction | None, list[GuardResult]]:
        """Returns (validated_action, guard_results).

        Delegates all guard execution to the ExecutionEngine. ``None`` validated
        action means the chassis rejected; the Runtime Context state machine
        in ``step()`` picks a fallback via the rejecting boundary's
        ``node.fallback``.
        """
        ctx = ValidationContext(
            cycle_id=self._cycle_id,
            guards=self._guards,
            stages=self._stages,
            active_containers=self._active_containers,
            active_container_names=self._active_container_names,
            boundary_containers=self._boundary_containers,
            node_start_times=dict(self._node_start_times),
            active_task=self._active_task,
            kinematics_resolver=self._kinematics_resolver,
            hardware_status=self._collect_hardware_status(obs),
            risk_controller=self._risk_controller,
            sink=self._sink,
            runtime=self,
            prev_validated_positions=self._prev_validated_positions,
            dynamics=self._select_dynamics(),
        )
        return self._engine.validate(obs, action, trace_id, ctx, now=now)

    def _run_context_hardware_monitors(
        self,
        obs: Observation,
        trace_id: str,
        existing_results: list[GuardResult],
        now: float | None,
    ) -> list[GuardResult]:
        """Run L3 / always-on monitors for Contexts that bypass the chassis."""
        if not self._active_context.monitors_hardware:
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
            kinematics_resolver=self._kinematics_resolver,
            hardware_status=self._collect_hardware_status(obs),
            risk_controller=self._risk_controller,
            sink=self._sink,
            runtime=self,
            prev_validated_positions=self._prev_validated_positions,
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
        return None

    def _collect_hardware_status(self, obs: Observation) -> dict[str, Any] | None:
        """Return hardware status from observation metadata.

        Telemetry (temperature, current, voltage) is read by the source
        adapter during its ``read()`` call and bundled into
        ``obs.metadata["hardware_status"]``.  No additional bus I/O here
        — guards must never block on hardware reads.
        """
        return obs.metadata.get("hardware_status") or None

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

    # ── Context state machine helpers ───────────────────────────────────────

    @property
    def _active_context(self) -> StepContext:
        return self._context_stack[-1]

    def _resolve_fallback_name(self, fallback_name: str) -> ResolvedFallback | None:
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
        # Inline shorthand: node.fallback is itself a registered Context name.
        if get_context_class(fallback_name) is not None:
            return ResolvedFallback(context_type=fallback_name)
        return None

    def _make_context_from_fallback_name(self, fallback_name: str) -> StepContext | None:
        """Resolve a fallback name + instantiate the Context with all the
        config (params, escalation). Returns None on unknown/invalid names."""
        resolved = self._resolve_fallback_name(fallback_name)
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
        # Apply escalation config as per-instance attrs (overrides class defaults).
        if resolved.escalate_after_seconds is not None:
            ctx.escalate_after_seconds = float(resolved.escalate_after_seconds)
        if resolved.escalate_to is not None:
            ctx.escalate_to = str(resolved.escalate_to)
        return ctx

    def _pick_context_for(self, rejected: GuardResult) -> StepContext | None:
        """Resolve the Context for a rejecting GuardResult.

        Route: rejected guard_name (often a boundary id after fan-out) →
        boundary's active node.fallback → resolve via fallbacks dict or
        builtin Context name. Falls back to ``self._default_fallback``
        (``safety.no_task_behavior``, default "emergency_stop") when the
        boundary has no fallback configured.
        """
        fallback_name: str | None = None
        container = self._boundary_containers.get(rejected.guard_name)
        if container is not None:
            node = container.get_active_node()
            if node is not None and getattr(node, "fallback", None):
                fallback_name = node.fallback
        # No boundary match (guard_name is a guard kind, or the reject came
        # from guard_code with no boundary context) → fall to runtime default.
        # Deliberately NOT guessing "first active container" — that would route
        # an unrelated boundary's fallback and be unpredictable.
        if fallback_name is None:
            fallback_name = self._default_fallback
        return self._make_context_from_fallback_name(fallback_name)

    def _push_context(
        self, new_ctx: StepContext, *, trigger: GuardResult | None, event: str
    ) -> None:
        """Push a new Context on top of the stack and fire on_enter."""
        from_ctx = self._active_context
        new_ctx.on_enter(self, trigger=trigger)
        self._context_stack.append(new_ctx)
        self._active_context_since = time.monotonic()
        self._record_transition(event=event, ctx=new_ctx, from_ctx=from_ctx, trigger=trigger)

    def _pop_context(self) -> None:
        """Pop the top Context (resume the previous). Never pops past
        the bottom NormalContext. The resumed Context is NOT re-entered —
        its internal state is preserved across the preemption. Resume restarts
        the escalation timer (the situation may have changed during preemption)."""
        if len(self._context_stack) <= 1:
            return  # already at Normal
        from_ctx = self._context_stack.pop()
        new_top = self._active_context
        self._active_context_since = time.monotonic()
        self._record_transition(event="exit", ctx=new_top, from_ctx=from_ctx, trigger=None)

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

    def _reset_context_stack(self) -> None:
        """Collapse the context stack back to NormalContext.

        Called on stop_task so a fresh start always begins in normal mode.
        Fires exit transitions for each popped context so MCAP / telemetry
        records the unwind.
        """
        while len(self._context_stack) > 1:
            self._pop_context()

    def _consume_pending_context_event(self) -> ContextEvent | None:
        ev = self._pending_context_event
        self._pending_context_event = None
        return ev

    @staticmethod
    def _find_worst_reject(results: list[GuardResult]) -> GuardResult | None:
        """Return the highest-priority REJECT/FAULT (or None if all PASS/CLAMP)."""
        bad = [r for r in results if r.decision in (GuardDecision.REJECT, GuardDecision.FAULT)]
        if not bad:
            return None
        # FAULT > REJECT; within same decision, higher layer wins on ties.
        return max(bad, key=lambda r: (r.decision.value, r.layer.value))

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

        # ── Read and Merge Multi-Source Observations ───────────────────────
        full_obs = None
        source_latencies: dict[str, float] = {}
        for name, src in self._sources.items():
            t_source = time.monotonic()
            s_obs = src.read()
            source_latencies[name] = (time.monotonic() - t_source) * 1000.0
            if full_obs is None:
                full_obs = s_obs
            else:
                # Merge logic: prioritize base keys, merge images
                if hasattr(s_obs, "images") and s_obs.images:
                    if full_obs.images is None:
                        object.__setattr__(full_obs, "images", {})
                    full_obs.images.update(s_obs.images)
                # If the secondary source provides images but is an OpenCV adapter,
                # it might just return a single frame. Ensure it lands in .images
                if not hasattr(s_obs, "images") and hasattr(s_obs, "frame"):
                    if full_obs.images is None:
                        object.__setattr__(full_obs, "images", {})
                    full_obs.images[name] = s_obs.frame

                # Merge metadata
                if s_obs.metadata:
                    if full_obs.metadata is None:
                        object.__setattr__(full_obs, "metadata", {})
                    full_obs.metadata.update(s_obs.metadata)

        if full_obs is None:
            raise RuntimeError("No hardware sources registered to GuardRuntime")

        obs = full_obs
        # Camera-frame provenance. Hardware adapters (e.g. OpenCV) run the
        # camera on their own thread and push frames straight into the shared
        # frame hub; the runtime injects those into the observation here.
        # Simulation / dataset sources instead deliver frames *on* the
        # observation and never touch the hub. Track which case this is so the
        # runtime — the single place every source's obs flows through — owns
        # the obs→hub bridge for source-embedded frames (and never re-encodes
        # hub-injected hardware frames).
        images_from_hub = False
        if self._frame_hub is not None and hasattr(self._frame_hub, "latest_arrays"):
            camera_images = self._frame_hub.latest_arrays()
            if camera_images:
                object.__setattr__(obs, "images", camera_images)
                images_from_hub = True
        active_camera_names = tuple(obs.images.keys()) if obs.images else ()

        # Built-in host health source — same role as a source adapter but
        # provided by the framework.  Injected into obs.metadata so L3
        # callbacks and the telemetry service both read from the observation
        # layer, just like motor channels.
        from dam.boundary.callbacks.hardware import collect_host_health

        obs.metadata["host_health"] = collect_host_health()

        t_obs = time.monotonic()

        # ── Context state machine: auto-escalate then pop done contexts ──
        # Only active in ENFORCE mode — monitor / log_only must observe without
        # intervening in the action stream.
        state_machine_on = self._enforcement_mode == EnforcementMode.ENFORCE
        if state_machine_on:
            # Auto-escalation: if current Context's timer fired AND it isn't
            # resolving (trigger still violating), push the escalate_to target.
            # Runs before pop so a stuck Context graduates to something stricter
            # rather than yielding back down.
            if not isinstance(self._active_context, NormalContext):
                elapsed = t_start - self._active_context_since
                if self._active_context.should_escalate(elapsed):
                    target_name = self._active_context.escalate_to
                    if target_name:
                        target = self._make_context_from_fallback_name(target_name)
                        if target is not None and target.severity > self._active_context.severity:
                            self._push_context(target, trigger=None, event="escalate")

            # Pop cascading: if a popped Context's predecessor is also done,
            # keep popping. Limit iterations to stack depth as a safety net.
            for _ in range(len(self._context_stack)):
                if isinstance(self._active_context, NormalContext):
                    break
                if not self._active_context.is_done(obs, self):
                    break
                self._pop_context()

        t_ctx = time.monotonic()

        # ── Action proposal (skipped when active Context doesn't need it) ──
        action: ActionProposal | None = (
            self._policy.predict(obs) if self._active_context.requires_proposal else None
        )
        t_policy = time.monotonic()

        # ── Run the active Context's step ──
        step_result = self._active_context.step(
            obs, self, proposal=action, trace_id=trace_id, now=t_start
        )
        guard_results = step_result.guard_results
        guard_results.extend(
            self._run_context_hardware_monitors(obs, trace_id, guard_results, now=t_start)
        )

        # ── Check for reject/fault → push higher-severity Context if any ──
        # Suppressed in non-ENFORCE modes — monitor must not intervene.
        rejected = self._find_worst_reject(guard_results) if state_machine_on else None
        if rejected is not None:
            new_ctx = self._pick_context_for(rejected)
            if new_ctx is not None and new_ctx.severity > self._active_context.severity:
                event = (
                    "preempt" if not isinstance(self._active_context, NormalContext) else "enter"
                )
                self._push_context(new_ctx, trigger=rejected, event=event)
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

        # Track last sent positions for following-error detection in HardwareGuard.
        if validated is not None and validated.target_joint_positions is not None:
            self._prev_validated_positions = validated.target_joint_positions.tolist()

        if validated is not None and self._sink is not None:
            # Use apply() (ActionAdapter ABC). write() is a deprecated alias on legacy sinks.
            if hasattr(self._sink, "apply"):
                self._sink.apply(validated)
            else:
                self._sink.write(validated)  # backward-compat for non-ABC sinks
        t_sink = time.monotonic()

        t_bus = time.monotonic()
        if obs.images and not images_from_hub:
            # Source-embedded frames (simulation / dataset). Bridge them into
            # the shared hub so live preview and the Rust MCAP image writer
            # both see them — exactly as the hardware camera-adapter path
            # already does, but owned here once instead of per source.
            self._publish_frames_to_hub(obs.images, obs.timestamp)
        if obs.images:
            object.__setattr__(obs, "images", None)
        self._obs_bus.write(obs)  # scalar observation ring buffer for loopback / MCAP capture
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
        self._metric_bus.push_stage("obs_bus", _obs_bus_ms)
        self._metric_bus.push_stage("context", _ctx_ms)
        self._metric_bus.push_stage("policy", _policy_ms)
        self._metric_bus.push_stage("guards", _guard_ms)
        self._metric_bus.push_stage("sink", _sink_ms)
        self._metric_bus.push_stage("total", _total_ms)
        self._metric_bus.commit_cycle()
        self._log_source_latency_if_slow(_src_ms, source_latencies)

        # Surface the active Context name in the legacy ``fallback_triggered``
        # field — None when we're sitting in NormalContext, the Context's name
        # otherwise. Will be renamed to ``active_context`` once all consumers
        # migrate (one-release back-compat alias).
        fallback_triggered = (
            self._active_context.name
            if not isinstance(self._active_context, NormalContext)
            else None
        )
        context_event = self._consume_pending_context_event()

        # ── Loopback: build CycleRecord and hand off to writer thread ────────
        if self._loopback is not None:
            self._submit_loopback(
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
                active_cameras=active_camera_names,
                active_context=self._active_context.name,
                context_severity=self._active_context.severity,
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
            mcap_filename=self._loopback.current_filename if self._loopback else None,
            hardware_snapshot=self._build_hardware_snapshot(obs),
        )

    def _publish_frames_to_hub(self, images: dict[str, Any], timestamp: float) -> None:
        """Bridge source-embedded camera frames into the shared frame hub.

        This is the single place that turns ``Observation.images`` (numpy
        arrays delivered by simulation / dataset sources) into JPEGs in the
        hub, so both live preview (``get_latest_images``) and the Rust MCAP
        image writer consume the one hub — sources never re-implement this.
        Hardware frames already arrive in the hub via the camera adapter and
        are intentionally not routed here (no double-encode).
        """
        if self._frame_hub is None or not hasattr(self._frame_hub, "put_jpeg"):
            return
        from dam.logging.loopback_writer import _compress_image

        for cam, arr in images.items():
            try:
                jpeg, w, h, _fmt = _compress_image(arr)
                if jpeg:
                    self._frame_hub.put_jpeg(str(cam), timestamp, jpeg, w, h)
            except Exception:  # noqa: BLE001 — a bad frame must not stall the loop
                continue

    def get_latest_images(self) -> dict[str, bytes]:
        """Return latest camera JPEGs for live preview.

        Backed solely by the shared camera frame hub: hardware adapters feed
        it from the camera thread, and the runtime bridges source-embedded
        (simulation) frames into it via ``_publish_frames_to_hub``. Live
        preview never forces images through the control-loop record path.
        """
        result = (
            self._frame_hub.latest_jpegs()
            if self._frame_hub is not None and hasattr(self._frame_hub, "latest_jpegs")
            else {}
        )
        if result:
            return {str(name): bytes(jpeg) for name, jpeg in result.items()}

        if not self._live_img_no_data_warned:
            self._live_img_no_data_warned = True
            logger.info(
                "get_latest_images: no cached camera images. "
                "Camera images require a dataset with observation.images.* keys "
                "or a real camera source (opencv/lerobot with cameras).",
            )
        return {}

    # ── Loopback helper ────────────────────────────────────────────────────

    def _submit_loopback(
        self,
        obs: Observation,
        action: ActionProposal | None,
        validated: ValidatedAction | None,
        guard_results: list[GuardResult],
        fallback_triggered: str | None,
        trace_id: str,
        latency_stages: dict[str, float],
        active_cameras: tuple[str, ...] = (),
        active_context: str = "normal",
        context_severity: int = 0,
        context_event: ContextEvent | None = None,
    ) -> None:
        """Build a CycleRecord and enqueue it on the LoopbackWriter.

        Runs entirely in the control-loop thread.  All heavy work (serialisation,
        disk I/O, image fetching) happens inside the writer thread.

        Numpy arrays are converted to Python lists here so the writer thread
        never calls .tolist() and never contends for the GIL on numpy ops.
        For a 7-DOF arm this costs ~5 µs total; the payoff is the writer thread
        running pure Python and keeping up with a 50 Hz producer.
        """
        from dam.logging.cycle_record import CycleRecord

        # ── Per-guard latency (embedded by execution methods in result.metadata) ──
        latency_guards: dict[str, float] = {
            r.guard_name: r.metadata.get("_latency_ms", 0.0) for r in guard_results
        }

        # ── Per-layer latency + violation / clamp masks (single O(n_guards) pass) ──
        latency_layers: dict[str, float] = {}
        violated_layer_mask = 0
        clamped_layer_mask = 0
        has_violation = False
        has_clamp = False
        for r in guard_results:
            lname = f"L{int(r.layer)}"
            latency_layers[lname] = latency_layers.get(lname, 0.0) + latency_guards.get(
                r.guard_name, 0.0
            )
            if r.decision in (GuardDecision.REJECT, GuardDecision.FAULT):
                violated_layer_mask |= 1 << int(r.layer)
                has_violation = True
            elif r.decision == GuardDecision.CLAMP:
                clamped_layer_mask |= 1 << int(r.layer)
                has_clamp = True

        # ── Pre-convert numpy → list so the writer thread is numpy-free ──────────
        # Observation.iter_channels() owns the name → field mapping; the
        # runtime stays oblivious to which channels exist.
        def _to_list(arr: Any) -> list[float] | None:
            return arr.tolist() if arr is not None else None

        obs_channels: dict[str, list[float]] = {
            name: arr.tolist() for name, arr in obs.iter_channels()
        }
        failure = self._build_failure_harvest(
            obs=obs,
            action=action,
            validated=validated,
            guard_results=guard_results,
            fallback_triggered=fallback_triggered,
            trace_id=trace_id,
            has_violation=has_violation,
            has_clamp=has_clamp,
            violated_layer_mask=violated_layer_mask,
            clamped_layer_mask=clamped_layer_mask,
            obs_channels=obs_channels,
        )

        rec = CycleRecord(
            cycle_id=self._cycle_id - 1,
            trace_id=trace_id,
            triggered_at=time.monotonic(),
            active_task=self._active_task,
            active_boundaries=tuple(self._active_container_names),
            active_cameras=active_cameras,
            obs_timestamp=obs.timestamp,
            obs_joint_positions=obs.joint_positions.tolist(),
            obs_channels=obs_channels,
            obs_metadata=dict(obs.metadata),
            action_positions=action.target_joint_positions.tolist() if action is not None else [],
            action_velocities=_to_list(action.target_joint_velocities)
            if action is not None
            else None,
            validated_positions=_to_list(validated.target_joint_positions if validated else None),
            validated_velocities=_to_list(validated.target_joint_velocities if validated else None),
            was_clamped=validated.was_clamped if validated else False,
            fallback_triggered=fallback_triggered,
            guard_results=tuple(guard_results),
            latency_stages=latency_stages,
            latency_layers=latency_layers,
            latency_guards=latency_guards,
            has_violation=has_violation,
            has_clamp=has_clamp,
            violated_layer_mask=violated_layer_mask,
            clamped_layer_mask=clamped_layer_mask,
            failure_type=failure["failure_type"],
            failure_guard_names=tuple(failure["guard_names"]),
            failure_layers=tuple(failure["layers"]),
            failure_decisions=tuple(failure["decisions"]),
            failure_reasons=tuple(failure["reasons"]),
            failure_tuple=failure["tuple"],
            config_version=self._config_version,
            active_context=active_context,
            context_severity=context_severity,
            context_event=context_event,
        )
        self._loopback.submit(rec)  # type: ignore[union-attr]

    def _build_failure_harvest(
        self,
        *,
        obs: Observation,
        action: ActionProposal | None,
        validated: ValidatedAction | None,
        guard_results: list[GuardResult],
        fallback_triggered: str | None,
        trace_id: str,
        has_violation: bool,
        has_clamp: bool,
        violated_layer_mask: int,
        clamped_layer_mask: int,
        obs_channels: dict[str, list[float]],
    ) -> dict[str, Any]:
        failure_results = select_failure_results(guard_results)
        guard_names = [r.guard_name for r in failure_results]
        layers = [f"L{int(r.layer)}" for r in failure_results]
        decisions = [r.decision.name for r in failure_results]
        reasons = [r.reason for r in failure_results]

        failure_type: str | None = classify_failure(failure_results)

        failure_tuple = None
        if failure_type is not None:
            failure_tuple = {
                "schema": "dam.failure_tuple.v1",
                "cycle_id": self._cycle_id - 1,
                "trace_id": trace_id,
                "timestamp": obs.timestamp,
                "failure_type": failure_type,
                "active_task": self._active_task,
                "active_boundaries": list(self._active_container_names),
                "guard_names": guard_names,
                "layers": layers,
                "decisions": decisions,
                "reasons": reasons,
                "fault_sources": [r.fault_source for r in failure_results],
                "has_violation": has_violation,
                "has_clamp": has_clamp,
                "violated_layer_mask": violated_layer_mask,
                "clamped_layer_mask": clamped_layer_mask,
                "fallback_triggered": fallback_triggered,
                "action_target_positions": (
                    action.target_joint_positions.tolist() if action is not None else []
                ),
                "validated_positions": (
                    validated.target_joint_positions.tolist()
                    if validated is not None and validated.target_joint_positions is not None
                    else None
                ),
                "observation_channels": sorted(obs_channels),
            }

        return {
            "failure_type": failure_type,
            "guard_names": guard_names,
            "layers": layers,
            "decisions": decisions,
            "reasons": reasons,
            "tuple": failure_tuple,
        }

    def _log_source_latency_if_slow(
        self,
        total_source_ms: float,
        source_latencies: dict[str, float],
    ) -> None:
        """Log per-source timing when the aggregate source stage exceeds budget."""
        source_budget_ms = 1000.0 / self._control_frequency_hz
        if total_source_ms <= source_budget_ms:
            return
        now = time.monotonic()
        if now - self._source_latency_log_t < 1.0:
            return
        self._source_latency_log_t = now
        parts = " ".join(f"{name}={lat:.1f}ms" for name, lat in source_latencies.items())
        logger.warning(
            "source stage over budget: total=%.1fms budget=%.1fms sources=%s",
            total_source_ms,
            source_budget_ms,
            parts,
        )

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

        if self._loopback is not None:
            try:
                self._loopback.shutdown()
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
        """Construct a GuardRuntime from a Stackfile YAML path.

        Thin wrapper: loads the YAML then delegates to ``_from_config``.
        Prefer calling ``_from_config`` directly when the config is already
        parsed (e.g. inside RuntimeFactory) to avoid re-parsing the file.
        """
        from dam.config.loader import StackfileLoader

        return cls._from_config(StackfileLoader.load(path))

    @classmethod
    def _from_config(cls, config: StackfileConfig, frame_hub: Any | None = None) -> GuardRuntime:
        """Construct a GuardRuntime from an already-parsed StackfileConfig.

        Called by ``from_stackfile`` and by ``RuntimeFactory`` (which already
        holds a parsed config and uses this to avoid re-parsing the YAML).
        """
        from dam.registry.callback import get_global_registry as _get_cb_registry
        from dam.registry.guard import get_guard_registry

        kinematics_resolver = cls._init_kinematics_resolver(config)
        guards_by_kind, boundary_to_kind, boundary_containers = cls._build_all_boundaries(
            config, _get_cb_registry(), get_guard_registry()
        )

        task_config = {tname: tcfg.boundaries for tname, tcfg in config.tasks.items()}
        always_active = config.safety.always_active_list()

        logger.info(
            "GuardRuntime.from_stackfile: %d guard kind(s) instantiated for %d boundary(s): %s",
            len(guards_by_kind),
            len(boundary_containers),
            list(guards_by_kind.keys()),
        )

        # Build the initial pool the same way hot reload does so guards see
        # boundary params (and auto-injected ``dt``) from cycle 0.
        initial_pool = cls._build_config_pool(config)

        runtime = cls(
            guards=list(guards_by_kind.values()),
            boundary_containers=boundary_containers,
            task_config=task_config,
            always_active=always_active,
            config_pool=initial_pool,
            control_frequency_hz=config.safety.control_frequency_hz,
            enforcement_mode=config.safety.enforcement_mode,
            risk_controller_config=config.risk_controller,
            loopback_config=config.loopback,
            kinematics_resolver=kinematics_resolver,
            boundary_to_kind=boundary_to_kind,
            frame_hub=frame_hub,
            default_fallback=config.safety.no_task_behavior,
        )

        # Per-guard phase/always override from stackfile guards section.
        _LAYER_KEYS = {"L0", "L1", "L2", "L3"}
        for item in config.guards if isinstance(config.guards, list) else []:
            if not isinstance(item, dict):
                continue
            kind_value: str | None = None
            for k, v in item.items():
                if k in _LAYER_KEYS and isinstance(v, str):
                    kind_value = v
                    break
            if kind_value is None:
                continue
            inst = guards_by_kind.get(kind_value)
            if inst is None:
                continue
            phase_override = item.get("phase")
            always_override = item.get("always")
            if phase_override is None and always_override is None:
                continue
            resolved_always = (
                bool(always_override) if always_override is not None else inst.is_always_on()
            )
            resolved_phase = (
                int(phase_override) if phase_override is not None and not resolved_always else None
            )
            if not resolved_always and resolved_phase is None:
                resolved_phase = inst.get_phase()
            inst.set_phase(resolved_phase, always=resolved_always)
            logger.info(
                "Stackfile override: guard '%s' phase=%s always=%s",
                kind_value,
                resolved_phase,
                resolved_always,
            )

        # Store the stackfile fallbacks: dict so _pick_context_for can resolve
        # node.fallback names → Context type + params at runtime.
        runtime._fallbacks_config = dict(config.fallbacks)

        return runtime

    @classmethod
    def _init_kinematics_resolver(cls, config: StackfileConfig) -> KinematicsResolver | None:
        if not (config.hardware and config.hardware.urdf_path):
            return None
        from dam.kinematics.resolver import KinematicsResolver

        try:
            return KinematicsResolver(config.hardware.urdf_path)
        except Exception as e:
            logger.warning(
                "GuardRuntime: failed to init KinematicsResolver from %s: %s",
                config.hardware.urdf_path,
                e,
            )
            return None

    @classmethod
    def _build_all_boundaries(
        cls,
        config: StackfileConfig,
        _cb_reg: Any,
        _guard_reg: Any,
    ) -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
        _LAYER_KIND_MAP = {
            "L0": "ood",
            "L1": "motion",
            "L2": "execution",
            "L3": "hardware",
        }
        guards_by_kind: dict[str, Any] = {}
        boundary_to_kind: dict[str, str] = {}
        boundary_containers: dict[str, Any] = {}

        for bname, bcfg in config.boundaries.items():
            layer_str = getattr(bcfg, "layer", "L2")
            nodes = []
            for ncfg in bcfg.nodes:
                cb_layer = cls._resolve_cb_layer(ncfg, layer_str, _cb_reg)
                guard_kind = _LAYER_KIND_MAP.get(cb_layer, "execution")
                cls._register_guard_if_new(guard_kind, ncfg, cb_layer, guards_by_kind, _guard_reg)
                if guard_kind in guards_by_kind:
                    boundary_to_kind[bname] = guard_kind
                nodes.append(cls._build_boundary_node(ncfg, guard_kind, config))
            boundary_containers[bname] = cls._make_container(bcfg, nodes)

        cls._configure_stackfile_guard_instances(config, guards_by_kind)
        return guards_by_kind, boundary_to_kind, boundary_containers

    @classmethod
    def _configure_stackfile_guard_instances(
        cls,
        config: StackfileConfig,
        guards_by_kind: dict[str, Any],
    ) -> None:
        """Apply stackfile-selected guard strategies after all boundaries are known."""
        motion_guard = guards_by_kind.get("motion")
        if motion_guard is None:
            return

        qp_values: list[str] = []
        for bcfg in config.boundaries.values():
            if getattr(bcfg, "layer", None) != "L1":
                continue
            for ncfg in bcfg.nodes:
                qp_solver = ncfg.params.get("qp_solver")
                if qp_solver is not None:
                    qp_values.append(str(qp_solver).lower())

        if not qp_values:
            return
        unsupported = sorted({v for v in qp_values if v != "proxsuite"})
        if unsupported:
            raise ValueError(f"Unsupported L1 qp_solver value(s): {unsupported}")

        # Fail fast at build time if QP fusion was requested but the solver is
        # missing.  Otherwise the missing backend only surfaces when
        # motion_qp_aggregator first sees QP metadata at runtime — which would
        # raise inside a guard's hot path and escalate to an emergency stop on
        # the very first cycle.  A misconfiguration should stop startup, not the
        # robot mid-motion.
        from dam.runtime import qp_solver as _qp_solver

        if not _qp_solver.available():
            raise RuntimeError(
                "An L1 boundary requested qp_solver='proxsuite' but the proxsuite "
                "backend is not importable. Install it (pip install proxsuite) or "
                "remove the qp_solver param to use the default box-clamp fusion."
            )

        from dam.guard.aggregators.motion_qp import motion_qp_aggregator

        motion_guard._clamp_aggregator = motion_qp_aggregator

    @classmethod
    def _resolve_cb_layer(cls, ncfg: Any, layer_str: str, cb_reg: Any) -> str:
        if not ncfg.callback:
            return layer_str
        try:
            fn = cb_reg.get(ncfg.callback)
            return getattr(fn, "_cb_layer", layer_str)
        except KeyError:
            return layer_str  # callback not yet registered — use boundary layer

    @classmethod
    def _register_guard_if_new(
        cls,
        guard_kind: str,
        ncfg: Any,
        cb_layer: str,
        guards_by_kind: dict[str, Any],
        guard_reg: Any,
    ) -> None:
        if guard_kind in guards_by_kind:
            return
        from dam.decorators import guard as guard_decorator

        guard_cls = guard_reg.get(guard_kind)
        if guard_cls and (guard_kind != "execution" or ncfg.callback is not None):
            decorated_cls = guard_decorator(cb_layer)(guard_cls)
            instance = decorated_cls()
            instance.set_name(guard_kind)
            instance._guard_kind = guard_kind
            guards_by_kind[guard_kind] = instance

    @classmethod
    def _build_boundary_node(cls, ncfg: Any, guard_kind: str, config: StackfileConfig) -> Any:
        # Every guard dispatches through the boundary-callback pipeline, so the
        # callback name is always kept on the constraint.  (OODGuard used to be
        # the exception — model-driven, name dropped — but it now also supports
        # an L0 callback path via ood_detector with a configured backend,
        # falling back to its own detector when no
        # callback is wired.)
        from dam.boundary.callbacks._registry import normalize_unit_params
        from dam.boundary.constraint import BoundaryConstraint
        from dam.boundary.node import BoundaryNode

        params = normalize_unit_params(ncfg.callback or "", ncfg.params)

        if "device" not in params and config.policy and config.policy.device:
            params["device"] = config.policy.device

        extra = ncfg.model_extra or {}
        if "max_speed" in extra and "max_speed" not in params:
            params["max_speed"] = extra["max_speed"]

        constraint = BoundaryConstraint(
            params=params,
            callback=ncfg.callback,
        )
        return BoundaryNode(
            node_id=ncfg.node_id,
            constraint=constraint,
            fallback=ncfg.fallback or config.safety.no_task_behavior,
            timeout_sec=ncfg.timeout_sec,
            warn_frames=max(1, int(ncfg.warn_frames)),
        )

    @classmethod
    def _make_container(cls, bcfg: Any, nodes: list[Any]) -> Any:
        from dam.boundary.list_container import ListContainer
        from dam.boundary.single import SingleNodeContainer

        if bcfg.type == "single":
            return SingleNodeContainer(nodes[0])
        if bcfg.type == "list":
            return ListContainer(nodes, loop=bcfg.loop)
        raise ValueError(f"Unsupported container type '{bcfg.type}' (graph requires Python setup)")
