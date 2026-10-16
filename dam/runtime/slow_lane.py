"""Slow-lane evaluator — async worker for expensive guards (L0 OOD, L2 task).

Design (single-writer principle):

- The control loop (fast lane) remains the ONLY writer to the action sink and
  the ONLY owner of the hardware bus.  It publishes an immutable snapshot of
  ``(obs, post-fast-lane action)`` after every cycle.
- This worker evaluates the slow-lane guard sub-pipeline (stage order is
  preserved: L0 before L2) against the **latest** snapshot at its own cadence
  and publishes a :class:`SlowVerdict`.
- Both hand-offs are latest-wins mailboxes: a single reference assignment
  (atomic under the GIL), no queues, no locks held across evaluation.  The
  worker can never fall behind unboundedly. The caller may explicitly wait
  for a verdict with a bounded deadline (by default on the first cycle).
- Staleness is the synchronisation contract: the verdict carries the wall
  (monotonic) time it was produced and the snapshot it was based on; the
  control loop degrades per ``slow_lane.stale_action`` when the snapshot
  underlying the verdict is older than ``max_staleness_ms``, including queue
  and evaluation latency.

``Observation`` is a frozen dataclass that copies its arrays on construction,
so sharing a snapshot across threads needs no further synchronisation.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dam.types.action import ActionProposal, ValidatedAction
    from dam.types.observation import Observation
    from dam.types.result import GuardResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SlowSnapshot:
    """What the fast lane publishes after each cycle.

    ``action`` is the post-fast-lane (post-L1-clamp) action — slow-lane L2
    guards therefore see the candidate command before slow gating, preserving the
    "L2 sees phase-0 clamps" ordering contract at a lower cadence.
    """

    obs: Observation
    action: ActionProposal | ValidatedAction
    trace_id: str
    cycle_id: int
    published_at: float  # time.monotonic()


@dataclass(frozen=True)
class SlowVerdict:
    """What the worker publishes after each evaluation."""

    results: list[GuardResult] = field(default_factory=list)
    basis_cycle_id: int = -1
    basis_obs_timestamp: float = 0.0
    produced_at: float = 0.0  # time.monotonic()
    evaluation_ms: float = 0.0
    basis_published_at: float | None = None

    def age_s(self, now: float) -> float:
        """Include queue and evaluation latency in the age of the evidence."""
        basis = self.basis_published_at
        return now - (self.produced_at if basis is None else min(basis, self.produced_at))


class SlowLaneWorker:
    """Runs ``evaluate_fn`` over the latest snapshot at a fixed cadence.

    ``evaluate_fn(snapshot) -> list[GuardResult]`` is supplied by the runtime
    (a closure over the engine + slow-lane stages); the worker owns only
    scheduling and the two mailboxes.
    """

    def __init__(
        self,
        evaluate_fn: Callable[[SlowSnapshot], list[Any]],
        frequency_hz: float = 10.0,
        name: str = "dam-slow-lane",
    ) -> None:
        self._evaluate_fn = evaluate_fn
        frequency = float(frequency_hz)
        if not math.isfinite(frequency) or frequency <= 0:
            raise ValueError("frequency_hz must be finite and positive")
        self._period_s = 1.0 / frequency
        self._name = name
        # Latest-wins mailboxes. Plain attribute reassignment is atomic under
        # the GIL; readers take a local reference before dereferencing.
        self._snapshot: SlowSnapshot | None = None
        self._verdict: SlowVerdict | None = None
        self._last_evaluated_cycle: int = -1
        self._wakeup = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started_at: float | None = None
        self._verdict_changed = threading.Condition()

    # ── Fast-lane API (called from the control loop thread) ───────────────

    def submit(self, snapshot: SlowSnapshot) -> None:
        """Publish the latest snapshot (latest-wins, never blocks)."""
        self._snapshot = snapshot
        self._wakeup.set()

    def latest_verdict(self) -> SlowVerdict | None:
        """The most recent verdict, or None before the first evaluation."""
        return self._verdict

    def wait_for_verdict(self, cycle_id: int, timeout: float) -> SlowVerdict | None:
        """Wait at most ``timeout`` seconds for this exact snapshot's verdict.

        The runtime is single-caller; an older verdict never satisfies a wait
        for the current command. Evaluation holds no condition lock.
        """
        with self._verdict_changed:
            self._verdict_changed.wait_for(
                lambda: (
                    self._stop.is_set()
                    or (self._verdict is not None and self._verdict.basis_cycle_id == cycle_id)
                ),
                timeout=timeout,
            )
            verdict = self._verdict
            if self._stop.is_set() or verdict is None or verdict.basis_cycle_id != cycle_id:
                return None
            return verdict

    def verdict_age_s(self, now: float) -> float | None:
        """Age of the verdict's basis snapshot; falls back to time since start
        so a worker that never produced a verdict still goes stale."""
        verdict = self._verdict
        if verdict is not None:
            return verdict.age_s(now)
        if self._started_at is not None:
            return now - self._started_at
        return None

    # ── Lifecycle ──────────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            if self._stop.is_set():
                raise RuntimeError("Slow-lane worker is still stopping")
            return
        if self._thread is not None:
            # A timed-out stop may finish after its caller returns. Do not
            # replay that old task's mailbox when the worker is restarted.
            self._snapshot = None
        self._verdict = None
        self._last_evaluated_cycle = -1
        self._stop.clear()
        self._started_at = time.monotonic()
        self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        self._wakeup.set()
        with self._verdict_changed:
            self._verdict_changed.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                raise TimeoutError("Slow-lane worker did not stop before timeout")
            self._thread = None
        self._snapshot = None
        self._verdict = None
        self._started_at = None

    # ── Worker loop ────────────────────────────────────────────────────────

    def _run(self) -> None:
        while not self._stop.is_set():
            # Wait for a snapshot (or shutdown); then pace at period_s.
            self._wakeup.wait(timeout=self._period_s)
            self._wakeup.clear()
            if self._stop.is_set():
                return

            snapshot = self._snapshot  # local ref — stable for this iteration
            if snapshot is None or snapshot.cycle_id == self._last_evaluated_cycle:
                continue  # nothing new — don't re-score identical state

            t0 = time.perf_counter()
            try:
                from dam.types.result import GuardDecision, GuardResult

                results = list(self._evaluate_fn(snapshot))
                if any(
                    not isinstance(r, GuardResult) or not isinstance(r.decision, GuardDecision)
                    for r in results
                ):
                    raise TypeError("Slow-lane evaluator returned an invalid guard result")
            except Exception as exc:  # noqa: BLE001 — failures must gate the next cycle
                from dam.guard.layer import GuardLayer
                from dam.types.result import GuardResult

                logger.exception("slow lane: evaluation failed; publishing FAULT")
                results = [GuardResult.fault(exc, "guard_code", "slow_lane_worker", GuardLayer.L0)]
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            if self._stop.is_set():
                return
            self._last_evaluated_cycle = snapshot.cycle_id
            with self._verdict_changed:
                self._verdict = SlowVerdict(
                    results=list(results),
                    basis_cycle_id=snapshot.cycle_id,
                    basis_obs_timestamp=snapshot.obs.timestamp,
                    produced_at=time.monotonic(),
                    evaluation_ms=elapsed_ms,
                    basis_published_at=snapshot.published_at,
                )
                self._verdict_changed.notify_all()
            if elapsed_ms > self._period_s * 1000.0:
                logger.warning(
                    "slow lane: evaluation took %.1f ms > period %.1f ms; "
                    "verdicts will lag (consider lowering slow_lane.task_hz)",
                    elapsed_ms,
                    self._period_s * 1000.0,
                )
            # Sleep out the remainder of the period so a burst of snapshots
            # doesn't turn into a busy loop.
            remaining = self._period_s - (time.perf_counter() - t0)
            if remaining > 0 and not self._stop.is_set():
                self._stop.wait(timeout=remaining)
