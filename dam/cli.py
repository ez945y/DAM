"""``dam`` command-line interface.

Subcommands
-----------
- ``dam validate [stack...]`` — schema-validate one or more Stackfiles
  (the CI stackfile gate). Exit 1 if any file is invalid. With no path,
  validates the ``.dam_stackfile.yaml`` convention file.
- ``dam callbacks`` — list every built-in boundary callback (name, layer,
  description). ``--layer L1`` filters; ``--json`` for machine output.
- ``dam run <stack>`` — build the runtime from a Stackfile and run a
  headless control loop for ``--cycles`` cycles.
- ``dam replay <mcap>`` — summarise the guard decisions recorded in a
  loopback ``.mcap`` session. ``--through-guards --stack <s>`` re-runs the
  recorded obs/action through that Stackfile and diffs recorded vs current
  decisions (regression / threshold tuning).
- ``dam experiment`` — list/run native experiment runners used by the console.
- ``dam doctor`` — check environment / dependency readiness.
- ``dam inspect <stack>`` — print the resolved Stackfile graph (guards,
  boundaries, tasks, fallbacks) without touching hardware.
- ``dam help [command]`` — show help for the CLI or a subcommand.

The console entry point is wired via ``[project.scripts] dam = dam.cli:main``.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import sys
from collections.abc import Callable, Sequence
from typing import Any

from dam import __version__

_LAYER_ORDER = ("L0", "L1", "L2", "L3")

# Convention file used when no Stackfile is given (mirrors scripts/dam_host.py).
_DEFAULT_STACK = ".dam_stackfile.yaml"


# ── validate ──────────────────────────────────────────────────────────────────


def _cmd_validate(args: argparse.Namespace) -> int:
    from dam.config.loader import StackfileLoader

    stacks = list(args.stacks) or [_DEFAULT_STACK]
    failed = 0
    for path in stacks:
        try:
            StackfileLoader.validate(path)
        except Exception as exc:  # noqa: BLE001 — surface any loader/schema error per file
            failed += 1
            print(f"FAIL  {path}\n      {type(exc).__name__}: {exc}")
        else:
            print(f"OK    {path}")
    total = len(stacks)
    print(f"\n{total - failed}/{total} valid")
    return 1 if failed else 0


# ── callbacks ─────────────────────────────────────────────────────────────────


def _cmd_callbacks(args: argparse.Namespace) -> int:
    from dam.boundary.callbacks import get_catalog

    catalog = get_catalog()
    if args.layer:
        catalog = [c for c in catalog if c["layer"] == args.layer]

    if args.json:
        print(json.dumps(catalog, indent=2, default=str))
        return 0

    if not catalog:
        print("No callbacks match." if args.layer else "No callbacks registered.")
        return 0

    by_layer: dict[str, list[dict[str, object]]] = {}
    for cb in catalog:
        by_layer.setdefault(str(cb["layer"]), []).append(cb)

    ordered = sorted(
        by_layer, key=lambda lay: (_LAYER_ORDER.index(lay) if lay in _LAYER_ORDER else 99, lay)
    )
    for layer in ordered:
        entries = sorted(by_layer[layer], key=lambda c: str(c["name"]))
        print(f"\n{layer}  ({len(entries)})")
        width = max(len(str(c["name"])) for c in entries)
        for cb in entries:
            print(f"  {str(cb['name']):<{width}}  {cb['description']}")
    print(f"\n{len(catalog)} callback(s) total")
    return 0


# ── run ───────────────────────────────────────────────────────────────────────


def _cmd_run(args: argparse.Namespace) -> int:
    from dam.api import run as run_api

    try:
        summary = run_api(args.stack, task=args.task, cycles=args.cycles)
    except KeyboardInterrupt:
        print("\ninterrupted — stopped", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — report build/connect/verify failures cleanly
        print(f"dam run: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"finished: status={summary.status} cycles={summary.cycles}")
    return 1 if summary.emergency else 0


# ── replay ────────────────────────────────────────────────────────────────────


def _open_mcap(path: str) -> Callable[..., Any] | None:
    """Return an mcap reader factory, or None (with a printed error)."""
    from pathlib import Path

    if not Path(path).is_file():
        print(f"dam replay: no such file: {path}", file=sys.stderr)
        return None
    try:
        from mcap.reader import make_reader
    except ImportError:
        print(
            "dam replay: the 'mcap' package is required "
            "(pip install 'dam[torch]' or pip install mcap)",
            file=sys.stderr,
        )
        return None
    return make_reader


def _norm_decision(name: str) -> str:
    """Collapse FAULT into the REJECT bucket (loopback can't distinguish)."""
    return "REJECT" if name in ("FAULT", "REJECT") else name


def _array_or_none(value: Any) -> Any:
    import numpy as np

    return np.asarray(value, dtype=float) if isinstance(value, list) else None


def _fmt_names(names: Sequence[str]) -> str:
    return ", ".join(names) if names else "none"


def _cmd_replay(args: argparse.Namespace) -> int:
    if args.through_guards:
        return _replay_through_guards(args)
    return _replay_summary(args)


def _replay_through_guards(args: argparse.Namespace) -> int:
    import numpy as np

    make_reader = _open_mcap(args.mcap)
    if make_reader is None:
        return 1
    if not args.stack:
        print("dam replay --through-guards: --stack STACK is required", file=sys.stderr)
        return 1

    obs_by_cycle: dict[int, dict[str, Any]] = {}
    action_by_cycle: dict[int, dict[str, Any]] = {}
    recorded: dict[int, str] = {}
    task_name: str | None = None

    with open(args.mcap, "rb") as fh:
        for _schema, channel, message in make_reader(fh).iter_messages():
            try:
                rec = json.loads(message.data)
                cid = int(rec["cycle_id"])
            except Exception:  # noqa: BLE001 — skip unparseable frames
                continue
            if channel.topic == "/dam/obs":
                obs_by_cycle[cid] = rec
            elif channel.topic == "/dam/action":
                action_by_cycle[cid] = rec
            elif channel.topic == "/dam/cycle":
                if rec.get("has_violation"):
                    recorded[cid] = "REJECT"
                elif rec.get("has_clamp"):
                    recorded[cid] = "CLAMP"
                else:
                    recorded[cid] = "PASS"
                if task_name is None:
                    task_name = rec.get("active_task")

    if not obs_by_cycle:
        print("dam replay: no /dam/obs frames in session", file=sys.stderr)
        return 1

    from dam.api import _register_builtins
    from dam.guard.aggregator import aggregate_decisions
    from dam.runtime.guard_runtime import GuardRuntime
    from dam.types.action import ActionProposal
    from dam.types.observation import Observation
    from dam.types.result import GuardDecision

    try:
        _register_builtins()
        runtime = GuardRuntime.from_stackfile(args.stack)
    except Exception as exc:  # noqa: BLE001 — surface build/registry errors cleanly
        print(f"dam replay: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    tasks = list(getattr(runtime, "_task_config", {}))
    chosen = task_name if task_name in tasks else (tasks[0] if tasks else None)
    if chosen is None:
        print("dam replay: stackfile defines no tasks", file=sys.stderr)
        return 1
    runtime.start_task(chosen)

    reserved = {"cycle_id", "timestamp", "joint_positions"}
    compared = matches = 0
    diverged: list[tuple[int, str, str]] = []
    saw_joint_velocities = False
    saw_end_effector_pose = False
    saw_force_torque = False

    for cid in sorted(obs_by_cycle):
        if cid not in recorded:
            continue
        o = obs_by_cycle[cid]
        jp = o.get("joint_positions")
        if not jp:
            continue
        joint_velocities = _array_or_none(o.get("joint_velocities"))
        end_effector_pose = _array_or_none(o.get("end_effector_pose"))
        force_torque = _array_or_none(o.get("force_torque"))
        saw_joint_velocities = saw_joint_velocities or joint_velocities is not None
        saw_end_effector_pose = saw_end_effector_pose or end_effector_pose is not None
        saw_force_torque = saw_force_torque or force_torque is not None

        typed_channels = {
            "joint_velocities",
            "end_effector_pose",
            "force_torque",
            "obs_channels",
        }
        nested_channels = o.get("obs_channels")
        channels = {
            k: np.asarray(v, dtype=float)
            for k, v in (nested_channels.items() if isinstance(nested_channels, dict) else ())
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
        # Loopback /dam/obs records positions + channels only. Missing
        # velocities are reconstructed as zeros so velocity-dependent guards
        # do not crash; they degrade (see the printed caveat).
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
        try:
            _, guard_results = runtime.validate(
                obs, action, trace_id=f"replay-{cid}", now=obs.timestamp
            )
            decision = (
                aggregate_decisions(guard_results).decision if guard_results else GuardDecision.PASS
            )
            replayed = _norm_decision(decision.name)
        except Exception as exc:  # noqa: BLE001 — a crashing guard re-run is itself a finding
            replayed = f"ERROR({type(exc).__name__})"
        compared += 1
        rec_dec = _norm_decision(recorded[cid])
        if rec_dec == replayed:
            matches += 1
        else:
            diverged.append((cid, rec_dec, replayed))

    from pathlib import Path

    pct = (100.0 * matches / compared) if compared else 0.0
    print(f"replay-through-guards: {Path(args.mcap).name}  stack={args.stack}  task={chosen}")
    print(f"cycles compared : {compared}")
    print(f"matches         : {matches} ({pct:.1f}%)")
    print(f"divergences     : {len(diverged)}")
    if diverged:
        print("  cycle  recorded -> replayed")
        for cid, rec_dec, rep in diverged[: args.limit]:
            print(f"  {cid:<6} {rec_dec:<8} -> {rep}")
        if len(diverged) > args.limit:
            print(f"  ... +{len(diverged) - args.limit} more")
    fields = [
        "joint_positions=recorded",
        f"joint_velocities={'recorded' if saw_joint_velocities else 'synthetic_zero'}",
        f"end_effector_pose={'recorded' if saw_end_effector_pose else 'missing'}",
        f"force_torque={'recorded' if saw_force_torque else 'missing'}",
    ]
    print(f"reconstructed  : {', '.join(fields)}")

    active = list(getattr(runtime, "_active_container_names", []))
    degraded: dict[str, list[str]] = {}

    def add_degraded(name: str, reason: str) -> None:
        degraded.setdefault(name, []).append(reason)

    if not saw_joint_velocities:
        for name in ("joint_velocity_limit",):
            if name in active:
                add_degraded(name, "joint_velocities synthetic")
    if not saw_end_effector_pose:
        for name in ("workspace",):
            if name in active:
                add_degraded(name, "end_effector_pose missing")
    for ft_name in ("force_limit", "force_torque_limit"):
        if not saw_force_torque and ft_name in active:
            add_degraded(ft_name, "force_torque missing")

    comparable = [name for name in active if name not in degraded]
    degraded_bits = [f"{name} ({'; '.join(reasons)})" for name, reasons in degraded.items()]
    print(f"comparable     : {_fmt_names(comparable)}")
    print(f"degraded       : {_fmt_names(degraded_bits)}")
    return 0


def _replay_summary(args: argparse.Namespace) -> int:
    make_reader = _open_mcap(args.mcap)
    if make_reader is None:
        return 1
    from pathlib import Path

    path = Path(args.mcap)
    decisions: dict[str, int] = {}
    violations: list[int] = []
    clamps: list[int] = []
    cycles: set[int] = set()

    with path.open("rb") as fh:
        reader = make_reader(fh)
        for _schema, channel, message in reader.iter_messages():
            topic = channel.topic
            if topic == "/dam/cycle":
                with contextlib.suppress(Exception):  # skip unparseable frames
                    cycles.add(int(json.loads(message.data)["cycle_id"]))
                continue
            if not topic.startswith("/dam/L"):
                continue
            try:
                rec = json.loads(message.data)
            except Exception:  # noqa: BLE001 — skip unparseable frames
                continue
            name = str(rec.get("decision_name", "?"))
            decisions[name] = decisions.get(name, 0) + 1
            cid = rec.get("cycle_id")
            if rec.get("is_violation") and isinstance(cid, int):
                violations.append(cid)
            if rec.get("is_clamp") and isinstance(cid, int):
                clamps.append(cid)

    print(f"session : {path.name}")
    print(f"cycles  : {len(cycles)}")
    print("decisions:")
    for name in sorted(decisions):
        print(f"  {name:<8} {decisions[name]}")
    if violations:
        head = sorted(set(violations))[: args.limit]
        print(
            f"violations ({len(set(violations))}): cycles {head}{' …' if len(set(violations)) > args.limit else ''}"
        )
    if clamps:
        head = sorted(set(clamps))[: args.limit]
        print(
            f"clamps ({len(set(clamps))}): cycles {head}{' …' if len(set(clamps)) > args.limit else ''}"
        )
    if not violations and not clamps:
        print("no violations or clamps recorded")
    return 0


# ── experiment ────────────────────────────────────────────────────────────────


def _cmd_experiment(args: argparse.Namespace) -> int:
    from dam.experiments import list_experiments, run_experiment

    if args.experiment_cmd == "list":
        for exp in list_experiments():
            print(f"{exp.id:<16} {exp.rq:<4} {exp.title}")
        return 0

    params: dict[str, Any] = {}
    if args.trials is not None:
        params["trials"] = args.trials
    if args.frames is not None:
        params["frames"] = args.frames
    if args.samples is not None:
        params["samples"] = args.samples
    if args.trials_per_scenario is not None:
        params["trials_per_scenario"] = args.trials_per_scenario
    if args.compare_ood_methods:
        params["compare_ood_methods"] = True
    if args.no_runtime_export:
        params["runtime_model_path"] = None
    elif args.runtime_model_path is not None:
        params["runtime_model_path"] = args.runtime_model_path
    if args.outdir is not None:
        params["outdir"] = args.outdir
    if not logging.getLogger().handlers:
        from dam.logging import setup_colored_logging

        setup_colored_logging()
    result = run_experiment(args.experiment_id, params)
    print(f"experiment : {result.id}")
    print(f"status     : {result.status}")
    print(f"elapsed    : {result.elapsed_sec:.2f}s")
    print(f"outdir     : {result.outdir}")
    if result.artifacts:
        print("artifacts  :")
        for path in result.artifacts:
            print(f"  {path}")
    return 0


# ── doctor ────────────────────────────────────────────────────────────────────


def _probe(module: str, attr: str | None = None) -> tuple[bool, str]:
    """Import *module* (optionally check *attr*); return (ok, detail)."""
    import importlib

    try:
        mod = importlib.import_module(module)
    except Exception as exc:  # noqa: BLE001 — any import failure means "not available"
        return False, f"{type(exc).__name__}: {exc}"
    if attr is not None and not hasattr(mod, attr):
        return False, f"imported but missing {attr!r}"
    version = getattr(mod, "__version__", "")
    return True, str(version)


def _cmd_doctor(args: argparse.Namespace) -> int:
    import platform

    # (label, module, attr, required)
    checks = [
        ("dam", "dam", None, True),
        ("dam_rs (Rust data plane)", "dam_rs", None, True),
        ("dam_rs.ImageHub (camera/loopback)", "dam_rs", "ImageHub", False),
        ("pinocchio (FK/Jacobian callbacks)", "pinocchio", None, False),
        ("torch (policy / OOD)", "torch", None, False),
        ("mcap (replay / loopback)", "mcap", None, False),
        ("lerobot (SO-ARM adapter)", "lerobot", None, False),
        ("rclpy (ROS 2 adapter)", "rclpy", None, False),
    ]
    print(f"python  : {platform.python_version()} ({sys.executable})")
    missing_required = 0
    for label, module, attr, required in checks:
        ok, detail = _probe(module, attr)
        if ok:
            mark = "OK  "
        elif required:
            mark = "FAIL"
            missing_required += 1
        else:
            mark = "WARN"
        suffix = f"  ({detail})" if detail else ""
        print(f"  {mark}  {label}{suffix}")
    if missing_required:
        print(f"\n{missing_required} required component(s) missing", file=sys.stderr)
        return 1
    print("\ncore environment OK")
    return 0


# ── inspect ───────────────────────────────────────────────────────────────────


def _format_guards(guards: object) -> str:
    if isinstance(guards, dict):
        return " ".join(f"{k}={v}" for k, v in guards.items())
    if isinstance(guards, list):
        parts: list[str] = []
        for g in guards:
            if isinstance(g, dict):
                parts += [f"{k}={v}" for k, v in g.items()]
            else:
                parts.append(str(g))
        return " ".join(parts)
    return str(guards)


def _cmd_inspect(args: argparse.Namespace) -> int:
    from dam.config.loader import StackfileLoader

    try:
        config = StackfileLoader.load(args.stack)
    except Exception as exc:  # noqa: BLE001 — surface any loader/schema error
        print(f"dam inspect: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    safety = config.safety
    print(f"stack   : {args.stack}  (version {config.version})")
    print(f"safety  : control_hz={safety.control_hz} no_task_behavior={safety.no_task_behavior}")
    print(f"guards  : {_format_guards(config.guards) or '—'}")

    print(f"\nboundaries ({len(config.boundaries)}):")
    for name, cont in config.boundaries.items():
        print(f"  {name}  [{cont.layer} {cont.type}]")
        for node in cont.nodes:
            params = ",".join(sorted(node.params)) or "—"
            timeout = "—" if node.timeout_sec is None else f"{node.timeout_sec}s"
            print(
                f"    - callback={node.callback} fallback={node.fallback} "
                f"timeout={timeout} params=[{params}]"
            )

    print(f"\ntasks ({len(config.tasks)}):")
    for name, task in config.tasks.items():
        print(f"  {name}: {list(task.boundaries)}")

    print(f"\nfallbacks ({len(config.fallbacks)}):")
    for name, fb in config.fallbacks.items():
        chain = f" -> {fb.escalate_to}" if fb.escalate_to else ""
        print(f"  {name}{chain}")
    return 0


# ── help ──────────────────────────────────────────────────────────────────────


def _cmd_help(args: argparse.Namespace) -> int:
    parser, choices = build_parser()
    topic = args.topic
    if topic:
        if topic in choices:
            choices[topic].print_help()
            return 0
        print(f"dam help: unknown command {topic!r}", file=sys.stderr)
        return 1
    parser.print_help()
    return 0


# ── parser ────────────────────────────────────────────────────────────────────


def build_parser() -> tuple[argparse.ArgumentParser, dict[str, argparse.ArgumentParser]]:
    parser = argparse.ArgumentParser(
        prog="dam",
        description="DAM — Detachable Action Monitor command-line interface.",
    )
    parser.add_argument("--version", action="version", version=f"dam {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p_val = sub.add_parser("validate", help="schema-validate Stackfile(s)")
    p_val.add_argument(
        "stacks",
        nargs="*",
        metavar="STACK",
        help=f"Stackfile path(s) (default: {_DEFAULT_STACK})",
    )
    p_val.set_defaults(func=_cmd_validate)

    p_cb = sub.add_parser("callbacks", help="list built-in boundary callbacks")
    p_cb.add_argument("--layer", choices=_LAYER_ORDER, help="filter by guard layer")
    p_cb.add_argument("--json", action="store_true", help="machine-readable output")
    p_cb.set_defaults(func=_cmd_callbacks)

    p_run = sub.add_parser("run", help="run a headless control loop from a Stackfile")
    p_run.add_argument(
        "stack",
        nargs="?",
        default=_DEFAULT_STACK,
        metavar="STACK",
        help=f"Stackfile path (default: {_DEFAULT_STACK})",
    )
    p_run.add_argument("--task", default="default", help="task name (default: default)")
    p_run.add_argument(
        "--cycles", type=int, default=100, help="cycles to run, -1 for unbounded (default: 100)"
    )
    p_run.set_defaults(func=_cmd_run)

    p_rep = sub.add_parser("replay", help="summarise / re-evaluate a loopback .mcap session")
    p_rep.add_argument("mcap", metavar="MCAP", help="loopback .mcap path")
    p_rep.add_argument("--limit", type=int, default=20, help="max cycle ids to list")
    p_rep.add_argument(
        "--through-guards",
        action="store_true",
        help="re-run recorded obs/action through --stack and diff decisions",
    )
    p_rep.add_argument(
        "--stack", metavar="STACK", help="Stackfile to re-evaluate against (--through-guards)"
    )
    p_rep.set_defaults(func=_cmd_replay)

    p_exp = sub.add_parser("experiment", help="list/run native experiment tools")
    exp_sub = p_exp.add_subparsers(dest="experiment_cmd", required=True)
    p_exp_list = exp_sub.add_parser("list", help="list available experiments")
    p_exp_list.set_defaults(func=_cmd_experiment)
    p_exp_run = exp_sub.add_parser("run", help="run an experiment")
    p_exp_run.add_argument(
        "experiment_id",
        choices=[
            "l0-calibration",
            "boundary-scan",
            "usability",
            "latency-bench",
            "failure-record-quality",
        ],
    )
    p_exp_run.add_argument("--trials", type=int, help="boundary-scan trials per level")
    p_exp_run.add_argument("--frames", type=int, help="latency-bench frames per config")
    p_exp_run.add_argument("--samples", type=int, help="l0-calibration max HF observations")
    p_exp_run.add_argument(
        "--compare-ood-methods",
        action="store_true",
        help="RQ1: compare Welford, MemoryBank, and Real-NVP OOD scores",
    )
    p_exp_run.add_argument(
        "--runtime-model-path",
        help="RQ1: publish a Stackfile-compatible OOD model bundle to this .pt path",
    )
    p_exp_run.add_argument(
        "--no-runtime-export",
        action="store_true",
        help="RQ1: skip publishing the calibrated model bundle for runtime use",
    )
    p_exp_run.add_argument(
        "--trials-per-scenario", type=int, help="usability trials per normal scenario"
    )
    p_exp_run.add_argument("--outdir", help="output directory")
    p_exp_run.set_defaults(func=_cmd_experiment)

    p_doc = sub.add_parser("doctor", help="check environment / dependency readiness")
    p_doc.set_defaults(func=_cmd_doctor)

    p_ins = sub.add_parser("inspect", help="print the resolved Stackfile graph")
    p_ins.add_argument(
        "stack",
        nargs="?",
        default=_DEFAULT_STACK,
        metavar="STACK",
        help=f"Stackfile path (default: {_DEFAULT_STACK})",
    )
    p_ins.set_defaults(func=_cmd_inspect)

    p_help = sub.add_parser("help", help="show help for the CLI or a subcommand")
    p_help.add_argument("topic", nargs="?", help="subcommand to describe")
    p_help.set_defaults(func=_cmd_help)

    choices: dict[str, argparse.ArgumentParser] = {
        "validate": p_val,
        "callbacks": p_cb,
        "run": p_run,
        "replay": p_rep,
        "experiment": p_exp,
        "doctor": p_doc,
        "inspect": p_ins,
        "help": p_help,
    }
    return parser, choices


def main(argv: Sequence[str] | None = None) -> int:
    parser, _ = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 1
    func = args.func
    result = func(args)
    return int(result)


if __name__ == "__main__":
    raise SystemExit(main())
