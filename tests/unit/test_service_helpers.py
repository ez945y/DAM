"""Service lifecycle regressions using fake services, never the DAM host."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def service_environment(tmp_path):
    binaries = tmp_path / "bin"
    binaries.mkdir()
    curl = binaries / "curl"
    curl.write_text('#!/bin/sh\nexit "${CURL_STATUS:-0}"\n')
    curl.chmod(0o755)
    logs = tmp_path / "logs"
    logs.mkdir()
    return {
        **os.environ,
        "PATH": f"{binaries}:{os.environ['PATH']}",
        "HELPERS": str(ROOT / "scripts/service_helpers.sh"),
        "SERVICE_LOG_DIR": str(logs),
        "DAM_STARTUP_TIMEOUT": "1",
        "CURL_STATUS": "0",
    }


def run_scenario(body, environment):
    preamble = """
set -euo pipefail
info() { echo "$*"; }
die() { echo "$*" >&2; exit 1; }
source "$HELPERS"
"""
    return subprocess.run(
        ["bash", "-c", preamble + body],
        env=environment,
        text=True,
        capture_output=True,
        timeout=20,
    )


def test_no_services_can_exit_cleanly(service_environment):
    result = run_scenario("exit 0", service_environment)
    assert result.returncode == 0, result.stderr
    assert "Services stopped" in result.stdout


def test_ready_wait_finishes_then_shutdown_stops_service(service_environment):
    result = run_scenario(
        """
sleep 30 > "$SERVICE_LOG_DIR/backend.log" 2>&1 &
register_service "$!" "Backend" "$SERVICE_LOG_DIR/backend.log"
wait_for_http "Backend" "http://127.0.0.1/unused" "$SERVICE_LOG_DIR/backend.log"
echo "READY_CONFIRMED"
exit 0
""",
        service_environment,
    )
    assert result.returncode == 0, result.stderr
    assert "READY_CONFIRMED" in result.stdout
    assert "Services stopped" in result.stdout


def test_shutdown_stops_descendant_process(service_environment):
    result = run_scenario(
        """
child_command='sleep 30 & echo $! > "$SERVICE_LOG_DIR/child.pid"; wait'
bash -c "$child_command" > "$SERVICE_LOG_DIR/backend.log" 2>&1 &
register_service "$!" "Backend" "$SERVICE_LOG_DIR/backend.log"
deadline=$((SECONDS + 3))
while [[ ! -f "$SERVICE_LOG_DIR/child.pid" ]]; do
    (( SECONDS < deadline )) || die "Child did not start"
    sleep 0.05
done
exit 0
""",
        service_environment,
    )
    assert result.returncode == 0, result.stderr
    child = int((Path(service_environment["SERVICE_LOG_DIR"]) / "child.pid").read_text())
    # A zombie has terminated even if the host has not yet reaped it.
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            return
        state = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(child)],
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.strip()
        if not state or state.startswith("Z"):
            return
        time.sleep(0.05)
    pytest.fail(f"Service descendant {child} survived shutdown")


def test_timeout_reports_log_without_claiming_ready(service_environment):
    result = run_scenario(
        """
echo "waiting for initialization" > "$SERVICE_LOG_DIR/backend.log"
sleep 30 >> "$SERVICE_LOG_DIR/backend.log" 2>&1 &
register_service "$!" "Backend" "$SERVICE_LOG_DIR/backend.log"
wait_for_http "Backend" "http://127.0.0.1/unused" "$SERVICE_LOG_DIR/backend.log"
echo "READY_CONFIRMED"
""",
        {**service_environment, "CURL_STATUS": "1"},
    )
    assert result.returncode == 1
    assert "did not respond" in result.stderr
    assert "waiting for initialization" in result.stderr
    assert "READY_CONFIRMED" not in result.stdout


@pytest.mark.parametrize("exit_status", [0, 7])
def test_unexpected_exit_stops_monitor_and_reports_cause(service_environment, exit_status):
    result = run_scenario(
        """
bash -c 'echo "frontend failed"; exit "$EXIT_STATUS"' > "$SERVICE_LOG_DIR/frontend.log" 2>&1 &
register_service "$!" "Frontend" "$SERVICE_LOG_DIR/frontend.log"
sleep 30 > "$SERVICE_LOG_DIR/backend.log" 2>&1 &
register_service "$!" "Backend" "$SERVICE_LOG_DIR/backend.log"
monitor_services
""",
        {**service_environment, "EXIT_STATUS": str(exit_status)},
    )
    assert result.returncode == 1
    assert f"Frontend exited unexpectedly (status {exit_status})" in result.stderr
    assert "frontend failed" in result.stderr
    assert "Services stopped" in result.stdout


@pytest.mark.parametrize("value", ["0", "-1", "abc", "99999999999999999999"])
def test_invalid_startup_timeout_is_rejected(service_environment, value):
    result = run_scenario("echo UNEXPECTED", {**service_environment, "DAM_STARTUP_TIMEOUT": value})
    assert result.returncode != 0
    assert "DAM_STARTUP_TIMEOUT" in result.stderr
    assert "UNEXPECTED" not in result.stdout
