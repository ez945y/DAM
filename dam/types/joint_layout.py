"""Joint layout contract — maps named chains to joint indices with optional grippers.

Callbacks read ``pool["joint_layout"]`` to discover which joints belong to
which kinematic chain, and which joints are grippers.  The layout is resolved
once at startup and is immutable for a session.

No chain names are predefined — they are user-supplied labels.

**Zero-config** for simple robots::

    # Just list joint_names — the system auto-detects grippers by name.
    so101_follower:
      joint_names: [shoulder_pan, shoulder_lift, elbow_flex,
                    wrist_flex, wrist_roll, gripper]
      # → arm: [shoulder_pan .. wrist_roll], arm.gripper: [gripper]

**Name-based chains** for complex robots (no index counting)::

    humanoid:
      joint_names: [torso_y, torso_p, l_sh, l_el, l_grip, r_sh, r_el, r_grip]
      chains:
        torso: [torso_y, torso_p]
        left_arm:
          joints: [l_sh, l_el]
          gripper: [l_grip]
        right_arm:
          joints: [r_sh, r_el]
          gripper: [r_grip]
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class JointChain:
    """One kinematic chain — its actuated joints plus optional gripper joints."""

    indices: list[int] = field(default_factory=list)
    gripper: list[int] = field(default_factory=list)
    _names: list[str] = field(default_factory=list, repr=False)
    _gripper_names: list[str] = field(default_factory=list, repr=False)

    @property
    def all_indices(self) -> list[int]:
        """All joint indices owned by this chain (actuated + gripper)."""
        return sorted(set(self.indices) | set(self.gripper))

    @property
    def has_gripper(self) -> bool:
        return len(self.gripper) > 0

    def __repr__(self) -> str:
        parts = []
        if self._names:
            parts.append(f"joints={self._names}")
        else:
            parts.append(f"joints={self.indices}")
        if self.gripper:
            if self._gripper_names:
                parts.append(f"gripper={self._gripper_names}")
            else:
                parts.append(f"gripper={self.gripper}")
        return f"JointChain({', '.join(parts)})"


@dataclass(frozen=True)
class JointLayout:
    """Immutable joint-index grouping for one robot configuration."""

    chains: dict[str, JointChain] = field(default_factory=dict)
    names: list[str] = field(default_factory=list)

    # ── Queries ───────────────────────────────────────────────────────────

    @property
    def n_joints(self) -> int:
        if self.names:
            return len(self.names)
        all_idx: set[int] = set()
        for chain in self.chains.values():
            all_idx.update(chain.indices)
            all_idx.update(chain.gripper)
        return (max(all_idx) + 1) if all_idx else 0

    @property
    def chain_names(self) -> list[str]:
        return list(self.chains.keys())

    def joint_indices(self, *chain_names: str) -> np.ndarray:
        """Sorted actuated (non-gripper) joint indices for the named chains."""
        out: set[int] = set()
        for name in chain_names:
            chain = self.chains.get(name)
            if chain:
                out.update(chain.indices)
        return np.array(sorted(out), dtype=np.intp)

    def gripper_indices(self, *chain_names: str) -> np.ndarray:
        """Sorted gripper joint indices for the named chains."""
        out: set[int] = set()
        for name in chain_names:
            chain = self.chains.get(name)
            if chain:
                out.update(chain.gripper)
        return np.array(sorted(out), dtype=np.intp)

    def all_indices(self, *chain_names: str) -> np.ndarray:
        """All joint indices (actuated + gripper) for the named chains."""
        out: set[int] = set()
        for name in chain_names:
            chain = self.chains.get(name)
            if chain:
                out.update(chain.all_indices)
        return np.array(sorted(out), dtype=np.intp)

    def mask(self, *chain_names: str, include_gripper: bool = True) -> np.ndarray:
        """Boolean mask of shape ``(n_joints,)``."""
        m = np.zeros(self.n_joints, dtype=bool)
        if include_gripper:
            m[self.all_indices(*chain_names)] = True
        else:
            m[self.joint_indices(*chain_names)] = True
        return m

    def has(self, chain_name: str) -> bool:
        return chain_name in self.chains

    def chain_of(self, joint_index: int) -> str | None:
        """Return the chain name that owns ``joint_index``, or None."""
        for name, chain in self.chains.items():
            if joint_index in chain.indices or joint_index in chain.gripper:
                return name
        return None

    def is_gripper(self, joint_index: int) -> bool:
        """True if ``joint_index`` is a gripper joint in any chain."""
        return any(joint_index in c.gripper for c in self.chains.values())

    def chains_with_gripper(self) -> list[str]:
        """Return names of all chains that have grippers."""
        return [name for name, chain in self.chains.items() if chain.has_gripper]

    @property
    def summary(self) -> str:
        """One-line human-readable summary."""
        parts = []
        for cname, chain in self.chains.items():
            n = len(chain.indices)
            g = len(chain.gripper)
            s = f"{cname}: {n}j"
            if g:
                s += f"+{g}g"
            parts.append(s)
        return " | ".join(parts)

    def __repr__(self) -> str:
        lines = ["JointLayout("]
        for cname, chain in self.chains.items():
            joint_label = chain._names if chain._names else chain.indices
            lines.append(f"  {cname}: {joint_label}")
            if chain.gripper:
                grip_label = chain._gripper_names if chain._gripper_names else chain.gripper
                lines.append(f"  {cname}.gripper: {grip_label}")
        lines.append(")")
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.summary

    # ── Factory ───────────────────────────────────────────────────────────

    @classmethod
    def from_config(
        cls,
        raw: dict[str, dict[str, list[str | int]] | list[str | int]],
        joint_names: list[str],
    ) -> JointLayout:
        """Build from a preset ``chains:`` config using **joint names**.

        Accepts two forms per chain::

            # Full form — joints by name, with gripper
            left_arm:
              joints: [l_shoulder, l_elbow, l_wrist]
              gripper: [l_finger]

            # Short form — just a list of joint names (no gripper)
            torso: [torso_yaw, torso_pitch, torso_roll]

        Also accepts raw integer indices for backward compat, but
        name-based is the intended interface.
        """
        name_to_idx = {n: i for i, n in enumerate(joint_names)}
        chains: dict[str, JointChain] = {}

        for chain_name, value in raw.items():
            if isinstance(value, list):
                indices, jnames = _resolve_names(value, name_to_idx, chain_name, "joints")
                chains[chain_name] = JointChain(
                    indices=indices,
                    _names=jnames,
                )
            elif isinstance(value, dict):
                j_raw = value.get("joints", [])
                g_raw = value.get("gripper", [])
                indices, jnames = _resolve_names(j_raw, name_to_idx, chain_name, "joints")
                gripper, gnames = _resolve_names(g_raw, name_to_idx, chain_name, "gripper")
                chains[chain_name] = JointChain(
                    indices=indices,
                    gripper=gripper,
                    _names=jnames,
                    _gripper_names=gnames,
                )
            else:
                msg = f"Invalid chain config for '{chain_name}': expected list or dict, got {type(value).__name__}"
                raise TypeError(msg)

        _validate_coverage(chains, joint_names)
        return cls(chains=chains, names=list(joint_names))

    @classmethod
    def from_names(
        cls,
        joint_names: list[str],
        *,
        gripper_keywords: tuple[str, ...] = ("gripper", "grip", "finger", "jaw"),
    ) -> JointLayout:
        """Auto-derive a single-chain layout from joint names.

        Joints whose name contains any gripper keyword become the gripper
        of the ``"arm"`` chain; the rest are the arm's actuated joints.
        """
        arm_idx: list[int] = []
        arm_names: list[str] = []
        grip_idx: list[int] = []
        grip_names: list[str] = []
        for i, name in enumerate(joint_names):
            if any(kw in name.lower() for kw in gripper_keywords):
                grip_idx.append(i)
                grip_names.append(name)
            else:
                arm_idx.append(i)
                arm_names.append(name)
        chain = JointChain(
            indices=arm_idx,
            gripper=grip_idx,
            _names=arm_names,
            _gripper_names=grip_names,
        )
        return cls(chains={"arm": chain}, names=list(joint_names))

    @classmethod
    def trivial(cls, n_joints: int) -> JointLayout:
        """All joints in a single ``"arm"`` chain with no gripper."""
        return cls(chains={"arm": JointChain(indices=list(range(n_joints)))})


# ── Internal helpers ──────────────────────────────────────────────────────


def _resolve_names(
    raw: list[str | int],
    name_to_idx: dict[str, int],
    chain_name: str,
    field_name: str,
) -> tuple[list[int], list[str]]:
    """Resolve a list of joint names (or ints) to sorted indices + names."""
    indices: list[int] = []
    names: list[str] = []
    for item in raw:
        if isinstance(item, int):
            indices.append(item)
        elif isinstance(item, str):
            if item not in name_to_idx:
                close = difflib.get_close_matches(item, name_to_idx.keys(), n=1, cutoff=0.6)
                hint = f" Did you mean '{close[0]}'?" if close else ""
                msg = f"Joint '{item}' in {chain_name}.{field_name} not found in joint_names.{hint}"
                raise ValueError(msg)
            indices.append(name_to_idx[item])
            names.append(item)
        else:
            msg = f"Expected joint name (str) or index (int) in {chain_name}.{field_name}, got {type(item).__name__}"
            raise TypeError(msg)
    return sorted(indices), names


def _validate_coverage(
    chains: dict[str, JointChain],
    joint_names: list[str],
) -> None:
    """Check for duplicate assignments across chains."""
    seen: dict[int, str] = {}
    for cname, chain in chains.items():
        for idx in chain.all_indices:
            if idx in seen:
                jname = joint_names[idx] if idx < len(joint_names) else str(idx)
                msg = f"Joint '{jname}' (index {idx}) assigned to both '{seen[idx]}' and '{cname}'"
                raise ValueError(msg)
            seen[idx] = cname
