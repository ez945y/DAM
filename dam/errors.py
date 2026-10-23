"""Public SDK error categories; original failures remain in ``__cause__``."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import ParamSpec, TypeVar


class DamError(Exception):
    """Base for operational SDK failures (guard rejection is a result)."""


class ConfigurationError(DamError, ValueError):
    """The session could not be constructed from its configuration."""


class InputContractError(DamError, ValueError):
    """The call does not satisfy the declared input contract."""


class SessionClosedError(DamError, RuntimeError):
    """The requested operation requires an open session."""


class OperationTimeoutError(DamError, TimeoutError):
    """An operation exceeded its wait/cleanup deadline."""


class ExecutionError(DamError, RuntimeError):
    """Execution or output conversion failed outside a guard result."""


_P = ParamSpec("_P")
_T = TypeVar("_T")


def _classify(error: type[DamError]) -> Callable[[Callable[_P, _T]], Callable[_P, _T]]:
    def decorate(fn: Callable[_P, _T]) -> Callable[_P, _T]:
        @wraps(fn)
        def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _T:
            try:
                return fn(*args, **kwargs)
            except DamError:
                raise
            except TimeoutError as exc:
                raise OperationTimeoutError(str(exc)) from exc
            except Exception as exc:
                raise error(str(exc)) from exc

        return wrapped

    return decorate
