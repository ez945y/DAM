from __future__ import annotations

from typing import Any

RUNTIME_POOL_KEYS: frozenset[str] = frozenset(
    {
        "obs",
        "action",
        "cycle_id",
        "trace_id",
        "timestamp",
        # Phase 2 additions
        "active_containers",  # List[BoundaryContainer] — active this cycle
        "node_start_times",  # Dict[str, float] — {container_name: time node activated}
        # Phase 3 additions
        "hardware_status",  # Dict[str, Any] | None — from ActionAdapter.get_hardware_status()
    }
)

RuntimePool = dict[str, Any]
ConfigPool = dict[str, Any]
