# Troubleshooting

Use this page when the first-run path does not behave as expected. Start with the symptom, try the shortest fix, then return to [Quick Start](quickstart.md).

## `make setup` Fails

| Symptom | Likely cause | Try this |
|---------|--------------|----------|
| `cargo: command not found` | Rust is not installed | Install Rust from [rustup.rs](https://rustup.rs/), restart the shell, run `make setup` again |
| Python import errors after setup | The virtual environment is incomplete | Run `.venv/bin/python -m dam.cli doctor`, then `make setup` to restore missing dependencies |
| npm install errors | Frontend dependencies did not install cleanly | Delete `dam-console/node_modules`, then run `make setup` |

## Console Does Not Open

`make dev` and `make run` check their service ports before startup. If a port is
occupied, startup stops with an explanation and leaves the existing service
running. Stop the owning service yourself after identifying it; on macOS/Linux,
`lsof -nP -iTCP:8080 -sTCP:LISTEN` shows the backend port owner (use `3000` for
the console). `make run` also checks for the frontend build before starting the
backend; run `make build` if it is missing.

Both launch modes wait for HTTP responses before printing the ready banner.
Each readiness check has a 60-second deadline; allow more time for a slow first
frontend compilation with `DAM_STARTUP_TIMEOUT=120 make dev` (1–3600 seconds).
If either service exits unexpectedly, the launcher reports its exit status and
stops the remaining services it started.

Each invocation prints its log directory under `logs/dev.*` or `logs/run.*`.
The separate `backend.log` and `frontend.log` files remain available after exit;
startup failures also print the latest output. Use `tail -f` on the printed path
to follow ongoing output. Ctrl+C stops the processes from that invocation,
including frontend child processes; unrelated services remain running.
Shutdown allows 10 seconds for graceful termination, then terminates any
remaining processes in the groups created by that invocation.

| Symptom | Likely cause | Try this |
|---------|--------------|----------|
| `http://localhost:3000` does not load | Console server is not running | Keep `make run` running in the terminal and refresh |
| Console loads but looks disconnected | API server is not reachable | Open `http://localhost:8080/docs`; if it fails, restart `make run` |
| Port 8080 is busy | Another process owns the backend port | Stop the other process, then run `make run` again |

## Commands Fail After Moving The Project

Virtual-environment console scripts can retain the old absolute Python path.
If `.venv/bin/python` still works, use:

```bash
make dam ARGS="doctor"
make validate
make docs-check
```

These targets invoke modules through the current project interpreter. The
equivalent CLI form is `.venv/bin/python -m dam.cli <command>`. If the interpreter
itself is missing or cannot import project dependencies, run `make setup`.
`init.sh` resolves the checkout from its own location and checks the project's
Python, so it can also be invoked from another working directory.

## Stackfile Validation Fails

Validate one file first:

```bash
.venv/bin/dam validate examples/stackfiles/demo.yaml
```

Expected result: `OK examples/stackfiles/demo.yaml`. If this fails before you edit anything, fix setup before changing Stackfiles.

| Symptom | Likely cause | Try this |
|---------|--------------|----------|
| YAML parse error | Indentation, colon, or list syntax issue | Undo the last edit and re-apply it one line at a time |
| Unknown callback | Boundary references a callback not registered in DAM | Use a built-in callback from [Boundary Callbacks](../boundary-callbacks.md) |
| Task not found | The `--task` name does not match the Stackfile | Inspect `tasks:` and run with that exact name |

## Demo Run Uses The Wrong Task

The demo Stackfile task is named `demo`:

```bash
.venv/bin/dam run examples/stackfiles/demo.yaml --cycles 200 --task demo
```

The hardware example task is named `soarm101`:

```bash
.venv/bin/dam run examples/stackfiles/so101.yaml --cycles 50 --task soarm101
```

Avoid `--task default` unless your own Stackfile actually defines a `default` task.

## First Model Run Is Slow

The no-hardware demo can still download or initialize model assets. If the first run pauses during policy setup, wait for the download/cache step to finish. Later runs should be faster.

## Still Stuck

Run:

```bash
.venv/bin/dam doctor
make validate
```

Expected result: required dependencies are available, and every example Stackfile reports `OK`.
Then use any remaining error text to decide whether the issue is environment setup, Stackfile syntax, or runtime connection.

## A Test Target Reports Missing Tools

`make test` requires every selected suite to run. Missing Rust tools or frontend
dependencies now produce a failed check instead of a skipped suite followed by
a success message. Install the missing dependencies, or explicitly select the
scope you need with `make test-py`, `make test-rs`, or `make test-ui`.
Rust-only and frontend-only test targets do not require a Python virtual
environment. The success summary refers only to the requested checks.
