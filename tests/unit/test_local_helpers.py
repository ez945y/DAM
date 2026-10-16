"""Regression checks for local startup without launching robot services."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_port_check_preserves_listener():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        command = [sys.executable, str(ROOT / "scripts/check_ports.py"), str(port)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
        assert result.returncode == 1
        assert f"Port {port} is unavailable" in result.stderr
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            connection, _ = listener.accept()
            connection.close()
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("port", ["0", "65536", "-1"])
def test_port_check_rejects_invalid_port(port):
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_ports.py"), port],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert "ports must be between" in result.stderr


@pytest.mark.parametrize("healthy", [True, False])
def test_init_uses_project_python_from_another_directory(tmp_path, healthy):
    project = tmp_path / "project with spaces"
    binaries = project / ".venv/bin"
    binaries.mkdir(parents=True)
    shutil.copy(ROOT / "init.sh", project / "init.sh")
    python = binaries / "python"
    python.write_text(
        '#!/bin/bash\nprintf "%s|%s\\n" "$PWD" "$*" >> calls.log\n'
        + ("exit 0\n" if healthy else "exit 1\n")
    )
    python.chmod(0o755)
    result = subprocess.run(
        ["bash", str(project / "init.sh")],
        cwd=tmp_path,
        env={**os.environ, "RUN_START_COMMAND": "0"},
        capture_output=True,
        text=True,
        timeout=10,
    )
    calls = (project / "calls.log").read_text().splitlines()
    assert all(line.startswith(f"{project.resolve()}|") for line in calls)
    if healthy:
        assert result.returncode == 0, result.stderr
        assert any("|-m pytest tests/unit/" in line for line in calls)
    else:
        assert result.returncode == 1
        assert "make setup" in result.stderr
        assert not any("pytest tests/unit/" in line for line in calls)


def test_frontend_checks_fail_when_dependencies_are_missing(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy(ROOT / "scripts/test.sh", scripts / "test.sh")
    result = subprocess.run(
        ["bash", str(scripts / "test.sh"), "--frontend"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert "Frontend tests unavailable" in result.stdout
    assert "All requested checks passed" not in result.stdout
    assert "Project Python not found" not in result.stderr


def test_rust_only_checks_do_not_require_python_environment(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (tmp_path / "dam-rust").mkdir()
    shutil.copy(ROOT / "scripts/test.sh", scripts / "test.sh")
    # An exported shell function takes precedence over installed toolchains.
    result = subprocess.run(
        [
            "bash",
            "-c",
            'cargo() { echo MOCK_CARGO "$@"; }; export -f cargo; bash "$1" --rust',
            "test-runner",
            str(scripts / "test.sh"),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "MOCK_CARGO test --workspace" in result.stdout
    assert "All requested checks passed" in result.stdout
