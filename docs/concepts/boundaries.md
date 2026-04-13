# Boundary System

**Boundaries** define the **safety envelope** for task execution. They are task-specific constraints that L3 (Task Execution guard) enforces. This document explains how boundaries work, how to design them, and common patterns.

---

## What Are Boundaries?

Boundaries are YAML-defined safety regions that constrain robot motion during specific tasks.

```yaml
boundaries:
  pick_and_place:
    type: list
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
        fallback: hold_position
        timeout_sec: 8.0
```

**Key idea:** Instead of hard-coding task logic in your policy, you **parameterize** it in a Stackfile. This lets you:
- ✅ Update constraints without retraining
- ✅ Enable/disable task phases dynamically
- ✅ Hot-reload on a running robot
- ✅ Audit and understand task constraints

---

## Container Types

DAM supports three types of boundary containers:

### 1. Single (Static Boundary)

A **single** node is active for the **entire task**.

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

**Use cases:**
- Idle/holding position
- Teleoperation (fixed safety zone)
- Maintenance mode

### 2. List (Sequential Phases)

A **list** contains multiple nodes. The runtime **advances** to the next node explicitly.

```yaml
boundaries:
  pick_and_place:
    type: list
    loop: false      # If true, wraps back to node 0 after last node
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

**Activation:**

```python
runtime.start_task("pick_and_place")  # Starts at node 0: "reach"
# ... control loop runs ...
runtime.advance_container("pick_and_place")  # Move to "grasp"
# ... control loop runs ...
runtime.advance_container("pick_and_place")  # Move to "lift"
```

**Use cases:**
- Multi-phase tasks (reach → grasp → lift → place)
- Sequential manipulation
- Progressive task execution

### 3. Graph (Arbitrary DAG)

A **graph** allows **arbitrary transitions** between nodes. Nodes form a directed acyclic graph (DAG).

```yaml
boundaries:
  recovery:
    type: graph
    nodes:
      - node_id: normal
        constraint:
          max_speed: 0.3
        fallback: hold_position
      
      - node_id: error_recovery
        constraint:
          max_speed: 0.05
        fallback: emergency_stop
      
      - node_id: shutdown
        constraint:
          max_speed: 0.0
        fallback: emergency_stop
```

**Activation (Python only, not yet supported via Stackfile):**

```python
runtime.start_task("recovery")         # Start at "normal"
# ... control loop ...
runtime.transition_to("recovery", "error_recovery")  # Jump to error_recovery
# ... control loop ...
runtime.transition_to("recovery", "shutdown")       # Move to shutdown
```

**Use cases:**
- Error recovery flows
- Dynamic task rescheduling
- State machine-based tasks

---

## Constraints (Per Node)

Each node has a **constraint** that L3 evaluates every cycle.

### Available Constraint Types

| Constraint | Type | Example | Behavior |
|-----------|------|---------|----------|
| `max_speed` | float | `0.3` | Reject if joint velocity norm > limit |
| `max_velocity` | list[float] | `[1.0, 1.0, ...]` | Reject if any joint velocity > limit |
| `bounds` | 3×2 floats | `[[-0.5, 0.5], ...]` | Reject if end-effector outside box |
| `upper_limits` | list[float] | `[1.57, ...]` | Reject if joint > limit |
| `lower_limits` | list[float] | `[-1.57, ...]` | Reject if joint < limit |
| `max_force_n` | float | `50.0` | Reject if force/torque norm > limit (sensor required) |
| `callback` | list[string] | `[my_check_fn]` | Reject if callback returns `False` |

### Example: Full Constraint

```yaml
boundaries:
  manipulation:
    type: single
    nodes:
      - node_id: reach_with_force_limit
        constraint:
          max_speed: 0.3                    # Limit joint velocity norm
          max_velocity: [1.5, 1.5, 1.5, 1.5, 1.5, 0.5]  # Per-joint limits
          bounds: [[-0.35, 0.35], [-0.05, 0.45], [0.01, 0.40]]
          max_force_n: 50.0                 # Limit contact force
          callback: [validate_trajectory]   # Custom checks
        fallback: hold_position
        timeout_sec: 20.0
```

### Evaluation Order

L3 evaluates constraints **in this order**. First failure stops evaluation:

1. **max_speed** — velocity norm
2. **bounds** — end-effector position
3. **max_force_n** — force sensor (if available)
4. **callback** — user-provided checks
5. **timeout_sec** — node duration

```python
# Pseudocode
def evaluate_constraint(action, obs, constraint):
    # Check 1: Velocity
    if velocity_norm(action) > constraint.max_speed:
        return REJECT
    
    # Check 2: Workspace
    if not in_bounds(fk(action), constraint.bounds):
        return REJECT
    
    # Check 3: Force
    if hasattr(constraint, 'max_force_n'):
        if force_norm(obs.force) > constraint.max_force_n:
            return REJECT
    
    # Check 4: Callbacks
    for cb_name in constraint.callback:
        if not callbacks[cb_name](obs, constraint):
            return REJECT
    
    # Check 5: Timeout
    if node.active_time > constraint.timeout_sec:
        return REJECT
    
    return PASS
```

---

## Fallback Strategies

When a boundary constraint is violated, what happens? That's determined by the **fallback strategy**.

### Available Fallbacks

| Strategy | Behavior | Use Case |
|----------|----------|----------|
| `hold_position` | Command zero velocity; stay put | Normal violations |
| `safe_retreat` | Move at low speed along predefined retreat path | Error recovery |
| `emergency_stop` | Stop all motion immediately; activate E-Stop | Critical failures |

### Configuration

```yaml
boundaries:
  reach:
    nodes:
      - node_id: reach
        fallback: hold_position      # Hold if constraint violated
        
      - node_id: approach_fragile
        fallback: emergency_stop     # E-Stop if we get near fragile object
```

### Fallback Escalation

DAM can chain fallbacks: if the first fallback fails, escalate to the next.

```python
from dam.fallback.chain import build_escalation_chain

fallback_registry = FallbackRegistry()
fallback_registry.register(HoldPosition())
fallback_registry.register(SafeRetreat(retreat_joint_positions=[...]))
fallback_registry.register(EmergencyStop())

build_escalation_chain(fallback_registry)
# Chain: hold → retreat → e-stop
```

---

## Workspace Bounds (Common Pattern)

The most common constraint type is **workspace bounds** — define a 3D box the end-effector cannot leave.

```yaml
boundaries:
  table_workspace:
    type: single
    nodes:
      - node_id: on_table
        constraint:
          # Workspace is 70 cm wide, 50 cm deep, 40 cm tall
          bounds:
            - [-0.35, 0.35]        # x: ±35 cm
            - [-0.05, 0.45]        # y: 5–45 cm
            - [0.01, 0.40]         # z: 1–40 cm
        fallback: hold_position
```

**How bounds are checked:**
1. Compute end-effector position via forward kinematics
2. Check if position is inside `[x_min..x_max, y_min..y_max, z_min..z_max]`
3. If outside → REJECT

**Coordinate system:** Relative to the robot base (usually the mount point).

---

## Multi-Boundary Tasks

A task can activate **multiple boundaries simultaneously**. They all apply.

```yaml
tasks:
  complex_manipulation:
    boundaries:
      - workspace_limits    # Always active
      - safety_zone         # Always active
      - task_specific_reach # Phase-dependent
```

**Evaluation:** All active boundaries are checked. If **any** rejects, the action is rejected.

---

## Task Activation

Tasks are the **entry point** for boundary execution. A task references one or more boundary containers.

```yaml
tasks:
  pick_and_place:
    boundaries:
      - pick_and_place         # Main task boundary
      - always_safe_zone       # Always active (workspace limit)
  
  idle:
    boundaries:
      - idle
```

**Starting a task:**

```python
runtime.start_task("pick_and_place")
# Now the boundary containers ["pick_and_place", "always_safe_zone"] are active
```

---

## Advanced: Custom Callbacks

For constraints beyond position/velocity/force, use **callbacks**.

### Define a Callback

```python
import dam

@dam.callback
def validate_grasp_target(obs, state, constraint):
    """
    Check if the proposed action moves toward the grasp target.
    Return True to pass, False to reject.
    """
    target = state.grasp_target  # Set by your task logic
    current_ee_pos = obs.end_effector_pos
    
    # Compute distance to target
    distance = np.linalg.norm(current_ee_pos - target)
    
    # Reject if we're moving away from target
    if distance > state.last_distance:
        return False  # REJECT
    
    state.last_distance = distance
    return True  # PASS

@dam.callback
def force_limited_grasp(obs, state, constraint):
    """Reject if contact force exceeds a threshold."""
    return obs.force_norm < 30.0  # Max 30 N
```

### Register in Stackfile

```yaml
boundaries:
  grasp_phase:
    type: single
    nodes:
      - node_id: grasp
        constraint:
          max_speed: 0.05
          callback: [validate_grasp_target, force_limited_grasp]
        fallback: hold_position
        timeout_sec: 5.0
```

### Callback Signature

```python
def my_callback(obs: Observation, state: RuntimeState, constraint: BoundaryConstraint) -> bool:
    """
    Parameters:
      obs: Current observation (sensor readings)
      state: Runtime state (task variables, history)
      constraint: The boundary constraint being evaluated
    
    Returns:
      True: constraint passed
      False: constraint failed (action will be rejected)
    """
    ...
```

---

## Design Patterns

### Pattern 1: Nested Workspace Boundaries

Start conservative, loosen as task progresses.

```yaml
boundaries:
  pick_and_place:
    type: list
    nodes:
      # Phase 1: Conservative approach
      - node_id: safe_approach
        constraint:
          bounds: [[-0.2, 0.2], [0.1, 0.3], [0.0, 0.2]]
          max_speed: 0.1
        timeout_sec: 10.0
      
      # Phase 2: Tighter bound for precision
      - node_id: precision_grasp
        constraint:
          bounds: [[-0.05, 0.05], [0.15, 0.25], [0.0, 0.1]]
          max_speed: 0.01
        timeout_sec: 5.0
      
      # Phase 3: Lift phase (larger space)
      - node_id: lift
        constraint:
          bounds: [[-0.3, 0.3], [0.05, 0.45], [0.0, 0.5]]
          max_speed: 0.1
        timeout_sec: 10.0
```

### Pattern 2: Error Recovery

Chain recovery boundaries for different failure modes.

```yaml
boundaries:
  main_task:
    type: list
    nodes:
      - node_id: normal_reach
        fallback: safe_retreat

  error_recovery:
    type: single
    nodes:
      - node_id: recover
        constraint:
          max_speed: 0.05
        fallback: hold_position

tasks:
  with_recovery:
    boundaries: [main_task, error_recovery]
```

### Pattern 3: Force-Limited Interaction

Use force bounds for soft manipulation.

```yaml
boundaries:
  soft_assembly:
    type: single
    nodes:
      - node_id: insert
        constraint:
          max_force_n: 10.0        # Max 10 N contact force
          max_speed: 0.01           # Very slow
          callback: [check_insertion_progress]
        fallback: hold_position
        timeout_sec: 30.0
```

### Pattern 4: Always-On Safety Zone

Define a global workspace limit active during all tasks.

```yaml
boundaries:
  global_safety:
    type: single
    nodes:
      - node_id: safe_space
        constraint:
          bounds: [[-0.5, 0.5], [-0.2, 0.6], [-0.1, 1.5]]
        fallback: emergency_stop

tasks:
  any_task:
    boundaries:
      - global_safety      # Always active
      - task_specific      # Task-dependent
```

---

## Debugging Boundaries

### Validate Stackfile

```bash
dam validate --stack mystack.yaml
```

### Inspect Active Boundaries

```python
runtime.start_task("my_task")
active = runtime.get_active_boundaries()
print(active)  # ["boundary_1", "boundary_2", ...]
```

### Log Boundary Violations

```python
result = runtime.step()
if result.was_rejected:
    print(f"Rejected by guard: {result.rejecting_guard}")
    print(f"Reason: {result.decision_reason}")
```

### Replay MCAP Buffer

```bash
# Export violations to JSON
curl http://localhost:8080/api/risk-log/export/json > violations.json

# Analyze with mcap CLI
mcap cat violations.mcap | jq '.[] | select(.rejecting_guard == "L3")'
```

---

## Best Practices

1. **Start Conservative**  
   Define tight bounds, then loosen as you validate behavior.

2. **Use Multiple Boundaries**  
   Combine global safety zone (always active) + task-specific boundaries.

3. **Test Fallbacks**  
   Verify that your fallback strategies work before deploying.

4. **Monitor Violations**  
   Track when boundaries are hit. High violation rates indicate need for adjustment.

5. **Callback Simplicity**  
   Keep callbacks simple (< 5 ms execution). Complex logic belongs in the policy.

6. **Version Boundaries**  
   Keep Stackfiles in version control. Track which boundary versions worked for which tasks.

---

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Workspace bounds too tight | Increase bounds; retest |
| Bounds in wrong coordinate frame | Verify base frame matches your robot |
| Timeout too short | Increase `timeout_sec` or test in simulation first |
| Callback always rejects | Add logging to debug; simplify logic |
| Force limit too low | Calibrate force sensor; adjust threshold |
| Fallback causes thrashing | Use less aggressive fallback (hold instead of e-stop) |

---

## Next Steps

- **Configure guards** → [Guard Stack Explained](guards-explained.md)
- **Deploy with examples** → [Quick Start Guide](../quick-stack.md)
- **Monitor execution** → [DAM Console](../console.md)
- **Full reference** → [Specification](../DAM_Specification.md)

---

## Examples

See the `examples/stackfiles/` directory in the repository for complete Stackfile examples:
- `sim_demo.yaml` — Simulation with basic boundaries
- `so101_act_pick_place.yaml` — SO-ARM101 pick-and-place with multi-phase boundaries
- `mobile_manipulation.yaml` — Mobile base + arm with integrated safety zones
