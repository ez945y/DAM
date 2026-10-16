# Console Walkthrough

Use this page the first time you open `http://localhost:3000`. The goal is to connect what you see in the console with the Stackfile and guard decisions you are learning.

## Start The Runtime

```bash
make run
```

Open:

- Console: `http://localhost:3000`
- API docs: `http://localhost:8080/docs`

Expected result: the console loads and shows a connected or ready runtime state.

## Check The Current Cycle

Look for:

| Panel | What it tells you |
|-------|-------------------|
| Risk gauge | Current risk level: normal, elevated, critical, or emergency |
| Stats cards | Cycle count, reject count, clamp count, and average latency |
| Guard status | Latest decision per active guard |
| Event log | Recent pass, clamp, reject, or control events |

You are not trying to memorize every field. You are learning how to answer: did DAM allow, modify, or reject the latest action?

## Read A Guard Decision

Use this pattern:

1. Find the latest non-PASS event in the event log or guard table.
2. Read the layer: L0, L1, L2, or L3.
3. Read the guard name and reason.
4. Open the Stackfile and find the matching boundary or guard layer.
5. Decide whether the event is expected for the current run.

If the event names a boundary, search the Stackfile for that boundary name first.

## Edit Saved Configurations

The Configuration and Guard pages share a Stackfile selector. Loading a file does
not automatically rewrite it; automatic saving starts after loading succeeds and
you make an edit. A load error leaves the current file unchanged. Use **Retry
load** if the initial load fails, or select another file to try switching again.

Use **New** to create a library entry. An existing name is rejected without
overwriting its contents. The API distinguishes exclusive creation
(`POST /api/stackfiles/{name}`, returning `409` for an existing name) from saving
(`PUT /api/stackfiles/{name}`). **Apply & Restart** applies the selected content to
the live system.

## Check Latency

The cycle latency panel tells you whether DAM is staying inside its time budget.

| Signal | Meaning |
|--------|---------|
| OK | Healthy timing headroom |
| NEAR | Watch more closely |
| TIGHT | Close to the cycle budget |
| OVER | The cycle exceeded the budget |

For learning runs, focus on whether latency is stable and whether guard decisions still make sense.

## Inspect An Incident

When a reject, fault, or repeated clamp needs investigation:

1. Note the cycle ID or timestamp.
2. Open MCAP Sessions if loopback logging is enabled.
3. Select the relevant session.
4. Inspect the cycle, guard result, action, observation, and nearby image context if available.

If no MCAP session appears, check the Stackfile `loopback:` section and confirm `backend: mcap`.

Deleting a session permanently removes its recording and camera frames from disk;
it does not move the file to a trash folder. The console asks for confirmation.
If a batch deletion only partly succeeds, deleted sessions disappear from the list
and the failed sessions remain selected so you can retry.

## Next Step

After you can read a decision in the console, go back to [Common Stackfile Edits](common-stackfile-edits.md), make one safe change, validate it, and watch how the console output changes.
