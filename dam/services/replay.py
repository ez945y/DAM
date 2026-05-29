"""Replay-through-guards core, refactored from ``dam.cli`` for streaming use.

Re-evaluates recorded ``/dam/obs`` + ``/dam/action`` frames from an MCAP
session against an arbitrary Stackfile and yields incremental progress so the
console can monitor several stackfiles side by side.

The CLI (``dam replay --through-guards``) keeps its own self-contained
implementation; this module is the API-facing equivalent that emits structured
events instead of printing.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any


def _norm_decision(name: str) -> str:
    """Collapse FAULT into the REJECT bucket (loopback can't distinguish)."""
    return "REJECT" if name in ("FAULT", "REJECT") else name


def _array_or_none(value: Any) -> Any:
    import numpy as np

    return np.asarray(value, dtype=float) if isinstance(value, list) else None


# Legacy Rust writers encoded CycleRecordData as positional msgpack arrays.
# Current writers use named msgpack maps; keep the old map only for replaying
# historical sessions.
_IDX_CYCLE = 0
_IDX_OBS_TIMESTAMP = 1
_IDX_HAS_VIOLATION = 2
_IDX_HAS_CLAMP = 3
_IDX_ACTIVE_TASK = 6
_IDX_OBS_JOINT_POSITIONS = 9
_IDX_OBS_CHANNELS = 10
_IDX_ACTION_POSITIONS = 11
_IDX_ACTION_VELOCITIES = 12


def iter_replay_through_guards(
    mcap_path: str,
    stack_path: str,
    *,
    should_stop: Callable[[], bool] | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield progress events while replaying ``mcap_path`` against ``stack_path``.

    Event shapes (all dicts with a ``type`` key):

    * ``{"type": "start", "total": int, "task": str}``
    * ``{"type": "progress", "compared", "matches", "divergences",
         "cycle", "recorded", "replayed"}``
    * ``{"type": "done", "summary": {...}}``
    * ``{"type": "error", "message": str}``

    ``should_stop`` is polled each cycle; when it returns ``True`` the iterator
    emits a ``done`` event flagged ``stopped`` and returns.
    """
    stop = should_stop or (lambda: False)

    if not Path(mcap_path).is_file():
        yield {"type": "error", "message": f"no such MCAP file: {mcap_path}"}
        return
    if not Path(stack_path).is_file():
        yield {"type": "error", "message": f"no such stackfile: {stack_path}"}
        return
    try:
        from mcap.reader import make_reader
    except ImportError:
        yield {"type": "error", "message": "the 'mcap' package is required"}
        return

    import numpy as np

    obs_by_cycle: dict[int, dict[str, Any]] = {}
    action_by_cycle: dict[int, dict[str, Any]] = {}
    recorded: dict[int, str] = {}
    task_name: str | None = None

    def _decode(data: Any) -> Any:
        try:
            import msgpack

            return msgpack.unpackb(data, raw=False)
        except Exception:  # noqa: BLE001 — fall back to JSON map
            try:
                return json.loads(data)
            except Exception:  # noqa: BLE001
                return None

    def _norm(rec: Any) -> dict[str, Any] | None:
        """Normalise a decoded /dam/cycle record (positional list *or* dict)
        into the field subset replay needs."""
        if isinstance(rec, list):

            def at(i: int) -> Any:
                return rec[i] if 0 <= i < len(rec) else None

            return {
                "cycle_id": at(_IDX_CYCLE),
                "obs_timestamp": at(_IDX_OBS_TIMESTAMP),
                "has_violation": at(_IDX_HAS_VIOLATION),
                "has_clamp": at(_IDX_HAS_CLAMP),
                "active_task": at(_IDX_ACTIVE_TASK),
                "obs_joint_positions": at(_IDX_OBS_JOINT_POSITIONS),
                "obs_channels": at(_IDX_OBS_CHANNELS),
                "action_positions": at(_IDX_ACTION_POSITIONS),
                "action_velocities": at(_IDX_ACTION_VELOCITIES),
            }
        if isinstance(rec, dict):
            return rec
        return None

    try:
        with open(mcap_path, "rb") as fh:
            for _schema, channel, message in make_reader(fh).iter_messages():
                rec = _norm(_decode(message.data))
                if not rec or rec.get("cycle_id") is None:
                    continue
                try:
                    cid = int(rec["cycle_id"])
                except (TypeError, ValueError):
                    continue
                topic = channel.topic
                if topic == "/dam/obs":
                    obs_by_cycle[cid] = rec  # legacy explicit obs topic
                elif topic == "/dam/action":
                    action_by_cycle[cid] = rec  # legacy explicit action topic
                elif topic == "/dam/cycle":
                    # Current format: the full record (obs + action + decision)
                    # is packed into the single /dam/cycle message. Reconstruct
                    # the obs/action views replay needs from its fields.
                    if rec.get("has_violation"):
                        recorded[cid] = "REJECT"
                    elif rec.get("has_clamp"):
                        recorded[cid] = "CLAMP"
                    else:
                        recorded[cid] = "PASS"
                    if task_name is None:
                        task_name = rec.get("active_task")
                    if cid not in obs_by_cycle:
                        ch = rec.get("obs_channels") or {}
                        obs_by_cycle[cid] = {
                            "joint_positions": rec.get("obs_joint_positions"),
                            "timestamp": rec.get("obs_timestamp", 0.0),
                            "obs_channels": ch,
                            **{k: v for k, v in ch.items() if isinstance(v, list)},
                        }
                    if cid not in action_by_cycle:
                        action_by_cycle[cid] = {
                            "target_positions": rec.get("action_positions"),
                            "target_velocities": rec.get("action_velocities"),
                        }
    except Exception as exc:  # noqa: BLE001
        yield {"type": "error", "message": f"failed to read MCAP: {exc}"}
        return

    if not obs_by_cycle:
        yield {
            "type": "error",
            "message": "no observation data in session (no /dam/cycle or /dam/obs records)",
        }
        return

    try:
        from dam.api import _register_builtins
        from dam.guard.aggregator import aggregate_decisions
        from dam.runtime.guard_runtime import GuardRuntime
        from dam.types.action import ActionProposal
        from dam.types.observation import Observation
        from dam.types.result import GuardDecision

        _register_builtins()
        runtime = GuardRuntime.from_stackfile(stack_path)
    except Exception as exc:  # noqa: BLE001 — surface build/registry errors cleanly
        yield {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
        return

    tasks = list(getattr(runtime, "_task_config", {}))
    chosen = task_name if task_name in tasks else (tasks[0] if tasks else None)
    if chosen is None:
        yield {"type": "error", "message": "stackfile defines no tasks"}
        return
    runtime.start_task(chosen)

    cycle_ids = [cid for cid in sorted(obs_by_cycle) if cid in recorded]
    total = len(cycle_ids)
    yield {"type": "start", "total": total, "task": chosen}

    reserved = {"cycle_id", "timestamp", "joint_positions"}
    typed_channels = {"joint_velocities", "end_effector_pose", "force_torque", "obs_channels"}
    compared = matches = 0
    diverged: list[dict[str, Any]] = []
    saw_jv = saw_eep = saw_ft = False
    # Boundary/guard aggregates over *all* compared cycles — the "where is
    # the problem" view, not per-frame noise.
    gstats: dict[str, dict[str, Any]] = {}
    rec_dist: dict[str, int] = {}
    rep_dist: dict[str, int] = {}

    def finish(*, stopped: bool) -> dict[str, Any]:
        return _summary(
            mcap_path,
            stack_path,
            chosen,
            compared,
            matches,
            diverged,
            saw_jv,
            saw_eep,
            saw_ft,
            runtime,
            gstats=gstats,
            rec_dist=rec_dist,
            rep_dist=rep_dist,
            stopped=stopped,
        )

    for idx, cid in enumerate(cycle_ids):
        if stop():
            yield {"type": "done", "summary": finish(stopped=True)}
            return

        o = obs_by_cycle[cid]
        jp = o.get("joint_positions")
        if not jp:
            continue
        joint_velocities = _array_or_none(o.get("joint_velocities"))
        end_effector_pose = _array_or_none(o.get("end_effector_pose"))
        force_torque = _array_or_none(o.get("force_torque"))
        saw_jv = saw_jv or joint_velocities is not None
        saw_eep = saw_eep or end_effector_pose is not None
        saw_ft = saw_ft or force_torque is not None

        nested = o.get("obs_channels")
        channels = {
            k: np.asarray(v, dtype=float)
            for k, v in (nested.items() if isinstance(nested, dict) else ())
            if isinstance(v, list)
        }
        channels.update(
            {
                k: np.asarray(v, dtype=float)
                for k, v in o.items()
                if k not in reserved and k not in typed_channels and isinstance(v, list)
            }
        )
        channels = {
            k: v
            for k, v in channels.items()
            if k not in {"joint_positions", "target_positions", "target_velocities"}
        }
        jp_arr = np.asarray(jp, dtype=float)
        if joint_velocities is None:
            joint_velocities = np.zeros_like(jp_arr)
        obs = Observation(
            timestamp=float(o.get("timestamp", 0.0)),
            joint_positions=jp_arr,
            joint_velocities=joint_velocities,
            end_effector_pose=end_effector_pose,
            force_torque=force_torque,
            channels=channels or None,
        )
        a = action_by_cycle.get(cid, {})
        tv = a.get("target_velocities")
        tp = np.asarray(a.get("target_positions", jp), dtype=float)
        action = ActionProposal(
            target_joint_positions=tp,
            target_joint_velocities=(np.asarray(tv, dtype=float) if isinstance(tv, list) else None),
        )
        replay_guards: list[dict[str, Any]] = []

        def _vec(arr: Any) -> list[float] | None:
            if arr is None:
                return None
            return [round(float(x), 4) for x in arr]

        in_pos = _vec(action.target_joint_positions)
        in_vel = _vec(action.target_joint_velocities)
        try:
            validated, guard_results = runtime.validate(
                obs, action, trace_id=f"replay-{cid}", now=obs.timestamp
            )
            out_pos = _vec(validated.target_joint_positions) if validated is not None else None
            out_vel = _vec(validated.target_joint_velocities) if validated is not None else None
            decision = (
                aggregate_decisions(guard_results).decision if guard_results else GuardDecision.PASS
            )
            replayed = _norm_decision(decision.name)
            # The guards that did *not* PASS are exactly the ones that produced
            # the replay decision — i.e. the knobs the user would tune.
            replay_guards = [
                {
                    "name": r.guard_name,
                    "layer": int(r.layer),
                    "decision": r.decision.name,
                    "reason": (r.reason or "")[:240],
                }
                for r in guard_results
                if r.decision != GuardDecision.PASS
            ]
            # Per-guard tally over every compared cycle (not just flips):
            # this is the boundary-level "how often did each guard fire".
            for r in guard_results:
                slot = gstats.setdefault(
                    r.guard_name,
                    {
                        "name": r.guard_name,
                        "layer": int(r.layer),
                        "clamp": 0,
                        "reject": 0,
                        "total": 0,
                        "samples": [],
                    },
                )
                slot["total"] += 1
                d = _norm_decision(r.decision.name)
                if d == "CLAMP":
                    slot["clamp"] += 1
                elif d == "REJECT":
                    slot["reject"] += 1
                # Keep a bounded set of concrete examples: the recorded
                # action that violated this boundary and the action the
                # guard produced instead.
                if d != "PASS" and len(slot["samples"]) < 25:
                    slot["samples"].append(
                        {
                            "cycle": cid,
                            "decision": r.decision.name,
                            "reason": (r.reason or "")[:240],
                            "in_pos": in_pos,
                            "in_vel": in_vel,
                            "out_pos": out_pos,
                            "out_vel": out_vel,
                        }
                    )
        except Exception as exc:  # noqa: BLE001 — a crashing guard re-run is a finding
            replayed = f"ERROR({type(exc).__name__})"
        compared += 1
        rec_dec = _norm_decision(recorded[cid])
        rec_dist[rec_dec] = rec_dist.get(rec_dec, 0) + 1
        rep_dist[replayed] = rep_dist.get(replayed, 0) + 1
        if rec_dec == replayed:
            matches += 1
        else:
            diverged.append(
                {
                    "cycle": cid,
                    "recorded": rec_dec,
                    "replayed": replayed,
                    "guards": replay_guards,
                }
            )

        # Throttle progress: every cycle for small runs, else ~1%.
        step = max(1, total // 100)
        if idx % step == 0 or idx == total - 1:
            yield {
                "type": "progress",
                "compared": compared,
                "matches": matches,
                "divergences": len(diverged),
                "cycle": cid,
                "recorded": rec_dec,
                "replayed": replayed,
                "guards": replay_guards if rec_dec != replayed else [],
                "done": idx + 1,
                "total": total,
            }

    yield {"type": "done", "summary": finish(stopped=False)}


def _summary(
    mcap_path: str,
    stack_path: str,
    task: str,
    compared: int,
    matches: int,
    diverged: list[dict[str, Any]],
    saw_jv: bool,
    saw_eep: bool,
    saw_ft: bool,
    runtime: Any,
    *,
    gstats: dict[str, dict[str, Any]],
    rec_dist: dict[str, int],
    rep_dist: dict[str, int],
    stopped: bool,
) -> dict[str, Any]:
    pct = (100.0 * matches / compared) if compared else 0.0
    active = list(getattr(runtime, "_active_container_names", []))
    degraded: dict[str, list[str]] = {}

    def add(name: str, reason: str) -> None:
        degraded.setdefault(name, []).append(reason)

    if not saw_jv:
        for name in ("joint_velocity_limit",):
            if name in active:
                add(name, "joint_velocities synthetic")
    if not saw_eep:
        for name in ("workspace",):
            if name in active:
                add(name, "end_effector_pose missing")
    for ft_name in ("force_limit", "force_torque_limit"):
        if not saw_ft and ft_name in active:
            add(ft_name, "force_torque missing")

    # Aggregate which guards drove the decision changes — the actionable
    # "what to tune in the replay stackfile" view. Keyed by guard name.
    drivers: dict[str, dict[str, Any]] = {}
    for d in diverged:
        for g in d.get("guards", []):
            slot = drivers.setdefault(
                g["name"],
                {
                    "name": g["name"],
                    "layer": g["layer"],
                    "count": 0,
                    "decisions": set(),
                    "sample_reason": "",
                },
            )
            slot["count"] += 1
            slot["decisions"].add(g["decision"])
            if not slot["sample_reason"] and g.get("reason"):
                slot["sample_reason"] = g["reason"]
    change_drivers = sorted(
        (
            {
                "name": v["name"],
                "layer": v["layer"],
                "count": v["count"],
                "decisions": sorted(v["decisions"]),
                "sample_reason": v["sample_reason"],
            }
            for v in drivers.values()
        ),
        key=lambda x: x["count"],
        reverse=True,
    )

    return {
        "mcap": Path(mcap_path).name,
        "stack": Path(stack_path).name,
        "task": task,
        "compared": compared,
        "matches": matches,
        "match_pct": round(pct, 1),
        "divergences": diverged,
        "divergence_count": len(diverged),
        "stopped": stopped,
        "reconstructed": {
            "joint_positions": "recorded",
            "joint_velocities": "recorded" if saw_jv else "synthetic_zero",
            "end_effector_pose": "recorded" if saw_eep else "missing",
            "force_torque": "recorded" if saw_ft else "missing",
        },
        "comparable": [n for n in active if n not in degraded],
        "degraded": {n: r for n, r in degraded.items()},
        "change_drivers": change_drivers,
        # Boundary-level statistics: how often each guard clamped/rejected
        # across the whole replay, and the recorded vs replay decision mix.
        "guard_stats": sorted(
            gstats.values(),
            key=lambda s: (s["reject"] + s["clamp"], s["name"]),
            reverse=True,
        ),
        "decision_dist": {
            "recorded": rec_dist,
            "replay": rep_dist,
        },
    }
