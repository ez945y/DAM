# Adversarial Testing

DAM is a safety middleware, so "the happy path works" is not enough — the
interesting question is whether a guard can be **bypassed** by a hostile or
degenerate input. This page states the threat model and how it is exercised
in CI.

## Threat model

The adversary controls the policy output and can influence observations
(sensor spoofing, numeric corruption, timing). They want an unsafe action
to reach hardware while every guard reports `PASS`.

| # | Threat | Invariant under test |
|---|--------|----------------------|
| T1 | **Non-finite injection** — NaN/Inf in joint state, EE pose, twist | A non-finite safety input is a *violation*, never a `PASS` (`nan > limit` is `False`, so naive checks silently pass) |
| T2 | **Boundary skimming** — values a float-epsilon outside a limit | A value past the limit is blocked; the limit itself is handled per the documented inclusive/exclusive rule |
| T3 | **Fault as bypass** — a guard raises / times out | Faults are aggregated as ≥ `REJECT`; the action never reaches the sink (fail-to-reject) |
| T4 | **Aggregator leakage** — one strict guard among many permissive | The most restrictive decision always wins; a single `REJECT`/`FAULT` dominates any number of `PASS`/`CLAMP` |

## Coverage

**v1 — deterministic regression (blocks merge).**
`tests/safety/test_adversarial_regression.py` encodes one or more concrete
attacks per threat above and asserts the safe outcome. It runs in the
`safety` CI job alongside the existing regression suites, so a regression
that re-opens a bypass fails the build.

T1 drove a real hardening change: `joint_position_limits`,
`joint_velocity_limit`, `keep_out_zone`, `orientation_limit`, and
`cartesian_velocity_limit` previously returned `PASS` on NaN-injected
inputs. They now treat any non-finite safety input as a violation
(`dam/boundary/callbacks/_helpers.py::_all_finite`).

**Deferred — randomized fuzz tier (nightly, non-blocking).**
A Hypothesis-driven tier (`tests/property/`) that searches the input space
for new bypasses, tracked as a trend rather than a merge gate. `hypothesis`
is already a dev dependency; this is an incremental follow-up, not part of
v1.

## Adding an attack

1. Pick the threat category; add a test to
   `tests/safety/test_adversarial_regression.py` asserting the *safe*
   outcome (never `PASS`).
2. If it reveals a real bypass, fix the guard/callback so the input is
   treated as a violation — then the test locks the fix in place.

A guard that cannot be bypassed by these inputs is the product claim; this
suite is the evidence.
