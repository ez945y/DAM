"""Isaac Sim 6.0 adapter — bridges DAM safety pipeline with NVIDIA Isaac Sim.

Usage (minimal):

    from dam.adapter.isaac import IsaacSafetyFilter

    filter = IsaacSafetyFilter("safety.yaml", task="manipulation")
    # In your Isaac Sim step loop:
    safe_action = filter(action_tensor, obs_tensor)

Usage (full runner with MCAP logging):

    import dam
    runner = dam.build_runner("franka_isaac.yaml")
    runner.connect()
    runner.start(task="pick_place")
"""

from dam.adapter.isaac.filter import IsaacSafetyFilter

__all__ = ["IsaacSafetyFilter"]
