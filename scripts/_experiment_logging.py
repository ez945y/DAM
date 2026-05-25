"""Logging setup shared by experiment scripts when invoked directly."""

from __future__ import annotations

import logging


def configure_cli_logging() -> None:
    """Install the console log format unless the host already configured logging."""
    if logging.getLogger().handlers:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)-7s] [%(name)-30s] %(message)s",
        datefmt="%H:%M:%S",
    )
