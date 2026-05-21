from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar

from dam.types.result import GuardDecision

if TYPE_CHECKING:
    from dam.guard.layer import GuardLayer
    from dam.types.result import GuardResult


class Guard(ABC):
    """Abstract guard contract.

    Subclasses must declare ``expected_decisions``: the set of GuardDecision
    verbs this guard may emit. FAULT is implicitly always allowed (any guard
    can surface an internal exception via ``GuardResult.fault``); subclasses
    that want to forbid FAULT should not — it is a safety invariant.

    The expected_decisions set is consumed by stage builders and contract
    tests, not by the hot path, so declaring it is documentation + checkable
    invariant, not runtime overhead.
    """

    # Concrete guards MUST override this with the actual decisions they emit.
    expected_decisions: ClassVar[frozenset[GuardDecision]]

    # Set by @dam.guard decorator at class definition time
    _guard_layer: GuardLayer
    _cached_param_names: list[str]
    _guard_name: str | None = None

    # Set by InjectionResolver at startup
    _static_kwargs: dict[str, Any]
    _runtime_keys: list[str]

    if TYPE_CHECKING:
        check: Any
    else:

        @abstractmethod
        def check(self, **kwargs: Any) -> GuardResult: ...

    def get_layer(self) -> GuardLayer:
        return self.__class__._guard_layer

    def get_name(self) -> str:
        return self._guard_name or self.__class__.__name__

    def set_name(self, name: str) -> None:
        self._guard_name = name

    def on_violation(self, result: GuardResult) -> None:  # noqa: B027
        """Handle violation event. Override in subclass if needed."""
        pass

    def preflight(self, **kwargs: Any) -> None:  # noqa: B027
        """Called before first execution cycle for heavy init (e.g. model loading)."""
        pass
