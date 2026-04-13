"""Abstract base classes for all DAM adapters.

Design principle: Guards NEVER import concrete adapters. They only declare parameter names
in their check() signature. The framework resolves and injects the correct objects from the
runtime pool at startup. This boundary is enforced at the import level.

Adapter roles:
  SensorAdapter   — external world  → Observation
  PolicyAdapter   — Observation     → ActionProposal
  ActionAdapter   — ValidatedAction → hardware
  SimulatorAdapter — (used by L1 only, never by guards directly)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dam.types.action import ActionProposal, ValidatedAction
    from dam.types.observation import Observation


class SensorAdapter(ABC):
    """Bridges a hardware / ROS2 / serial source to DAM Observation.

    Implementations: LeRobotSourceAdapter, ROS2SourceAdapter, SerialSourceAdapter …
    Declared in Stackfile hardware.sources; instantiated by StackfileLoader, never by user code.
    """

    @abstractmethod
    def connect(self) -> None:
        """Open connection to the sensor / topic."""
        ...

    @abstractmethod
    def read(self) -> Observation:
        """Read one sample and return a fully-typed Observation."""
        ...

    @abstractmethod
    def is_healthy(self) -> bool:
        """Return True if the sensor is reachable and data is fresh."""
        ...

    @abstractmethod
    def disconnect(self) -> None:
        """Close connection gracefully."""
        ...


class PolicyAdapter(ABC):
    """Wraps any policy model behind a single predict() contract.

    Implementations: LeRobotPolicyAdapter, RandomPolicyAdapter (testing) …
    The adapter hides framework-specific APIs (torch tensor shapes, chunk semantics, etc.)
    so that guards and the runtime never depend on the policy library.
    """

    @abstractmethod
    def initialize(self, config: dict[str, Any]) -> None:
        """Load weights and prepare model for inference."""
        ...

    @abstractmethod
    def predict(self, obs: Observation) -> ActionProposal:
        """Run a forward pass and return the next action proposal."""
        ...

    @abstractmethod
    def get_policy_name(self) -> str:
        """Return a human-readable identifier, e.g. 'lerobot_act_so101'."""
        ...

    @abstractmethod
    def reset(self) -> None:
        """Reset any internal recurrent state (called on task start)."""
        ...


class ActionAdapter(ABC):
    """Sends a ValidatedAction to the physical hardware or ROS2 topic.

    Implementations: LeRobotSinkAdapter, ROS2SinkAdapter …
    Declared in Stackfile hardware.sinks; instantiated by StackfileLoader.
    """

    @abstractmethod
    def connect(self) -> None:
        """Open connection to the actuator / topic."""
        ...

    @abstractmethod
    def apply(self, action: ValidatedAction) -> None:
        """Send the validated action to hardware. Must be non-blocking in hot path."""
        ...

    @abstractmethod
    def emergency_stop(self) -> None:
        """Immediately halt all motion. Must be callable from any thread."""
        ...

    @abstractmethod
    def get_hardware_status(self) -> dict[str, Any]:
        """Return a dict of diagnostic information (temperatures, torques, errors)."""
        ...

    @abstractmethod
    def disconnect(self) -> None:
        """Stop actuator and close connection gracefully."""
        ...


class SimulatorAdapter(ABC):
    """Rollout interface used exclusively by L1 (SimPreflightGuard).

    Guards must NOT import this class directly — L1 receives the simulator object
    via injection pool key 'simulator'. Other guards must not declare 'simulator'
    in their check() signatures.

    The adapter's job is to synchronise with real-world state, step forward one action,
    and report whether the result is safe. If is_available() returns False, L1 skips
    the sim check and returns PASS (graceful degradation — simulator is optional).
    """

    @abstractmethod
    def reset(self, obs: Observation) -> None:
        """Synchronise the simulator state with the current real-world observation."""
        ...

    @abstractmethod
    def step(self, action: ActionProposal) -> Observation:
        """Apply action in simulation and return the resulting observation."""
        ...

    @abstractmethod
    def has_collision(self) -> bool:
        """Return True if the most recent step produced a collision."""
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Return False if the simulator process is unavailable; L1 will PASS gracefully."""
        ...
