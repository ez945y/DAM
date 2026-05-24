# Command-Line Interface

DAM installs a `dam` console command (entry point `dam.cli:main`). When the
package is installed (`pip install -e .` / `make setup`) use `dam …`; in any
checkout it is equivalent to `python -m dam.cli …`.

```text
dam <command> [options]

  validate   schema-validate Stackfile(s)
  callbacks  list built-in boundary callbacks
  run        run a headless control loop from a Stackfile
  replay     summarise a loopback .mcap session
  doctor     check environment / dependency readiness
  inspect    print the resolved Stackfile graph
  help       show help for the CLI or a subcommand
```

`dam --version` prints the package version. `dam` with no command prints
help and exits non-zero.

### Default Stackfile

`validate`, `run`, and `inspect` fall back to the **`.dam_stackfile.yaml`**
convention file (repo root) when no `STACK` argument is given — the same
file `make run` / the host use. So from a project with that file:

```bash
dam inspect          # == dam inspect .dam_stackfile.yaml
dam validate         # == dam validate .dam_stackfile.yaml
dam run --cycles 50
```

### Via make

The venv must be active for a bare `dam`; otherwise use the Make wrappers
(they call `.venv/bin/dam` directly, no activation needed):

```bash
make dam ARGS="inspect"                 # any subcommand
make dam ARGS="callbacks --layer L1"
make validate                           # validate all example Stackfiles (CI gate)
```

---

## `dam validate`

Schema-validates one or more Stackfiles. Exit code is `1` if **any** file
fails — this is the CI stackfile gate.

```bash
dam validate                              # → .dam_stackfile.yaml
dam validate examples/stackfiles/test.yaml
dam validate examples/stackfiles/*.yaml
```

```text
OK    examples/stackfiles/demo.yaml
FAIL  broken.yaml
      YAMLError: ...

5/6 valid
```

CI runs `dam validate examples/stackfiles/*.yaml` so a malformed example
fails the build.

## `dam callbacks`

Lists every built-in boundary callback grouped by guard layer (name,
description), straight from the registry catalog.

```bash
dam callbacks                 # all, grouped L0→L3
dam callbacks --layer L1      # one layer only
dam callbacks --json          # machine-readable (name, layer, description, params)
```

## `dam run`

Builds the runtime from a Stackfile and runs a headless control loop.

```bash
dam run examples/stackfiles/demo.yaml --cycles 200 --task demo
```

| Option | Default | Description |
|---|---|---|
| `--task` | `default` | Task to start |
| `--cycles` | `100` | Cycles to run (`-1` = unbounded, Ctrl-C to stop) |

Exit code `1` if the runtime ends in the `EMERGENCY` state or fails to
build/connect. Use `make run` instead when you want the web console.

## `dam replay`

Summarises the guard decisions recorded in a loopback `.mcap` session
(post-incident triage) — cycle count, decision tally, and the cycles where
violations or clamps occurred.

```bash
dam replay data/robot/sessions/session-001.mcap --limit 20
```

Requires the optional `mcap` dependency (`pip install 'dam[torch]'`).

## `dam doctor`

Checks that the runtime dependencies are importable in the current
interpreter — `dam`, `dam_rs` (+ `ImageHub`), `pinocchio`, `torch`, `mcap`,
`lerobot`, `rclpy`. Required components missing → exit `1`; optional ones
report `WARN`. Use it to diagnose a broken venv (e.g. a missing
`dam_rs.ImageHub`).

```bash
dam doctor
```

## `dam inspect`

Loads a Stackfile and prints the resolved graph — safety settings, active
guards, every boundary (layer, container type, callback, fallback, param
keys), tasks, and the fallback escalation chain. Pure config read; no
hardware, registry, or runtime is started.

```bash
dam inspect examples/stackfiles/manipulation_safe.yaml
```

## `dam help`

```bash
dam help            # top-level help
dam help validate   # help for one subcommand
```

---

## Adding a subcommand

Subcommands live in [`dam/cli.py`](https://github.com/ez945y/DAM/blob/main/dam/cli.py):
add a `_cmd_<name>(args) -> int` function, register a subparser in
`build_parser()`, and add it to the `choices` map so `dam help <name>` works.
Return `0` on success, non-zero on failure.
