import numpy as np

from dam.decorators import guard as guard_decorator
from dam.guard.base import Guard
from dam.guard.builtin.motion import MotionGuard
from dam.injection.pool import RUNTIME_POOL_KEYS
from dam.injection.static import precompute_injection
from dam.types.result import GuardResult


def test_runtime_pool_keys_exist():
    assert "obs" in RUNTIME_POOL_KEYS
    assert "action" in RUNTIME_POOL_KEYS
    assert "cycle_id" in RUNTIME_POOL_KEYS


def test_precompute_splits_correctly():
    """Verify precompute_injection partitions params into runtime vs static.

    Uses a synthetic guard whose check() signature exposes both runtime
    (``obs``, ``action``) and static (``upper``, ``lower``) keys so we don't
    rely on a particular builtin guard's current parameter list.
    """

    class _SyntheticGuard(Guard):
        expected_decisions = frozenset()

        def check(
            self,
            obs=None,
            action=None,
            upper=None,
            lower=None,
        ) -> GuardResult:
            return GuardResult.success(guard_name="synthetic", layer=self.get_layer())

    decorated = guard_decorator("L1")(_SyntheticGuard)
    g = decorated()
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


def test_motion_guard_signature_advertises_pipeline_keys():
    """MotionGuard delegates to boundary callbacks; its check() signature
    only declares the runtime + dt keys the pipeline needs."""
    decorated = guard_decorator("L1")(MotionGuard)
    g = decorated()
    precompute_injection(g, {"dt": 0.02})
    # Pipeline-driven guards expose ``active_containers`` so the runtime can
    # hand them the active L1 boundaries' constraints.
    assert "active_containers" in g._runtime_keys
    assert "obs" in g._runtime_keys
    assert "action" in g._runtime_keys
    assert g._static_kwargs.get("dt") == 0.02
