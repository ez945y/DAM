"""DAM Services — REST + WebSocket API layer.

Provides:
    TelemetryService    — WebSocket broadcaster for real-time CycleResult streaming
    RiskLogService      — Historical risk event store with query/export
    BoundaryConfigService — CRUD for BoundaryContainer management
    RuntimeControlService — start/pause/resume/stop/E-Stop for GuardRuntime
    create_app()        — FastAPI app combining all services
"""

from dam.services.runtime_control import RuntimeControlService
from dam.services.telemetry import TelemetryService

__all__ = [
    "TelemetryService",
    "RuntimeControlService",
]

# Optional services — only available when their extra dependencies are installed.
try:
    from dam.services.risk_log import RiskEvent, RiskLogService  # requires msgspec

    __all__ += ["RiskLogService", "RiskEvent"]
except ImportError:
    pass

try:
    from dam.services.boundary_config import BoundaryConfigService

    __all__ += ["BoundaryConfigService"]
except ImportError:
    pass
