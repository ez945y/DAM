"""ServiceContainer — single unit for all DAM services.

Passing one ``ServiceContainer`` instead of six positional arguments makes
``create_app`` and the ``Bootstrapper`` signatures stable: adding a new
service only requires adding a field here, not changing every call site.
"""

from __future__ import annotations

import dataclasses
from typing import Any


@dataclasses.dataclass
class ServiceContainer:
    """Holds all optional service instances wired into the DAM API.

    Any field may be ``None``; the corresponding API routes will return
    ``503 Service Unavailable`` rather than crashing on startup.
    """

    telemetry: Any | None = None  # TelemetryService
    risk_log: Any | None = None  # RiskLogService
    boundary: Any | None = None  # BoundaryConfigService
    control: Any | None = None  # RuntimeControlService
    ood_trainer: Any | None = None  # OODTrainerService
    mcap_sessions: Any | None = None  # McapSessionService
