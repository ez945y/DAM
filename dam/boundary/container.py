from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from dam.boundary.node import BoundaryNode

if TYPE_CHECKING:
    from dam.types.action import ActionProposal
    from dam.types.observation import Observation
    from dam.types.result import GuardResult


class BoundaryContainer(ABC):
    """Abstract container that owns one or more BoundaryNodes.

    A BoundaryContainer is the runtime object that the GuardRuntime holds. It always
    has an *active node* — the constraint set currently in effect. Guards receive the
    active node's constraint values via the injection pool; they never call evaluate()
    directly. The container drives node transitions when advance() is called by the
    runtime after a successful cycle.

    Container types
    ---------------
    SingleNodeContainer  — one node, advance() is a no-op, returns the same node ID.
    ListContainer        — sequential list, advance() moves to the next node.
    GraphContainer       — DAG of nodes, advance() follows an edge chosen by the
                           transition function (Phase 2+, requires Python setup).

    Threading
    ---------
    Implementations must be safe to call from a single thread (the control loop thread).
    No internal locking is required.
    """

    @abstractmethod
    def get_active_node(self) -> BoundaryNode:
        """Return the currently active BoundaryNode."""
        ...

    @abstractmethod
    def get_all_nodes(self) -> list[BoundaryNode]:
        """Return all nodes owned by this container (for inspection / logging)."""
        ...

    @abstractmethod
    def evaluate(
        self,
        obs: Observation,
        action: ActionProposal,
    ) -> GuardResult:
        """Evaluate the active node's constraint against the given obs/action pair.

        This is a convenience entry point for containers that embed their own check
        logic (e.g. GraphContainer with edge conditions). Most built-in guards use
        the injection pool path instead and never call this method directly.
        """
        ...

    @abstractmethod
    def advance(self, obs: Observation | None = None) -> str | None:
        """Move to the next node and return the new node's ID.

        Parameters
        ----------
        obs     Optional observation used by condition-based transitions (GraphContainer).
                Ignored by SingleNodeContainer and ListContainer.

        Returns
        -------
        str     Node ID of the node that became active after the transition.
        None    If the container has reached its terminal state (end of list without
                loop, or no outgoing edge in a graph).
        """
        ...

    @abstractmethod
    def reset(self) -> None:
        """Return to the initial node (called on task restart)."""
        ...

    @abstractmethod
    def snapshot(self) -> dict[str, Any]:
        """Return a serialisable snapshot of current state for hot-reload double-buffering."""
        ...

    @abstractmethod
    def restore(self, state: dict[str, Any]) -> None:
        """Restore state from a snapshot produced by snapshot()."""
        ...
