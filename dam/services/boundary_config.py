"""Boundary Config Service — CRUD for BoundaryContainer management."""

from __future__ import annotations

import builtins
import threading
from typing import Any


class BoundaryConfigService:
    """In-memory CRUD service for boundary container configuration.

    The service stores raw configuration dicts (JSON-serialisable).
    The GuardRuntime holds actual BoundaryContainer instances; this service
    acts as the config store that the UI / REST API reads/writes.

    Config dict schema (mirrors Stackfile ``boundaries`` section):

        {
            "name": "workspace",
            "type": "single" | "list" | "graph",
            "loop": false,
            "nodes": [
                {
                    "node_id": "default",
                    "fallback": "emergency_stop",
                    "timeout_sec": null,
                    "constraint": {
                        "max_speed": 1.0,
                        "upper_limits": [...],
                        "lower_limits": [...],
                        ...
                    }
                }
            ]
        }
    """

    def __init__(self) -> None:
        self._configs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def list(self) -> builtins.list[dict[str, Any]]:
        """Return all boundary configs as a list."""
        with self._lock:
            return list(self._configs.values())

    def get(self, name: str) -> dict[str, Any] | None:
        """Return a single config by name, or None if not found."""
        with self._lock:
            return self._configs.get(name)

    def create(self, config: dict[str, Any]) -> dict[str, Any]:
        """Create a new boundary config.

        Raises:
            ValueError: if a config with this name already exists.
        """
        name = config.get("name", "")
        if not name:
            raise ValueError("Boundary config must have a non-empty 'name' field")
        with self._lock:
            if name in self._configs:
                raise ValueError(f"Boundary '{name}' already exists. Use update() to modify.")
            self._configs[name] = dict(config)
        return self._configs[name]

    def update(self, name: str, config: dict[str, Any]) -> dict[str, Any]:
        """Replace an existing boundary config.

        Raises:
            KeyError: if no config with this name exists.
        """
        with self._lock:
            if name not in self._configs:
                raise KeyError(f"Boundary '{name}' not found")
            merged = dict(config)
            merged["name"] = name  # ensure name stays consistent
            self._configs[name] = merged
        return self._configs[name]

    def delete(self, name: str) -> bool:
        """Delete a boundary config.  Returns True if deleted, False if not found."""
        with self._lock:
            if name in self._configs:
                del self._configs[name]
                return True
        return False

    def upsert(self, config: dict[str, Any]) -> dict[str, Any]:
        """Create or replace a boundary config."""
        name = config.get("name", "")
        if not name:
            raise ValueError("Boundary config must have a non-empty 'name' field")
        with self._lock:
            self._configs[name] = dict(config)
        return self._configs[name]

    # ── Bulk load ─────────────────────────────────────────────────────────────

    def load_from_stackfile(self, boundaries_dict: dict[str, Any]) -> int:
        """Populate from a parsed Stackfile ``boundaries`` section.

        Args:
            boundaries_dict: The ``boundaries`` key from a loaded Stackfile.

        Returns:
            Number of boundaries loaded.
        """
        count = 0
        for bname, bcfg in boundaries_dict.items():
            cfg: dict[str, Any] = {"name": bname}
            if isinstance(bcfg, dict):
                cfg.update(bcfg)
            self.upsert(cfg)
            count += 1
        return count
