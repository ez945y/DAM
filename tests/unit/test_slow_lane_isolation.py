"""Ownership and generation checks at the asynchronous hand-off."""

import threading
import time
from dataclasses import replace

import numpy as np
import pytest

from dam.guard.layer import GuardLayer
from dam.runtime.slow_lane import SlowLaneWorker, SlowSnapshot
from dam.types.action import ActionProposal, ValidatedAction
from dam.types.observation import Observation
from dam.types.result import GuardResult


def test_submit_detaches_all_nested_input_buffers():
    obs = Observation(
        timestamp=1.0,
        joint_positions=np.array([1.0]),
        joint_velocities=np.array([2.0]),
        end_effector_pose=np.arange(7),
        force_torque=np.arange(6),
        channels={"current": np.array([3.0])},
        images={"camera": np.ones((2, 2, 3), dtype=np.uint8)},
        metadata={"nested": {"values": [4.0]}},
    )
    proposal = ActionProposal(
        np.array([5.0]),
        target_joint_velocities=np.array([6.0]),
        target_ee_pose=np.arange(7),
        metadata={"nested": [7.0]},
    )
    action = ValidatedAction(np.array([8.0]), original_proposal=proposal)
    snapshot = SlowSnapshot(obs, action, "trace", 1, time.monotonic())
    seen = []
    worker = SlowLaneWorker(lambda s: seen.append(s) or [], frequency_hz=200)
    worker.submit(snapshot)
    obs.joint_positions[:] = 99
    obs.joint_velocities[:] = 99
    obs.end_effector_pose[:] = 99
    obs.force_torque[:] = 99
    obs.channels["current"][:] = 99
    obs.images["camera"][:] = 99
    obs.metadata["nested"]["values"][0] = 99
    action.target_joint_positions[:] = 99
    proposal.target_joint_positions[:] = 99
    proposal.target_joint_velocities[:] = 99
    proposal.target_ee_pose[:] = 99
    proposal.metadata["nested"][0] = 99
    worker.start()
    try:
        assert worker.wait_for_verdict(1, 2) is not None
        captured = seen[0]
        assert captured.obs.joint_positions.tolist() == [1.0]
        assert captured.obs.joint_velocities.tolist() == [2.0]
        assert captured.obs.end_effector_pose.tolist() == list(range(7))
        assert captured.obs.force_torque.tolist() == list(range(6))
        assert captured.obs.channels["current"].tolist() == [3.0]
        assert np.all(captured.obs.images["camera"] == 1)
        assert captured.obs.metadata["nested"]["values"] == [4.0]
        assert captured.action.target_joint_positions.tolist() == [8.0]
        original = captured.action.original_proposal
        assert original.target_joint_positions.tolist() == [5.0]
        assert original.target_joint_velocities.tolist() == [6.0]
        assert original.target_ee_pose.tolist() == list(range(7))
        assert original.metadata["nested"] == [7.0]
    finally:
        worker.stop()


def test_published_verdict_is_detached_from_producer_and_readers():
    result = GuardResult.success("test", GuardLayer.L0)
    result.metadata["values"] = [1]
    worker = SlowLaneWorker(lambda s: [result], frequency_hz=200)
    worker.submit(
        SlowSnapshot(Observation(1.0), ActionProposal(np.zeros(1)), "t", 1, time.monotonic())
    )
    worker.start()
    try:
        first = worker.wait_for_verdict(1, 2)
        assert first is not None
        result.metadata["values"][0] = 2
        first.results[0].metadata["values"][0] = 3
        second = worker.latest_verdict()
        assert second.results[0].metadata["values"] == [1]
        second.results.clear()
        assert len(worker.latest_verdict().results) == 1
    finally:
        worker.stop()


def test_repeated_submissions_never_observe_post_submit_mutation():
    entered, release = threading.Event(), threading.Event()

    def evaluate(snapshot):
        entered.set()
        assert release.wait(2)
        result = GuardResult.success("stress", GuardLayer.L0)
        result.metadata["value"] = snapshot.obs.metadata["nested"][0]
        result.metadata["pixel"] = int(snapshot.obs.images["camera"][0, 0])
        return [result]

    worker = SlowLaneWorker(evaluate, frequency_hz=1000)
    worker.start()
    try:
        for cycle in range(1, 41):
            entered.clear()
            release.clear()
            obs = Observation(
                1.0, images={"camera": np.full((2, 2), cycle)}, metadata={"nested": [cycle]}
            )
            worker.submit(
                SlowSnapshot(obs, ActionProposal(np.zeros(1)), "stress", cycle, time.monotonic())
            )
            assert entered.wait(1)
            obs.metadata["nested"][0] = -1
            obs.images["camera"][:] = -1
            release.set()
            verdict = worker.wait_for_verdict(cycle, 2)
            assert verdict.results[0].metadata == {"value": cycle, "pixel": cycle}
    finally:
        release.set()
        worker.stop()


def test_result_copy_failure_is_reported_as_fault():
    from dam.types.result import GuardDecision

    result = GuardResult.success("test", GuardLayer.L0)
    result.metadata["lock"] = threading.Lock()
    worker = SlowLaneWorker(lambda s: [result], frequency_hz=200)
    worker.submit(
        SlowSnapshot(Observation(1.0), ActionProposal(np.zeros(1)), "t", 1, time.monotonic())
    )
    worker.start()
    try:
        verdict = worker.wait_for_verdict(1, 2)
        assert verdict.results[0].decision == GuardDecision.FAULT
    finally:
        worker.stop()
