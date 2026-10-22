"""Hot-reload manager — thread-safe config swap at cycle boundaries.

Extracted from ``guard_runtime.py`` to isolate the double-buffer + lock
pattern from the runtime orchestrator.  Owns the pending config queue,
config pool, and version counter.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
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
        self._lock = threading.RLock()
        self._config_pool = dict(config_pool)
        self._config_version: int = 0
        self._pending_id: str | None = None
        self._inflight_id: str | None = None
        self._receipts: dict[str, dict[str, Any]] = {}
        self._task_epoch = 0

    # ── Properties ─────────────────────────────────────────────────────────

    @property
    def config_pool(self) -> dict[str, Any]:
        return self._config_pool

    @property
    def config_version(self) -> int:
        return self._config_version

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    @property
    def task_epoch(self) -> int:
        with self._lock:
            return self._task_epoch

    # ── Queue / consume ────────────────────────────────────────────────────

    def queue_reload(
        self,
        new_config: StackfileConfig,
        *,
        expected_version: int | None = None,
        expected_task: str | None = None,
        expected_epoch: int | None = None,
    ) -> str:
        """Store a new config for application at the next cycle boundary."""
        with self._lock:
            if self._pending_config is not None or self._inflight_id is not None:
                raise ValueError("A configuration reload is already pending")
            request_id = uuid.uuid4().hex
            self._pending_config = new_config.model_copy(deep=True)
            self._pending_id = request_id
            self._receipts[request_id] = {
                "id": request_id,
                "status": "pending",
                "message": "Waiting for a control-cycle boundary",
                "expected_version": expected_version,
                "expected_task": expected_task,
                "expected_epoch": self._task_epoch if expected_epoch is None else expected_epoch,
            }
            # Only retain bounded management metadata; no file IO on the loop.
            while len(self._receipts) > 100:
                del self._receipts[next(iter(self._receipts))]
            return request_id

    def consume_pending(self) -> StackfileConfig | None:
        """Atomically take the pending config (if any)."""
        with self._lock:
            pending = self._pending_config
            if pending is not None:
                self._pending_config = None
                self._inflight_id = self._pending_id
                self._pending_id = None
        return pending

    def check_expected_state(self, active_task: str | None) -> None:
        with self._lock:
            receipt = self._receipts.get(self._inflight_id or "", {})
            expected_version = receipt.get("expected_version")
            expected_task = receipt.get("expected_task")
            if receipt.get("status") == "rejected":
                raise ValueError(receipt["message"])
            expected_epoch = receipt.get("expected_epoch")
            if expected_epoch is not None and expected_epoch != self._task_epoch:
                raise ValueError("Task lifecycle changed after the switch preview")
            if expected_version is not None and expected_version != self._config_version:
                raise ValueError("Configuration changed after the switch preview")
            if expected_task is not None and expected_task != active_task:
                raise ValueError("Active task changed after the switch preview")

    def finish_reload(self, success: bool, message: str) -> None:
        with self._lock:
            receipt = self._receipts.get(self._inflight_id or "")
            if receipt is not None:
                receipt.update(
                    status="applied" if success else "rejected",
                    message=message,
                    config_version=self._config_version,
                )
            self._inflight_id = None

    def reload_receipt(self, request_id: str) -> dict[str, Any] | None:
        with self._lock:
            receipt = self._receipts.get(request_id)
            return dict(receipt) if receipt is not None else None

    def has_pending(self) -> bool:
        with self._lock:
            return self._pending_config is not None or self._inflight_id is not None

    def cancel_pending(self, request_id: str, message: str) -> bool:
        """Cancel only requests that have not entered the control cycle."""
        with self._lock:
            if self._pending_id != request_id:
                return False
            self._pending_id = None
            self._pending_config = None
            self._receipts[request_id].update(status="rejected", message=message)
            return True

    def cancel_task_switch(self) -> None:
        """Task-bound previews cannot survive a stop/start or task change."""
        with self._lock:
            self._task_epoch += 1
            receipt = self._receipts.get(self._pending_id or "")
            if receipt is not None and receipt.get("expected_task") is not None:
                receipt.update(
                    status="rejected", message="Task lifecycle changed before application"
                )
                self._pending_id = None
                self._pending_config = None

    # ── Config pool building (static) ──────────────────────────────────────

    @staticmethod
    def build_config_pool(new_config: StackfileConfig) -> dict[str, Any]:
        """Pure function: merge boundary node params into a fresh config pool."""
        from dam.boundary.callbacks._registry import normalize_unit_params
        from dam.runtime.merge_policy import resolve as resolve_merge

        pool: dict[str, Any] = {}

        if new_config.safety and new_config.safety.control_hz > 0:
            pool["dt"] = 1.0 / new_config.safety.control_hz

        # Joint names: ordered observation/joint identifiers from the preset
        # (which owns the physical description). Used to align the observation
        # vector with the URDF dynamics model by name during FK.
        preset = _resolve_preset(new_config)
        if new_config.hardware and new_config.hardware.joint_names:
            pool["joint_names"] = list(new_config.hardware.joint_names)
        elif preset and preset.joint_names:
            pool["joint_names"] = list(preset.joint_names)
        if new_config.hardware and new_config.hardware.action_layout:
            pool["action_layout"] = [dict(item) for item in new_config.hardware.action_layout]
        elif preset and preset.action_layout:
            pool["action_layout"] = [dict(item) for item in preset.action_layout]

        _STRUCTURAL = {
            "joint_names",
            "action_layout",
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
