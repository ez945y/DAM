from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

# ── Guard pipeline configs ─────────────────────────────────────────────────
# All guard-specific parameters (model paths, thresholds, joint_position_limits, …)
# live in boundary node ``params`` blocks and reach guards via the config pool.


# ── Boundary / task configs ────────────────────────────────────────────────


class NodeConfig(BaseModel):
    """Unified boundary node configuration.

    Only structural / routing fields live at the top level.
    All constraint parameters (max_speed, bounds, joint_position_limits, …)
    belong inside ``params`` and are forwarded as-is to the Guard / callback.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)
    node_id: str = "default"
    params: dict[str, Any] = {}
    callback: str | None = None
    fallback: str = "emergency_stop"
    timeout_sec: float | None = None


# Container type normalisation: accept both "single"/"list"/"graph"
# and "SingleNodeContainer"/"ListContainer"/"GraphContainer"
_CONTAINER_TYPE_MAP: dict[str, str] = {
    "single": "single",
    "singlenodecontainer": "single",
    "list": "list",
    "listcontainer": "list",
    "graph": "graph",
    "graphcontainer": "graph",
}


class ContainerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str  # normalised to lowercase short form
    nodes: list[NodeConfig]
    layer: str = "L2"  # Default to L2 for safety
    loop: bool = False

    @field_validator("type", mode="before")
    @classmethod
    def normalise_type(cls, v: str) -> str:
        key = v.lower().replace("_", "").replace("-", "")
        if key not in _CONTAINER_TYPE_MAP:
            raise ValueError(
                f"Unknown container type '{v}'. "
                f"Valid values: single, list, graph (or CamelCase variants)"
            )
        return _CONTAINER_TYPE_MAP[key]


# ── Task config ────────────────────────────────────────────────────────────


class TaskConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    boundaries: list[str] = []
    description: str = ""


# ── Safety config ──────────────────────────────────────────────────────────


class SafetyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Phase 2: single string name; Phase 1: list of names. Both accepted.
    always_active: str | list[str] = []
    no_task_behavior: str = "emergency_stop"
    control_frequency_hz: float = 50.0
    max_obs_age_sec: float = 0.1
    cycle_budget_ms: float = 20.0
    # Enforcement mode — controls whether guard decisions block action dispatch.
    # enforce:   full validation; rejected/clamped actions are blocked (production default)
    # monitor:   validation runs and is logged but does NOT block action dispatch
    # log_only:  guard pipeline is skipped; only logs that a cycle occurred
    enforcement_mode: str = "enforce"

    @field_validator("enforcement_mode")
    @classmethod
    def valid_enforcement_mode(cls, v: str) -> str:
        allowed = {"enforce", "monitor", "log_only"}
        if v not in allowed:
            raise ValueError(f"enforcement_mode must be one of {allowed}, got '{v}'")
        return v

    @field_validator("control_frequency_hz")
    @classmethod
    def must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("control_frequency_hz must be positive")
        return v

    def always_active_list(self) -> list[str]:
        """Normalise to list regardless of YAML format."""
        if isinstance(self.always_active, str):
            return [self.always_active] if self.always_active else []
        return self.always_active


# ── Phase 2+ optional top-level sections ──────────────────────────────────
# All use extra="allow" so future keys don't fail validation.
# Schema will be tightened per section when Phase 2 implementation begins.


class HardwareJointConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    limits_rad: list[float] | None = None


class HardwareSourceConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str
    port: str | None = None
    id: str | None = None
    topic: str | None = None
    msg_type: str | None = None
    mapping: dict[str, str] | None = None
    cameras: dict[str, Any] | None = None
    # Absolute or relative path to the calibration directory / file.
    # Supports shared-volume mounts (e.g. /mnt/dam_data/calibration/).
    calibration_path: str | None = None


class HardwareSinkConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    ref: str | None = None
    type: str | None = None
    topic: str | None = None


class HardwareConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    preset: str | None = None
    urdf_path: str | None = None
    joints: dict[str, HardwareJointConfig] | None = None
    sources: dict[str, HardwareSourceConfig] | None = None
    sinks: dict[str, HardwareSinkConfig] | None = None


class PolicyConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str
    pretrained_path: str | None = None
    dataset_repo_id: str | None = None
    device: str = "cpu"
    # Diffusion-specific inference params
    noise_scheduler_type: str | None = None  # e.g. "DDIM"
    num_inference_steps: int | None = None  # e.g. 15


class SimulationConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str
    preset: str | None = None
    scene: str | None = None
    lookahead_steps: int = 10
    # Dataset replay — when set, DatasetSimSource replays from this HF repo
    dataset_repo_id: str | None = None
    episode: int = 0
    # LeRobot SO-101 datasets store joint positions in degrees; set True to
    # convert deg→rad before passing observations into the guard pipeline.
    degrees_mode: bool = True


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    mode: str = "passive"  # managed | passive
    control_frequency_hz: float = 50.0
    max_obs_age_sec: float = 0.1
    cycle_budget_ms: float = 20.0


class LoopbackConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    backend: str = "mcap"  # "mcap" | "pickle"
    output_dir: str = "/tmp/dam_loopback"  # session files written here
    window_sec: float = 10.0  # ring-buffer depth for pre-event images
    pre_event_sec: float = 10.0  # capture N seconds before event (0 = capture all)
    rotate_mb: float = 500.0  # rotate file after this many MB
    rotate_minutes: float = 60.0  # rotate file after this many minutes
    max_queue_depth: int = 256  # drop normal cycles if queue exceeds this
    capture_images_on_clamp: bool = False  # also fetch images on CLAMP events


class RiskControllerConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    window_sec: float = 10.0
    clamp_threshold: int = 5
    reject_threshold: int = 2


# ── Top-level Stackfile ────────────────────────────────────────────────────


class StackfileConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    version: str = "1"
    # Unified architecture
    boundaries: dict[str, ContainerConfig] = {}
    tasks: dict[str, TaskConfig] = {}
    safety: SafetyConfig = SafetyConfig()
    # Hierarchical list of active guards (e.g. [{"L0": "ood"}, {"L1": "preflight"}])
    # Also supports dict format for builtin registration in Phase 1
    guards: list[dict[str, str]] | list[str] | dict[str, Any] = []
    # Phase 2+  (all optional)
    hardware: HardwareConfig | None = None
    policy: PolicyConfig | None = None
    simulation: SimulationConfig | None = None
    runtime: RuntimeConfig | None = None
    loopback: LoopbackConfig | None = None
    risk_controller: RiskControllerConfig | None = None
