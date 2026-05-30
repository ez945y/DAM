"""Joint layout contract — maps named chains to joint indices with optional grippers.

Callbacks read ``pool["joint_layout"]`` to discover which joints belong to
which kinematic chain, and which joints are grippers.  The layout is resolved
once at startup and is immutable for a session.

No chain names are predefined — they are user-supplied labels.

Example stackfile (single arm)::

    safety:
      joint_layout:
        arm:
          joints: [0, 1, 2, 3, 4]
          gripper: [5]

Example stackfile (humanoid)::

    safety:
      joint_layout:
        torso:
          joints: [0, 1, 2]
        left_arm:
          joints: [3, 4, 5, 6, 7, 8]
          gripper: [15]
        right_arm:
          joints: [9, 10, 11, 12, 13, 14]
          gripper: [16]
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class JointChain:
    """One kinematic chain — its actuated joints plus optional gripper joints."""

    indices: list[int] = field(default_factory=list)
    gripper: list[int] = field(default_factory=list)

    @property
    def all_indices(self) -> list[int]:
        """All joint indices owned by this chain (actuated + gripper)."""
        return sorted(set(self.indices) | set(self.gripper))

    @property
    def has_gripper(self) -> bool:
        return len(self.gripper) > 0


@dataclass(frozen=True)
class JointLayout:
    """Immutable joint-index grouping for one robot configuration.

    Parameters
    ----------
    chains
        ``{chain_name: JointChain}``.  Every joint should appear in
        exactly one chain.
    names
        Optional per-joint names (length = total joint count).
    """

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

    # ── Factory ───────────────────────────────────────────────────────────

    @classmethod
    def from_config(
        cls,
        raw: dict[str, dict[str, list[int]] | list[int]],
        names: list[str] | None = None,
    ) -> JointLayout:
        """Build from a stackfile-style config dict.

        Accepts two forms per chain::

            # Full form
            arm:
              joints: [0, 1, 2, 3, 4]
              gripper: [5]

            # Short form (no gripper)
            torso: [0, 1, 2]
        """
        chains: dict[str, JointChain] = {}
        for chain_name, value in raw.items():
            if isinstance(value, list):
                chains[chain_name] = JointChain(indices=sorted(value))
            elif isinstance(value, dict):
                chains[chain_name] = JointChain(
                    indices=sorted(value.get("joints", [])),
                    gripper=sorted(value.get("gripper", [])),
                )
            else:
                msg = f"Invalid chain config for '{chain_name}': expected list or dict"
                raise ValueError(msg)
        return cls(chains=chains, names=list(names or []))

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
        arm: list[int] = []
        gripper: list[int] = []
        for i, name in enumerate(joint_names):
            if any(kw in name.lower() for kw in gripper_keywords):
                gripper.append(i)
            else:
                arm.append(i)
        chain = JointChain(indices=arm, gripper=gripper)
        return cls(chains={"arm": chain}, names=list(joint_names))

    @classmethod
    def trivial(cls, n_joints: int) -> JointLayout:
        """All joints in a single ``"arm"`` chain with no gripper."""
        return cls(chains={"arm": JointChain(indices=list(range(n_joints)))})
