# Installation

This page covers all installation configurations — from the fastest Docker setup to manual Python, Rust, and ROS2 deployments.

---

## Requirements

| Requirement | Minimum Version | Notes |
|-------------|----------------|-------|
| Python | 3.11+ | Required |
| pip or uv | Latest stable | uv is recommended for speed |
| Docker + Compose | 24.0+ | Recommended — simplest setup |
| Rust + maturin | Rust 1.80+ | Optional — needed for Rust data plane |
| ROS2 | Humble or Iron | Optional — needed for ROS2 adapters |
| lerobot | Latest | Optional — needed for SO-ARM101 hardware |

---

## Docker (recommended)

Docker is the fastest way to get a fully reproducible environment with Python, Rust, and all dependencies pre-built. No local toolchain setup required.

```bash
# Clone the repo
git clone https://github.com/ez945y/DAM.git && cd DAM

# Start a full dev shell (Python + Rust data plane compiled)
docker compose run --rm dev
```

Your local source tree is volume-mounted at `/workspace` inside the container — edits on the host are reflected immediately without rebuilding.

| Service | Description | Command |
|---------|-------------|---------|
| `dev` | Full dev shell — Python + Rust compiled | `docker compose run --rm dev` |
| `dev-light` | Python-only — faster cold build, no Rust | `docker compose run --rm dev-light` |
| `dev-lerobot` | Full dev + LeRobot hardware driver | `docker compose run --rm dev-lerobot` |
| `test` | Pure-Python CI runner | `docker compose run --rm test` |
| `test-rust` | CI runner with Rust data plane | `docker compose run --rm test-rust` |
| `lint` | ruff + mypy checks | `docker compose run --rm lint` |
| `rust-ci` | cargo fmt + clippy + test | `docker compose run --rm rust-ci` |
| `ci` | Full CI: lint → test → rust-ci | `docker compose run --rm ci` |

**Hardware access (USB passthrough):** Use the helper script that reads your Stackfile and generates the override automatically:

```bash
./scripts/dam-compose.sh \
    --stackfile examples/stackfiles/so101_act_pick_place.yaml \
    -- --profile hardware run --rm dev-hardware
```

---

## Quick Install (Python only)

The Python-only install is sufficient for development, simulation, and policy testing. No Rust toolchain required.

```bash
pip install "dam[dev]"
```

Using uv (recommended — faster dependency resolution):

```bash
uv pip install "dam[dev]"
```

Verify:

```bash
python -c "import dam; print(dam.__version__)"
```

---

## With Policy Inference (PyTorch + MCAP logging)

Required if you are loading pretrained policies (ACT, Diffusion Policy, etc.) or want MCAP violation logs.

```bash
pip install "dam[dev,torch]"
```

This adds:
- `torch>=2.0` — policy inference
- `mcap>=1.1` — binary log format for the loopback violation buffer

---

## With LeRobot Hardware Driver

Required for SO-ARM101 / Koch v1.1 hardware control via the LeRobot adapter.

```bash
pip install "dam[dev,torch,lerobot]"
```

> **Note:** `lerobot` installs additional system dependencies (libusb, etc.). See the [LeRobot documentation](https://github.com/huggingface/lerobot) for platform-specific setup steps.

---

## With REST API + UI Console

Required to run the DAM API server and real-time dashboard.

```bash
pip install "dam[dev,services]"
```

Start the API server:

```bash
uvicorn dam.services.api:app --host 0.0.0.0 --port 8080
```

Then open `http://localhost:8080` in your browser.

---

## With ROS2

The `rclpy` package is **not available on PyPI** — it is distributed by the ROS2 installer. You must install ROS2 first.

**Step 1 — Install ROS2 Humble or Iron:**

Follow the official ROS2 installation guide for your platform:
- Ubuntu 22.04: [Humble install guide](https://docs.ros.org/en/humble/Installation/Ubuntu-Install-Debs.html)
- Ubuntu 24.04: [Iron install guide](https://docs.ros.org/en/iron/Installation/Ubuntu-Install-Debs.html)

**Step 2 — Source the ROS2 environment:**

```bash
source /opt/ros/humble/setup.bash
# or
source /opt/ros/iron/setup.bash
```

**Step 3 — Install DAM with the ROS2 extras group:**

```bash
pip install "dam[ros2]"
```

This installs `transforms3d>=0.4`, a pure-Python quaternion/rotation library used by the ROS2 adapter. The `rclpy` import will succeed because it is already available from the ROS2 system install.

**Step 4 — Verify:**

```bash
python -c "import rclpy; import dam; print('ROS2 + DAM ready')"
```

---

## Compile Rust Data Plane (optional but recommended for production)

The Rust data plane provides the `ObservationBus`, `ActionBus`, `WatchdogTimer`, and `RiskController` — all of which run outside the Python GIL for deterministic, real-time-safe throughput. In development and simulation, DAM falls back to pure-Python implementations automatically.

**Step 1 — Install the Rust toolchain:**

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source ~/.cargo/env
```

**Step 2 — Compile and install the Python extension:**

```bash
# From the repository root
cd dam-rust/dam-py
maturin develop --release
```

**Step 3 — Verify:**

```bash
python -c "from dam.bus import HAS_RUST; print(HAS_RUST)"
# True
```

If `HAS_RUST` prints `False`, the compiled extension was not found. Re-run `maturin develop --release` and ensure you are in the same Python environment.

---

## Verify Installation

After any installation method, verify the core package and run the pure-Python test suite:

```bash
python -c "import dam; print(dam.__version__)"

pytest tests/ -m "not hardware and not rust and not ros2" -v
```

All tests should pass. Expected output ends with something like:

```
passed in 2.3s
```
