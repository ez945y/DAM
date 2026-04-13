from __future__ import annotations

from dam.types.action import ActionProposal, ValidatedAction
from dam.types.observation import Observation


class MockSourceAdapter:
    def __init__(self, obs_sequence: list[Observation]) -> None:
        self._queue = list(obs_sequence)
        self._index = 0

    def read(self) -> Observation:
        if self._index >= len(self._queue):
            raise StopIteration("MockSourceAdapter: no more observations")
        obs = self._queue[self._index]
        self._index += 1
        return obs

    def reset(self) -> None:
        self._index = 0


class MockPolicyAdapter:
    def __init__(self, actions: list[ActionProposal]) -> None:
        self._queue = list(actions)
        self._index = 0

    def predict(self, obs: Observation) -> ActionProposal:
        if self._index >= len(self._queue):
            raise StopIteration("MockPolicyAdapter: no more actions")
        action = self._queue[self._index]
        self._index += 1
        return action

    def reset(self) -> None:
        self._index = 0


class MockSinkAdapter:
    def __init__(self) -> None:
        self.received: list[ValidatedAction] = []

    def write(self, action: ValidatedAction) -> None:
        self.received.append(action)

    def clear(self) -> None:
        self.received.clear()
