"""Hot-reload manager — thread-safe config swap at cycle boundaries.

Extracted from ``guard_runtime.py`` to isolate the double-buffer + lock
pattern from the runtime orchestrator.  Owns the pending config queue,
config pool, and version counter.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import numpy as np

from dam.injection.static import precompute_injection

if TYPE_CHECKING:
    from dam.config.schema import StackfileConfig
    from dam.guard.base import Guard
    from dam.preset.registry import RobotPreset

logger = logging.getLogger(__name__)


def _resolve_preset(config: StackfileConfig) -> RobotPreset | None:
    """Look up the preset from the stackfile's hardware section, if available."""
    preset_name = getattr(config.hardware, "preset", None) if config.hardware else None
    if not preset_name:
        return None
    try:
        from dam.preset.registry import get_preset

        return get_preset(preset_name)
    except Exception:  # noqa: BLE001
        return None


class HotReloadManager:
    """Thread-safe hot-reload double-buffer for stackfile config."""

    def __init__(self, config_pool: dict[str, Any]) -> None:
        self._pending_config: StackfileConfig | None = None
        self._lock = threading.Lock()
        self._config_pool = dict(config_pool)
        self._config_version: int = 0

    # ── Properties ─────────────────────────────────────────────────────────

    @property
    def config_pool(self) -> dict[str, Any]:
        return self._config_pool

    @property
    def config_version(self) -> int:
        return self._config_version

    @property
    def lock(self) -> threading.Lock:
        return self._lock

    # ── Queue / consume ────────────────────────────────────────────────────

    def queue_reload(self, new_config: StackfileConfig) -> None:
        """Store a new config for application at the next cycle boundary."""
        with self._lock:
            self._pending_config = new_config

    def consume_pending(self) -> StackfileConfig | None:
        """Atomically take the pending config (if any)."""
        with self._lock:
            pending = self._pending_config
            if pending is not None:
                self._pending_config = None
        return pending

    # ── Config pool building (static) ──────────────────────────────────────

    @staticmethod
    def build_config_pool(new_config: StackfileConfig) -> dict[str, Any]:
        """Pure function: merge boundary node params into a fresh config pool."""
        from dam.boundary.callbacks._registry import normalize_unit_params
        from dam.runtime.merge_policy import resolve as resolve_merge

        pool: dict[str, Any] = {}

        if new_config.safety and new_config.safety.control_hz > 0:
            pool["dt"] = 1.0 / new_config.safety.control_hz

        # Joint layout: resolved from preset (which owns the physical description).
        preset = _resolve_preset(new_config)
        if preset and preset.joint_names:
            pool["joint_layout"] = preset.joint_layout
        if new_config.hardware and new_config.hardware.action_layout:
            pool["action_layout"] = [dict(item) for item in new_config.hardware.action_layout]
        elif preset and preset.action_layout:
            pool["action_layout"] = [dict(item) for item in preset.action_layout]

        _STRUCTURAL = {
            "layer",
            "type",
            "callback",
            "fallback",
            "timeout_sec",
            "node_id",
            "nodes",
            "loop",
            "params",
        }

        for bname, bcfg in new_config.boundaries.items():
            if bcfg.type == "list":
                continue
            for ncfg in bcfg.nodes:
                cb_name = getattr(ncfg, "callback", None) or ""
                normalised = normalize_unit_params(cb_name, ncfg.params)
                for pk, pv in normalised.items():
                    if pk in _STRUCTURAL:
                        continue
                    if pk not in pool:
                        pool[pk] = pv
                        continue
                    merge_fn = resolve_merge(pk)
                    merged = merge_fn(pool[pk], pv)
                    if merge_fn.__name__ == "overwrite" and not np.array_equal(pool[pk], pv):
                        logger.warning(
                            "GuardRuntime: Parameter '%s' overwritten by boundary "
                            "'%s' (prev: %s, new: %s).  Register a merge strategy "
                            "in dam.runtime.merge_policy if this isn't desired.",
                            pk,
                            bname,
                            pool[pk],
                            pv,
                        )
                    pool[pk] = merged
        return pool

    # ── Apply swap ─────────────────────────────────────────────────────────

    def apply_swap(
        self,
        new_config: StackfileConfig,
        candidate_pool: dict[str, Any],
        guards: list[Guard],
        apply_guards_config: Callable[[StackfileConfig, dict[str, Any]], None],
        node_start_times: dict[str, float],
    ) -> None:
        """Commit a pre-validated config pool.

        The caller is responsible for building candidate_pool (via
        ``build_config_pool``) and catching validation errors.
        """
        self._config_pool = candidate_pool
        apply_guards_config(new_config, candidate_pool)
        for g in guards:
            precompute_injection(g, candidate_pool)
        now = time.monotonic()
        for cname in node_start_times:
            node_start_times[cname] = now

        self._config_version += 1
        logger.info("GuardRuntime: config swap committed (v%d)", self._config_version)
