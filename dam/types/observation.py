from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass(frozen=True)
class Observation:
    """Snapshot of robot state at a single point in time.

    Fields
    ------
    timestamp           Monotonic time in seconds (time.monotonic()).
    joint_positions     Joint angles [rad], shape (n_joints,). Always required.
    joint_velocities    Joint velocities [rad/s]. Optional — some sensors don't provide it;
                        guards that need it must handle None or declare it non-optional.
    end_effector_pose   [x, y, z, qx, qy, qz, qw], shape (7,). Optional — computed from FK
                        when not directly available.
    force_torque        [Fx, Fy, Fz, Tx, Ty, Tz] [N / N·m], shape (6,). Optional.
    images              Camera frames keyed by camera name.
    metadata            Arbitrary pass-through info (frame_id, sensor_id, …).
    """

    timestamp: float
    # Empty array when the embodiment has no joints (e.g. a mobile base whose
    # state is a base pose carried in ``channels``). Builtin joint guards skip
    # an empty vector; custom callbacks read their own ``channels`` groups.
    joint_positions: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float64))
    joint_velocities: np.ndarray | None = None  # [rad/s]; None if sensor unavailable
    end_effector_pose: np.ndarray | None = None  # [x,y,z,qx,qy,qz,qw]; None if not computed
    force_torque: np.ndarray | None = None  # [Fx,Fy,Fz,Tx,Ty,Tz]; None if no F/T sensor
    images: dict[str, np.ndarray] | None = None
    # Device-specific observation channels keyed by name (e.g. "current", "temperature").
    # Each value is shape (n_joints,).  Populated when register-type sources are
    # declared in the stackfile alongside the main device source.
    channels: dict[str, np.ndarray] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "joint_positions", np.asarray(self.joint_positions, dtype=np.float64).copy()
        )
        if self.joint_velocities is not None:
            object.__setattr__(
                self, "joint_velocities", np.asarray(self.joint_velocities, dtype=np.float64).copy()
            )
        if self.end_effector_pose is not None:
            object.__setattr__(
                self,
                "end_effector_pose",
                np.asarray(self.end_effector_pose, dtype=np.float64).copy(),
            )
        if self.force_torque is not None:
            object.__setattr__(
                self, "force_torque", np.asarray(self.force_torque, dtype=np.float64).copy()
            )
        if self.channels is not None:
            object.__setattr__(
                self,
                "channels",
                {k: np.asarray(v, dtype=np.float64).copy() for k, v in self.channels.items()},
            )

    # Typed fields that should also appear as named channels in the cycle
    # record / MCAP.  Defining the mapping here keeps the name → field
    # association in exactly one place; the runtime iterates blindly.
    _TYPED_CHANNEL_FIELDS: tuple[str, ...] = (
        "joint_velocities",
        "end_effector_pose",
        "force_torque",
    )

    _UNSET: Any = object()

    def merged(
        self,
        *,
        images: dict[str, np.ndarray] | None = _UNSET,
        channels: dict[str, np.ndarray] | None = _UNSET,
        metadata: dict[str, Any] | None = _UNSET,
    ) -> Observation:
        """Return a new Observation with dicts merged (new keys win).

        Pass an explicit ``None`` to clear a field; omit to keep current value.
        """
        if images is self._UNSET:
            new_images = self.images
        elif images is None:
            new_images = None
        else:
            new_images = {**(self.images or {}), **images} or None

        if channels is self._UNSET:
            new_channels = self.channels
        elif channels is None:
            new_channels = None
        else:
            new_channels = {**(self.channels or {}), **channels} or None

        if metadata is self._UNSET:
            new_metadata = dict(self.metadata)
        elif metadata is None:
            new_metadata = {}
        else:
            new_metadata = {**self.metadata, **metadata}

        return Observation(
            timestamp=self.timestamp,
            joint_positions=self.joint_positions,
            joint_velocities=self.joint_velocities,
            end_effector_pose=self.end_effector_pose,
            force_torque=self.force_torque,
            images=new_images,
            channels=new_channels,
            metadata=new_metadata,
        )

    def iter_channels(self) -> Iterator[tuple[str, np.ndarray]]:
        """Yield every named array carried by this Observation.

        Includes the typed optional fields (joint_velocities, end_effector_pose,
        force_torque) and every entry in the dynamic ``channels`` dict.
        Order is stable: typed fields first, then dynamic-dict insertion order.
        """
        for name in self._TYPED_CHANNEL_FIELDS:
            value = getattr(self, name)
            if value is not None:
                yield name, value
        if self.channels:
            yield from self.channels.items()
