"""Joint layout contract — maps joint indices to named groups.

Callbacks read ``pool["joint_layout"]`` to determine which joints are arm,
gripper, base, etc.  The layout is defined once in the stackfile (or
auto-derived from the preset + dynamics) and is immutable for a session.

No categories are predefined — groups are user-supplied labels.  The only
convention is that the group marked ``ee_chain=True`` (or the first group
whose indices match the Jacobian column count) is the one CBF constraints
apply to.

Example stackfile::

    safety:
      joint_layout:
        arm: [0, 1, 2, 3, 4]
        gripper: [5]

Example for AMR + arm::

    safety:
      joint_layout:
        base: [0, 1]
        arm: [2, 3, 4, 5, 6, 7]
        gripper: [8]
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class JointLayout:
    """Immutable joint-index grouping for one robot configuration.

    Parameters
    ----------
    groups
        ``{group_name: sorted list of 0-based joint indices}``.
        Every joint must appear in exactly one group.
    names
        Optional per-joint names (length = total joint count).
    """

    groups: dict[str, list[int]] = field(default_factory=dict)
    names: list[str] = field(default_factory=list)

    # ── Queries ───────────────────────────────────────────────────────────

    @property
    def n_joints(self) -> int:
        if self.names:
            return len(self.names)
        if not self.groups:
            return 0
        return max(max(idx) for idx in self.groups.values()) + 1

    def indices(self, *group_names: str) -> np.ndarray:
        """Return sorted joint indices for one or more group names."""
        out: list[int] = []
        for g in group_names:
            out.extend(self.groups.get(g, []))
        return np.array(sorted(set(out)), dtype=np.intp)

    def mask(self, *group_names: str) -> np.ndarray:
        """Boolean mask of shape ``(n_joints,)`` — True for joints in the named groups."""
        m = np.zeros(self.n_joints, dtype=bool)
        m[self.indices(*group_names)] = True
        return m

    def slice_for(self, group_name: str) -> slice | None:
        """Return a contiguous ``slice`` if the group's indices are sequential, else None."""
        idx = self.groups.get(group_name)
        if not idx:
            return None
        if idx == list(range(idx[0], idx[0] + len(idx))):
            return slice(idx[0], idx[0] + len(idx))
        return None

    def has(self, group_name: str) -> bool:
        return group_name in self.groups

    def group_of(self, joint_index: int) -> str | None:
        """Return the group name containing ``joint_index``, or None."""
        for name, indices in self.groups.items():
            if joint_index in indices:
                return name
        return None

    # ── Factory ───────────────────────────────────────────────────────────

    @classmethod
    def from_dict(cls, raw: dict[str, list[int]], names: list[str] | None = None) -> JointLayout:
        """Build from a stackfile-style ``{group_name: [indices]}`` dict."""
        groups = {k: sorted(v) for k, v in raw.items()}
        return cls(groups=groups, names=list(names or []))

    @classmethod
    def from_names(
        cls,
        joint_names: list[str],
        *,
        gripper_keywords: tuple[str, ...] = ("gripper", "grip", "finger", "jaw"),
    ) -> JointLayout:
        """Auto-derive groups from joint names by keyword matching.

        Joints whose name contains any of ``gripper_keywords`` (case-insensitive)
        are placed in the ``"gripper"`` group; the rest go into ``"arm"``.
        """
        arm: list[int] = []
        gripper: list[int] = []
        for i, name in enumerate(joint_names):
            if any(kw in name.lower() for kw in gripper_keywords):
                gripper.append(i)
            else:
                arm.append(i)
        groups: dict[str, list[int]] = {"arm": arm}
        if gripper:
            groups["gripper"] = gripper
        return cls(groups=groups, names=list(joint_names))

    @classmethod
    def trivial(cls, n_joints: int) -> JointLayout:
        """All joints in a single ``"arm"`` group — no gripper."""
        return cls(groups={"arm": list(range(n_joints))})
