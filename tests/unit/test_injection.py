import numpy as np

from dam.decorators import guard as guard_decorator
from dam.guard.builtin.motion import MotionGuard
from dam.injection.pool import RUNTIME_POOL_KEYS
from dam.injection.static import precompute_injection


def test_runtime_pool_keys_exist():
    assert "obs" in RUNTIME_POOL_KEYS
    assert "action" in RUNTIME_POOL_KEYS
    assert "cycle_id" in RUNTIME_POOL_KEYS


def test_precompute_splits_correctly():
    KG = guard_decorator("L2")(MotionGuard)
    g = KG()
    config_pool = {
        "upper": np.ones(6),
        "lower": -np.ones(6),
    }
    precompute_injection(g, config_pool)
    assert "upper" in g._static_kwargs
    assert "lower" in g._static_kwargs
    assert "obs" in g._runtime_keys
    assert "action" in g._runtime_keys
    assert "obs" not in g._static_kwargs
