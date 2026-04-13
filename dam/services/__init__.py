"""DAM Services — REST + WebSocket API layer.

Provides:
    TelemetryService    — WebSocket broadcaster for real-time CycleResult streaming
    RiskLogService      — Historical risk event store with query/export
    BoundaryConfigService — CRUD for BoundaryContainer management
    RuntimeControlService — start/pause/resume/stop/E-Stop for GuardRuntime
    create_app()        — FastAPI app combining all services
"""

from dam.services.boundary_config import BoundaryConfigService
from dam.services.risk_log import RiskEvent, RiskLogService
from dam.services.runtime_control import RuntimeControlService
from dam.services.telemetry import TelemetryService

__all__ = [
    "TelemetryService",
    "RiskLogService",
    "RiskEvent",
    "BoundaryConfigService",
    "RuntimeControlService",
]
