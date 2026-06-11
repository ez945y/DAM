"""DynamicsContext — shared pinocchio FK + Jacobian state with per-cycle cache.

Why this exists
---------------
Guards that need geometric quantities (workspace CBF, manipulability,
end-effector pose, force projection) all need the same things:

  - forward kinematics for ``q``
  - frame placements ``oMf``
  - frame Jacobians (typically LOCAL_WORLD_ALIGNED)

Naive approach — each guard runs ``pin.computeJointJacobians`` itself —
duplicates ~30-50 µs of work per guard per cycle.  At 50 Hz with 3 such
guards that's a measurable chunk of the cycle budget, and worse, the
adapter ALREADY computed FK once during ``read()`` and threw the data away.

This class is a thin wrapper around ``(pin.Model, pin.Data)`` that:

  1. Lets the adapter call ``update(q)`` once per cycle — pinocchio
     refreshes ``oMf`` and computes joint Jacobians once.
  2. Exposes ``frame_placement(frame_id)`` and ``frame_jacobian(frame_id)``
     for guards — Jacobians are cached per frame in a tiny dict.
  3. Carries the joint-name order so guards can map without re-reading the
     URDF.

If pinocchio is unavailable, ``DynamicsContext.unavailable()`` returns a
sentinel instance whose ``frame_jacobian`` raises ``RuntimeError`` — guards
should check ``ctx.available`` before requesting Jacobians and fall back
gracefully when running on hardware without a URDF.
"""

from __future__ import annotations

from typing import Any

import numpy as np


class DynamicsContext:
    """Shared pinocchio FK + Jacobian state, refreshed once per control cycle.

    Lifetime: built once at adapter startup (from URDF), then ``update(q)``
    is called from the sensor adapter's read path.  Guards retrieve the
    context via the injection pool (key: ``dynamics``) and consume cached
    placements / Jacobians.
    """

    def __init__(
        self,
        model: Any,
        data: Any,
        joint_names: list[str],
        frame_ids: dict[str, int] | None = None,
    ) -> None:
        """
        Parameters
        ----------
        model
            ``pinocchio.Model`` (already reduced if applicable).
        data
            ``pinocchio.Data`` paired with *model*.
        joint_names
            Joint names in the ``q`` order this context expects.  Guards use
            this to align observation arrays with the pinocchio convention.
        frame_ids
            Optional mapping of human-readable name → pinocchio frame id.
            Useful so guards reference ``"gripper"`` rather than an integer.
        """
        self._model = model
        self._data = data
        self._joint_names = list(joint_names)
        self._frame_ids: dict[str, int] = dict(frame_ids or {})
        self._jac_cache: dict[int, np.ndarray] = {}
        self._q_indices_cache: dict[tuple[str, ...], np.ndarray | None] = {}
        self._last_q: np.ndarray | None = None
        # The first registered frame is treated as the "primary" end-effector
        # frame for safety filters (workspace CBF, force projection) that
        # don't want to know specific URDF link names.
        self._default_frame_id: int | None = (
            next(iter(self._frame_ids.values())) if self._frame_ids else None
        )

    # ── Properties ─────────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        """False when no pinocchio model is installed (sentinel instance)."""
        return self._model is not None

    @property
    def model(self) -> Any:
        return self._model

    @property
    def data(self) -> Any:
        return self._data

    @property
    def joint_names(self) -> list[str]:
        return list(self._joint_names)

    def q_indices_for(self, observation_names: list[str]) -> np.ndarray | None:
        """Map this model's q layout into an observation vector, by joint name.

        Returns an index array ``idx`` such that ``obs[idx]`` is the model's
        ``q`` (e.g. SO-ARM: observation lists 6 motors, the reduced model
        covers the 5 arm joints — ``idx`` picks those 5 wherever they sit).
        Returns None when any model joint is missing from the observation,
        so callers can fall back to positional alignment.
        """
        key = tuple(observation_names)
        if key in self._q_indices_cache:
            return self._q_indices_cache[key]
        name_to_obs = {n: i for i, n in enumerate(observation_names)}
        try:
            idx = np.array([name_to_obs[n] for n in self._joint_names], dtype=np.intp)
        except KeyError:
            idx = None
        self._q_indices_cache[key] = idx
        return idx

    @property
    def nq(self) -> int:
        if self._model is None:
            return 0
        n: int = int(self._model.nq)
        return n

    @property
    def default_frame_id(self) -> int | None:
        """The primary end-effector frame id (used by workspace CBF / FK)."""
        return self._default_frame_id

    def frame_id(self, name: str) -> int:
        """Look up the pinocchio frame id by human-readable name."""
        if name in self._frame_ids:
            return self._frame_ids[name]
        fid: int = int(self._model.getFrameId(name))
        self._frame_ids[name] = fid
        return fid

    # ── Per-cycle refresh ──────────────────────────────────────────────────

    def update(self, q: np.ndarray) -> None:
        """Refresh FK + Jacobians for joint configuration *q*.

        Called once per cycle by the sensor adapter (or by the first guard
        that touches the context).  Idempotent for the same *q* — repeated
        calls with the identical array are no-ops, keeping the per-cycle
        cost at ~one FK + JointJacobians sweep.
        """
        if self._model is None:
            return
        q = np.asarray(q, dtype=np.float64)
        # Truncate / pad to match nq.  Pinocchio rejects mismatched arrays
        # so we silently slice the leading nq entries (gripper joints often
        # live outside the reduced model).
        if q.shape[0] > self.nq:
            q = q[: self.nq]
        elif q.shape[0] < self.nq:
            padded = np.zeros(self.nq, dtype=np.float64)
            padded[: q.shape[0]] = q
            q = padded
        if self._last_q is not None and np.array_equal(q, self._last_q):
            return
        import pinocchio as pin

        pin.computeJointJacobians(self._model, self._data, q)
        pin.updateFramePlacements(self._model, self._data)
        self._last_q = q.copy()
        self._jac_cache.clear()

    # ── Cached lookups ─────────────────────────────────────────────────────

    def frame_placement(self, frame: int | str) -> Any:
        """Return ``data.oMf[frame_id]`` (SE3); requires prior ``update()``."""
        fid = self.frame_id(frame) if isinstance(frame, str) else frame
        return self._data.oMf[fid]

    def frame_jacobian(
        self, frame: int | str, reference: str = "LOCAL_WORLD_ALIGNED"
    ) -> np.ndarray:
        """Return the 6×nq frame Jacobian, cached per (frame_id, reference).

        Default reference is ``LOCAL_WORLD_ALIGNED`` — most useful for
        Cartesian CBF (linear part rows 0:3 give ``v_ee = J @ q̇`` in world
        frame).  Pass ``"LOCAL"`` or ``"WORLD"`` for the spatial / world
        variants when the safety condition is in those frames.
        """
        if self._model is None:
            raise RuntimeError("DynamicsContext: pinocchio unavailable; cannot compute Jacobian")
        fid = self.frame_id(frame) if isinstance(frame, str) else frame
        cache_key = hash((fid, reference))
        if cache_key in self._jac_cache:
            return self._jac_cache[cache_key]
        import pinocchio as pin

        ref_map = {
            "LOCAL": pin.LOCAL,
            "WORLD": pin.WORLD,
            "LOCAL_WORLD_ALIGNED": pin.LOCAL_WORLD_ALIGNED,
        }
        ref = ref_map.get(reference, pin.LOCAL_WORLD_ALIGNED)
        J: np.ndarray = np.asarray(
            pin.getFrameJacobian(self._model, self._data, fid, ref), dtype=np.float64
        ).copy()
        self._jac_cache[cache_key] = J
        return J

    # ── Sentinel for adapters without URDF ─────────────────────────────────

    @classmethod
    def unavailable(cls) -> DynamicsContext:
        """Return a no-op context for adapters that lack a URDF.

        ``ctx.available`` is False; ``frame_jacobian`` raises so guards
        gating on ``ctx.available`` skip the constraint gracefully.
        """
        return cls(model=None, data=None, joint_names=[])
