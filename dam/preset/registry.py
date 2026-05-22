"""Robot preset registry — two-layer, file-backed.

The registry is read from two YAML files and merged at lookup time:

  - ``BUNDLED_PATH`` — ``assets/presets.yaml`` shipped in-git. Seed presets
    (e.g. so101_follower). Treated as read-only by this module — never
    written to so the repo working tree stays clean.

  - ``USER_PATH`` — ``${DAM_DATA_ROOT}/presets.yaml`` (default ``./data/``,
    gitignored). All CRUD writes land here. User entries override bundled
    entries on key collision. To "delete" a bundled preset, this module
    writes a tombstone (``{_deleted: true}``) into the user file.

Both writes are atomic (write to ``.tmp``, then rename) and guarded by an
fcntl advisory lock so multi-worker Uvicorn doesn't race on the user file.

A preset captures only what is intrinsic to a robot model:
  - ``joint_names`` (ordered list of joint identifiers)
  - ``degrees_mode`` (True if hardware speaks degrees natively)
  - ``urdf_path`` (for FK; relative paths resolved against repo root,
    absolute paths used as-is — user-uploaded URDFs typically land at
    ``${DAM_DATA_ROOT}/urdf/<file>.urdf``)

Limits / max velocities / gripper handling live on boundary callbacks in
the stackfile — never here.
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


# dam/preset/registry.py → parents[2] is the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_PATH = _REPO_ROOT / "assets" / "presets.yaml"


def _user_path() -> Path:
    """User registry path — re-resolved each call so tests can override
    ``DAM_DATA_ROOT`` between test cases."""
    return Path(os.environ.get("DAM_DATA_ROOT", "./data")) / "presets.yaml"


@dataclass
class RobotPreset:
    """Intrinsic hardware description for one robot model."""

    name: str
    joint_names: list[str] = field(default_factory=list)
    degrees_mode: bool = True
    default_urdf_relpath: str | None = None


# ── Registry I/O ─────────────────────────────────────────────────────────────

_lock = threading.Lock()


def _load_one(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    with path.open() as f:
        raw = yaml.safe_load(f) or {}
    return raw.get("presets", {}) or {}


def _load_merged() -> dict[str, dict[str, Any]]:
    """Read bundled + user, merge with user taking precedence, drop tombstones."""
    merged = dict(_load_one(BUNDLED_PATH))
    user = _load_one(_user_path())
    for name, entry in user.items():
        if not isinstance(entry, dict):
            continue
        if entry.get("_deleted"):
            merged.pop(name, None)
        else:
            merged[name] = entry
    return merged


def _save_user(presets: dict[str, dict[str, Any]]) -> None:
    """Atomically write the user registry, guarded by an advisory file lock."""
    path = _user_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as f:
        # fcntl is POSIX-only; on Windows fall back to the threading lock above.
        with contextlib.suppress(ImportError, OSError):
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        yaml.safe_dump({"presets": presets}, f, sort_keys=False, default_flow_style=False)
    os.replace(tmp, path)


def _to_preset(name: str, entry: dict[str, Any]) -> RobotPreset:
    return RobotPreset(
        name=name,
        joint_names=list(entry.get("joint_names", []) or []),
        degrees_mode=bool(entry.get("degrees_mode", True)),
        default_urdf_relpath=entry.get("urdf_path"),
    )


def _normalize_key(name: str) -> str:
    return name.lower().replace("-", "_")


def get_preset(name: str) -> RobotPreset:
    """Look up a preset by name. Raises KeyError for unknown names."""
    key = _normalize_key(name)
    with _lock:
        merged = _load_merged()
        if key not in merged:
            raise KeyError(f"Unknown robot preset '{name}'. Available: {sorted(merged)}")
        return _to_preset(key, merged[key])


def list_presets() -> list[str]:
    with _lock:
        return sorted(_load_merged())


def list_preset_entries() -> list[dict[str, Any]]:
    """Return all presets as plain dicts (for API serialization)."""
    with _lock:
        merged = _load_merged()
    return [
        {
            "name": name,
            "joint_names": list(entry.get("joint_names", []) or []),
            "degrees_mode": bool(entry.get("degrees_mode", True)),
            "urdf_path": entry.get("urdf_path"),
        }
        for name, entry in sorted(merged.items())
    ]


def upsert_preset(
    name: str,
    *,
    joint_names: list[str],
    degrees_mode: bool,
    urdf_path: str | None,
    rename_from: str | None = None,
) -> RobotPreset:
    """Create or update a preset (writes to the user file).

    ``rename_from``: if set, also remove the old key in the same atomic
    write — used by the Console when the user edits a preset's name.
    """
    key = _normalize_key(name)
    if not key:
        raise ValueError("Preset name must not be empty")
    if not joint_names:
        raise ValueError("Preset must have at least one joint name")
    entry = {
        "joint_names": [str(j) for j in joint_names],
        "degrees_mode": bool(degrees_mode),
        "urdf_path": urdf_path or None,
    }
    with _lock:
        user = _load_one(_user_path())
        bundled = _load_one(BUNDLED_PATH)
        if rename_from:
            old_key = _normalize_key(rename_from)
            if old_key != key:
                user.pop(old_key, None)
                if old_key in bundled:
                    # Old name was a bundled preset — tombstone it so the
                    # rename is observable in the merged view.
                    user[old_key] = {"_deleted": True}
        user[key] = entry
        _save_user(user)
    logger.info(
        "upsert_preset: saved '%s'%s",
        key,
        f" (renamed from '{rename_from}')" if rename_from else "",
    )
    return _to_preset(key, entry)


def delete_preset(name: str) -> bool:
    """Remove a preset from the merged view. Returns True if it existed.

    Bundled-only presets are hidden via a tombstone entry in the user file.
    """
    key = _normalize_key(name)
    with _lock:
        user = _load_one(_user_path())
        bundled = _load_one(BUNDLED_PATH)
        in_user = key in user and not user[key].get("_deleted")
        in_bundled = key in bundled
        if not in_user and not in_bundled:
            return False
        if in_bundled:
            user[key] = {"_deleted": True}
        else:
            user.pop(key, None)
        _save_user(user)
    logger.info("delete_preset: removed '%s'", key)
    return True
