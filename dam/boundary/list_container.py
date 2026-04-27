from __future__ import annotations

from typing import TYPE_CHECKING, Any

from dam.boundary.container import BoundaryContainer
from dam.boundary.node import BoundaryNode
from dam.types.result import GuardResult

if TYPE_CHECKING:
    from dam.types.action import ActionProposal
    from dam.types.observation import Observation


class ListContainer(BoundaryContainer):
    """Container that steps through a fixed ordered list of BoundaryNodes.

    advance() moves to the next node.  When the end is reached:
    - loop=True  → wraps back to index 0 and returns that node's ID.
    - loop=False → stays at the last node and returns None (terminal state).
    """

    def __init__(self, nodes: list[BoundaryNode], loop: bool = False) -> None:
        if not nodes:
            raise ValueError("ListContainer requires at least one node")
        self._nodes = nodes
        self._loop = loop
        self._current_index = 0

    def get_active_node(self) -> BoundaryNode:
        return self._nodes[self._current_index]

    def get_all_nodes(self) -> list[BoundaryNode]:
        return list(self._nodes)

    def evaluate(self, _obs: Observation, _action: ActionProposal) -> GuardResult:
        # Constraint enforcement is handled by guards through the injection pool.
        return GuardResult.success(
            guard_name=f"ListContainer[{self._current_index}]({self._nodes[self._current_index].node_id})",
            layer=None,  # type: ignore[arg-type]
        )

    def advance(self, _obs: Observation | None = None) -> str | None:
        """Advance to the next node.  obs is ignored (sequential, no conditions).

        Returns
        -------
        str   Node ID of the new active node.
        None  If the end of the list has been reached and loop=False.
        """
        if self._current_index < len(self._nodes) - 1:
            self._current_index += 1
            return self._nodes[self._current_index].node_id
        elif self._loop:
            self._current_index = 0
            return self._nodes[0].node_id
        # Terminal state — stay at last node, signal caller.
        return None

    def reset(self) -> None:
        self._current_index = 0

    def snapshot(self) -> dict[str, Any]:
        return {"index": self._current_index}

    def restore(self, state: dict[str, Any]) -> None:
        self._current_index = int(state["index"])
