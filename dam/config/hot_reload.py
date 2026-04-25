"""Hot-reload support for Stackfile configuration.

``StackfileWatcher`` polls a Stackfile path for modification-time changes
and invokes a callback when a change is detected.  The GuardRuntime uses
double-buffering: the new config is stored in ``_pending_config`` and swapped
in at the *start* of the next ``step()`` call so that a reload never happens
mid-cycle.

Thread model
------------
- A daemon thread polls ``os.path.getmtime`` every ``poll_interval_s`` seconds.
- On change: calls ``on_change(new_config: SafetyConfig)`` in the watcher thread.
- The runtime's ``apply_pending_reload`` callback stores the new config under a
  lock; ``step()`` checks and applies the swap atomically before each cycle.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)


class StackfileWatcher:
    """Polls a Stackfile path for modification time changes.

    Thread: starts a daemon thread that polls every ``poll_interval_s`` seconds.
    On change: calls ``on_change(new_config)`` callback in the watcher thread.
    The runtime uses this to double-buffer config: pending swap applied at
    the start of the next cycle (never mid-cycle).

    Parameters
    ----------
    path            : Absolute or relative path to the Stackfile YAML.
    on_change       : Callable receiving the new ``SafetyConfig`` (or full
                      ``StackfileConfig``) when a change is detected.
    poll_interval_s : How often to check mtime (default 0.5 s).
    """

    def __init__(
        self,
        path: str,
        on_change: Callable[..., None],
        poll_interval_s: float = 0.5,
    ) -> None:
        self._path = path
        self._on_change = on_change
        self._poll_interval_s = poll_interval_s
        self._last_mtime: float | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    # ── Public API ─────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background polling thread."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("StackfileWatcher: already running")
            return

        self._stop_event.clear()

        # Snapshot current mtime so we only fire on *changes*
        try:
            self._last_mtime = os.path.getmtime(self._path)
        except OSError:
            self._last_mtime = None

        self._thread = threading.Thread(
            target=self._poll_loop,
            name=f"StackfileWatcher({os.path.basename(self._path)})",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "StackfileWatcher started: watching '%s' every %.2fs",
            self._path,
            self._poll_interval_s,
        )

    def stop(self) -> None:
        """Signal the background thread to exit and wait for it to finish."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._poll_interval_s * 3)
            if self._thread.is_alive():
                logger.warning("StackfileWatcher: thread did not exit cleanly")
            self._thread = None
        logger.info("StackfileWatcher stopped")

    def is_running(self) -> bool:
        """Return True if the watcher thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    # ── Internal ───────────────────────────────────────────────────────────

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            self._check_once()
            self._stop_event.wait(timeout=self._poll_interval_s)

    def _check_once(self) -> None:
        try:
            mtime = os.path.getmtime(self._path)
        except OSError:
            return  # file temporarily missing — skip

        if self._last_mtime is None or mtime != self._last_mtime:
            self._last_mtime = mtime
            try:
                from dam.config.loader import StackfileLoader

                new_config = StackfileLoader.load(self._path)
                logger.info(
                    "StackfileWatcher: change detected in '%s', invoking callback",
                    self._path,
                )
                self._on_change(new_config)
            except Exception as exc:
                logger.error("StackfileWatcher: failed to reload '%s': %s", self._path, exc)
