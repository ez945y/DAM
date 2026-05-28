"""Cycle telemetry — loopback recording, frame hub bridging, latency logging.

Extracted from ``guard_runtime.py`` to keep the runtime orchestrator focused
on cycle execution.  This module owns the LoopbackWriter lifecycle and all
per-cycle recording / telemetry.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from dam.runtime.context import ContextEvent
from dam.runtime.failure_classify import classify_failure, select_failure_results
from dam.types.result import GuardDecision, GuardResult

if TYPE_CHECKING:
    from dam.types.action import ActionProposal, ValidatedAction
    from dam.types.observation import Observation

logger = logging.getLogger(__name__)


class CycleTelemetry:
    """Owns loopback recording, frame hub bridging, and latency logging."""

    def __init__(
        self,
        loopback: Any | None,
        frame_hub: Any | None,
        control_frequency_hz: float,
    ) -> None:
        self._loopback = loopback
        self._frame_hub = frame_hub
        self._control_frequency_hz = control_frequency_hz
        self._live_img_no_data_warned = False
        self._source_latency_log_t = 0.0

    # ── Recording lifecycle ────────────────────────────────────────────────

    def start_recording(self) -> None:
        if self._loopback is not None:
            self._loopback.start()

    def stop_recording(self) -> None:
        if self._loopback is not None:
            self._loopback.shutdown()

    @property
    def loopback(self) -> Any | None:
        return self._loopback

    # ── Frame hub bridge ───────────────────────────────────────────────────

    def publish_frames_to_hub(self, images: dict[str, Any], timestamp: float) -> None:
        """Bridge source-embedded camera frames into the shared frame hub."""
        if self._frame_hub is None or not hasattr(self._frame_hub, "put_jpeg"):
            return
        from dam.logging.loopback_writer import _compress_image

        for cam, arr in images.items():
            try:
                jpeg, w, h, _fmt = _compress_image(arr)
                if jpeg:
                    self._frame_hub.put_jpeg(str(cam), timestamp, jpeg, w, h)
            except Exception:  # noqa: BLE001
                continue

    def get_latest_images(self) -> dict[str, bytes]:
        """Return latest camera JPEGs for live preview."""
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

    # ── Loopback submission ────────────────────────────────────────────────

    def submit_loopback(
        self,
        *,
        obs: Observation,
        action: ActionProposal | None,
        validated: ValidatedAction | None,
        guard_results: list[GuardResult],
        fallback_triggered: str | None,
        trace_id: str,
        latency_stages: dict[str, float],
        cycle_id: int,
        active_task: str | None,
        active_container_names: list[str],
        config_version: int,
        active_cameras: tuple[str, ...] = (),
        active_context: str = "normal",
        context_severity: int = 0,
        context_event: ContextEvent | None = None,
    ) -> None:
        """Build a CycleRecord and enqueue it on the LoopbackWriter."""
        from dam.logging.cycle_record import CycleRecord

        latency_guards: dict[str, float] = {
            r.guard_name: r.metadata.get("_latency_ms", 0.0) for r in guard_results
        }

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
            cycle_id=cycle_id,
            active_task=active_task,
            active_container_names=active_container_names,
            has_violation=has_violation,
            has_clamp=has_clamp,
            violated_layer_mask=violated_layer_mask,
            clamped_layer_mask=clamped_layer_mask,
            obs_channels=obs_channels,
        )

        rec = CycleRecord(
            cycle_id=cycle_id,
            trace_id=trace_id,
            triggered_at=time.monotonic(),
            active_task=active_task,
            active_boundaries=tuple(active_container_names),
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
            config_version=config_version,
            active_context=active_context,
            context_severity=context_severity,
            context_event=context_event,
        )
        self._loopback.submit(rec)  # type: ignore[union-attr]

    @staticmethod
    def _build_failure_harvest(
        *,
        obs: Observation,
        action: ActionProposal | None,
        validated: ValidatedAction | None,
        guard_results: list[GuardResult],
        fallback_triggered: str | None,
        trace_id: str,
        cycle_id: int,
        active_task: str | None,
        active_container_names: list[str],
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
                "cycle_id": cycle_id,
                "trace_id": trace_id,
                "timestamp": obs.timestamp,
                "failure_type": failure_type,
                "active_task": active_task,
                "active_boundaries": list(active_container_names),
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

    # ── Source latency logging ─────────────────────────────────────────────

    def log_source_latency_if_slow(
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
