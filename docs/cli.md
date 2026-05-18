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
  help       show help for the CLI or a subcommand
```

`dam --version` prints the package version. `dam` with no command prints
help and exits non-zero.

---

## `dam validate`

Schema-validates one or more Stackfiles. Exit code is `1` if **any** file
fails — this is the CI stackfile gate.

```bash
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
dam run examples/stackfiles/demo.yaml --cycles 200 --task default
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
