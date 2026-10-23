"""Public session management snapshots."""

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ReloadReceipt:
    id: str
    status: str
    message: str
    config_version: int | None = None

    @property
    def accepted(self) -> bool:
        """Currently queued or successfully applied; never implies applied."""
        return self.status in ("pending", "applied")

    def to_dict(self) -> dict[str, Any]:
        return {"accepted": self.accepted, **asdict(self)}


@dataclass(frozen=True)
class SessionStatus:
    state: str
    task: str
    config_version: int
    state_generation: int
    cycle_id: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
