"""Robot preset registry — two-layer, file-backed.

The registry is read from two YAML files and merged at lookup time:

  - ``BUNDLED_PATH`` — ``assets/presets.yaml`` shipped in-git. Seed presets
    (e.g. so101_follower). Treated as read-only by this module — never
    written to so the repo working tree stays clean.

  - ``USER_PATH`` — by default the same ``assets/presets.yaml`` file. Tests
    and deployments can override it with ``DAM_DATA_ROOT``. CRUD writes land
    here so presets remain YAML-managed.

Both writes are atomic (write to ``.tmp``, then rename) and guarded by an
fcntl advisory lock so multi-worker Uvicorn doesn't race on the user file.

A preset captures only what is intrinsic to a robot model:
  - ``joint_names`` (ordered list of joint identifiers)
  - ``asset`` (the single robot description resource, e.g. URDF or USD)
  - ``solvers`` (robot-owned solver definitions; a preset can expose multiple
    capabilities such as arm kinematics, base dynamics, collision, etc.)
  - ``action_layout`` (named segments in the policy action vector)

Limits / max velocities / gripper handling live on boundary callbacks in
the stackfile — never here. deg<->rad mode (``degrees_mode``) is an interface
concern declared on the motor interface, not robot identity — never here.
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dam.types.joint_layout import JointLayout

import yaml

logger = logging.getLogger(__name__)


# dam/preset/registry.py → parents[2] is the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_PATH = _REPO_ROOT / "assets" / "presets.yaml"


def _user_path() -> Path:
    """Preset registry path — re-resolved so tests can override it."""
    data_root = os.environ.get("DAM_DATA_ROOT")
    if data_root:
        return Path(data_root) / "presets.yaml"
    return BUNDLED_PATH


@dataclass
class RobotPreset:
    """Intrinsic hardware description for one robot model."""

    name: str
    joint_names: list[str] = field(default_factory=list)
    asset: dict[str, str] | None = None
    solvers: dict[str, Any] = field(default_factory=dict)
    action_layout: list[dict[str, Any]] = field(default_factory=list)
    chains: dict[str, Any] | None = field(default=None, repr=False)

    def asset_path(self) -> str | None:
        if not self.asset:
            return None
        return self.asset.get("path")

    def asset_type(self) -> str | None:
        if not self.asset:
            return None
        value = self.asset.get("type")
        return str(value).lower() if value else None

    @property
    def joint_layout(self) -> JointLayout:
        """Resolved joint layout — explicit from chains config, or auto-derived from joint_names."""
        from dam.types.joint_layout import JointLayout

        if self.chains:
            return JointLayout.from_config(self.chains, joint_names=self.joint_names)
        if self.joint_names:
            return JointLayout.from_names(self.joint_names)
        return JointLayout.trivial(0)


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
    asset = entry.get("asset")
    return RobotPreset(
        name=name,
        joint_names=list(entry.get("joint_names", []) or []),
        asset=dict(asset) if isinstance(asset, dict) else None,
        solvers=dict(entry.get("solvers") or {}),
        action_layout=list(entry.get("action_layout") or []),
        chains=entry.get("chains"),
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
    entries: list[dict[str, Any]] = []
    for name, entry in sorted(merged.items()):
        asset = entry.get("asset")
        entries.append(
            {
                "name": name,
                "joint_names": list(entry.get("joint_names", []) or []),
                "asset": dict(asset) if isinstance(asset, dict) else None,
                "solvers": dict(entry.get("solvers") or {}),
                "action_layout": list(entry.get("action_layout") or []),
                "chains": entry.get("chains"),
            }
        )
    return entries


def upsert_preset(
    name: str,
    *,
    joint_names: list[str],
    asset: dict[str, str] | None = None,
    solvers: dict[str, Any] | None = None,
    action_layout: list[dict[str, Any]] | None = None,
    chains: dict[str, Any] | None = None,
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
    entry: dict[str, Any] = {
        "joint_names": [str(j) for j in joint_names],
    }
    clean_asset = _clean_asset(asset)
    if clean_asset:
        entry["asset"] = clean_asset
    if solvers:
        entry["solvers"] = dict(solvers)
    if action_layout:
        entry["action_layout"] = [dict(item) for item in action_layout]
    if chains:
        entry["chains"] = chains
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


def _clean_asset(asset: dict[str, str] | None) -> dict[str, str] | None:
    if not asset:
        return None
    asset_type = str(asset.get("type") or "").strip().lower()
    path = str(asset.get("path") or "").strip()
    if not asset_type and not path:
        return None
    if not asset_type or not path:
        raise ValueError("Preset asset requires both 'type' and 'path'")
    return {"type": asset_type, "path": path}


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
