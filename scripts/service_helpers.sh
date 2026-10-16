#!/usr/bin/env bash
# Shared lifecycle helpers for run.sh and run_prod.sh (Bash 3.2 compatible).
# Callers provide info(), die(), and SERVICE_LOG_DIR before sourcing this file.

# Each background service gets its own process group, including npm children.
# Shutdown only targets groups started by this shell.
set -m
_child_pids=()
_child_names=()
_child_logs=()
_child_count=0
_shutdown_done=0

STARTUP_TIMEOUT="${DAM_STARTUP_TIMEOUT:-60}"
case "$STARTUP_TIMEOUT" in
    ''|*[!0-9]*) die "DAM_STARTUP_TIMEOUT must be a positive number of seconds." ;;
esac
[[ ${#STARTUP_TIMEOUT} -le 6 ]] || die "DAM_STARTUP_TIMEOUT is too large."
# Force decimal interpretation (e.g. an environment value of 08).
STARTUP_TIMEOUT=$((10#$STARTUP_TIMEOUT))
(( STARTUP_TIMEOUT > 0 && STARTUP_TIMEOUT <= 3600 )) \
    || die "DAM_STARTUP_TIMEOUT must be between 1 and 3600 seconds."

register_service() {
    _child_pids+=("$1")
    _child_names+=("$2")
    _child_logs+=("$3")
    _child_count=$((_child_count + 1))
}

show_service_log() {
    local log_file="$1"
    if [[ -f "$log_file" ]]; then
        echo "Recent output (${log_file}):" >&2
        tail -30 "$log_file" >&2 || true
    fi
}

check_services() {
    local index pid status
    for ((index = 0; index < _child_count; index++)); do
        pid="${_child_pids[$index]}"
        if ! kill -0 "$pid" 2>/dev/null; then
            status=0
            wait "$pid" || status=$?
            show_service_log "${_child_logs[$index]}"
            die "${_child_names[$index]} exited unexpectedly (status ${status}). Logs: ${SERVICE_LOG_DIR}"
        fi
    done
}

wait_for_http() {
    local name="$1" url="$2" log_file="$3"
    local deadline=$((SECONDS + STARTUP_TIMEOUT))
    while (( SECONDS < deadline )); do
        # Check all services, including a sibling that may have failed while
        # this endpoint is still warming up.
        check_services
        if curl --noproxy '*' -fsS --connect-timeout 1 --max-time 2 "$url" >/dev/null 2>&1; then
            check_services
            info "${name} ready at ${url}"
            return 0
        fi
        sleep 0.25
    done
    show_service_log "$log_file"
    die "${name} did not respond at ${url} within ${STARTUP_TIMEOUT}s. Logs: ${SERVICE_LOG_DIR}"
}

monitor_services() {
    while true; do
        check_services
        sleep 0.5
    done
}

_shutdown() {
    local index pid deadline any_running
    [[ "$_shutdown_done" == "0" ]] || return 0
    _shutdown_done=1
    # Repeated Ctrl+C must not interrupt cleanup and leave an owned group alive.
    trap '' INT TERM
    info "Shutting down services started by this command…"
    for ((index = 0; index < _child_count; index++)); do
        pid="${_child_pids[$index]}"
        kill -TERM -- "-$pid" 2>/dev/null || true
    done
    deadline=$((SECONDS + 10))
    while (( SECONDS < deadline )); do
        any_running=false
        for ((index = 0; index < _child_count; index++)); do
            pid="${_child_pids[$index]}"
            if kill -0 -- "-$pid" 2>/dev/null; then
                any_running=true
            fi
        done
        $any_running || break
        sleep 0.1
    done
    for ((index = 0; index < _child_count; index++)); do
        pid="${_child_pids[$index]}"
        if kill -0 -- "-$pid" 2>/dev/null; then
            info "Service group ${pid} did not stop within 10s; terminating it."
            kill -KILL -- "-$pid" 2>/dev/null || true
        fi
        wait "$pid" 2>/dev/null || true
    done
    info "Services stopped. Logs retained at ${SERVICE_LOG_DIR}."
}

trap 'exit 130' INT
trap 'exit 143' TERM
trap _shutdown EXIT
