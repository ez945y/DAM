# Copyright (c) 2024, Robot Control Library
# SPDX-License-Identifier: BSD-3-Clause
# ruff: noqa: I001  -- import order is intentional: Isaac modules must be
# imported only after AppLauncher starts the simulation app.

"""
Twin-lane Jetbot DAM boundary demo (Isaac Lab).

Both cars chase the same scripted 2D target stream. The RAW car applies its
nominal differential-drive command directly. The DAM car sends the same
``[v, omega]`` command through robot-dam's SafetyGuard first; a diff-drive /
Ackermann solver rolls the command forward and guards against entering the red
boundary bands. The ``[v, omega]`` action layout is declared by the
``jetbot_diff_drive`` preset (assets/presets.yaml); the stackfile is
examples/stackfiles/jetbot_lane_safety.yaml.

Prerequisites:
    pip install robot-dam[isaac]   # plus the user's Isaac Lab controll_scripts

Usage:
    python examples/isaac_jetbot_lane_demo.py
    python examples/isaac_jetbot_lane_demo.py --target-scale 1.25
"""

import argparse
import math
import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from isaaclab.app import AppLauncher

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

parser = argparse.ArgumentParser(description="Twin-lane Jetbot RAW vs DAM boundary demo")
parser.add_argument("--num_envs", type=int, default=1)
parser.add_argument("--steps", type=int, default=960, help="Scripted replay length")
parser.add_argument(
    "--target-scale",
    type=float,
    default=1.15,
    help="How aggressively the raw target pushes into red bands",
)
parser.add_argument("--drive-gain", type=float, default=4.2, help="Target follower drive gain")
parser.add_argument("--log-every", type=int, default=60)
parser.add_argument(
    "--stackfile",
    type=str,
    default=None,
    help="DAM stackfile (default: bundled examples/stackfiles/jetbot_lane_safety.yaml)",
)
parser.add_argument(
    "--summary-path", type=str, default=None, help="Optional Markdown summary path."
)
AppLauncher.add_app_launcher_args(parser)
args_cli = parser.parse_args()
if args_cli.num_envs != 1:
    parser.error("Twin-lane Jetbot demo currently supports --num_envs 1 only.")
if args_cli.steps <= 0:
    parser.error("--steps must be positive.")

from tools.livestream.livestream_support import apply_livestream_defaults

apply_livestream_defaults(args_cli)

app_launcher = AppLauncher(args_cli)
simulation_app = app_launcher.app

import torch

import isaaclab.sim as sim_utils
from isaaclab.actuators import ImplicitActuatorCfg
from isaaclab.assets import AssetBaseCfg
from isaaclab.assets.articulation import ArticulationCfg
from isaaclab.scene import InteractiveScene, InteractiveSceneCfg
from isaaclab.utils.assets import ISAAC_NUCLEUS_DIR

from controll_scripts.safety import JetbotDAMWrapper
from controll_scripts.utils import physx_to_torch

import dam
from dam.kinematics.ackermann import AckermannSolver


# Public solver-registration API: the name you register under IS the name the
# stackfile/preset references (key == type == implementation) — same sugar as
# @dam.register_callback. DAM also ships a built-in "ackermann" factory; this
# registers it again from application code (replace=True) to show the path.
@dam.register_solver_factory("ackermann", capabilities=["rollout"], replace=True)
def make_ackermann(params):
    return AckermannSolver(
        wheel_base=params.get("wheel_base"),
        track_width=params.get("track_width"),
    )


JETBOT_TRACK_WIDTH = 0.12
RAW_LANE_Y = 0.72
DAM_LANE_Y = -0.72
LANE_CENTER_X = 0.42
ARENA_LENGTH = 2.45
ARENA_WIDTH = 0.92
BOTTOM_BAND_X = -0.48
SAFE_X_MIN = -0.28
SAFE_X_MAX = 1.20
SAFE_Y_LIMIT = 0.24
SIDE_BAND_WIDTH = 0.12
START_X = 0.18
START_LOCAL_Y = 0.0

JETBOT_CONFIG = ArticulationCfg(
    spawn=sim_utils.UsdFileCfg(usd_path=f"{ISAAC_NUCLEUS_DIR}/Robots/NVIDIA/Jetbot/jetbot.usd"),
    actuators={
        "wheel_acts": ImplicitActuatorCfg(joint_names_expr=[".*"], damping=None, stiffness=None)
    },
)


def _cuboid(
    size: tuple[float, float, float],
    color: tuple[float, float, float],
    *,
    collision: bool = False,
) -> sim_utils.CuboidCfg:
    return sim_utils.CuboidCfg(
        size=size,
        collision_props=sim_utils.CollisionPropertiesCfg(collision_enabled=collision),
        visual_material=sim_utils.PreviewSurfaceCfg(diffuse_color=color),
    )


def create_scene_cfg() -> type:
    red = (0.92, 0.04, 0.04)
    green = (0.03, 0.45, 0.16)
    raw_floor_color = (0.36, 0.07, 0.06)
    dam_floor_color = (0.04, 0.24, 0.11)
    white = (0.86, 0.86, 0.86)

    class SceneCfg(InteractiveSceneCfg):
        ground = AssetBaseCfg(
            prim_path="/World/defaultGroundPlane", spawn=sim_utils.GroundPlaneCfg()
        )
        dome_light = AssetBaseCfg(
            prim_path="/World/Light",
            spawn=sim_utils.DomeLightCfg(intensity=3800.0, color=(0.85, 0.85, 0.85)),
        )

        raw_floor = AssetBaseCfg(
            prim_path="/World/RawArena/Floor",
            spawn=_cuboid((ARENA_LENGTH, ARENA_WIDTH, 0.012), raw_floor_color),
            init_state=AssetBaseCfg.InitialStateCfg(pos=(LANE_CENTER_X, RAW_LANE_Y, 0.006)),
        )
        dam_floor = AssetBaseCfg(
            prim_path="/World/DamArena/Floor",
            spawn=_cuboid((ARENA_LENGTH, ARENA_WIDTH, 0.012), dam_floor_color),
            init_state=AssetBaseCfg.InitialStateCfg(pos=(LANE_CENTER_X, DAM_LANE_Y, 0.006)),
        )
        raw_safe = AssetBaseCfg(
            prim_path="/World/RawArena/SafeRegion",
            spawn=_cuboid((SAFE_X_MAX - SAFE_X_MIN, SAFE_Y_LIMIT * 2.0, 0.018), green),
            init_state=AssetBaseCfg.InitialStateCfg(
                pos=((SAFE_X_MIN + SAFE_X_MAX) / 2.0, RAW_LANE_Y, 0.018)
            ),
        )
        dam_safe = AssetBaseCfg(
            prim_path="/World/DamArena/SafeRegion",
            spawn=_cuboid((SAFE_X_MAX - SAFE_X_MIN, SAFE_Y_LIMIT * 2.0, 0.018), green),
            init_state=AssetBaseCfg.InitialStateCfg(
                pos=((SAFE_X_MIN + SAFE_X_MAX) / 2.0, DAM_LANE_Y, 0.018)
            ),
        )

        raw_left_forbidden = AssetBaseCfg(
            prim_path="/World/RawArena/LeftForbidden",
            spawn=_cuboid((ARENA_LENGTH, SIDE_BAND_WIDTH, 0.035), red),
            init_state=AssetBaseCfg.InitialStateCfg(
                pos=(LANE_CENTER_X, RAW_LANE_Y + ARENA_WIDTH / 2.0 - SIDE_BAND_WIDTH / 2.0, 0.026)
            ),
        )
        raw_right_forbidden = AssetBaseCfg(
            prim_path="/World/RawArena/RightForbidden",
            spawn=_cuboid((ARENA_LENGTH, SIDE_BAND_WIDTH, 0.035), red),
            init_state=AssetBaseCfg.InitialStateCfg(
                pos=(LANE_CENTER_X, RAW_LANE_Y - ARENA_WIDTH / 2.0 + SIDE_BAND_WIDTH / 2.0, 0.026)
            ),
        )
        raw_bottom_forbidden = AssetBaseCfg(
            prim_path="/World/RawArena/BottomForbidden",
            spawn=_cuboid((0.32, ARENA_WIDTH, 0.035), red),
            init_state=AssetBaseCfg.InitialStateCfg(pos=(BOTTOM_BAND_X, RAW_LANE_Y, 0.026)),
        )
        dam_left_forbidden = AssetBaseCfg(
            prim_path="/World/DamArena/LeftForbidden",
            spawn=_cuboid((ARENA_LENGTH, SIDE_BAND_WIDTH, 0.035), red),
            init_state=AssetBaseCfg.InitialStateCfg(
                pos=(LANE_CENTER_X, DAM_LANE_Y + ARENA_WIDTH / 2.0 - SIDE_BAND_WIDTH / 2.0, 0.026)
            ),
        )
        dam_right_forbidden = AssetBaseCfg(
            prim_path="/World/DamArena/RightForbidden",
            spawn=_cuboid((ARENA_LENGTH, SIDE_BAND_WIDTH, 0.035), red),
            init_state=AssetBaseCfg.InitialStateCfg(
                pos=(LANE_CENTER_X, DAM_LANE_Y - ARENA_WIDTH / 2.0 + SIDE_BAND_WIDTH / 2.0, 0.026)
            ),
        )
        dam_bottom_forbidden = AssetBaseCfg(
            prim_path="/World/DamArena/BottomForbidden",
            spawn=_cuboid((0.32, ARENA_WIDTH, 0.035), red),
            init_state=AssetBaseCfg.InitialStateCfg(pos=(BOTTOM_BAND_X, DAM_LANE_Y, 0.026)),
        )
        divider = AssetBaseCfg(
            prim_path="/World/Divider",
            spawn=_cuboid((ARENA_LENGTH, 0.045, 0.045), white),
            init_state=AssetBaseCfg.InitialStateCfg(pos=(LANE_CENTER_X, 0.0, 0.027)),
        )
        raw_badge = AssetBaseCfg(
            prim_path="/World/RawArena/RawBadge",
            spawn=_cuboid((0.42, 0.08, 0.055), (1.0, 0.08, 0.05)),
            init_state=AssetBaseCfg.InitialStateCfg(pos=(1.35, RAW_LANE_Y, 0.05)),
        )
        dam_badge = AssetBaseCfg(
            prim_path="/World/DamArena/DamBadge",
            spawn=_cuboid((0.42, 0.08, 0.055), (0.08, 0.95, 0.22)),
            init_state=AssetBaseCfg.InitialStateCfg(pos=(1.35, DAM_LANE_Y, 0.05)),
        )

        raw_jetbot = JETBOT_CONFIG.replace(prim_path="/World/RawArena/Jetbot")
        dam_jetbot = JETBOT_CONFIG.replace(prim_path="/World/DamArena/Jetbot")

    return SceneCfg


@dataclass
class BoundaryMetrics:
    steps: int = 0
    raw_forbidden_frames: int = 0
    dam_forbidden_frames: int = 0
    max_target_delta: float = 0.0
    raw_min_margin: float = float("inf")
    dam_min_margin: float = float("inf")
    decisions: Counter = field(default_factory=Counter)

    @property
    def interventions(self) -> int:
        return sum(self.decisions[name] for name in ("CLAMP", "REJECT", "FAULT"))


def _raw_target(step: int, total_steps: int, lane_y: float, device: str) -> torch.Tensor:
    progress = step / max(total_steps - 1, 1)
    loop = progress * math.pi * 2.0
    x = 0.42 + args_cli.target_scale * (0.64 * math.sin(loop * 0.86) - 0.35 * math.sin(loop * 1.7))
    local_y = args_cli.target_scale * (0.36 * math.sin(loop * 1.23))
    if 0.58 < progress < 0.78:
        x -= args_cli.target_scale * 0.55
    return torch.tensor([[x, lane_y + local_y]], dtype=torch.float32, device=device)


def _to_local_target(target: torch.Tensor, lane_y: float) -> torch.Tensor:
    local = target.clone()
    local[:, 1] -= lane_y
    return local


def _to_world_target(local_target: torch.Tensor, lane_y: float) -> torch.Tensor:
    world = local_target.clone()
    world[:, 1] += lane_y
    return world


def _decision_color(decision: str) -> str:
    colors = {
        "PASS": "\033[92m",
        "CLAMP": "\033[93m",
        "REJECT": "\033[91m",
        "FAULT": "\033[95m",
    }
    reset = "\033[0m"
    return f"{colors.get(decision, '\033[90m')}{decision:<6}{reset}"


class TwinLaneDAMDemo:
    def __init__(self) -> None:
        self.sim = None
        self.scene = None
        self.raw_jetbot = None
        self.dam_jetbot = None
        self.dam_guard: JetbotDAMWrapper | None = None
        self.sim_dt = 0.0
        self.metrics = BoundaryMetrics()
        self.stackfile = args_cli.stackfile or "examples/stackfiles/jetbot_lane_safety.yaml"

    def setup(self) -> bool:
        sim_cfg = sim_utils.SimulationCfg(device=args_cli.device)
        self.sim = sim_utils.SimulationContext(sim_cfg)
        self.sim.set_camera_view([2.15, -2.2, 1.75], [0.35, 0.0, 0.02])
        self.sim_dt = self.sim.get_physics_dt()

        scene_cfg = create_scene_cfg()(num_envs=1, env_spacing=2.0)
        self.scene = InteractiveScene(scene_cfg)
        self.sim.reset()

        self.raw_jetbot = self.scene["raw_jetbot"]
        self.dam_jetbot = self.scene["dam_jetbot"]
        self.dam_guard = JetbotDAMWrapper(self.stackfile, device=self.sim.device)
        self._reset_robots()

        print("\n" + "=" * 78)
        print("  Twin-Lane Jetbot DAM Boundary Demo")
        print(f"  Stackfile:     {self.stackfile}")
        print("  Red bands:     forbidden left/right/bottom boundary zones")
        print("  Green region:  allowed target region")
        print("  RAW lane:      applies nominal [v, omega] directly")
        print("  DAM lane:      applies SafetyGuard-validated [v, omega]")
        print("=" * 78 + "\n")
        return True

    def run(self) -> None:
        for step in range(args_cli.steps):
            if not simulation_app.is_running():
                return

            raw_target = _raw_target(step, args_cli.steps, RAW_LANE_Y, self.sim.device)
            dam_raw_target = _raw_target(step, args_cli.steps, DAM_LANE_Y, self.sim.device)
            raw_command = self._target_to_command(self.raw_jetbot, raw_target)
            dam_raw_command = self._target_to_command(self.dam_jetbot, dam_raw_target)
            dam_state = self._local_state(self.dam_jetbot, DAM_LANE_Y)
            safe_command = self.dam_guard.filter(dam_raw_command, dam_state)

            raw_wheels = self._command_to_wheels(raw_command)
            dam_wheels = self.dam_guard.command_to_wheels(safe_command)

            self.raw_jetbot.set_joint_velocity_target_index(target=raw_wheels)
            self.dam_jetbot.set_joint_velocity_target_index(target=dam_wheels)
            self.scene.write_data_to_sim()
            self.sim.step()
            self.scene.update(self.sim_dt)

            self._record_step(dam_raw_command, safe_command)
            if step % args_cli.log_every == 0 or step == args_cli.steps - 1:
                self._print_step(step, dam_raw_command, safe_command)

        summary = self._format_summary()
        print(summary)
        if args_cli.summary_path:
            path = Path(args_cli.summary_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(summary, encoding="utf-8")
            print(f"[INFO] Wrote boundary summary: {path}")

    def close(self) -> None:
        if self.dam_guard is not None:
            self.dam_guard.close()

    def _reset_robots(self) -> None:
        self._reset_robot(self.raw_jetbot, RAW_LANE_Y)
        self._reset_robot(self.dam_jetbot, DAM_LANE_Y)
        self.scene.reset()
        self.raw_jetbot.update(dt=self.sim_dt)
        self.dam_jetbot.update(dt=self.sim_dt)

    def _reset_robot(self, robot, lane_y: float) -> None:
        root_pose = robot.data.default_root_pose.torch.clone()
        root_pose[:, 0] = START_X
        root_pose[:, 1] = lane_y + START_LOCAL_Y
        root_pose[:, 2] = 0.05
        robot.write_root_pose_to_sim_index(root_pose=root_pose)
        robot.write_root_velocity_to_sim_index(
            root_velocity=torch.zeros_like(robot.data.default_root_vel.torch)
        )
        robot.write_joint_position_to_sim_index(position=robot.data.default_joint_pos.torch.clone())
        robot.write_joint_velocity_to_sim_index(
            velocity=torch.zeros_like(robot.data.default_joint_vel.torch)
        )

    def _position(self, robot) -> torch.Tensor:
        return physx_to_torch(robot.data.root_pos_w)[:, :2].to(
            device=self.sim.device, dtype=torch.float32
        )

    def _yaw(self, robot) -> torch.Tensor:
        quat = physx_to_torch(robot.data.root_quat_w)
        w, x, y, z = quat[:, 0], quat[:, 1], quat[:, 2], quat[:, 3]
        return torch.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))

    def _local_state(self, robot, lane_y: float) -> torch.Tensor:
        pos = self._position(robot)
        yaw = self._yaw(robot).unsqueeze(1)
        local = pos.clone()
        local[:, 1] -= lane_y
        return torch.cat([local, yaw], dim=1)

    def _target_to_command(self, robot, target_xy: torch.Tensor) -> torch.Tensor:
        pos = self._position(robot)
        state = torch.cat([pos, self._yaw(robot).unsqueeze(1)], dim=1)
        delta = target_xy - pos
        yaw = state[:, 2]
        desired_heading = torch.atan2(delta[:, 1], delta[:, 0])
        heading_error = torch.atan2(
            torch.sin(desired_heading - yaw), torch.cos(desired_heading - yaw)
        )
        distance = torch.linalg.norm(delta, dim=1)
        forward = torch.clamp(args_cli.drive_gain * distance * torch.cos(heading_error), -1.2, 1.2)
        omega = torch.clamp(5.0 * heading_error, -5.0, 5.0)
        return torch.stack([forward, omega], dim=1).to(dtype=torch.float32)

    @staticmethod
    def _command_to_wheels(command: torch.Tensor) -> torch.Tensor:
        left = command[:, 0] - command[:, 1] * JETBOT_TRACK_WIDTH / 2.0
        right = command[:, 0] + command[:, 1] * JETBOT_TRACK_WIDTH / 2.0
        return torch.stack([left, right], dim=1).to(dtype=torch.float32)

    def _record_step(self, raw_command: torch.Tensor, safe_command: torch.Tensor) -> None:
        raw_pos = self._position(self.raw_jetbot)
        dam_pos = self._position(self.dam_jetbot)
        raw_margin = self._safe_margin(_to_local_target(raw_pos, RAW_LANE_Y))
        dam_margin = self._safe_margin(_to_local_target(dam_pos, DAM_LANE_Y))
        decision = self.dam_guard.last_decision

        self.metrics.steps += 1
        self.metrics.decisions[decision] += 1
        self.metrics.max_target_delta = max(
            self.metrics.max_target_delta, self.dam_guard.last_delta
        )
        self.metrics.raw_min_margin = min(self.metrics.raw_min_margin, raw_margin)
        self.metrics.dam_min_margin = min(self.metrics.dam_min_margin, dam_margin)
        if raw_margin < 0.0:
            self.metrics.raw_forbidden_frames += 1
        if dam_margin < 0.0:
            self.metrics.dam_forbidden_frames += 1

    @staticmethod
    def _safe_margin(local_xy: torch.Tensor) -> float:
        x = local_xy[0, 0].item()
        y = local_xy[0, 1].item()
        return min(x - SAFE_X_MIN, SAFE_X_MAX - x, SAFE_Y_LIMIT - abs(y))

    def _print_step(self, step: int, raw_command: torch.Tensor, safe_command: torch.Tensor) -> None:
        tag = _decision_color(self.dam_guard.last_decision)
        raw_v, raw_omega = raw_command[0].tolist()
        safe_v, safe_omega = safe_command[0].tolist()
        print(
            f"[DAM {tag}] step={step:04d} "
            f"raw_cmd=({raw_v:+.2f},{raw_omega:+.2f}) "
            f"safe_cmd=({safe_v:+.2f},{safe_omega:+.2f}) "
            f"delta={self.dam_guard.last_delta:.2f} "
            f"interventions={self.metrics.interventions}"
        )

    def _format_summary(self) -> str:
        decisions = " ".join(
            f"{name}={self.metrics.decisions[name]}"
            for name in ("PASS", "CLAMP", "REJECT", "FAULT")
        )
        return "\n".join(
            [
                "",
                "TWIN-LANE DAM BOUNDARY SUMMARY",
                "Same target stream; RAW applies nominal [v, omega], DAM applies SafetyGuard-validated [v, omega].",
                f"Steps: {self.metrics.steps}",
                f"DAM decisions: {decisions}",
                f"DAM interventions: {self.metrics.interventions} ({self.dam_guard.intervention_rate:.1%})",
                f"Max DAM command correction: {self.metrics.max_target_delta:.2f}",
                f"RAW forbidden-zone frames: {self.metrics.raw_forbidden_frames}",
                f"DAM forbidden-zone frames: {self.metrics.dam_forbidden_frames}",
                f"RAW min safe margin: {self.metrics.raw_min_margin:.3f}m",
                f"DAM min safe margin: {self.metrics.dam_min_margin:.3f}m",
                "",
            ]
        )


def main() -> None:
    demo = TwinLaneDAMDemo()
    try:
        if demo.setup():
            demo.run()
            print(
                "[demo] replay complete; keeping the visualizer loop alive. Press Ctrl+C to exit.",
                flush=True,
            )
            while demo.sim.is_headless_or_exist_active_visualizer():
                demo.sim.step()
    finally:
        demo.close()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
    finally:
        simulation_app.close()
