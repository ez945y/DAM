# Glossary

Technical terms used throughout DAM documentation.

---

## A

**Action**
A proposed robot command from the ML policy. Contains target joint positions, velocities, or forces. DAM evaluates every action against the guard stack before hardware execution.

**Adapter**
A software interface that connects DAM to external hardware or policies. Examples: LeRobot adapter (SO-ARM101), ROS 2 adapter, custom policy adapter. Adapters are duck-typed (no inheritance required).

---

## B

**Boundary**
A task-specific safety envelope that constrains robot behavior. Defined in a Stackfile and attached to a guard layer such as L0, L1, L2, or L3.

**Boundary Container**
A data structure holding boundary nodes. Three types: **Single** (one static node), **List** (sequential phases), **Graph** (arbitrary DAG).

**Boundary Node**
A single safety configuration within a container. In a Stackfile, a node normally names a `callback`, passes `params`, and may define a `fallback` and `timeout_sec`.

---

## C

**Clamp**
An action adjustment to satisfy constraints without rejection. Example: if proposed velocity exceeds limit, DAM scales all joints down proportionally. Most restrictive decision wins; clamped actions still execute.

**Callback**
A registered function that evaluates a named boundary rule. Built-in callbacks cover common checks; custom callbacks are optional and should be introduced after the Stackfile workflow is clear.

**Constraint**
A safety rule expressed through a boundary callback and its parameters. Common parameters include `max_speed`, `bounds`, `max_velocities`, `upper`, and `lower`.

**Control Plane**
The Python layer handling Stackfile parsing, component lifecycle, hot-reload logic, and telemetry collection. Coordinates guard evaluation and data plane execution.

**Cycle**
One iteration of the control loop: read observations → propose action → evaluate guards → execute or reject → log telemetry. Typical frequency: 50 Hz.

---

## D

**Data Plane**
The Rust layer running the real-time critical path: observation multiplexing, guard evaluation, action validation, and dispatch. Eliminates GIL contention and helps provide more predictable latency. Optional but recommended for hardware-oriented development.

**Decision**
The outcome of guard evaluation: **PASS** (execute action), **CLAMP** (adjust action), or **REJECT** (forbid action).

---

## E

**Emergency Stop (E-Stop)**
A fallback strategy that immediately halts all motion and activates the hardware emergency stop circuit if available.

**Evaluation Order**
The sequence in which active guard layers and their boundary callbacks are evaluated for a cycle. Users usually only need the final rule: the most restrictive decision wins.

---

## F

**Fail-to-Reject**
Core safety principle: any guard exception, timeout, or unexpected behavior results in immediate action rejection. No graceful degradation into unsafe execution.

**Fallback**
A recovery strategy executed when a constraint is violated. Common types include **hold_position** (stop and stay put), **retreat** (low-speed retreat), **slow_down**, **wait_and_retry**, and **emergency_stop** (immediate halt).

**Fallback Registry**
A data structure mapping fallback names to implementations. Supports fallback escalation chains.

**Feature Extractor**
A neural network (typically pretrained ResNet) that encodes observations into a low-dimensional vector for OOD detection. Trained during the L0 guard setup phase.

**Forward Kinematics (FK)**
Computing end-effector position/orientation from joint angles. Used by motion and task checks when workspace or end-effector constraints are configured.

---

## G

**Graph Container**
A boundary type where nodes form a directed acyclic graph (DAG). Allows arbitrary transitions between nodes. Currently Python-only (not supported via Stackfile).

**Guard**
An independent constraint evaluator running once per cycle. DAM has 4 guards (L0–L3). Each uses different inputs and logic. Decisions are combined using "most restrictive wins."

**Guard Stack**
The collection of all 4 guard layers (L0–L3) evaluating in sequence. Each layer votes independently; the most restrictive decision is applied.

---

## H

**Hardware Adapter** (See **Adapter**)

**Hardware Sink**
A software interface to robot hardware accepting validated actions and executing them on motors/actuators. Must implement `write()` and `health_check()` methods.

**Hardware Source**
A software interface reading sensor data from robot hardware (joint positions, velocities, forces, cameras). Feeds observations into DAM.

**Health Status**
Information about hardware health returned by a sink: motor fault flags, temperature, watchdog status, connection state. Evaluated by L3 guard.

**Hold Position**
A fallback strategy that commands zero velocity, keeping the robot at its current position without moving.

**Hot-Reload**
Runtime update of boundary constraints and guard parameters from a modified Stackfile, without stopping the control loop. Changes apply atomically at cycle boundaries.

---

## I

**Inference** (See **Policy**)

---

## J

**Joint Limits**
Hardware constraints on joint positions. In Stackfiles, the built-in `joint_position_limits` callback reads `upper` and `lower` arrays in radians.

---

## K

**Kinematic Constraints**
Robot motion constraints based on geometry: joint limits, workspace bounds, velocity/acceleration limits. Enforced by L1 motion guard.

---

## L

**L0 Guard** (See **OOD Detection**)

**L1 Guard** (See **Motion Safety**)

**L2 Guard** (See **Task Execution**)

**L3 Guard** (See **Hardware Monitoring**)

**Layer** (See **Guard**)

**LeRobot**
An open-source robotics framework from Hugging Face. DAM has a built-in adapter for LeRobot hardware (SO-ARM101, Koch v1.1).

**List Container**
A boundary type with multiple nodes activated sequentially. Runtime advances to next node via `advance_container()`. Used for multi-phase tasks.

---

## M

**MCAP**
A standardized record format for robotics data. DAM uses MCAP for the loopback buffer, capturing ±30 seconds of sensor data and guard decisions around safety violations.

**Memory Bank**
A precomputed nearest-neighbor data structure used by L0 OOD detection. Built during training; stores reference feature vectors from normal operations.

**Motion Guard** (See **L1 Guard**)

---

## N

**Nearest Neighbor (NN)**
An OOD detection method: compute distance from current observation's feature vector to closest point in the memory bank. High distance = unfamiliar state.

---

## O

**Observation**
Current state of the robot and environment, including joint positions, velocities, forces, camera images, etc. Read from hardware sources and passed to the policy and guards.

**OOD Detection** (L0)
Guard layer detecting out-of-distribution observations. Rejects actions when the robot enters an unfamiliar state. Uses memory bank or Welford z-score methods.

---

## P

**Pass**
Guard decision allowing the action to execute unmodified.

**Phase**
A stage in a multi-phase task. Example: "reach", "grasp", and "lift" phases in pick-and-place. Typically each phase is represented by a boundary node with its own callback parameters.

**Policy**
The ML model (PyTorch, Diffusion Policy, ACT, etc.) that proposes actions given observations. DAM does NOT modify the policy; it validates its outputs.

**Policy Adapter**
Software interface wrapping the policy. Implements `step(obs) → action` method.

**Simulation Lookahead**
Future optional checker that may simulate actions before execution using a physics engine. Not part of the current built-in L0-L3 runtime stack.

---

## R

**Reject**
Guard decision forbidding action execution. Hardware is not commanded; a fallback strategy is activated instead.

**Retreat**
A fallback strategy moving the robot at low speed along a predefined safe path away from the error condition.

**Risk Controller**
A Phase 2 feature that aggregates risk levels across a time window and triggers escalation (e.g., hold → retreat → e-stop) when risk exceeds thresholds.

**Risk Level**
A telemetry metric indicating severity: NORMAL → ELEVATED → CRITICAL → EMERGENCY. Computed from guard decisions and latency.

**ROS 2**
Robot Operating System 2. DAM has a built-in adapter for ROS 2 nodes, supporting joint states and trajectory messages.

**Runtime**
The main DAM execution engine (`GuardRuntime`). Orchestrates sources, policy, guards, sinks, and telemetry. Can run in managed mode (fixed frequency) or passive mode (caller-driven).

---

## S

**Safe Retreat** (See **Retreat**)

**Safety Model**
The conditions, assumptions, and limits that describe how DAM is designed to behave when guards and boundaries are configured correctly. See [Safety Model](../concepts/safety.md) before using hardware.

**Sensor**
Hardware device providing observations: encoders (joint position), IMUs, force/torque sensors, cameras. Read by sources.

**Single Container**
A boundary type with one static node active for the entire task.

**Sink** (See **Hardware Sink**)

**Source** (See **Hardware Source**)

**Stackfile**
YAML configuration file defining hardware adapters, policy, guards, boundaries, and tasks. No Python code required for tier-1 deployments. Can be hot-reloaded.

**Stale Observation**
An observation older than the configured threshold (e.g., 0.1 seconds). DAM warns or rejects if observations become stale, indicating sensor failure.

---

## T

**Task**
A named job that activates one or more boundary containers. Example: "pick_and_place" task activates "reach" and "place" boundaries.

**Task Execution Guard** (L2)
Guard layer enforcing task-specific boundary callbacks such as speed limits, workspace bounds, force limits, and node timeouts.

**Telemetry**
Real-time data logged by DAM: cycle time, risk level, guard decisions, latencies. Streamed via WebSocket and queryable via REST API.

**Timeout**
Maximum time a boundary node or guard check can be active. If exceeded, DAM rejects the action under fail-to-reject semantics and triggers the configured fallback path.

---

## U

**Upper Limits** (See **Joint Limits**)

---

## V

**Velocity Limits**
Constraints on joint rotation speed (rad/s). Enforced by L1 guard via proportional scaling of all joints.

---

## W

**Watchdog**
A timeout mechanism for detecting when the control loop exceeds its configured budget. If cycle time exceeds budget, watchdog behavior should trigger rejection or escalation according to configuration.

**Welford Z-Score**
An online statistical method for OOD detection. Maintains running mean and variance; rejects if any dimension's z-score exceeds threshold.

**Workspace Bounds**
3D spatial limits (x, y, z ranges in meters) for the end-effector. L1 workspace boundaries can halt or clamp motion, while L2 task workspace boundaries reject task actions outside the configured zone.

---

## Z

**Z-Score** (See **Welford Z-Score**)

---

## See Also

- [Architecture Overview](../concepts/architecture.md) — System design
- [Guard Stack Explained](../concepts/guards-explained.md) — Detailed guard behavior
- [Boundary System](../concepts/boundaries.md) — Task configuration
- [Safety Model](../concepts/safety.md) — Safety assumptions and limitations
