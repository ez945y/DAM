"""Stage DAG — groups of guards that can run in parallel within a control cycle.

A Stage is a named group of Guard instances.  When ``parallel=True`` a
``ThreadPoolExecutor`` is used so all guards in the stage run concurrently.
Stages are run sequentially (stage 0 → stage 1 → …) by
``GuardRuntime._run_staged()``.

Design notes
------------
- Thread safety: each guard's ``check()`` is assumed to be thread-safe for its
  own instance state (standard pattern for stateless or lock-protected guards).
- Timeout: if ``stage.timeout_ms`` is exceeded the entire stage is treated as a
  FAULT so the runtime can escalate to the fallback strategy.
- Early exit: if any guard in a sequential stage returns REJECT or FAULT the
  runtime still collects all results before aggregating; parallel stages do the
  same via ``as_completed``.
- Boundary groups: when multiple boundaries share the same guard instance
  (e.g. joint_position_limits + joint_velocity_limit + bounds → MotionGuard),
  they form a **group**.  The guard runs once with the merged config pool;
  results are fanned out to every boundary name in the group for UI reporting.
  No boundary is privileged — they are all peers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dam.guard.base import Guard


@dataclass
class Stage:
    """A group of guards that run together within a single control cycle.

    Attributes
    ----------
    name               : Human-readable identifier (used for logging and debugging).
    guards             : Ordered list of Guard instances (used when guard_boundary_pairs
                         is empty — the legacy / direct-construction path).
    guard_boundary_pairs: Ordered list of (Guard, boundary_names) pairs.  Each pair
                         is a **group**: one guard instance with the list of boundary
                         names it covers.  The guard runs once; results are fanned out
                         to every name in the group for the dashboard.
    parallel  : If True, all entries run concurrently via ThreadPoolExecutor.
                If False (default), entries run sequentially in list order.
    timeout_ms: Per-stage wall-clock timeout in milliseconds.  If the stage
                (or any single guard) exceeds this, the remaining guards are
                cancelled and a FAULT result is returned for each timed-out guard.
    """

    name: str
    guards: list[Guard] = field(default_factory=list)
    guard_boundary_pairs: list[tuple[Guard, list[str]]] = field(default_factory=list)
    parallel: bool = False
    timeout_ms: float = 10.0
