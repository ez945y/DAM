#!/usr/bin/env python3
"""DAM + LeRobot safe recording — three integration levels.

Demonstrates how to safety-guard actions during IL (Imitation Learning)
data collection.  No hardware required — run this example directly:

    python examples/safe_record.py

Three API levels, from simplest to most powerful:
  Level 1: dam.safe()             — one-liner, stateless
  Level 2: dam.SafetyGuard       — stateful, best performance
  Level 3: dam.SafetyProcessorStep — native lerobot pipeline integration
"""

import numpy as np

import dam

# Shared config
STACKFILE = "examples/stackfiles/safety.yaml"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Level 1: dam.safe() — one-liner for notebooks and scripts
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
print("=" * 60)
print("Level 1: dam.safe() — one-liner")
print("=" * 60)

# Using ndarray format (radians, degrees_mode=False)
obs = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0])

# Safe action — within all limits
safe_action = np.array([0.04, -0.03, 0.02, 0.01, -0.02, 0.0])
result = dam.safe(
    safe_action,
    obs,
    stackfile=STACKFILE,
    degrees_mode=False,
    joint_names=[
        "shoulder_pan",
        "shoulder_lift",
        "elbow_flex",
        "wrist_flex",
        "wrist_roll",
        "gripper",
    ],
)
print(f"  Safe input:    {safe_action}")
print(f"  Output:        {result}")
print(f"  Unchanged:     {np.allclose(result, safe_action)}")

# Dangerous action — violates position limits
dangerous_action = np.array([3.0, -3.0, 0.02, 0.01, -0.02, 0.0])
result = dam.safe(
    dangerous_action,
    obs,
    stackfile=STACKFILE,
    degrees_mode=False,
    joint_names=[
        "shoulder_pan",
        "shoulder_lift",
        "elbow_flex",
        "wrist_flex",
        "wrist_roll",
        "gripper",
    ],
)
print(f"\n  Dangerous input: {dangerous_action}")
print(f"  Clamped output:  {result}")
print("  (clamped by velocity limit — can't jump 3 rad in one step)")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Level 2: dam.SafetyGuard — stateful guard for recording loops
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
print("\n" + "=" * 60)
print("Level 2: dam.SafetyGuard — stateful guard")
print("=" * 60)

# SafetyGuard auto-detects joint_names and degrees_mode from the preset
guard = dam.SafetyGuard(STACKFILE, task="record")
print(f"  Auto-detected: joint_names={guard._joint_names}")
print(f"  Auto-detected: degrees_mode={guard._degrees_mode}")

# Simulate a teleop recording loop (dict format, degrees)
print("\n  Simulating 10-step recording loop (dict format, degrees):")
position = 30.0  # start at 30°
for step in range(10):
    obs_dict = {
        "shoulder_pan.pos": position,
        "shoulder_lift.pos": 0.0,
        "elbow_flex.pos": 0.0,
        "wrist_flex.pos": 0.0,
        "wrist_roll.pos": 0.0,
        "gripper.pos": 50.0,
    }
    # Teleop wants to move +3° per step (may be too fast for velocity limit)
    target = position + 3.0
    action_dict = {
        "shoulder_pan.pos": target,
        "shoulder_lift.pos": 0.0,
        "elbow_flex.pos": 0.0,
        "wrist_flex.pos": 0.0,
        "wrist_roll.pos": 0.0,
        "gripper.pos": 50.0,
    }
    safe_dict = guard(action_dict, obs_dict)
    actual_move = safe_dict["shoulder_pan.pos"] - position
    was_clamped = any(r.decision.name == "CLAMP" for r in guard.last_results)
    tag = "CLAMP" if was_clamped else "PASS "
    print(
        f"    Step {step:2d}: {tag}  target=+3.0°  actual=+{actual_move:.2f}°  pos={safe_dict['shoulder_pan.pos']:.1f}°"
    )
    position = safe_dict["shoulder_pan.pos"]

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Level 3: SafetyProcessorStep — lerobot pipeline integration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
print("\n" + "=" * 60)
print("Level 3: SafetyProcessorStep — lerobot integration")
print("=" * 60)

from dam import SafetyProcessorStep

step_proc = SafetyProcessorStep(STACKFILE, task="record")
print(f"  Processor step created: {step_proc}")
print(f"  Lazy guard (not initialized yet): {step_proc._guard is None}")

# Simulate calling it as lerobot would
transition = {
    "observation": {
        "shoulder_pan.pos": 50.0,
        "shoulder_lift.pos": 0.0,
        "elbow_flex.pos": 0.0,
        "wrist_flex.pos": 0.0,
        "wrist_roll.pos": 0.0,
        "gripper.pos": 50.0,
    },
    "action": {
        "shoulder_pan.pos": 52.0,  # +2° — safe step
        "shoulder_lift.pos": 0.0,
        "elbow_flex.pos": 0.0,
        "wrist_flex.pos": 0.0,
        "wrist_roll.pos": 0.0,
        "gripper.pos": 50.0,
    },
    "reward": None,
    "done": None,
    "truncated": None,
    "info": None,
    "complementary_data": None,
}
result_transition = step_proc(transition)
print(f"  After first call, guard initialized: {step_proc._guard is not None}")
print(f"  Action output: shoulder_pan={result_transition['action']['shoulder_pan.pos']:.1f}°")

# Show how to use it in real lerobot recording:
print("\n  ── Integration with lerobot-record ──")
print("  Add ONE line to your recording setup:")
print()
print("    from lerobot.processor.factory import make_default_processors")
print("    from dam import SafetyProcessorStep")
print()
print("    teleop, robot_action, obs = make_default_processors()")
print('    robot_action.steps.insert(0, SafetyProcessorStep("safety.yaml"))')
print()
print("  Or use the convenience wrapper:")
print()
print("    from dam.processor import make_safe_processors")
print('    teleop, robot_action, obs = make_safe_processors("safety.yaml")')
print()
print("  Or just run:")
print('    make record ARGS="--robot.type=so101_follower ..."')

print("\n" + "=" * 60)
print("All examples completed successfully!")
print("=" * 60)
