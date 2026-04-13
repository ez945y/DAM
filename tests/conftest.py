import numpy as np
import pytest

from dam.types.action import ActionProposal
from dam.types.observation import Observation


@pytest.fixture
def obs_nominal():
    return Observation(
        timestamp=0.0,
        joint_positions=np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
        joint_velocities=np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
        end_effector_pose=np.array([0.0, 0.0, 0.5, 0.0, 0.0, 0.0, 1.0]),
    )


@pytest.fixture
def action_nominal():
    return ActionProposal(
        target_joint_positions=np.array([0.1, 0.1, 0.1, 0.1, 0.1, 0.1]),
        confidence=0.9,
    )
