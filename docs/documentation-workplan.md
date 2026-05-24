# Documentation Workplan

This is the PM lane for making the MkDocs site easier to use and learn from. It favors reader outcomes over implementation exposure.

## Product Goal

Help a new DAM user go from "what is this?" to "I can run a demo, read a guard decision, edit a Stackfile safely, and know where to learn next" without reading source code.

## Operating Principles

- Start from user jobs: install, run, configure, monitor, debug, extend.
- Prefer examples and checkpoints over architecture detail.
- Keep implementation detail behind reference pages.
- Update one reader journey at a time, then verify navigation and commands.
- Log meaningful PM checkpoints with `harness/docs/log_writer.py`.

## Work Lanes

| Lane | Task | Completion indicator |
|------|------|----------------------|
| Learning path | Expose Learn pages in navigation and give newcomers a sequenced path | The nav has a Learning section, and the first page links to install, tutorial, stackfiles, console, and glossary |
| Quickstart | Make the first run path explicit and testable | A user can identify prerequisites, run command, expected ports, and verification command in under 5 minutes |
| Stackfile education | Separate "how to use" from full schema detail | The guide opens with a minimal runnable example, then explains common edits before field reference |
| Console workflow | Teach how to inspect pass, clamp, reject, and latency | The console page answers "what should I click or read after an event?" without requiring API knowledge |
| Troubleshooting | Add symptom-led fixes | Common setup, validation, port, and task-name issues have short recovery steps |
| Reference hygiene | Keep deep implementation detail discoverable but secondary | Reference pages are linked after workflows, not used as the main onboarding path |

## Definition Of Done

A documentation improvement is complete when:

- The target reader and job are named.
- The page has a clear next action.
- Commands include expected success signals.
- Examples match the current Stackfile schema.
- Related pages link forward and backward.
- A short log entry records what changed and how it was checked.

## Current Priority Queue

1. Learning path visibility: add a Learning nav section and a learner landing page.
2. Quickstart validation: align install/run/validate commands with current Makefile and CLI behavior.
3. Stackfile guide cleanup: keep the walkthrough beginner-friendly, then decide whether to split `quick-stack.md` further.
4. Console task flow: show how to use the console during a safety event.
5. Continue reducing reference noise: split or archive older implementation-heavy pages that are not part of the user learning path.
