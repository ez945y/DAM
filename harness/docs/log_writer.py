#!/usr/bin/env python3
"""Append timestamped documentation work logs as JSONL."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_LOG = Path("harness/docs/logs/docs_pm_log.jsonl")


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def write_log(
    *,
    message: str,
    phase: str,
    status: str,
    actor: str,
    files: list[str],
    metrics: list[str],
    path: Path,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "phase": phase,
        "status": status,
        "actor": actor,
        "message": message,
        "files": files,
        "metrics": metrics,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("message", help="Short summary of the documentation work.")
    parser.add_argument("--phase", default="docs-alignment", help="Work phase or PM lane.")
    parser.add_argument("--status", default="done", help="Current status for this log entry.")
    parser.add_argument("--actor", default="codex-pm", help="Person or agent writing the log.")
    parser.add_argument("--files", default="", help="Comma-separated files touched or reviewed.")
    parser.add_argument("--metrics", default="", help="Comma-separated completion metrics.")
    parser.add_argument("--path", type=Path, default=DEFAULT_LOG, help="JSONL log file path.")
    args = parser.parse_args()

    written = write_log(
        message=args.message,
        phase=args.phase,
        status=args.status,
        actor=args.actor,
        files=_csv(args.files),
        metrics=_csv(args.metrics),
        path=args.path,
    )
    print(f"Wrote documentation log: {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
