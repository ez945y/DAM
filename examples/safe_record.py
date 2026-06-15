#!/usr/bin/env python3
"""DAM + LeRobot safe recording — three integration levels.

Demonstrates how to safety-guard actions during IL (Imitation Learning)
data collection.  No hardware required — run this example directly:

    python examples/safe_record.py

Three API levels, from simplest to most powerful:
  Level 1: dam.guardrail()           — one-liner, stateless
  Level 2: dam.Guardrail             — stateful, best performance
  Level 3: dam.GuardrailProcessorStep — native lerobot pipeline integration
"""

import numpy as np

import dam

# Shared config
STACKFILE = "examples/stackfiles/safety.yaml"
_JOINTS = [
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
]

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Level 1: dam.guardrail() — one-liner for notebooks and scripts
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
print("=" * 60)
print("Level 1: dam.guardrail() — one-liner")
print("=" * 60)

# ndarray format (radians, degrees_mode=False). The input dict carries the
# observation (`joints`) plus the command to validate (`action`).
obs = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0])

safe_action = np.array([0.04, -0.03, 0.02, 0.01, -0.02, 0.0])
result = dam.guardrail(
    {"joints": obs, "action": safe_action},
    STACKFILE,
    degrees_mode=False,
    joint_names=_JOINTS,
)
print(f"  Safe input:    {safe_action}")
print(f"  Output:        {result}")
print(f"  Unchanged:     {np.allclose(result, safe_action)}")

dangerous_action = np.array([3.0, -3.0, 0.02, 0.01, -0.02, 0.0])
result = dam.guardrail(
    {"joints": obs, "action": dangerous_action},
    STACKFILE,
    degrees_mode=False,
    joint_names=_JOINTS,
)
print(f"\n  Dangerous input: {dangerous_action}")
print(f"  Clamped output:  {result}")
print("  (clamped by velocity limit — can't jump 3 rad in one step)")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Level 2: dam.Guardrail — stateful guard for recording loops
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
print("\n" + "=" * 60)
print("Level 2: dam.Guardrail — stateful guard")
print("=" * 60)

# Guardrail auto-detects joint_names and degrees_mode from the preset, and
# prints the obs/action contract it expects on construction.
guard = dam.Guardrail(STACKFILE, task="record")

print("\n  Simulating 10-step recording loop (dict format, degrees):")
position = 30.0  # start at 30°
for step in range(10):
    obs_dict = {f"{n}.pos": 0.0 for n in _JOINTS}
    obs_dict["shoulder_pan.pos"] = position
    obs_dict["gripper.pos"] = 50.0
    # Teleop wants to move +3° per step (may be too fast for the velocity limit)
    action_dict = {f"{n}.pos": 0.0 for n in _JOINTS}
    action_dict["shoulder_pan.pos"] = position + 3.0
    action_dict["gripper.pos"] = 50.0

    safe_dict = guard({**obs_dict, "action": action_dict})
    actual_move = safe_dict["shoulder_pan.pos"] - position
    was_clamped = any(r.decision.name == "CLAMP" for r in guard.last_results)
    tag = "CLAMP" if was_clamped else "PASS "
    print(
        f"    Step {step:2d}: {tag}  target=+3.0°  actual=+{actual_move:.2f}°  "
        f"pos={safe_dict['shoulder_pan.pos']:.1f}°"
    )
    position = safe_dict["shoulder_pan.pos"]

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Level 3: GuardrailProcessorStep — lerobot pipeline integration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
print("\n" + "=" * 60)
print("Level 3: GuardrailProcessorStep — lerobot integration")
print("=" * 60)

from dam import GuardrailProcessorStep

step_proc = GuardrailProcessorStep(STACKFILE, task="record")
print(f"  Lazy guard (not initialized yet): {step_proc._guard is None}")

transition = {
    "observation": {f"{n}.pos": 0.0 for n in _JOINTS} | {"shoulder_pan.pos": 50.0},
    "action": {f"{n}.pos": 0.0 for n in _JOINTS} | {"shoulder_pan.pos": 52.0},
    "reward": None,
    "done": None,
    "truncated": None,
    "info": None,
    "complementary_data": None,
}
result_transition = step_proc(transition)
print(f"  After first call, guard initialized: {step_proc._guard is not None}")
print(f"  Action output: shoulder_pan={result_transition['action']['shoulder_pan.pos']:.1f}°")

print("\n  ── Integration with lerobot-record ──")
print("  Add ONE line to your recording setup:")
print()
print("    from lerobot.processor.factory import make_default_processors")
print("    from dam import GuardrailProcessorStep")
print()
print("    teleop, robot_action, obs = make_default_processors()")
print('    robot_action.steps.insert(0, GuardrailProcessorStep("safety.yaml"))')

print("\n" + "=" * 60)
print("All examples completed successfully!")
print("=" * 60)
