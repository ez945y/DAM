"""FastAPI router modules — one per API domain."""

from dam.services.routers.boundaries import create_boundaries_router
from dam.services.routers.control import create_control_router
from dam.services.routers.mcap import create_mcap_router
from dam.services.routers.ood import create_ood_router
from dam.services.routers.risk_log import create_risk_log_router
from dam.services.routers.system import create_system_router
from dam.services.routers.telemetry import create_telemetry_router

__all__ = [
    "create_telemetry_router",
    "create_risk_log_router",
    "create_boundaries_router",
    "create_control_router",
    "create_system_router",
    "create_ood_router",
    "create_mcap_router",
]
