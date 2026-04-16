"""Immutable snapshot of one complete control cycle, handed off to LoopbackWriter.

Design note — no numpy in this dataclass
----------------------------------------
All array fields are stored as ``list[float]`` (already converted by
``_submit_loopback`` in the control-loop thread). This means:

- The writer thread never calls ``.tolist()`` and never competes with the
  control loop for the GIL on numpy operations.
- ``json.dumps`` on a ``list[float]`` is significantly faster than on an
  ``np.ndarray`` that still needs conversion.
- The dataclass itself has no numpy import, so it can be pickled / sent
  across process boundaries without numpy being present.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dam.types.result import GuardResult


@dataclass(frozen=True)
class CycleRecord:
    """Produced in the control-loop thread, consumed in the writer thread.

    Violation encoding
    ------------------
    has_violation        : True when any guard returned REJECT or FAULT.
    has_clamp            : True when any guard returned CLAMP (action was modified).
    violated_layer_mask  : Bitmask — bit i set when layer Li had REJECT/FAULT.
    clamped_layer_mask   : Bitmask — bit i set when layer Li had a CLAMP.

    Multiple boundaries firing at once each produce a separate message on
    ``/dam/L{i}`` with ``is_violation`` or ``is_clamp`` set, all sharing
    the same ``cycle_id`` for easy joining in analysis.

    Latency fields
    --------------
    latency_stages : source / policy / guards / sink / total  (ms)
    latency_layers : L0 … L4 accumulated guard time per layer (ms)
    latency_guards : per-guard individual time, keyed by guard_name (ms)
    """

    # ── Identity ──────────────────────────────────────────────────────────────
    cycle_id: int
    trace_id: str
    triggered_at: float  # time.monotonic() from the control-loop thread

    # ── Context ───────────────────────────────────────────────────────────────
    active_task: str | None
    active_boundaries: tuple[str, ...]
    active_cameras: tuple[str, ...]

    # ── Observation (pre-converted lists, no numpy) ───────────────────────────
    obs_timestamp: float
    obs_joint_positions: list[float]
    obs_joint_velocities: list[float] | None
    obs_end_effector_pose: list[float] | None
    obs_force_torque: list[float] | None
    obs_metadata: dict[str, Any]

    # ── Action (pre-converted lists, no numpy) ────────────────────────────────
    action_positions: list[float]
    action_velocities: list[float] | None
    validated_positions: list[float] | None  # None when action was rejected
    validated_velocities: list[float] | None
    was_clamped: bool
    fallback_triggered: str | None

    # ── Guard results (flat; writer groups by layer) ───────────────────────────
    guard_results: tuple[GuardResult, ...]

    # ── Latencies (ms) ────────────────────────────────────────────────────────
    latency_stages: dict[str, float]  # source / policy / guards / sink / total
    latency_layers: dict[str, float]  # L0 … L4
    latency_guards: dict[str, float]  # per guard_name

    # ── Violation / clamp summary ─────────────────────────────────────────────
    has_violation: bool  # any REJECT or FAULT this cycle
    has_clamp: bool  # any CLAMP this cycle
    violated_layer_mask: int  # bitmask: bit i → layer Li had REJECT/FAULT
    clamped_layer_mask: int  # bitmask: bit i → layer Li had CLAMP
