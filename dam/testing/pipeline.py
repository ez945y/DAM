from __future__ import annotations

from dam.runtime.guard_runtime import GuardRuntime
from dam.testing.mocks import MockPolicyAdapter, MockSinkAdapter, MockSourceAdapter
from dam.types.action import ActionProposal
from dam.types.observation import Observation
from dam.types.risk import CycleResult


def run_pipeline(
    stackfile_path: str,
    obs_seq: list[Observation],
    actions: list[ActionProposal],
    task_name: str = "default",
) -> list[CycleResult]:
    runtime = GuardRuntime.from_stackfile(stackfile_path)
    source = MockSourceAdapter(obs_seq)
    policy = MockPolicyAdapter(actions)
    sink = MockSinkAdapter()
    runtime.register_source("main", source)
    runtime.register_policy(policy)
    runtime.register_sink(sink)

    if task_name in runtime._task_config:
        runtime.start_task(task_name)
    results = []
    for _ in range(len(obs_seq)):
        result = runtime.step()
        results.append(result)
    runtime.stop_task()
    return results
