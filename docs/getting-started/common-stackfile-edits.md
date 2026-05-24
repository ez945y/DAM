# Common Stackfile Edits

Use these edits after you can validate and inspect `examples/stackfiles/demo.yaml`. Make one change at a time, validate, then run.

## Workflow

```bash
cp examples/stackfiles/demo.yaml /tmp/dam-demo.yaml
.venv/bin/dam validate /tmp/dam-demo.yaml
.venv/bin/dam inspect /tmp/dam-demo.yaml
```

Expected result: validation reports `OK`, and inspect shows the task, guards, boundaries, and fallbacks you expect.

## Change The Active Task Boundaries

Find:

```yaml
tasks:
  demo:
    boundaries: [ood_welford, workspace, joint_position_limits, joint_velocity_limit, hardware_watchdog, host_health]
```

To temporarily focus on motion checks, remove `ood_welford` from the copied file:

```yaml
tasks:
  demo:
    boundaries: [workspace, joint_position_limits, joint_velocity_limit, hardware_watchdog, host_health]
```

Validate before running. If a boundary name appears in `tasks.demo.boundaries`, it must exist under `boundaries:`.

## Tighten Workspace Bounds

Find the `workspace` boundary:

```yaml
params:
  bounds: [[-0.4000, 0.4000], [-0.4000, 0.4000], [0.0200, 0.6000]]
```

Reduce one limit:

```yaml
params:
  bounds: [[-0.3000, 0.3000], [-0.4000, 0.4000], [0.0200, 0.6000]]
```

This makes the L1 workspace check stricter on the x axis.

## Switch Monitor To Enforce

The demo starts in monitor mode:

```yaml
safety:
  enforcement_mode: monitor
```

Use `enforce` only when you are ready for DAM to apply clamp/reject behavior:

```yaml
safety:
  enforcement_mode: enforce
```

For first learning runs, keep `monitor` until you understand the guard decisions you are seeing.

## Change A Policy Device

For a CPU-only run:

```yaml
policy:
  device: cpu
```

For Apple Silicon acceleration, use `mps` only if your local PyTorch install supports it:

```yaml
policy:
  device: mps
```

If policy startup fails after changing the device, switch back to `cpu` and validate the rest of the Stackfile first.

## Add A New Boundary Safely

1. Add the boundary under `boundaries:`.
2. Add its name to the intended task under `tasks:`.
3. Validate the file.
4. Inspect the file and confirm the boundary appears under the task.

Avoid editing fallback escalation, loopback rotation, or custom callbacks until the basic loop is working.
