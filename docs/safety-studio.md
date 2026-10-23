# Safety Studio

Open **Setup → Safety Studio** (`/safety`) to assemble a safety plan, inspect its
requirements, review a switch, and keep a record of the result. A plan compiles to
an ordinary Stackfile; existing CLI and Python integrations can consume that file.

## Compose a plan

1. Import a saved Stackfile, import the Config draft, or paste a Stackfile.
2. Choose the explicit target task. It must exist in the base Stackfile.
3. Add components and their boundaries:

| Component | Activation |
| --- | --- |
| Baseline | Added to `safety.always_active` |
| Scene | Added to the selected task |
| Task | Added to the selected task |
| Mode | Added to the selected task |

Existing boundaries are preserved. Names must be distinct: components add
constraints rather than silently replacing definitions. The compiled view shows
which component supplied each rule. Static checks reject detectable incompatible
joint/workspace boxes; arbitrary constraints can still conflict at runtime.

Save the plan to keep an editable draft in `data/safety-plans/`. Saving a plan does
not modify the running configuration. Export the compiled configuration to the
Stackfile library to use it in an A/B comparison.

Edits are also saved in this browser's local storage, including unfinished
boundary JSON and declaration text. Refreshing restores this local draft, but
not compile results, switch previews, or coordinate-frame confirmation: review
and check again before applying. Local draft status is separate from saving a
plan to the library. Storage errors are shown with a retry action; keep the page
open while unsaved edits remain. A damaged draft is not overwritten on load or
retry; making a new edit replaces it with the current editor contents.

`examples/safety_plans/precision.json` is a complete, hardware-free authoring
example: one joint, a roomy base limit, and an additional precision-mode limit.
It declares its radians and joint observation explicitly. The local demo plan
is available as **demo-precision** in Saved plans.

## Check requirements

**Compile & check** reports each active boundary's requirements and identifies
inactive definitions, missing observations, unknown callbacks, unsuitable action
semantics, dimensions, units, and missing FK declarations. Optional input
contract fields make explicit the observation groups, joint ordering, native
action units, and solver coordinate frame used by an embedded integration.
Checks also reject non-finite values, malformed declarations, degenerate regions,
and detectable conflicts after converting degree-based limits to radians.

A `declaration-only` result establishes static completeness. It is not a live
sensor measurement, solver initialization result, or hardware readiness check.
The normal runtime preflight and guards still apply.

## Draw regions

Add a workspace boundary to edit a three-dimensional axis-aligned box, or a
keep-out boundary to edit spheres. Drag the projected shape along X/Y and edit
all coordinates numerically. Coordinates and radii are in metres. The visual
editor and generated boundary parameters update together.

All coordinates must already be in the FK solver's native frame. Confirm that
frame before compiling regions. This editor does not transform coordinates,
track moving objects, or infer a robot pose. Speed forms define a global
end-effector limit; they do not create a conditional spatial speed zone.

## Draft rules from language

The local draft parser accepts a deliberately bounded set of explicit Chinese or
English rules with values and units. Its response contains editable components
and a separate list of unresolved requests. It does not call a remote model or
invent thresholds from words such as "slower" or "nearby".

Review the generated parameters, add the components to the plan, and run the
requirement checks. Conditional, ambiguous, or unsupported clauses must be
resolved explicitly. Generating a draft never applies a configuration.
If any clause remains unresolved, the response contains no components to add;
clarify the request and generate it again.

## Preview and switch

A preview includes a parameter diff, candidate hash, current runtime version,
and one of four outcomes:

| Outcome | Meaning |
| --- | --- |
| `hot_reload` | Parameter-only change, same task, running loop, no active slow-lane worker |
| `restart_required` | Save a candidate while idle/stopped, then use the existing reload workflow |
| `blocked` | Resolve missing requirements, protected baseline changes, or another pending switch |
| `unchanged` | This configuration and task are already active |

Task changes and structural changes require reload. Protected `always_active`
boundaries cannot be removed or changed through a safety switch. The switch
service never starts, reconnects, resets, or moves hardware.

A preview expires after ten minutes and is invalidated by changes to the active
configuration, task, runtime, lifecycle state, or saved file. Editing the plan in
the UI also discards its preview. Review again before applying.

Hot reload first returns `pending`. Only the control loop's receipt establishes
`applied`. Preparation failure leaves the previous configuration active and
returns `rejected`. Applied changes preserve motion history, boundary phase, and
node timers. Stopping or starting a task invalidates queued task-bound switches.

The service saves a hot-reloaded configuration after acknowledgement. If another
editor changed the file, it does not overwrite that edit. History then reports
that the in-memory change is applied but not persisted. **Active hash** and
**Saved hash** intentionally remain separate. A saved structural candidate is
labelled `saved_restart_required`, never `applied`.

Unreadable or invalid saved configurations are reported and block new switches
until repaired. A storage failure does not hide an acknowledged runtime change:
it remains `applied` with `persisted: false`. If another runtime or configuration
has already replaced that change, its historical receipt is retained without
writing the superseded configuration back to disk.
The history label **Applied change was not saved** describes that receipt; it
does not establish that the configuration is still active. Check **Active hash**
for the currently reported configuration.

History provides a rollback preview using the previous configuration snapshot.
Rollback goes through the same validation, baseline checks, diff, and application
flow. It does not automatically resume the robot. History retains the latest 100
switches in `data/safety-switches/history.json`.

## Compare and retain regression cases

See [Safety A/B comparison and regression cases](safety-comparison.md) for the
supported recording formats and a runnable five-cycle example.

On **Comparison**, choose one MCAP recording and two saved Stackfiles. A/B
comparison evaluates the same recorded observations and proposed commands under
both configurations. Inspect decision distributions, per-cycle changes, output
correction magnitudes, and the largest differences. `FAULT` remains distinct from
`REJECT`.

Missing data and unsupported historical context are reported explicitly; they
are not filled with zero values or counted as successful comparisons. Slow-lane
rules can be evaluated synchronously offline, but this does not reproduce their
original scheduling, stale verdicts, or timeout behavior. Sequence/graph phase
transitions, live host state, and inputs absent from the recording cannot be
reconstructed reliably.

Save a cycle interval as a regression case, choosing the expected decisions or
capturing an evaluated side's decisions and outputs. Cases retain configuration
snapshots and the recording fingerprint. Re-running reports pass, fail, or error
and lists mismatches; a changed recording cannot silently satisfy an old case.

A/B evaluates recorded inputs. It does not simulate the different physical
trajectory that a modified command would produce. The offline comparison does
not send commands to hardware. Current comparison requests are bounded to 64 MiB
recordings and 20,000 cycles.

## API

| Endpoint | Purpose |
| --- | --- |
| `GET /api/safety/plans` | List saved plans |
| `GET /api/safety/plans/{name}` | Read a plan |
| `PUT /api/safety/plans/{name}` | Save a draft plan atomically |
| `DELETE /api/safety/plans/{name}` | Delete a saved plan |
| `POST /api/safety/compile` | Compile a plan and return static diagnostics |
| `POST /api/safety/draft` | Draft components from `{text, frame?}` |
| `POST /api/safety/switch/preview` | Review `{yaml, task, contract?}` |
| `POST /api/safety/switch/apply` | Apply a previously reviewed `{token}` |
| `GET /api/safety/switch/status` | Read active/saved identity and switch history |
| `POST /api/safety/switch/rollback/preview` | Review rollback for `{id, contract?}` |
| `POST /api/safety/comparisons` | Compare `{mcap, left_stack, right_stack}` |
| `GET /api/safety/comparisons/{id}` | Retrieve a comparison |
| `GET /api/safety/regressions` | List saved regression cases |
| `POST /api/safety/regressions` | Save a comparison interval and expectations |
| `POST /api/safety/regressions/{id}/run` | Re-run a saved case |

A plan has `name`, `base_yaml`, `task`, `components`, and optional `contract`.
Each component has `name`, `kind`, and a `boundaries` mapping using the ordinary
Stackfile boundary schema. Authoring fields reject unknown keys. The OpenAPI
schema at `/docs` exposes the full request models.
