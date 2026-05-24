# Troubleshooting

Use this page when the first-run path does not behave as expected. Start with the symptom, try the shortest fix, then return to [Quick Start](quickstart.md).

## `make setup` Fails

| Symptom | Likely cause | Try this |
|---------|--------------|----------|
| `cargo: command not found` | Rust is not installed | Install Rust from [rustup.rs](https://rustup.rs/), restart the shell, run `make setup` again |
| Python import errors after setup | The virtual environment is incomplete | Run `make clean`, then `make setup` |
| npm install errors | Frontend dependencies did not install cleanly | Delete `dam-console/node_modules`, then run `make setup` |

## Console Does Not Open

| Symptom | Likely cause | Try this |
|---------|--------------|----------|
| `http://localhost:3000` does not load | Console server is not running | Keep `make run` running in the terminal and refresh |
| Console loads but looks disconnected | API server is not reachable | Open `http://localhost:8080/docs`; if it fails, restart `make run` |
| Port 8080 is busy | Another process owns the backend port | Stop the other process, then run `make run` again |

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
.venv/bin/dam run examples/stackfiles/test.yaml --cycles 50 --task soarm101
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
