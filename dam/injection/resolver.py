from __future__ import annotations

from typing import Any

from dam.injection.pool import ConfigPool, RuntimePool
from dam.registry.callback import CallbackRegistry


class InjectionResolver:
    def __init__(self, callback_registry: CallbackRegistry) -> None:
        self._callback_registry = callback_registry

    def call_callback(
        self,
        name: str,
        runtime_pool: RuntimePool,
        config_pool: ConfigPool,
    ) -> Any:
        fn = self._callback_registry.get(name)
        param_names = self._callback_registry.get_params(name)
        merged = {**config_pool, **runtime_pool}  # runtime wins on collision
        kwargs = {k: merged[k] for k in param_names if k in merged}
        return fn(**kwargs)
