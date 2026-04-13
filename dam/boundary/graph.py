from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from dam.boundary.container import BoundaryContainer
from dam.boundary.node import BoundaryNode
from dam.types.result import GuardResult

if TYPE_CHECKING:
    from dam.types.action import ActionProposal
    from dam.types.observation import Observation


@dataclass
class Transition:
    to_node: str
    condition: Callable[[], bool] | None = None
    priority: int = 0


class GraphContainer(BoundaryContainer):
    """BoundaryContainer that models a DAG of BoundaryNodes connected by Transitions.

    advance() evaluates outgoing edges from the current node (sorted by priority,
    highest first) and follows the first edge whose condition is True (or unconditional).
    The observation passed to advance() is available to condition callables via closure
    if needed.

    Phase 2+ only — requires Python setup code to build the graph.
    GraphContainer cannot be declared purely in YAML (use 'type: graph' as a marker and
    configure edges in your Python setup script).
    """

    def __init__(
        self,
        nodes: dict[str, BoundaryNode],
        transitions: dict[str, list[Transition]],
        initial_node_id: str,
    ) -> None:
        if initial_node_id not in nodes:
            raise ValueError(f"Initial node '{initial_node_id}' not in nodes dict")
        self._nodes = nodes
        self._transitions = transitions
        self._current_node_id = initial_node_id

    def get_active_node(self) -> BoundaryNode:
        return self._nodes[self._current_node_id]

    def get_all_nodes(self) -> list[BoundaryNode]:
        return list(self._nodes.values())

    def evaluate(self, obs: Observation, action: ActionProposal) -> GuardResult:
        # Constraint enforcement is handled by guards through the injection pool.
        return GuardResult.success(
            guard_name=f"GraphContainer({self._current_node_id})",
            layer=None,  # type: ignore[arg-type]
        )

    def advance(self, obs: Observation | None = None) -> str | None:
        """Follow the highest-priority satisfied outgoing edge.

        Returns the target node ID, or None if no edge is satisfied (terminal state).
        ``obs`` is ignored by built-in conditions but can be captured via closure by
        user-supplied condition callables.
        """
        edges = self._transitions.get(self._current_node_id, [])
        sorted_edges = sorted(edges, key=lambda t: -t.priority)
        for transition in sorted_edges:
            if transition.condition is None or transition.condition():
                if transition.to_node not in self._nodes:
                    raise ValueError(f"Transition target '{transition.to_node}' not in nodes dict")
                self._current_node_id = transition.to_node
                return self._current_node_id
        return None  # No satisfied edge — terminal state

    def reset(self) -> None:
        # Graph containers intentionally do not reset — maintain current node.
        pass

    def snapshot(self) -> dict[str, Any]:
        return {"node_id": self._current_node_id}

    def restore(self, state: dict[str, Any]) -> None:
        self._current_node_id = str(state["node_id"])
