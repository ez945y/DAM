from __future__ import annotations

from typing import TYPE_CHECKING

from dam.injection.pool import RUNTIME_POOL_KEYS, ConfigPool

if TYPE_CHECKING:
    from dam.guard.base import Guard


def precompute_injection(guard: Guard, config_pool: ConfigPool) -> None:
    """
    Split guard parameters into static (config) and runtime keys at startup.
    Called once per guard instance during GuardRuntime initialization.
    """
    param_names = guard.__class__._cached_param_names

    static_kwargs = {}
    runtime_keys = []

    for key in param_names:
        if key in RUNTIME_POOL_KEYS:
            runtime_keys.append(key)
        elif key in config_pool:
            static_kwargs[key] = config_pool[key]
        # Unknown keys that are optional (have defaults) are silently skipped.
        # Unknown keys without defaults will fail at check() call time — acceptable.

    guard._static_kwargs = static_kwargs
    guard._runtime_keys = runtime_keys
