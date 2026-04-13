from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any


class CallbackRegistry:
    def __init__(self) -> None:
        self._registry: dict[str, Callable[..., Any]] = {}
        self._sig_cache: dict[str, list[str]] = {}

    def register(
        self,
        name: str,
        fn: Callable[..., Any],
        valid_keys: set[str] | None = None,
    ) -> None:
        if name in self._registry:
            raise ValueError(f"Callback '{name}' is already registered")
        sig = inspect.signature(fn)
        param_names = [p for p in sig.parameters if p != "self"]
        if valid_keys is not None:
            unknown = set(param_names) - valid_keys
            if unknown:
                raise ValueError(f"Callback '{name}' has unknown parameter(s): {unknown}")
        self._registry[name] = fn
        self._sig_cache[name] = param_names

    def get(self, name: str) -> Callable[..., Any]:
        if name not in self._registry:
            raise KeyError(
                f"Callback '{name}' not found. Registered: {sorted(self._registry.keys())}"
            )
        return self._registry[name]

    def get_params(self, name: str) -> list[str]:
        return self._sig_cache[name]

    def list_all(self) -> list[str]:
        return sorted(self._registry.keys())


# Module-level singleton instance
_registry = CallbackRegistry()


def get_global_registry() -> CallbackRegistry:
    return _registry
