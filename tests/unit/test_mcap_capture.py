import os
import tempfile

import numpy as np

from dam.logging.mcap_capture import MCAPContextCapture
from dam.types.observation import Observation


def make_obs(ts=0.0):
    return Observation(
        timestamp=ts,
        joint_positions=np.zeros(6),
        joint_velocities=np.zeros(6),
        end_effector_pose=np.zeros(7),
    )


def test_capture_disabled_returns_none():
    cap = MCAPContextCapture(capture_on_violation=False)
    cap.record(make_obs())
    result = cap.capture_violation("test reason")
    assert result is None


def test_capture_creates_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, "test_capture")
        cap = MCAPContextCapture(output_path=out, capture_on_violation=True)
        for i in range(10):
            cap.record(make_obs(ts=float(i) * 0.02))
        path = cap.capture_violation("workspace breach")
        assert path is not None
        assert os.path.exists(path)


def test_capture_contains_observations():
    import pickle

    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, "test")
        cap = MCAPContextCapture(output_path=out, window_sec=5.0, hz=50.0)
        for i in range(20):
            cap.record(make_obs(ts=float(i) * 0.02))
        path = cap.capture_violation("test")
        if path.endswith(".pkl"):
            with open(path, "rb") as f:
                data = pickle.load(f)
            assert len(data["observations"]) == 20
            assert data["reason"] == "test"


def test_ring_buffer_limits_size():
    """Ring buffer should not grow unboundedly."""
    cap = MCAPContextCapture(window_sec=1.0, hz=10.0)  # max 10+10 entries
    for i in range(200):
        cap.record(make_obs(ts=float(i) * 0.1))
    assert cap._bus.len() <= 20  # capped by maxlen
