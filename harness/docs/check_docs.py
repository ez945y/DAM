#!/usr/bin/env python3
"""Run lightweight documentation quality checks used during PM docs work."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_GLOBS = (
    "docs/getting-started/*.md",
    "docs/learn/*.md",
    "docs/cli.md",
    "docs/quick-stack.md",
    "docs/console.md",
    "docs/index.md",
)

FORBIDDEN_PATTERNS = {
    r"dam run --stack": "Use positional Stackfile paths: dam run <stackfile>.",
    r"dam validate --stack": "Use positional Stackfile paths: dam validate <stackfile>.",
    r"dam run my_stackfile\.yaml --task default": "Avoid implying every Stackfile has a default task.",
}


def _expand(root: Path, patterns: tuple[str, ...]) -> list[Path]:
    files: list[Path] = []
    for pattern in patterns:
        files.extend(sorted(root.glob(pattern)))
    return [path for path in files if path.is_file()]


def check_forbidden_patterns(root: Path, files: list[Path]) -> list[str]:
    failures: list[str] = []
    compiled = [(re.compile(pattern), message) for pattern, message in FORBIDDEN_PATTERNS.items()]
    for path in files:
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(root)
        for lineno, line in enumerate(text.splitlines(), start=1):
            for pattern, message in compiled:
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
