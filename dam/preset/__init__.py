"""Robot preset registry — generic, hardware-agnostic.

Re-exports the file-backed registry from :mod:`dam.preset.registry`.
Adapters (lerobot, ros2, …) consume presets via :func:`get_preset`;
the FastAPI router exposes CRUD over :func:`list_preset_entries`,
:func:`upsert_preset`, and :func:`delete_preset`.
"""

from dam.preset.registry import (
    BUNDLED_PATH,
    RobotPreset,
    delete_preset,
    get_preset,
    list_preset_entries,
    list_presets,
    upsert_preset,
)

__all__ = [
    "BUNDLED_PATH",
    "RobotPreset",
    "delete_preset",
    "get_preset",
    "list_preset_entries",
    "list_presets",
    "upsert_preset",
]
