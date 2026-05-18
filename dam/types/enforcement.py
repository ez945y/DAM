from __future__ import annotations

from enum import StrEnum


class EnforcementMode(StrEnum):
    """Controls whether guard decisions block action dispatch.

    ENFORCE:  full validation; rejects/clamps unsafe actions and may trigger fallbacks
    MONITOR:  validation runs and is logged; actions pass through unchanged and no
              violation hooks or fallbacks are triggered
    LOG_ONLY: guard pipeline is skipped; only logs that a cycle occurred
    """

    ENFORCE = "enforce"
    MONITOR = "monitor"
    LOG_ONLY = "log_only"
