# Documentation Workplan

This is the PM lane for making the MkDocs site easier to use and learn from. It favors reader outcomes over implementation exposure.

## Product Goal

Help a new DAM user go from "what is this?" to "I can run a demo, read a guard decision, edit a Stackfile safely, and know where to learn next" without reading source code.

## Operating Principles

- Start from user jobs: install, run, configure, monitor, debug, extend.
- Prefer examples and checkpoints over architecture detail.
- Keep implementation detail behind reference pages.
- Update one reader journey at a time, then verify navigation and commands.
- Log meaningful PM checkpoints with `scripts/log_writer.py`.

## Work Lanes

| Lane | Task | Completion indicator |
|------|------|----------------------|
| Learning path | Expose Learn pages in navigation and give newcomers a sequenced path | Done: Learning nav and learner landing page are visible |
| Quickstart | Make the first run path explicit and testable | Done: Quick Start names success signals, expected ports, validation, and troubleshooting |
| Stackfile education | Separate "how to use" from full schema detail | Done: Stackfile Walkthrough explains the no-hardware demo before the reference guide |
| Console workflow | Teach how to inspect pass, clamp, reject, and latency | Done: Console overview starts with operator questions and healthy signals |
| Troubleshooting | Add symptom-led fixes | Done: setup, ports, validation, model startup, and task-name issues have short recovery steps |
| Reference hygiene | Keep deep implementation detail discoverable but secondary | Done: first-run pages point to Stackfiles, commands, and console before architecture internals |
| Harness | Prevent repeated onboarding regressions | Done: `make docs-check` runs strict MkDocs plus command, safety wording, and learner Stackfile checks |
| Hardware readiness | Slow the transition from demo to physical robot | Done: hardware examples use bounded cycles and require independent stop readiness |
| Safety wording | Keep research-grade claims conditional and auditable | Done: docs use Safety Model language and harness blocks common overclaims |

## Definition Of Done

A documentation improvement is complete when:

- The target reader and job are named.
- The page has a clear next action.
- Commands include expected success signals.
- Examples match the current Stackfile schema.
- Related pages link forward and backward.
- A short log entry records what changed and how it was checked.

## Current Priority Queue

1. Keep `make docs-check` passing as the documentation quality gate.
2. Add stable console screenshots or sample event snippets when demo artifacts are available.
3. Review deep reference pages for outdated callback parameter names after each schema change.
4. Keep English and Chinese specification examples aligned when public commands change.
5. Continue logging PM checkpoints in `logs/pm_log.jsonl`.
