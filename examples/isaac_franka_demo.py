#!/usr/bin/env python3
"""DAM + Isaac Sim 6.0 — Franka Panda safety-guarded manipulation demo.

Demonstrates the full DAM guard pipeline running inside Isaac Sim:
  - Full runner loop with MCAP logging
  - torch.Tensor I/O (no CPU↔GPU copies on the critical path)
  - Franka Panda joint/velocity/acceleration limits enforced in real-time

Prerequisites:
  pip install robot-dam[isaac]
  # or: pip install robot-dam isaacsim

Usage:
  python examples/isaac_franka_demo.py

For vectorized (multi-env) usage with Isaac Lab:
  See IsaacSafetyFilter.from_articulation() in dam.adapter.isaac
"""

from __future__ import annotations

import torch

import dam
from dam.adapter.isaac import IsaacSafetyFilter

STACKFILE = "examples/stackfiles/franka_safety.yaml"


def demo_single_env():
    """Single-environment demo — SafetyGuard with torch.Tensor."""
    print("=" * 60)
    print("Demo 1: Single env — dam.SafetyGuard + torch.Tensor")
    print("=" * 60)

    guard = dam.SafetyGuard(STACKFILE, task="manipulation")

    # Simulate Isaac Sim joint state (radians, on GPU if available)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    obs = torch.zeros(9, device=device)

    # Safe action — small motion within limits
    safe_action = torch.tensor(
        [0.1, -0.05, 0.02, -0.01, 0.03, 0.01, -0.02, 0.0, 0.0],
        device=device,
    )
    result = guard(safe_action, obs)
    print(f"  Device:    {result.device}")
    print(f"  Input:     {safe_action[:4]}...")
    print(f"  Output:    {result[:4]}...")
    print(f"  Unchanged: {torch.allclose(result, safe_action)}")

    # Dangerous action — violates velocity limits
    dangerous = torch.tensor(
        [3.0, -3.0, 2.0, -2.0, 1.5, 1.0, 0.5, 0.05, 0.05],
        device=device,
    )
    result = guard(dangerous, obs)
    clamped = any(r.decision.name == "CLAMP" for r in guard.last_results)
    print(f"\n  Dangerous: {dangerous[:4]}...")
    print(f"  Clamped:   {result[:4]}...")
    print(f"  Was clamp: {clamped}")

    guard.close()


def demo_vectorized():
    """Multi-environment demo — IsaacSafetyFilter for Isaac Lab."""
    print("\n" + "=" * 60)
    print("Demo 2: Vectorized — IsaacSafetyFilter (N=8 envs)")
    print("=" * 60)

    num_envs = 8
    filt = IsaacSafetyFilter(STACKFILE, task="manipulation", num_envs=num_envs)

    device = "cuda" if torch.cuda.is_available() else "cpu"

    # Batch of observations (N x 9 joints)
    obs_batch = torch.zeros(num_envs, 9, device=device)

    # Batch of actions — some safe, some dangerous
    actions = torch.zeros(num_envs, 9, device=device)
    actions[0, :7] = 0.05  # safe
    actions[1, :7] = 0.1  # safe
    actions[2, 0] = 3.0  # dangerous — velocity violation
    actions[3, :4] = -2.5  # dangerous — multiple joints
    actions[4:, :7] = 0.02  # all safe

    safe_actions = filt(actions, obs_batch)

    print(f"  Input shape:  {actions.shape}")
    print(f"  Output shape: {safe_actions.shape}")
    print(f"  Device:       {safe_actions.device}")

    for i in range(num_envs):
        changed = not torch.allclose(actions[i], safe_actions[i])
        tag = "CLAMPED" if changed else "PASS   "
        print(f"    Env {i}: {tag}  max_delta={torch.abs(actions[i] - safe_actions[i]).max():.4f}")

    filt.close()


def demo_isaac_sim_loop():
    """Pseudo-code showing integration with Isaac Sim 6.0 control loop."""
    print("\n" + "=" * 60)
    print("Demo 3: Isaac Sim integration pattern (pseudo-code)")
    print("=" * 60)

    print("""
    # In your Isaac Sim extension or standalone script:

    from isaacsim import SimulationApp
    app = SimulationApp({"headless": False})

    from omni.isaac.core import World
    from omni.isaac.franka import Franka
    from dam.adapter.isaac import IsaacSafetyFilter

    world = World(physics_dt=1/1000)
    franka = world.scene.add(Franka(prim_path="/World/Franka"))
    world.reset()

    # Build filter from articulation (auto-detects joint names + num_envs)
    filt = IsaacSafetyFilter.from_articulation(
        franka.get_articulation_controller(),
        "franka_safety.yaml",
        task="manipulation",
    )

    # Control loop
    while app.is_running():
        world.step(render=True)
        obs = franka.get_joint_positions()           # torch.Tensor
        action = your_policy(obs)                     # torch.Tensor
        safe_action = filt(action, obs)               # clamped by DAM
        franka.set_joint_positions(safe_action)

    filt.close()
    app.close()
    """)


if __name__ == "__main__":
    demo_single_env()
    demo_vectorized()
    demo_isaac_sim_loop()
    print("\n" + "=" * 60)
    print("All demos completed.")
    print("=" * 60)
