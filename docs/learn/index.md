# Learn DAM

Use this path when you are new to DAM or teaching it to someone else. The goal is to learn how to run, configure, observe, and safely iterate on a robot deployment before diving into internals.

## Choose Your Starting Point

| If you want to... | Start here | You are done when... |
|-------------------|------------|----------------------|
| Run DAM locally | [Quick Start](../getting-started/quickstart.md) | `make run` opens the console and the backend is reachable |
| Install dependencies carefully | [Installation](../installation.md) | `make setup` finishes and import checks pass |
| Understand the mental model | [Complete Tutorial](tutorial.md) | You can explain pass, clamp, reject, and fallback in your own words |
| Configure a deployment | [Stackfile Walkthrough](../getting-started/stackfile-walkthrough.md) | You can validate an example Stackfile and point to the active boundaries |
| Watch a run | [Console Overview](../console.md) | You can find guard decisions, latency, and the active task |
| Fix a blocked first run | [Troubleshooting](../getting-started/troubleshooting.md) | You can identify whether the issue is setup, ports, validation, or task naming |
| Look up terms | [Glossary](glossary.md) | You can resolve unfamiliar DAM vocabulary without reading source code |

## Recommended Learning Order

1. Install and run the demo stack with the quick start.
2. Read the guard stack explanation in the tutorial.
3. Validate one example Stackfile with `make validate`, then read it with the Stackfile walkthrough.
4. Open the console and identify one pass, clamp, or reject event.
5. Change one boundary value in a copy of an example Stackfile, validate it, and explain the expected safety effect.

## What To Avoid At First

Do not start by reading the Rust data plane, MCAP parser, or service router internals. Those are useful after you know the user workflow. For first-pass learning, prefer the Stackfile, console, and guard decision concepts.

## Checkpoint

You are ready to move from learning to integration when you can answer:

- What system produces the proposed action?
- Which boundaries are always active?
- Which task boundaries are active only during a task?
- What happens when a guard times out?
- Where would you look after a rejection: console, logs, or Stackfile?
