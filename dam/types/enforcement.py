from __future__ import annotations

from enum import StrEnum


class EnforcementMode(StrEnum):
    """Controls whether guard decisions block action dispatch.

    ENFORCE:  full validation; rejected/clamped actions are blocked (production default)
    MONITOR:  validation runs and is logged but does NOT block action dispatch
    LOG_ONLY: guard pipeline is skipped; only logs that a cycle occurred
    """

    ENFORCE = "enforce"
    MONITOR = "monitor"
    LOG_ONLY = "log_only"
