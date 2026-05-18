# Library API

Besides the `dam` CLI and the web console, DAM can be embedded as a library.
The public surface is intentionally small and stable:

```python
import dam

dam.build_runner(stack, *, ros2_node=None) -> dam.Runner
dam.run(stack, *, task="default", cycles=100, ros2_node=None) -> dam.RunSummary
dam.Runner          # = the runner ABC (lifecycle: connect/verify/start/stop/shutdown)
dam.RunnerStatus    # IDLE / STARTING / RUNNING / PAUSED / STOPPING / STOPPED / EMERGENCY
dam.RunSummary      # frozen: .status (str), .cycles (int), .emergency (bool)
```

Built-in callbacks, fallbacks, and guards are auto-registered for you.

## Managed loop

The full lifecycle in one call — build → connect → verify → start → wait
for a terminal state → shutdown:

```python
import dam

summary = dam.run("examples/stackfiles/demo.yaml", cycles=200)
print(summary.status, summary.cycles)
if summary.emergency:
    raise SystemExit("runtime ended in EMERGENCY")
```

`cycles=-1` runs unbounded until stopped or faulted. Build/connect failures
raise; `KeyboardInterrupt` stops the runner and shuts down before re-raising.

## Manual control

When you need to drive the lifecycle yourself (custom loop, pausing,
inspecting the runner):

```python
import dam

runner = dam.build_runner("examples/stackfiles/demo.yaml")  # built, not connected
runner.connect()
runner.verify()
runner.start(task="default", n_cycles=50)
try:
    while runner.status not in (dam.RunnerStatus.STOPPED, dam.RunnerStatus.EMERGENCY):
        ...  # observe runner.cycle_count / runner.status
finally:
    runner.shutdown()
```

The `dam run` CLI subcommand is a thin shell over `dam.run` — same
behaviour, same exit semantics (exit 1 on `EMERGENCY`).
