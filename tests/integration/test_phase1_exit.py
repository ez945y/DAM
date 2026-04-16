"""Phase 1 exit criterion: dam.step() must complete a full sense->validate->act cycle."""

import tempfile
import textwrap

import numpy as np

from dam.runtime.guard_runtime import GuardRuntime
from dam.testing.mocks import MockPolicyAdapter, MockSinkAdapter, MockSourceAdapter
from dam.types.action import ActionProposal
from dam.types.observation import Observation
from dam.types.risk import CycleResult

PHASE1_STACKFILE = textwrap.dedent("""\
    version: "1"
    guards:
      builtin:
        motion:
          enabled: true
    boundaries:
      main_boundary:
        layer: L2
        type: single
        nodes:
          - node_id: default
            fallback: hold_position
            params:
              upper: [3.14, 3.14, 3.14, 3.14, 3.14, 3.14]
              lower: [-3.14, -3.14, -3.14, -3.14, -3.14, -3.14]
              bounds: [[-0.8, 0.8], [-0.8, 0.8], [0.0, 1.2]]
    tasks:
      test_task:
        boundaries: [main_boundary]
    safety:
      always_active: []
      control_frequency_hz: 50.0
""")


def make_obs(ts=0.0):
    return Observation(
        timestamp=ts,
        joint_positions=np.zeros(6),
        joint_velocities=np.zeros(6),
        end_effector_pose=np.array([0.1, 0.1, 0.5, 0.0, 0.0, 0.0, 1.0]),
    )


def make_action():
    return ActionProposal(
        target_joint_positions=np.array([0.1, 0.1, 0.1, 0.1, 0.1, 0.1]),
        confidence=0.9,
    )


def test_phase1_exit_criterion():
    """dam.step() in pure Python mock environment completes full sense->validate->act cycle."""
    path = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    path.write(PHASE1_STACKFILE)
    path.close()

    obs1, obs2, obs3 = make_obs(0.0), make_obs(0.02), make_obs(0.04)
    action1, action2, action3 = make_action(), make_action(), make_action()

    runtime = GuardRuntime.from_stackfile(path.name)
    runtime.register_source("main", MockSourceAdapter([obs1, obs2, obs3]))
    runtime.register_policy(MockPolicyAdapter([action1, action2, action3]))
    sink = MockSinkAdapter()
    runtime.register_sink(sink)

    runtime.start_task("test_task")
    results = []
    for _ in range(3):
        result = runtime.step()
        assert isinstance(result, CycleResult)
        assert result.risk_level is not None
        results.append(result)
    runtime.stop_task()

    # At least some actions reached the sink (not all rejected)
    assert len(sink.received) > 0
    assert len(results) == 3
