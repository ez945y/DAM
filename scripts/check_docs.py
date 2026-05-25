#!/usr/bin/env python3
"""Run lightweight documentation quality checks used during PM docs work."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_GLOBS = (
    "README.md",
    "docs/*.md",
    "docs/getting-started/*.md",
    "docs/learn/*.md",
    "docs/concepts/*.md",
    "docs/cli.md",
    "docs/quick-stack.md",
    "docs/console.md",
    "docs/index.md",
)

FORBIDDEN_PATTERNS = {
    r"dam run --stack": "Use positional Stackfile paths: dam run <stackfile>.",
    r"dam validate --stack": "Use positional Stackfile paths: dam validate <stackfile>.",
    r"dam run my_stackfile\.yaml --task default": "Avoid implying every Stackfile has a default task.",
    r"ready to deploy safety-critical": "Avoid claiming safety-critical deployment readiness.",
    r"production-ready Stackfiles": "Prefer validated or deployment-oriented Stackfiles.",
    r"Fully implemented and production-ready": "Avoid production-readiness claims in research-grade docs.",
    r"Deterministic Safety": "Avoid deterministic safety claims in research-grade docs.",
    r"確定性安全": "Avoid deterministic safety claims in research-grade docs.",
    r"production deployment": "Prefer supervised hardware deployment in research-grade docs.",
    r"zero-latency": "Avoid latency guarantees; describe measured or low-latency behavior.",
    r"Real-time safe": "Avoid real-time safety claims; describe measured timing health.",
    r"Safety Guarantees": "Use Safety Model for research-grade safety documentation.",
    r"Deploy DAM to simulation and hardware": "Prefer supervised hardware preparation wording.",
    r"dam run examples/stackfiles/so101\.yaml(?!.*--cycles)": (
        "Use a bounded --cycles value for hardware-oriented run examples."
    ),
}

LEARNER_STACKFILE_FILES = {
    Path("docs/quick-stack.md"),
    Path("docs/learn/tutorial.md"),
    Path("docs/concepts/boundaries.md"),
    Path("docs/concepts/architecture.md"),
    Path("docs/concepts/guards-explained.md"),
    Path("docs/concepts/safety.md"),
    Path("docs/boundary-callbacks.md"),
    Path("docs/guards-reference.md"),
}

LEARNER_FORBIDDEN_PATTERNS = {
    r"\bnode_id:": "Use current Stackfile nodes with callback and params, not node_id examples.",
    r"\bconstraint:": "Use current Stackfile nodes with callback and params, not constraint examples.",
    r"\bsafe_retreat\b": "Use the built-in retreat fallback name.",
    r"BoundaryConstraint": "Avoid low-level boundary internals in learner-facing Stackfile docs.",
    r"\bupper_limits\b": "Use the Stackfile callback param upper in user-facing examples.",
    r"\blower_limits\b": "Use the Stackfile callback param lower in user-facing examples.",
    r"\bmax_velocity\b": "Use the Stackfile callback param max_velocities in user-facing examples.",
}


def _expand(root: Path, patterns: tuple[str, ...]) -> list[Path]:
    seen: set[Path] = set()
    files: list[Path] = []
    for pattern in patterns:
        for path in sorted(root.glob(pattern)):
            if path.is_file() and path not in seen:
                seen.add(path)
                files.append(path)
    return files


def check_forbidden_patterns(root: Path, files: list[Path]) -> list[str]:
    failures: list[str] = []
    compiled = [(re.compile(pattern), message) for pattern, message in FORBIDDEN_PATTERNS.items()]
    learner_compiled = [
        (re.compile(pattern), message) for pattern, message in LEARNER_FORBIDDEN_PATTERNS.items()
    ]
    for path in files:
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(root)
        for lineno, line in enumerate(text.splitlines(), start=1):
            for pattern, message in compiled:
                if pattern.search(line):
                    failures.append(f"{rel}:{lineno}: {message}")
            if rel in LEARNER_STACKFILE_FILES:
                for pattern, message in learner_compiled:
                    if pattern.search(line):
                        failures.append(f"{rel}:{lineno}: {message}")
    return failures


def run_mkdocs_strict(root: Path) -> int:
    return subprocess.run(
        [sys.executable, "-m", "mkdocs", "build", "--strict"],
        cwd=root,
        check=False,
    ).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Repository root.")
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Only run static text checks; skip mkdocs build --strict.",
    )
    args = parser.parse_args()

    root = args.root.resolve()
    files = _expand(root, DEFAULT_GLOBS)
    failures = check_forbidden_patterns(root, files)
    if failures:
        print("Documentation command-pattern checks failed:")
        for failure in failures:
            print(f"  {failure}")
        return 1

    if not args.skip_build:
        return run_mkdocs_strict(root)

    print(f"Checked {len(files)} documentation files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
