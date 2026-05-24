# Stackfile Walkthrough

This walkthrough explains the demo Stackfile as a user-facing configuration, not as source code. Read this after [Quick Start](quickstart.md) and before the full [Stackfile Guide](../quick-stack.md).

## Start With A Known-Good File

Use the demo Stackfile:

```bash
.venv/bin/dam inspect examples/stackfiles/demo.yaml
```

Expected result: the output lists one task named `demo`, four guard layers, several boundaries, and MCAP loopback logging.

The same file is safe to validate without robot hardware:

```bash
.venv/bin/dam validate examples/stackfiles/demo.yaml
```

Expected result: `OK examples/stackfiles/demo.yaml`.

## Read The Stackfile In Six Parts

| Section | Question it answers | What to check first |
|---------|---------------------|---------------------|
| `hardware` | Where do observations come from, and where do validated actions go? | Demo uses a dataset source and a matching sink reference |
| `policy` | What proposes actions? | Demo uses an ACT policy on CPU |
| `safety` | How strict is the run? | Demo uses `monitor`, so it observes decisions without enforcing hardware changes |
| `guards` | Which layers are active? | L0, L1, L2, and L3 are declared explicitly |
| `boundaries` | What safety rules can fire? | Look for `layer`, `callback`, `params`, and optional `fallback` |
| `tasks` | Which boundaries run together? | The `demo` task activates the named boundary list |

## Follow One Boundary

Find the `workspace` boundary:

```yaml
workspace:
  layer: L1
  type: single
  nodes:
    - callback: workspace
      params:
        bounds: [[-0.4000, 0.4000], [-0.4000, 0.4000], [0.0200, 0.6000]]
```

Read it as: during any task that includes `workspace`, the L1 motion layer checks whether the robot stays inside the configured 3D box.

The important learning pattern is:

1. `tasks.demo.boundaries` includes `workspace`.
2. `boundaries.workspace.layer` says L1 owns the check.
3. `callback: workspace` names the built-in rule.
4. `params.bounds` gives the rule its limits.

## Make One Safe Edit

Copy the demo file before editing:

```bash
cp examples/stackfiles/demo.yaml /tmp/dam-demo.yaml
```

Change only one value, such as `safety.enforcement_mode` from `monitor` to `enforce` or a workspace bound from `0.4000` to `0.3000`.

Validate the copy:

```bash
.venv/bin/dam validate /tmp/dam-demo.yaml
```

Expected result: `OK /tmp/dam-demo.yaml`. If validation fails, undo the last edit and inspect indentation first.

## Run The Headless Demo

Run a bounded headless loop:

```bash
.venv/bin/dam run examples/stackfiles/demo.yaml --cycles 200 --task demo
```

Expected result: DAM loads the Stackfile, starts task `demo`, runs the requested cycles, and exits without requiring physical robot hardware.

## What To Defer

You do not need these on the first pass:

- Custom Python callbacks
- Custom fallback classes
- Graph boundaries
- Rust data-plane internals
- MCAP channel schema details
- Full service API payloads

Use [Common Stackfile Edits](common-stackfile-edits.md) when you are ready to make safe changes. Use [Stackfile Guide](../quick-stack.md) when you need the full field reference.

## When To Move To Real Hardware

The first hardware-oriented example is `examples/stackfiles/test.yaml`, not the demo file.

Key differences:

| Demo file | Hardware file |
|-----------|---------------|
| `sources.main.type: dataset` | `sources.arm.type: motor` |
| `sinks.main.ref: sources.main` | `sinks.command.ref: sources.arm` |
| `policy.device: cpu` | `policy.device: mps` |
| `enforcement_mode: monitor` | `enforcement_mode: enforce` |
| Task name: `demo` | Task name: `soarm101` |

Use the hardware file only after you have a connected SO-ARM101, correct serial port, camera indexes, and a device setting that matches your machine. Follow [Hardware Readiness](hardware-readiness.md) before running physical hardware.

```bash
.venv/bin/dam inspect examples/stackfiles/test.yaml
.venv/bin/dam run examples/stackfiles/test.yaml --task soarm101
```

Do not use `--task default` for these examples; the task names are explicit in each Stackfile.
