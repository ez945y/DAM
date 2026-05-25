# Command Cheat Sheet

Use this page when you know what you want to do and just need the command.

If you are unsure, use this order: validate the Stackfile, inspect what it resolves to, then run a bounded task.

## Setup And Run

| Goal | Command |
|------|---------|
| First-time setup | `make setup` |
| Start backend and console | `make run` |
| Open docs preview | `make docs` |
| Show Makefile help | `make help` |

## Stackfiles

| Goal | Command |
|------|---------|
| Validate all examples | `make validate` |
| Validate one Stackfile | `.venv/bin/dam validate examples/stackfiles/demo.yaml` |
| Inspect one Stackfile | `.venv/bin/dam inspect examples/stackfiles/demo.yaml` |
| Run demo task headlessly | `.venv/bin/dam run examples/stackfiles/demo.yaml --cycles 200 --task demo` |
| Run short SO-ARM101 hardware check | `.venv/bin/dam run examples/stackfiles/so101.yaml --cycles 50 --task soarm101` |

## Tests And Docs

| Goal | Command |
|------|---------|
| Full test suite | `make test` |
| Python tests only | `make test-py` |
| Rust tests only | `make test-rs` |
| Frontend tests only | `make test-ui` |
| Documentation quality gate | `make docs-check` |

## Diagnostics

| Goal | Command |
|------|---------|
| Environment readiness | `.venv/bin/dam doctor` |
| List built-in callbacks | `.venv/bin/dam callbacks` |
| List L1 callbacks | `.venv/bin/dam callbacks --layer L1` |

If a command fails during first setup, go to [Troubleshooting](troubleshooting.md).
