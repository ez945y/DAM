# ── docker/Dockerfile ────────────────────────────────────────────────────────
#
# Three named stages — select with --target:
#
#   builder  ghcr.io/pyo3/maturin:main  →  compiles dam_rs wheel to /dist
#   tester   FROM builder               →  cargo CI checks (baked) + pytest CMD
#   runner   python:3.11-slim           →  wheel installed; no build tools
#
# Usage:
#   docker build --target runner  -t dam_service:latest -f docker/Dockerfile .
#   docker build --target tester  -t dam_service:test   -f docker/Dockerfile .
#   docker build --target builder -t dam_service:build  -f docker/Dockerfile .
#
# Build args:
#   PYTHON_VERSION   Python interpreter version  (default: 3.11)
#   EXTRAS           pip extras in runner        (default: services)

ARG PYTHON_VERSION=3.11

# ══════════════════════════════════════════════════════════════════════════════
# builder — compile Rust extension → /dist/dam_rs-*.whl
#
# Base: ghcr.io/pyo3/maturin:main (manylinux + Rust toolchain + maturin)
# Python lives at /opt/python/cpXX-cpXX/bin/ — symlinks added to PATH.
# ══════════════════════════════════════════════════════════════════════════════
FROM ghcr.io/pyo3/maturin:main AS builder

ARG PYTHON_VERSION=3.11

# Python is not on PATH in the manylinux base.  Add /usr/local/bin symlinks so
# `python3.11`, `python3`, and `pip` resolve without hard-coded paths.
RUN PYVER=$(echo "${PYTHON_VERSION}" | tr -d '.') && \
    PYBIN="/opt/python/cp${PYVER}-cp${PYVER}/bin" && \
    ln -sf "${PYBIN}/python${PYTHON_VERSION}" /usr/local/bin/python${PYTHON_VERSION} && \
    ln -sf "${PYBIN}/python${PYTHON_VERSION}"  /usr/local/bin/python3 && \
    ln -sf "${PYBIN}/pip${PYTHON_VERSION}"     /usr/local/bin/pip

# Copy only Rust workspace — layer is cached until dam-rust/ changes
COPY dam-rust/ /build/dam-rust/

# Compile release wheel; maturin resolves the interpreter via the symlink above
RUN cd /build/dam-rust/dam-py && \
    maturin build --release --out /dist/ --interpreter python${PYTHON_VERSION}

# ══════════════════════════════════════════════════════════════════════════════
# tester — Rust CI baked in at build time; pytest is the runtime CMD
#
# cargo fmt --check / clippy / test run as RUN instructions: the image build
# fails immediately if any Rust check fails.  No shell conditionals needed.
# ══════════════════════════════════════════════════════════════════════════════
FROM builder AS tester

ARG PYTHON_VERSION=3.11
ARG EXTRAS=dev

# The maturin base image sets ENTRYPOINT ["maturin"] — reset it so pytest can run directly.
ENTRYPOINT []

WORKDIR /workspace

# ── Python deps layer (cached until pyproject.toml changes) ──────────────────
COPY pyproject.toml ./
RUN mkdir -p dam && touch dam/__init__.py && \
    python${PYTHON_VERSION} -m pip install --no-cache-dir --prefer-binary ".[${EXTRAS}]" && \
    python${PYTHON_VERSION} -m pip install --no-cache-dir /dist/*.whl

# ── Rust CI checks — baked into the image; build fails on any violation ───────
RUN cd /build/dam-rust && \
    cargo fmt --check && \
    cargo clippy -- -D warnings && \
    cargo test --release

# ── Full project source ───────────────────────────────────────────────────────
COPY . .
RUN python${PYTHON_VERSION} -m pip install --no-cache-dir -e ".[${EXTRAS}]"

CMD ["python3.11", "-m", "pytest", "tests/", "-v", "--tb=short", "-m", "not hardware and not ros2"]

# ══════════════════════════════════════════════════════════════════════════════
# runner — minimal production image; no Rust toolchain, no build tools
# ══════════════════════════════════════════════════════════════════════════════
FROM python:${PYTHON_VERSION}-slim AS runner

ARG EXTRAS=services

WORKDIR /workspace

# ── Permanent runtime libs ────────────────────────────────────────────────────
# • libusb-1.0-0          — pyserial needs this to open /dev/ttyACM* / ttyUSB*
# • libgl1 / libglib2.0-0 — required by opencv-python-headless at import time
RUN pip install --no-cache-dir uv \
    && apt-get update && apt-get install -y --no-install-recommends \
        libusb-1.0-0 \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# ── Python deps layer (cached until pyproject.toml changes) ──────────────────
COPY pyproject.toml ./
# Build-time only: build-essential (gcc + libc6-dev + make) and linux-libc-dev
# are needed to compile evdev from source on aarch64 (no binary wheel exists).
# They are purged after pip install to keep the image lean.
# pynput (keyboard control) and evdev are uninstalled afterwards — they are
# only needed for lerobot's interactive CLI, not for our server runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        linux-libc-dev \
    && mkdir -p dam && touch dam/__init__.py \
    && uv pip install --system ".[${EXTRAS}]" \
    && uv pip uninstall --system pynput evdev 2>/dev/null || true \
    && (uv pip uninstall --system opencv-python 2>/dev/null || true) \
    && uv pip install --system opencv-python-headless \
    && apt-get purge -y --auto-remove build-essential linux-libc-dev \
    && rm -rf /var/lib/apt/lists/* /root/.cache/uv

# ── Compiled Rust extension — copied from builder, no toolchain included ─────
COPY --from=builder /dist /tmp/dam-dist
RUN uv pip install --system /tmp/dam-dist/*.whl && rm -rf /tmp/dam-dist

# ── Full Python source ────────────────────────────────────────────────────────
COPY . .
# All deps already installed above; --no-deps re-registers dam in editable mode
# without triggering a rebuild of evdev (build tools were purged).
RUN uv pip install --system -e . --no-deps

CMD ["bash"]
