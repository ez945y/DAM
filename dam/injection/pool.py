from __future__ import annotations

from typing import Any

RUNTIME_POOL_KEYS: frozenset[str] = frozenset(
    {
        "obs",
        "action",
        "cycle_id",
        "trace_id",
        "timestamp",
        "now",
        # Phase 2 additions
        "active_containers",  # List[BoundaryContainer] — active this cycle
        "node_start_times",  # Dict[str, float] — {container_name: time node activated}
        # Phase 4: shared kinematics — DynamicsContext refreshed by the
        # sensor adapter once per cycle.  Guards (workspace CBF, manipulability,
        # …) consume cached FK + Jacobian without recomputing.
        "dynamics",
        # The full runtime pool dict — guards that run their own callbacks
        # (ExecutionGuard) forward it so L2 callbacks can read pre-computed
        # FK data (ee_pos, J_linear, etc.) from the same pool.
        "runtime_pool",
    }
)

RuntimePool = dict[str, Any]
ConfigPool = dict[str, Any]
