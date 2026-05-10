"""Event-driven Stackfile change watcher.

Uses ``watchfiles`` (Rust-backed notify/inotify/FSEvents/ReadDirectoryChanges)
for low-latency edge-triggered file events, and falls back to mtime polling
when ``watchfiles`` is not installed.  The runtime double-buffers reloads via
``GuardRuntime.apply_pending_reload`` — the swap is applied atomically at the
*start* of the next ``step()`` call so config never changes mid-cycle.

Editor-safety
-------------
Many editors (vim, IntelliJ) write to a temp file and then ``rename`` over
the target.  The ``watchfiles`` backend reports this as a delete + create
on the watched path, which still triggers our reload callback.  The polling
fallback handles it via the next mtime read.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)


class StackfileWatcher:
    """Watches a Stackfile path and fires a callback on every change.

    Parameters
    ----------
    path            : Path to the Stackfile YAML.
    on_change       : Called with the freshly loaded ``StackfileConfig`` on
                      every detected change.  Invoked from the watcher
                      thread; the callback must be thread-safe.
    poll_interval_s : Used only by the polling fallback; ignored by the
                      event-driven backend.  Default 0.5 s.
    """

    def __init__(
        self,
        path: str,
        on_change: Callable[..., None],
        poll_interval_s: float = 0.5,
    ) -> None:
        self._path = os.path.abspath(path)
        self._on_change = on_change
        self._poll_interval_s = poll_interval_s
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    # ── Public API ─────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            logger.warning("StackfileWatcher: already running")
            return

        self._stop_event.clear()
        target, backend = self._select_backend()
        self._thread = threading.Thread(
            target=target,
            name=f"StackfileWatcher({os.path.basename(self._path)})",
            daemon=True,
        )
        self._thread.start()
        logger.info("StackfileWatcher started (%s): watching '%s'", backend, self._path)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=max(self._poll_interval_s * 3, 2.0))
            if self._thread.is_alive():
                logger.warning("StackfileWatcher: thread did not exit cleanly")
            self._thread = None
        logger.info("StackfileWatcher stopped")

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── Internal ───────────────────────────────────────────────────────────

    def _select_backend(self) -> tuple[Callable[[], None], str]:
        """Return (loop_target, label).  Prefer event-driven, fall back to polling."""
        try:
            import watchfiles  # noqa: F401
        except ImportError:
            return self._poll_loop, "polling — watchfiles not installed"
        return self._event_loop, "event-driven via watchfiles"

    def _event_loop(self) -> None:
        from watchfiles import watch

        try:
            # `watch` yields a set of (Change, path) tuples whenever something
            # below the watched path changes.  Debounce so a rapid burst of
            # writes (editor rename-over-tmp) collapses into one reload.
            for _changes in watch(
                self._path,
                stop_event=self._stop_event,
                debounce=50,
                rust_timeout=200,
                raise_interrupt=False,
            ):
                if self._stop_event.is_set():
                    break
                self._reload()
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "StackfileWatcher: event loop crashed (%s); falling back to polling",
                exc,
                exc_info=True,
            )
            self._poll_loop()

    def _poll_loop(self) -> None:
        last_mtime: float | None
        try:
            last_mtime = os.path.getmtime(self._path)
        except OSError:
            last_mtime = None

        while not self._stop_event.is_set():
            try:
                mtime = os.path.getmtime(self._path)
                if last_mtime is None or mtime != last_mtime:
                    last_mtime = mtime
                    self._reload()
            except OSError:
                # File temporarily missing (editor rename) — try again next tick
                pass
            self._stop_event.wait(timeout=self._poll_interval_s)

    def _reload(self) -> None:
        try:
            from dam.config.loader import StackfileLoader

            new_config = StackfileLoader.load(self._path)
        except Exception:  # noqa: BLE001
            logger.error("StackfileWatcher: failed to reload '%s'", self._path, exc_info=True)
            return
        logger.info("StackfileWatcher: change detected in '%s'", self._path)
        self._on_change(new_config)
