from __future__ import annotations

import contextlib
import logging
import threading
import time
import uuid
from typing import TYPE_CHECKING, Any, cast

import numpy as np

from dam.injection.static import precompute_injection
from dam.runtime.execution_engine import ExecutionEngine, ValidationContext, _filter_kwargs
from dam.types.action import ValidatedAction
from dam.types.enforcement import EnforcementMode
from dam.types.observation import Observation
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

from dam.bus import ObservationBus, PipelineMetricBus, RiskController, WatchdogTimer

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
        enforcement_mode: EnforcementMode | str = EnforcementMode.ENFORCE,
        risk_controller_config: Any | None = None,  # Optional["RiskControllerConfig"]
        loopback_config: Any | None = None,  # Optional["LoopbackConfig"]
        kinematics_resolver: KinematicsResolver | None = None,
        boundary_to_kind: dict[str, str] | None = None,
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
        self._sources: dict[str, Any] = {}
        self._policy: Any = None
        self._sink: Any = None
        self._kinematics_resolver = kinematics_resolver
        self._stages: list[Any] | None = None

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
            )
            self._loopback.start()

        # Hot reload double-buffer
        self._pending_config: StackfileConfig | None = None
        self._hot_reload_lock = threading.Lock()
        self._config_pool = dict(config_pool)

        self._running = False
        self._live_img_no_data_warned = False  # one-shot warning for missing camera images

        # Execution pipeline (pure compute — no hardware I/O)
        self._engine = ExecutionEngine(
            enforcement_mode=enforcement_mode,
            fallback_registry=fallback_registry,
            metric_bus=self._metric_bus,
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

    def register_policy(self, policy: Any) -> None:
        self._policy = policy

    def register_sink(self, sink: Any) -> None:
        self._sink = sink

    def start_task(self, name: str) -> None:
        if name not in self._task_config:
            raise KeyError(f"Task '{name}' not found. Available: {list(self._task_config.keys())}")
        self._active_task = name
        self._active_containers = []
        self._active_container_names = []
        self._node_start_times = {}
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
                self._active_containers.append(container)
                self._active_container_names.append(bname)
                self._node_start_times[bname] = now

        # Rebuild Stage DAG when boundary_to_kind is available (from_stackfile path).
        # On the direct-construction path (tests, set_stages()), leave _stages untouched
        # so manually configured stages are respected.
        if self._boundary_to_kind:
            self._stages = self._build_stages_for_task(active_bnames)

        # Preflight: call each guard once per boundary it will handle
        stages_to_preflight = self._stages or []
        for stage in stages_to_preflight:
            pairs = (
                stage.guard_boundary_pairs
                if stage.guard_boundary_pairs
                else [(g, cast("str | None", None)) for g in stage.guards]
            )
            for g, pair_bname in pairs:
                try:
                    kwargs = dict(g._static_kwargs)
                    kwargs.update(
                        {k: self._config_pool[k] for k in g._runtime_keys if k in self._config_pool}
                    )
                    if pair_bname is not None and pair_bname in self._boundary_containers:
                        node = self._boundary_containers[pair_bname].get_active_node()
                        if node and node.constraint:
                            kwargs.update(node.constraint.params)
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

    def _build_stages_for_task(self, active_bnames: list[str]) -> list[Any]:
        """Build Stage DAG from active boundaries using singleton guard instances.

        Groups active boundaries by the layer of their assigned guard, then creates
        one Stage per layer.  Each stage carries ``guard_boundary_pairs`` so the
        same guard instance is invoked once per boundary with that boundary's params.
        """
        from dam.guard.stage import Stage

        layer_to_pairs: dict[int, list[tuple[Any, str | None]]] = {}
        layer_to_name: dict[int, str] = {}

        for bname in active_bnames:
            kind = self._boundary_to_kind.get(bname)
            if kind is None:
                continue
            guard = self._guards_by_kind.get(kind)
            if guard is None:
                continue
            layer_val = guard.get_layer().value
            layer_to_pairs.setdefault(layer_val, []).append((guard, bname))
            layer_to_name[layer_val] = guard.get_layer().name

        stages = []
        for layer_val in sorted(layer_to_pairs):
            pairs = layer_to_pairs[layer_val]
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

    # ── Core validate (thin wrapper → ExecutionEngine) ───────────────────────

    def validate(
        self,
        obs: Observation,
        action: ActionProposal,
        trace_id: str,
        now: float | None = None,
    ) -> tuple[ValidatedAction | None, list[GuardResult], str | None]:
        """Returns (validated_action, guard_results, fallback_name_triggered).

        Delegates all guard execution and enforcement logic to the ExecutionEngine.
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
        )
        return self._engine.validate(obs, action, trace_id, ctx, now=now)

    def _collect_hardware_status(self, obs: Observation) -> dict[str, Any]:
        """Merge hardware status from sink and observation metadata."""
        hardware_status: dict[str, Any] = {}
        if self._sink is not None and hasattr(self._sink, "get_hardware_status"):
            with contextlib.suppress(Exception):
                sink_status = self._sink.get_hardware_status()
                if sink_status:
                    hardware_status.update(sink_status)
        obs_hw_status = obs.metadata.get("hardware_status")
        if obs_hw_status:
            hardware_status.update(obs_hw_status)
        return hardware_status

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
        for name, src in self._sources.items():
            s_obs = src.read()
            if full_obs is None:
                full_obs = s_obs
            else:
                # Merge logic: prioritize base keys, merge images
                if hasattr(s_obs, "images") and s_obs.images:
                    full_obs.images.update(s_obs.images)
                # If the secondary source provides images but is an OpenCV adapter,
                # it might just return a single frame. Ensure it lands in .images
                if not hasattr(s_obs, "images") and hasattr(s_obs, "frame"):
                    full_obs.images[name] = s_obs.frame

                # Merge metadata
                if s_obs.metadata:
                    full_obs.metadata.update(s_obs.metadata)

        if full_obs is None:
            raise RuntimeError("No hardware sources registered to GuardRuntime")

        obs = full_obs
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
        _src_ms = (t_obs - t_start) * 1000.0
        _policy_ms = (t_policy - t_obs) * 1000.0
        _guard_ms = (t_validate - t_policy) * 1000.0
        _sink_ms = (t_sink - t_validate) * 1000.0
        _total_ms = (t_sink - t_start) * 1000.0

        self._metric_bus.push_stage("source", _src_ms)
        self._metric_bus.push_stage("policy", _policy_ms)
        self._metric_bus.push_stage("guards", _guard_ms)
        self._metric_bus.push_stage("sink", _sink_ms)
        self._metric_bus.push_stage("total", _total_ms)
        self._metric_bus.commit_cycle()

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
                "policy": (t_policy - t_obs) * 1000,
                "validate": (t_validate - t_policy) * 1000,
                "sink": (t_sink - t_validate) * 1000,
                "total": (t_sink - t_start) * 1000,
            },
            risk_level=risk,
            active_task=self._active_task,
            active_boundaries=list(self._active_container_names),
        )

    def get_latest_images(self) -> dict[str, bytes]:
        """Return JPEG-compressed bytes for the most recent observation images.

        Reads the last entry from the ObservationBus ring buffer and encodes
        each camera frame as JPEG.  Returns an empty dict when no images are
        available or when encoding fails.  Intended for live telemetry preview
        only — not for archival; the loopback MCAP path handles that.
        """
        try:
            obs = self._obs_bus.read_latest()
        except Exception:
            logger.debug("get_latest_images: read_latest() failed", exc_info=True)
            return {}
        if obs is None:
            return {}
        from dam.types.observation import Observation

        if not isinstance(obs, Observation) or not obs.images:
            if not self._live_img_no_data_warned:
                self._live_img_no_data_warned = True
                logger.info(
                    "get_latest_images: observation has no images "
                    "(obs.images=%r, type=%s). "
                    "Camera images require a dataset with observation.images.* keys "
                    "or a real camera source (opencv/lerobot with cameras).",
                    type(obs.images) if isinstance(obs, Observation) else "N/A",
                    type(obs).__name__,
                )
            return {}

        result: dict[str, bytes] = {}
        for cam_name, frame in obs.images.items():
            # Pickle/unpickle through the Rust ring buffer may deserialise numpy
            # arrays as nested lists — convert back to ndarray before encoding.
            if not isinstance(frame, np.ndarray):
                try:
                    frame = np.asarray(frame, dtype=np.uint8)
                except Exception:
                    logger.debug(
                        "get_latest_images: camera %r frame is not array-like: %r",
                        cam_name,
                        type(frame),
                    )
                    continue
            if frame.size == 0:
                continue
            try:
                from dam.logging.loopback_writer import _compress_image

                jpeg_bytes, w, h, fmt = _compress_image(frame)
                result[cam_name] = jpeg_bytes
            except Exception:
                logger.warning(
                    "get_latest_images: failed to encode camera %r", cam_name, exc_info=True
                )
        return result

    # ── Loopback helper ────────────────────────────────────────────────────

    def _submit_loopback(
        self,
        obs: Observation,
        action: ActionProposal,
        validated: ValidatedAction | None,
        guard_results: list[GuardResult],
        fallback_triggered: str | None,
        trace_id: str,
        latency_stages: dict[str, float],
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
        def _to_list(arr: Any) -> list[float] | None:
            return arr.tolist() if arr is not None else None

        rec = CycleRecord(
            cycle_id=self._cycle_id - 1,
            trace_id=trace_id,
            triggered_at=time.monotonic(),
            active_task=self._active_task,
            active_boundaries=tuple(self._active_container_names),
            active_cameras=tuple(obs.images.keys()) if obs.images else (),
            obs_timestamp=obs.timestamp,
            obs_joint_positions=obs.joint_positions.tolist(),
            obs_joint_velocities=_to_list(obs.joint_velocities),
            obs_end_effector_pose=_to_list(obs.end_effector_pose),
            obs_force_torque=_to_list(obs.force_torque),
            obs_metadata=dict(obs.metadata),
            action_positions=action.target_joint_positions.tolist(),
            action_velocities=_to_list(action.target_joint_velocities),
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
        )
        # Determine if we should capture images this cycle
        want_images = obs.images is not None and (
            has_violation or (self._loopback._capture_images_on_clamp and has_clamp)  # type: ignore[union-attr]
        )
        images = obs.images if want_images else None

        self._loopback.submit(rec, images)  # type: ignore[union-attr]

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
            # Ensure the watchdog thread is always stopped even if run() crashes
            if watchdog is not None:
                with contextlib.suppress(Exception):
                    watchdog.disarm()
            self._running = False

        return results

    def stop(self) -> None:
        """Signal ``run()`` to exit after the current cycle completes, then shutdown."""
        self._running = False
        # Ensure loopback writer is properly shutdown
        if self._loopback is not None:
            self._loopback.shutdown()

    def shutdown(self) -> None:
        """Disconnect from hardware and stop background threads.

        Must be called before discarding the runtime instance to prevent
        resource leaks (semaphores, camera handles).
        """
        self._running = False
        if hasattr(self, "_watchdog") and self._watchdog is not None:
            with contextlib.suppress(Exception):
                self._watchdog.disarm()

        for name, src in self._sources.items():
            if hasattr(src, "disconnect"):
                try:
                    src.disconnect()
                except Exception as exc:
                    logger.debug("GuardRuntime: source '%s' disconnect failed: %s", name, exc)

        if self._sink is not None:
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
    def _from_config(cls, config: StackfileConfig) -> GuardRuntime:
        """Construct a GuardRuntime from an already-parsed StackfileConfig.

        Called by ``from_stackfile`` and by ``RuntimeFactory`` (which already
        holds a parsed config and uses this to avoid re-parsing the YAML).
        """
        from dam.fallback.chain import build_escalation_chain
        from dam.fallback.registry import get_global_registry
        from dam.registry.callback import get_global_registry as _get_cb_registry
        from dam.registry.guard import get_guard_registry

        kinematics_resolver = cls._init_kinematics_resolver(config)
        guards_by_kind, boundary_to_kind, boundary_containers = cls._build_all_boundaries(
            config, _get_cb_registry(), get_guard_registry()
        )

        fallback_registry = get_global_registry()
        build_escalation_chain(fallback_registry)

        task_config = {tname: tcfg.boundaries for tname, tcfg in config.tasks.items()}
        always_active = config.safety.always_active_list()

        logger.info(
            "GuardRuntime.from_stackfile: %d guard kind(s) instantiated for %d boundary(s): %s",
            len(guards_by_kind),
            len(boundary_containers),
            list(guards_by_kind.keys()),
        )

        return cls(
            guards=list(guards_by_kind.values()),
            boundary_containers=boundary_containers,
            fallback_registry=fallback_registry,
            task_config=task_config,
            always_active=always_active,
            config_pool={},
            control_frequency_hz=config.safety.control_frequency_hz,
            enforcement_mode=config.safety.enforcement_mode,
            risk_controller_config=config.risk_controller,
            loopback_config=config.loopback,
            kinematics_resolver=kinematics_resolver,
            boundary_to_kind=boundary_to_kind,
        )

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
            "L1": "preflight",
            "L2": "motion",
            "L3": "execution",
            "L4": "hardware",
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

        return guards_by_kind, boundary_to_kind, boundary_containers

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

        GuardClass = guard_reg.get(guard_kind)
        if GuardClass and (guard_kind != "execution" or ncfg.callback is not None):
            Decorated = guard_decorator(cb_layer)(GuardClass)
            instance = Decorated()
            instance.set_name(guard_kind)
            instance._guard_kind = guard_kind
            guards_by_kind[guard_kind] = instance

    @classmethod
    def _build_boundary_node(cls, ncfg: Any, guard_kind: str, config: StackfileConfig) -> Any:
        from dam.boundary.constraint import BoundaryConstraint
        from dam.boundary.node import BoundaryNode

        stores_callback = guard_kind == "execution"
        params = dict(ncfg.params)

        if "device" not in params and config.policy and config.policy.device:
            params["device"] = config.policy.device

        extra = ncfg.model_extra or {}
        if "max_speed" in extra and "max_speed" not in params:
            params["max_speed"] = extra["max_speed"]

        constraint = BoundaryConstraint(
            params=params,
            callback=ncfg.callback if stores_callback else None,
        )
        return BoundaryNode(
            node_id=ncfg.node_id,
            constraint=constraint,
            fallback=ncfg.fallback,
            timeout_sec=ncfg.timeout_sec,
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
