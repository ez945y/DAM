from __future__ import annotations

from typing import TYPE_CHECKING, Any

from dam.boundary.container import BoundaryContainer
from dam.boundary.node import BoundaryNode
from dam.types.result import GuardResult

if TYPE_CHECKING:
    from dam.types.action import ActionProposal
    from dam.types.observation import Observation


class SingleNodeContainer(BoundaryContainer):
    """Container with exactly one, permanently active BoundaryNode.

    advance() is a no-op — there is nowhere to transition to.
    evaluate() returns PASS because constraint enforcement is delegated to guards
    via the injection pool; the container itself has no veto.
    """

    def __init__(self, node: BoundaryNode) -> None:
        self._node = node

    def get_active_node(self) -> BoundaryNode:
        return self._node

    def get_all_nodes(self) -> list[BoundaryNode]:
        return [self._node]

    def evaluate(self, obs: Observation, action: ActionProposal) -> GuardResult:
        # Constraint enforcement is handled by guards through the injection pool.
        # Containers do not duplicate that logic — return PASS as a no-op result.
        return GuardResult.success(
            guard_name=f"SingleNodeContainer({self._node.node_id})",
            layer=None,  # type: ignore[arg-type]
        )

    def advance(self, obs: Observation | None = None) -> str | None:
        # Single node — no transition; return current node ID to signal "still active".
        return self._node.node_id

    def reset(self) -> None:
        pass

    def snapshot(self) -> dict[str, Any]:
        return {}

    def restore(self, state: dict[str, Any]) -> None:
        pass
