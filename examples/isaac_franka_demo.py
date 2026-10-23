#!/usr/bin/env python3
"""Offline Franka-shaped tensor and batch validation examples.

Run: python -m examples.isaac_franka_demo
No simulator or device is launched. SDK validation uses CPU conversion and
preserves the returned tensor device/dtype; this is not a 1 kHz timing claim.
"""

from __future__ import annotations

from pathlib import Path

import torch

import dam
from dam.adapter.isaac import IsaacSafetyFilter

STACKFILE = str(Path(__file__).parent / "stackfiles" / "franka_safety.yaml")


def demo_single_env():
    """Single-environment demo — Guardrail with torch.Tensor."""
    print("=" * 60)
    print("Demo 1: Single env — dam.Guardrail + torch.Tensor")
    print("=" * 60)

    guard = dam.Guardrail(STACKFILE, task="manipulation", quiet=True)

    try:
        # Simulate Isaac Sim joint state (radians, on GPU if available)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        obs = torch.tensor([0, -0.5, 0, -1.5, 0, 1.0, 0, 0.02, 0.02], device=device)

        # Safe action — small motion within limits
        safe_action = obs.clone()
        safe_action[0] += 0.0001
        result = guard({"joints": obs, "action": safe_action})
        torch.testing.assert_close(result, safe_action)
        assert all(r.decision == dam.GuardDecision.PASS for r in guard.last_results)
        print(f"  Device:    {result.device}")
        print(f"  Input:     {safe_action[:4]}...")
        print(f"  Output:    {result[:4]}...")
        print(f"  Unchanged: {torch.allclose(result, safe_action)}")

        # Dangerous action — violates velocity limits
        dangerous = torch.tensor(
            [3.0, -3.0, 2.0, -2.0, 1.5, 1.0, 0.5, 0.05, 0.05],
            device=device,
        )
        result = guard({"joints": obs, "action": dangerous})
        decision = max(r.decision for r in guard.last_results)
        assert decision in (dam.GuardDecision.CLAMP, dam.GuardDecision.REJECT)
        assert torch.isfinite(result).all()
        print(f"\n  Dangerous: {dangerous[:4]}...")
        print(f"  Clamped:   {result[:4]}...")
        print(f"  Decision:  {decision.name}")

    finally:
        guard.close()


def demo_vectorized():
    """Multi-environment demo — IsaacSafetyFilter for Isaac Lab."""
    print("\n" + "=" * 60)
    print("Demo 2: Vectorized — IsaacSafetyFilter (N=8 envs)")
    print("=" * 60)

    num_envs = 8
    filt = IsaacSafetyFilter(STACKFILE, task="manipulation", num_envs=num_envs)

    try:
        device = "cuda" if torch.cuda.is_available() else "cpu"

        # Batch of observations (N x 9 joints)
        obs_batch = torch.tensor([0, -0.5, 0, -1.5, 0, 1.0, 0, 0.02, 0.02], device=device).repeat(
            num_envs, 1
        )

        # Batch of actions — some safe, some dangerous
        actions = obs_batch.clone()
        actions[:, 0] += 0.0001
        actions[2, 0] = 3.0
        actions[3, 0] = -3.0

        safe_actions = filt(actions, obs_batch)

        print(f"  Input shape:  {actions.shape}")
        print(f"  Output shape: {safe_actions.shape}")
        print(f"  Device:       {safe_actions.device}")

        for i in range(num_envs):
            decision = max(r.decision for r in filt.last_results[i])
            assert decision != dam.GuardDecision.FAULT
            if decision == dam.GuardDecision.REJECT:
                torch.testing.assert_close(safe_actions[i], obs_batch[i])
            tag = decision.name
            print(
                f"    Env {i}: {tag}  max_delta={torch.abs(actions[i] - safe_actions[i]).max():.4f}"
            )

    finally:
        filt.close()


if __name__ == "__main__":
    demo_single_env()
    demo_vectorized()
    print("\n" + "=" * 60)
    print("Offline tensor and batch checks completed; no simulator was launched.")
    print("=" * 60)
