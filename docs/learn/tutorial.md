# Complete Tutorial

A structured learning path to master DAM from zero to production. Estimated time: **2–3 hours** (can be done in modules).

---

## Learning Path Overview

```
START → Module 1 → Module 2 → Module 3 → Module 4 → Module 5 → END
        15 min     20 min     25 min     30 min     20 min
```

### What You'll Learn

| Module | Topic | Outcome |
|--------|-------|---------|
| 1 | **Core Concepts** | Understand guard stack, fail-to-reject, defense-in-depth |
| 2 | **Stackfiles** | Write production-ready YAML configurations |
| 3 | **Boundaries** | Design multi-phase task constraints |
| 4 | **Deployment** | Run on real hardware (or simulator) |
| 5 | **Monitoring** | Use console and API to observe safety |

---

## Module 1: Core Concepts (15 min)

### 1.1 The Problem

You have a robot and an ML policy. The policy sometimes makes bad decisions:
- Moves joints beyond limits
- Ignores workspace constraints
- Proposes dangerous force levels
- Hallucinates in unfamiliar states

**Solution:** A safety layer that intercepts **every** policy output.

### 1.2 The Guard Stack

DAM evaluates actions through **5 independent layers**:

```
Policy Output
    ↓
[ L0 OOD ]        ← Is the observation familiar?
[ L1 Preflight ]  ← Will this work physically?
[ L2 Motion ]     ← Are joints and workspace safe?
[ L3 Task ]       ← Does this fit the task?
[ L4 Hardware ]   ← Is the robot healthy?
    ↓
DECISION
```

**Each layer asks a different question.** If any layer says "REJECT", the action is rejected.

### 1.3 Core Principle: Fail-to-Reject

The most important rule in DAM:

> Any guard timeout, exception, or crash → **immediate rejection**

This means:
- ✅ You can't accidentally execute unsafe code
- ✅ If a guard breaks, you default to rejection
- ✅ No "best effort" unsafe execution

### 1.4 Key Decisions

Each guard can make three decisions:

1. **PASS** — action is safe, execute it
2. **CLAMP** — adjust action to be safe, then execute
3. **REJECT** — action is unsafe, forbid execution and trigger fallback

**Example:**

```
Policy: "Move joint 1 to 2.0 rad"
Limit:  "Joint 1 max = 1.57 rad"
L2 Guard: CLAMP to 1.57 rad, then execute
```

---

### Exercise 1.1: Understand Guard Layering

Read these two scenarios. What should DAM do?

**Scenario A:**
- L0 OOD: PASS (observation is normal)
- L2 Motion: REJECT (exceeds joint limit)
- L3 Task: (not evaluated)
- **Result:** Action is **REJECTED**

**Scenario B:**
- L0 OOD: PASS
- L2 Motion: CLAMP (scales velocity down)
- L3 Task: PASS
- **Result:** Action is **CLAMPED** and executed

**Key insight:** Most restrictive decision wins.

### Quiz

1. What happens if L3 rejects but L2 passes?
   - Answer: **L3 rejection wins**; the action is **rejected**

2. If L2 clamps an action and L3 passes, what happens?
   - Answer: The **clamped** action is executed

3. If all guards pass but the guard latency exceeds timeout?
   - Answer: **Rejected** (watchdog timeout triggers fail-to-reject)

---

## Module 2: Stackfiles (20 min)

### 2.1 What is a Stackfile?

A **Stackfile** is a YAML file that configures DAM without Python code.

```yaml
dam:
  version: "1"

guards:
  builtin:
    motion: { ... }      # L2 config
    ood: { ... }         # L0 config

boundaries:
  containers:
    reach: { ... }       # Task boundary

tasks:
  my_task: { ... }       # Task definition
```

**Why YAML?**
- ✅ Non-programmers can modify it
- ✅ Easy to version control
- ✅ Hot-reloadable (modify mid-run)
- ✅ Human-readable

### 2.2 Minimal Stackfile

Here's the absolute minimum:

```yaml
dam:
  version: "1"

guards:
  builtin:
    motion:
      enabled: true
      upper_limits: [1.57, 1.57, 1.57]
      lower_limits: [-1.57, -1.57, -1.57]

boundaries:
  always_active: default
  containers:
    default:
      type: single
      nodes:
        - node_id: default
          constraint: {}

tasks:
  demo:
    boundaries: [default]
```

This enables L2 motion guard only. No other constraints.

### 2.3 Adding More Guards

Enable L0 (OOD detection):

```yaml
guards:
  builtin:
    ood:
      enabled: true
      params:
        nn_threshold: 0.5
    motion:
      enabled: true
      upper_limits: [...]
```

### 2.4 Adding Constraints

Add velocity and workspace limits:

```yaml
guards:
  builtin:
    motion:
      enabled: true
      upper_limits: [1.57, 1.57, 1.57]
      lower_limits: [-1.57, -1.57, -1.57]
      max_velocity: [1.5, 1.5, 1.5]
      bounds: [[-0.5, 0.5], [-0.5, 0.5], [0.0, 1.5]]
```

### 2.5 Task Boundaries

Add task-specific constraints:

```yaml
boundaries:
  always_active: safe_zone
  containers:
    safe_zone:
      type: single
      nodes:
        - node_id: workspace
          constraint:
            max_speed: 0.3
            bounds: [[-0.3, 0.3], [-0.3, 0.3], [0.1, 1.4]]
          fallback: hold_position
          timeout_sec: 300
```

### Exercise 2.1: Build a Stackfile

Create `tutorial_stackfile.yaml` with:
1. L2 motion guard with joint limits
2. A single boundary with max_speed = 0.2
3. Fallback: hold_position

### Exercise 2.2: Validate It

```bash
dam validate --stack tutorial_stackfile.yaml
```

Should print: `✓ Stackfile is valid`

---

## Module 3: Boundaries (25 min)

### 3.1 Single Boundaries

A **single** boundary has one node active for the entire task.

```yaml
boundaries:
  idle:
    type: single
    nodes:
      - node_id: idle_position
        constraint:
          max_speed: 0.05
          bounds: [[-0.1, 0.1], [0.2, 0.3], [0.0, 0.2]]
        fallback: emergency_stop
```

**Use:** Teleoperation, maintenance, holding position.

### 3.2 List Boundaries

A **list** has multiple nodes. You advance through them sequentially.

```yaml
boundaries:
  pick_and_place:
    type: list
    loop: false
    nodes:
      - node_id: reach
        constraint:
          max_speed: 0.3
          bounds: [[-0.35, 0.35], [-0.05, 0.45], [0.01, 0.40]]
        fallback: hold_position
        timeout_sec: 15.0

      - node_id: grasp
        constraint:
          max_speed: 0.08
          bounds: [[-0.20, 0.20], [0.05, 0.35], [0.01, 0.15]]
        fallback: hold_position
        timeout_sec: 8.0

      - node_id: lift
        constraint:
          max_speed: 0.15
        fallback: hold_position
        timeout_sec: 10.0
```

**In code:**

```python
runtime.start_task("pick_and_place")  # Starts at "reach"
# ... control loop ...
runtime.advance_container("pick_and_place")  # Move to "grasp"
# ... control loop ...
runtime.advance_container("pick_and_place")  # Move to "lift"
```

### 3.3 Constraint Types

| Constraint | Example | Behavior |
|-----------|---------|----------|
| `max_speed` | `0.3` | Reject if velocity norm > 0.3 |
| `bounds` | `[[-0.5, 0.5], ...]` | Reject if out of workspace box |
| `max_force_n` | `50.0` | Reject if force > 50 N |
| `callback` | `[my_check]` | Call Python function; reject if returns False |

### 3.4 Fallback Strategies

When a constraint is violated:

| Strategy | Behavior |
|----------|----------|
| `hold_position` | Stop, hold current position |
| `safe_retreat` | Move at low speed away from danger |
| `emergency_stop` | Halt all motion immediately |

### 3.5 Design Pattern: Nested Workspaces

Start conservative, loosen as task progresses:

```yaml
nodes:
  # Phase 1: Conservative
  - node_id: approach
    constraint:
      bounds: [[-0.2, 0.2], [0.1, 0.3], [0.0, 0.2]]

  # Phase 2: Precision (tighter)
  - node_id: insert
    constraint:
      bounds: [[-0.05, 0.05], [0.15, 0.25], [0.0, 0.1]]

  # Phase 3: Lift (wider)
  - node_id: lift
    constraint:
      bounds: [[-0.4, 0.4], [0.05, 0.45], [0.0, 0.5]]
```

### Exercise 3.1: Design a Multi-Phase Task

Create a Stackfile with a **list** boundary for:
1. Reach phase: max_speed = 0.3, large workspace
2. Grasp phase: max_speed = 0.05, tight workspace
3. Lift phase: max_speed = 0.2, medium workspace

---

## Module 4: Deployment (30 min)

### 4.1 Three Deployment Modes

#### Simulation
```python
from dam.runtime.guard_runtime import GuardRuntime

runtime = GuardRuntime.from_stackfile("mystack.yaml")
runtime.register_source("robot", sim_robot)
runtime.register_policy(policy)
runtime.register_sink(sim_sink)

runtime.start_task("my_task")
for _ in range(1000):
    result = runtime.step()
```

#### Managed Mode (Fixed Frequency)
```python
runtime.run()  # Runs at 50 Hz until KeyboardInterrupt
```

#### Hardware (LeRobot)
```python
from dam.runner.lerobot import LeRobotRunner

runner = LeRobotRunner.from_stackfile("mystack.yaml")
runner.start_task("pick_and_place")
runner.run()
```

### 4.2 Testing Checklist

Before deploying to hardware:

- [ ] Validate Stackfile: `dam validate --stack mystack.yaml`
- [ ] Test in simulation: `python run_sim.py` (no errors, no rejections)
- [ ] Test with loose constraints: bounds ±50cm, max_speed = 1.0
- [ ] Test with tight constraints: gradually decrease bounds
- [ ] Test fallback behavior: manually trigger rejections
- [ ] Test hot-reload: modify Stackfile mid-run
- [ ] Monitor latency: all cycles < 5ms at 50 Hz

### 4.3 Hardware Setup Example (LeRobot)

```yaml
dam:
  version: "1"

hardware:
  preset: so101_follower
  sources:
    follower:
      type: lerobot
      port: /dev/tty.usbmodem5AA90244141
      cameras:
        top:
          type: opencv
          index: 0

policy:
  type: lerobot
  model_id: lerobot/aloha-2-mobile-aloha/2024-07-29

guards:
  builtin:
    motion:
      enabled: true
      upper_limits: [1.57, 1.57, 1.57, 1.57, 1.57, 0.08]
      lower_limits: [-1.57, -1.57, -1.57, -1.57, -1.57, 0.0]
      max_velocity: [1.0, 1.0, 1.0, 1.0, 1.0, 0.3]

boundaries:
  always_active: safe_zone
  containers:
    safe_zone:
      type: single
      nodes:
        - node_id: safe
          constraint:
            max_speed: 0.3
            bounds: [[-0.35, 0.35], [-0.05, 0.45], [0.01, 0.40]]

tasks:
  pick_and_place:
    boundaries: [safe_zone]
```

Deploy:
```python
runner = LeRobotRunner.from_stackfile("hardware_setup.yaml")
runner.start_task("pick_and_place")
runner.run()
```

### Exercise 4.1: Deploy to Simulation

Write a Python script that:
1. Loads your Module 3 Stackfile
2. Creates a simple simulated robot
3. Creates a policy that moves sinusoidally
4. Runs 500 cycles
5. Prints cycle statistics

---

## Module 5: Monitoring (20 min)

### 5.1 Telemetry Basics

Every cycle produces telemetry:

```python
result = runtime.step()

print(f"Risk: {result.risk_level}")           # NORMAL, ELEVATED, CRITICAL, EMERGENCY
print(f"Passed: {result.was_passed}")         # Bool
print(f"Clamped: {result.was_clamped}")       # Bool
print(f"Rejected: {result.was_rejected}")      # Bool
print(f"Guard: {result.rejecting_guard}")     # Which guard rejected?
print(f"Reason: {result.decision_reason}")     # Why?
print(f"Latency: {result.latency_ms}")        # Per-cycle time (ms)
```

### 5.2 REST API

Start the DAM API server:

```bash
uvicorn dam.services.api:app --host 0.0.0.0 --port 8080
```

Query telemetry:

```bash
# Last 50 cycles
curl http://localhost:8080/api/telemetry/history

# Risk statistics
curl http://localhost:8080/api/risk-log/stats

# Export all events
curl http://localhost:8080/api/risk-log/export/json > events.json
```

### 5.3 WebSocket Streaming

Connect to live data stream:

```python
import asyncio
import websockets
import json

async def stream_telemetry():
    uri = "ws://localhost:8080/ws/telemetry"
    async with websockets.connect(uri) as websocket:
        while True:
            data = await websocket.recv()
            event = json.loads(data)
            print(f"Cycle {event['cycle']}: {event['risk_level']}")

asyncio.run(stream_telemetry())
```

### 5.4 DAM Console

Open the web dashboard:

```bash
docker compose up console-dev
# http://localhost:3000
```

Features:
- Real-time risk gauge
- Guard status table
- Latency charts
- Event log with filtering

### 5.5 MCAP Loopback Buffer

When an action is rejected, DAM captures ±30s of context:

```bash
# Export MCAP file
curl http://localhost:8080/api/risk-log/violations/export?format=mcap > violations.mcap

# Inspect with mcap CLI
mcap cat violations.mcap | jq '.'
```

Use this for post-incident analysis: "Why did the robot reject that action?"

### Exercise 5.1: Build a Monitor

Write a Python script that:
1. Runs your Module 4 deployment
2. Prints real-time telemetry (risk, latency, rejections)
3. Tracks statistics (total cycles, reject rate, avg latency)
4. Exports events to JSON when done

---

## Final Project: Multi-Phase Manipulation Task

Combine everything you learned into one complete project.

### Requirements

1. **Stackfile:**
   - L2 motion guard with realistic limits
   - 3-phase list boundary (approach → grasp → lift)
   - Different constraints per phase
   - Fallback strategies

2. **Policy:**
   - Proposes actions for each phase
   - Respects phase transitions

3. **Hardware (Simulated):**
   - 6-DOF arm with joint limits
   - Force/torque sensor for L4 guard
   - Realistic forward kinematics

4. **Monitoring:**
   - API server running
   - Console displaying real-time data
   - Export violations to MCAP

### Success Criteria

- ✅ Stackfile validates without errors
- ✅ Run 500+ cycles without hardware errors
- ✅ All 5 guards enabled and functional
- ✅ Reject/clamp decisions logged
- ✅ Console shows live telemetry
- ✅ MCAP violations exported and readable

### Bonus

- Add a custom callback constraint (L3)
- Test hot-reload by modifying Stackfile mid-run
- Implement fallback escalation (hold → retreat → e-stop)

---

## Learning Outcomes

After completing this tutorial, you can:

✅ Explain the guard stack and fail-to-reject principle
✅ Write production-ready Stackfiles
✅ Design multi-phase task boundaries
✅ Deploy DAM to simulation and hardware
✅ Monitor and analyze safety events
✅ Debug and optimize safety constraints

---

## Next Steps

- **Deploy to real hardware** → [Installation Guide](../installation.md)
- **Advanced safety** → [Safety Guarantees](../concepts/safety.md)
- **Deep dive into guards** → [Guard Stack Explained](../concepts/guards-explained.md)
- **Troubleshoot** → [Glossary](glossary.md)
- **Contribute** → [Contributing](../contributing.md)

---

## Summary Checklist

| Module | Topics | Completed |
|--------|--------|-----------|
| 1 | Guard stack, fail-to-reject, layering | ☐ |
| 2 | Stackfiles, guards, constraints | ☐ |
| 3 | Boundaries, fallbacks, design patterns | ☐ |
| 4 | Simulation, hardware, testing | ☐ |
| 5 | Telemetry, API, console, MCAP | ☐ |

---

**Congratulations! You've completed the DAM tutorial. You're now ready to deploy safety-critical robot systems.** 🚀
