"""Bus module — Rust data plane (mandatory).

``dam_rs`` is a compiled Rust extension (PyO3/maturin).  It MUST be built
before importing this module.  There is no Python fallback.

Build instructions:
    cd dam-rust/dam-py
    maturin develop --release        # editable dev install
    # or via Docker:
    docker build -t dam:latest -f docker/Dockerfile .

If the extension is missing, an ImportError is raised immediately so the
problem is caught at startup rather than silently degrading to slower,
non-real-time Python code.
"""

try:
    from dam_rs import (  # noqa: F401
        ActionBus,
        MetricBus,
        ObservationBus,
        RiskController,
        WatchdogTimer,
    )
except ImportError as _err:
    raise RuntimeError(
        "\n\n"
        "  ╔══════════════════════════════════════════════════════════════╗\n"
        "  ║  DAM: Rust extension 'dam_rs' is not installed.             ║\n"
        "  ║                                                              ║\n"
        "  ║  Build it with:                                              ║\n"
        "  ║    cd dam-rust/dam-py && maturin develop --release           ║\n"
        "  ║                                                              ║\n"
        "  ║  Or build the production image:                              ║\n"
        "  ║    docker build --target runner -t dam_engine:latest         ║\n"
        "  ║      -f docker/Dockerfile .                                  ║\n"
        "  ╚══════════════════════════════════════════════════════════════╝\n"
    ) from _err

__all__ = ["ObservationBus", "WatchdogTimer", "RiskController", "MetricBus", "ActionBus"]
