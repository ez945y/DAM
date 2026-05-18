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
  loopback ``.mcap`` session.
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
import sys
from collections.abc import Sequence

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
    from dam.boundary.callbacks import register_all as reg_callbacks
    from dam.fallback.builtin import register_all as reg_fallbacks
    from dam.guard.builtin import register_all as reg_guards
    from dam.runner.base import RunnerStatus
    from dam.runtime.factory import RuntimeFactory

    reg_callbacks()
    reg_fallbacks()
    reg_guards()

    try:
        runner = RuntimeFactory.build_from_stackfile(args.stack)
        runner.connect()
        runner.verify()
        runner.start(task=args.task, n_cycles=args.cycles)
    except Exception as exc:  # noqa: BLE001 — report build/connect/verify failures cleanly
        print(f"dam run: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    import time

    terminal = (
        RunnerStatus.STOPPED,
        RunnerStatus.IDLE,
        RunnerStatus.EMERGENCY,
    )
    try:
        while runner.status not in terminal:
            time.sleep(0.05)
    except KeyboardInterrupt:
        print("\ninterrupted — stopping", file=sys.stderr)
        runner.stop()
    finally:
        cycles = getattr(runner, "cycle_count", "?")
        status = runner.status
        runner.shutdown()

    print(f"finished: status={status.name} cycles={cycles}")
    return 0 if status != RunnerStatus.EMERGENCY else 1


# ── replay ────────────────────────────────────────────────────────────────────


def _cmd_replay(args: argparse.Namespace) -> int:
    from pathlib import Path

    path = Path(args.mcap)
    if not path.is_file():
        print(f"dam replay: no such file: {path}", file=sys.stderr)
        return 1
    try:
        from mcap.reader import make_reader
    except ImportError:
        print(
            "dam replay: the 'mcap' package is required "
            "(pip install 'dam[torch]' or pip install mcap)",
            file=sys.stderr,
        )
        return 1

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
    print(
        f"safety  : control_frequency_hz={safety.control_frequency_hz} "
        f"no_task_behavior={safety.no_task_behavior}"
    )
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
        chain = f" -> {fb.escalates_to}" if fb.escalates_to else ""
        print(f"  {name}: {fb.type}{chain}")
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

    p_rep = sub.add_parser("replay", help="summarise a loopback .mcap session")
    p_rep.add_argument("mcap", metavar="MCAP", help="loopback .mcap path")
    p_rep.add_argument("--limit", type=int, default=20, help="max cycle ids to list per category")
    p_rep.set_defaults(func=_cmd_replay)

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
