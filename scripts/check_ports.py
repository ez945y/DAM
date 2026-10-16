"""Check local service ports without terminating their current owners."""

from __future__ import annotations

import argparse
import socket
import sys


def check_ports(ports: list[int]) -> int:
    unavailable = False
    for port in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError as exc:
                print(f"Port {port} is unavailable: {exc}", file=sys.stderr)
                unavailable = True
    if unavailable:
        print(
            "Stop the service using the port before starting DAM. No processes were stopped.",
            file=sys.stderr,
        )
    return int(unavailable)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ports", nargs="+", type=int)
    args = parser.parse_args()
    if any(port < 1 or port > 65535 for port in args.ports):
        parser.error("ports must be between 1 and 65535")
    return check_ports(args.ports)


if __name__ == "__main__":
    raise SystemExit(main())
