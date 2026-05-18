#!/usr/bin/env python3
"""Sync DAM release versions from the root pyproject.toml file."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "pyproject.toml"


def load_version() -> str:
    data = tomllib.loads(VERSION_FILE.read_text())
    version = data.get("project", {}).get("version")
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise SystemExit("pyproject.toml must contain [project].version like 0.4.0")
    return version


def replace(path: Path, pattern: str, repl: str, *, count: int = 0) -> None:
    text = path.read_text()
    new_text = re.sub(pattern, repl, text, count=count, flags=re.MULTILINE)
    if new_text != text:
        path.write_text(new_text)


def sync_json_package(path: Path, version: str) -> None:
    data = json.loads(path.read_text())
    data["version"] = version
    path.write_text(json.dumps(data, indent=2) + "\n")


def sync_package_lock(path: Path, version: str) -> None:
    data = json.loads(path.read_text())
    data["version"] = version
    root_pkg = data.get("packages", {}).get("")
    if isinstance(root_pkg, dict):
        root_pkg["version"] = version
    path.write_text(json.dumps(data, indent=2) + "\n")


def sync_cargo_lock(path: Path, version: str) -> None:
    project_crates = {
        "action-bus",
        "dam-py",
        "decision-aggregator",
        "image-writer",
        "loopback",
        "mcap-writer",
        "metric-bus",
        "observation-bus",
        "risk-controller",
        "serializer-bus",
        "watchdog",
    }
    lines = path.read_text().splitlines()
    current_name: str | None = None
    for i, line in enumerate(lines):
        name_match = re.fullmatch(r'name = "([^"]+)"', line)
        if name_match:
            current_name = name_match.group(1)
            continue
        if current_name in project_crates and line.startswith("version = "):
            lines[i] = f'version = "{version}"'
            current_name = None
    path.write_text("\n".join(lines) + "\n")


def main() -> None:
    version = load_version()

    replace(ROOT / "uv.lock", r'(name = "dam"\nversion = )"[^"]+"', rf'\1"{version}"')

    sync_json_package(ROOT / "dam-console/package.json", version)
    sync_package_lock(ROOT / "dam-console/package-lock.json", version)

    for path in [
        *sorted((ROOT / "dam-rust/crates").glob("*/Cargo.toml")),
        ROOT / "dam-rust/dam-py/Cargo.toml",
    ]:
        replace(path, r'^version = "[^"]+"', f'version = "{version}"', count=1)
    replace(
        ROOT / "dam-rust/dam-py/pyproject.toml",
        r'^version = "[^"]+"',
        f'version = "{version}"',
        count=1,
    )
    sync_cargo_lock(ROOT / "dam-rust/Cargo.lock", version)

    replace(ROOT / "mkdocs.yml", r"^  version: .+$", f"  version: {version}", count=1)


if __name__ == "__main__":
    main()
